"""
Experiment bridge: serves oTree game decisions for MiroFish agents.

Phase 2 uses LLM-free dummy policies so the oTree <-> MiroFish path can be
exercised end-to-end. Phase 3 swaps DecisionPolicy for one that interviews
agents through the step-server.

State is per oTree session (session_code) and kept in memory, with an
append-only JSONL log under uploads/experiments/<session_code>/.
"""

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.experiment_bridge')

EXPERIMENT_DATA_DIR = os.path.join(Config.UPLOAD_FOLDER, 'experiments')

COOPERATE = 'A'
DEFECT = 'B'
CHOICES = (COOPERATE, DEFECT)
POLICIES = ('random', 'allc', 'alld', 'tft')
# How long a retry waits for an in-flight request for the same decision
IN_FLIGHT_WAIT_SEC = 300.0


@dataclass
class BridgeSettings:
    """Per-session knobs. Fault injection is for Phase 2 robustness tests only."""
    policy: str = 'random'
    seed: int = 0
    inject_delay_sec: float = 0.0
    inject_error_rate: float = 0.0


@dataclass
class Decision:
    choice: str
    reason: str
    source: str
    latency_sec: float
    missing: bool = False


@dataclass
class SessionState:
    session_code: str
    settings: BridgeSettings = field(default_factory=BridgeSettings)
    # (round_number, agent_id) -> Decision; makes /decide idempotent under retries
    decisions: Dict[tuple, Decision] = field(default_factory=dict)
    completed_rounds: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    attempts: Dict[tuple, int] = field(default_factory=dict)
    in_flight: Dict[tuple, threading.Event] = field(default_factory=dict)
    injected_errors: int = 0


class InjectedFailure(RuntimeError):
    """Raised on purpose by fault injection."""


class ExperimentBridge:
    def __init__(self, data_dir: str = EXPERIMENT_DATA_DIR):
        self._data_dir = data_dir
        self._sessions: Dict[str, SessionState] = {}
        self._lock = threading.Lock()

    # -- sessions ------------------------------------------------------------

    def _session(self, session_code: str) -> SessionState:
        state = self._sessions.get(session_code)
        if state is None:
            state = SessionState(session_code=session_code)
            self._sessions[session_code] = state
        return state

    def configure(self, session_code: str, **kwargs) -> Dict[str, Any]:
        policy = kwargs.get('policy')
        if policy is not None and policy not in POLICIES:
            raise ValueError(f"unknown policy: {policy} (expected one of {POLICIES})")
        with self._lock:
            state = self._session(session_code)
            for key, value in kwargs.items():
                if value is not None and hasattr(state.settings, key):
                    setattr(state.settings, key, type(getattr(state.settings, key))(value))
            settings = asdict(state.settings)
        self._log(session_code, {'event': 'configure', 'settings': settings})
        return settings

    # -- decide --------------------------------------------------------------

    def decide(
        self,
        session_code: str,
        round_number: int,
        agent_id: int,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Return this agent's choice for the round.

        Args:
            history: previous rounds of this pair, [{"round_number", "own", "partner", "payoff"}]
        """
        key = (round_number, agent_id)
        while True:
            with self._lock:
                state = self._session(session_code)
                cached = state.decisions.get(key)
                if cached is not None:
                    return {**asdict(cached), 'cached': True}
                in_flight = state.in_flight.get(key)
                if in_flight is None:
                    # This request computes the decision
                    in_flight = threading.Event()
                    state.in_flight[key] = in_flight
                    settings = BridgeSettings(**asdict(state.settings))
                    attempt = state.attempts.get(key, 0) + 1
                    state.attempts[key] = attempt
                    break
            # A retry arrived while the first request is still computing (the
            # client timed out). Wait for it instead of computing twice, which
            # would be a second LLM call in Phase 3.
            in_flight.wait(timeout=IN_FLIGHT_WAIT_SEC)

        try:
            decision = self._compute(session_code, round_number, agent_id, history, settings, attempt)
            with self._lock:
                state.decisions[key] = decision
        finally:
            with self._lock:
                state.in_flight.pop(key, None)
            in_flight.set()

        self._log(session_code, {
            'event': 'decide', 'round_number': round_number, 'agent_id': agent_id,
            'attempt': attempt, **asdict(decision),
        })
        return {**asdict(decision), 'cached': False}

    def _compute(self, session_code, round_number, agent_id, history, settings, attempt) -> Decision:
        t0 = time.time()
        if settings.inject_delay_sec > 0:
            time.sleep(settings.inject_delay_sec)
        # Failures are seeded per attempt so a retry can succeed; the policy
        # rng is seeded per (session, round, agent) so choices are reproducible.
        fail_rng = random.Random(f"fail:{settings.seed}:{session_code}:{round_number}:{agent_id}:{attempt}")
        rng = random.Random(f"{settings.seed}:{session_code}:{round_number}:{agent_id}")
        if settings.inject_error_rate > 0 and fail_rng.random() < settings.inject_error_rate:
            with self._lock:
                self._sessions[session_code].injected_errors += 1
            raise InjectedFailure(f"injected failure round={round_number} agent={agent_id}")

        choice, reason = self._apply_policy(settings.policy, rng, history or [])
        return Decision(
            choice=choice,
            reason=reason,
            source=f"bridge_dummy:{settings.policy}",
            latency_sec=round(time.time() - t0, 3),
        )

    @staticmethod
    def _apply_policy(policy: str, rng: random.Random, history: List[Dict[str, Any]]):
        if policy == 'allc':
            return COOPERATE, 'always cooperate'
        if policy == 'alld':
            return DEFECT, 'always defect'
        if policy == 'tft':
            if not history:
                return COOPERATE, 'tit-for-tat: open with cooperation'
            last = history[-1].get('partner')
            if last not in CHOICES:
                return COOPERATE, 'tit-for-tat: partner move unknown'
            return last, 'tit-for-tat: copy partner'
        return rng.choice(CHOICES), 'random'

    # -- barrier ---------------------------------------------------------------

    def round_complete(self, session_code: str, round_number: int, summary: Dict[str, Any]) -> Dict[str, Any]:
        """All players in the session finished the round. Idempotent per round.

        summary["outcomes"] ([{agent_id, choice, source, missing}]) is what oTree
        actually recorded and is authoritative: when a client timed out and used
        its default, the bridge's own cached decision was never applied.
        Phase 4 starts the debate phase from here.
        """
        outcomes = summary.get('outcomes') or []
        with self._lock:
            state = self._session(session_code)
            if round_number in state.completed_rounds:
                return {'accepted': False, 'duplicate': True, 'round_number': round_number}
            decided = sum(1 for (r, _a) in state.decisions if r == round_number)
            mismatches = []
            for o in outcomes:
                bridge_decision = state.decisions.get((round_number, o.get('agent_id')))
                if bridge_decision is not None and bridge_decision.choice != o.get('choice'):
                    mismatches.append({
                        'agent_id': o.get('agent_id'),
                        'bridge_choice': bridge_decision.choice,
                        'recorded_choice': o.get('choice'),
                        'recorded_source': o.get('source'),
                    })
            record = {**summary, 'decided_by_bridge': decided, 'mismatches': mismatches,
                      'received_at': time.time()}
            state.completed_rounds[round_number] = record
        self._log(session_code, {'event': 'round_complete', 'round_number': round_number, **record})
        return {'accepted': True, 'duplicate': False, 'round_number': round_number,
                'decided_by_bridge': decided, 'n_outcomes': len(outcomes),
                'n_mismatches': len(mismatches)}

    # -- state -----------------------------------------------------------------

    def get_state(self, session_code: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._sessions.get(session_code)
            if state is None:
                return None
            per_round: Dict[int, int] = {}
            for (r, _a) in state.decisions:
                per_round[r] = per_round.get(r, 0) + 1
            return {
                'session_code': session_code,
                'settings': asdict(state.settings),
                'decisions_per_round': dict(sorted(per_round.items())),
                'completed_rounds': sorted(state.completed_rounds),
                'injected_errors': state.injected_errors,
                'retried_requests': sum(1 for n in state.attempts.values() if n > 1),
            }

    # -- log -------------------------------------------------------------------

    def _log(self, session_code: str, record: Dict[str, Any]) -> None:
        try:
            session_dir = os.path.join(self._data_dir, session_code)
            os.makedirs(session_dir, exist_ok=True)
            record = {'ts': time.time(), **record}
            with open(os.path.join(session_dir, 'bridge_log.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError as e:
            logger.warning(f"Failed to write bridge log for {session_code}: {e}")


_bridge: Optional[ExperimentBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> ExperimentBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = ExperimentBridge()
        return _bridge
