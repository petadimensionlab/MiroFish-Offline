"""
Experiment bridge: serves oTree game decisions for MiroFish agents.

Policies:
  random / allc / alld / tft   LLM-free dummies (Phase 2)
  llm                          interview agents through the experiment
                               step-server (scripts/run_experiment_env.py)

For the llm policy, decisions for a whole round are prefetched in one
batched GAME_INTERVIEW as soon as the previous round's barrier fires
(round_complete), because the oTree bot runner calls /decide strictly
serially. /decide then waits on the in-flight batch instead of issuing its
own LLM call.

Debate phase (llm policy, Phase 4): before each round's prefetch the
bridge runs, through the step-server,
  round 1:   opening_post (topic seeding), then debate_rounds debate rounds
  round r+1: round r results posted as each agent's own post (or one
             summary post), then debate_rounds debate rounds
so decisions see a timeline shaped by the previous round. debate_rounds=0
is the no-debate treatment.

Pair chat (chat_turns > 0): after the debate phase and before the decision,
the two partners of each pair exchange chat_turns private messages about
the game (cheap talk; the opener alternates by round). Messages are
generated with the game rules, the pair's history and their earlier chat
in the prompt, and the transcript goes into both partners' decision
prompts. The debate rounds alone never turned to the game (NOTES.md #34,
#44, #46), so without this the agents never actually talk to each other.

Network chat (net_topology != none, NOTES.md #54): instead of the partner,
each agent talks one to one with graph neighbours who are NOT its partner /
group member. The graph is fixed per (seed, topology); how many conversations
an agent starts in a round is Poisson(lambda_i) with gamma-distributed lambda_i.
The conversations run in waves (one batched interview each, every agent at
most once per wave) before the round's decisions, which see the agent's own
conversations. PD and pgg only; network.json records graph and lambdas.

Other games (game = pgg | beauty | trust | ultimatum, see games.py):
agents carry group_agent_ids (and role for sequential games). In
sequential games (trust, ultimatum) the round prefetch covers the first
movers only; when oTree reports them with /stage_complete, the second
movers' decisions are prefetched with the first move in their prompts.
The pair chat is PD only.

State is per oTree session (session_code) and kept in memory, with
append-only JSONL logs under <simulation_dir>/game/<session_code>/ for the
llm policy, else uploads/experiments/<session_code>/.
"""

import hashlib
import json
import os
import random
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, Iterable, List, Optional

from ..config import Config
from ..utils.logger import get_logger
from .game_decision import (
    render_decision_prompt, parse_decision, wants_no_think, make_labels, Labels, LABEL_SCHEMES,
    render_belief_prompt, parse_likert, DEFAULT_BELIEF_STATEMENT,
    render_comprehension_prompt, parse_comprehension,
    render_chat_prompt, parse_chat_message, chat_view,
    render_network_chat_prompt, _option_blocks, _shown_history,
)
from . import network_chat as nc
from . import public_goods as pg
from .games import GAMES as GAME_REGISTRY, parse_comprehension as parse_game_comprehension
from .simulation_ipc import SimulationIPCClient, CommandStatus

logger = get_logger('mirofish.experiment_bridge')

EXPERIMENT_DATA_DIR = os.path.join(Config.UPLOAD_FOLDER, 'experiments')

COOPERATE = 'A'
DEFECT = 'B'
CHOICES = (COOPERATE, DEFECT)
DUMMY_POLICIES = ('random', 'allc', 'alld', 'tft')
POLICIES = DUMMY_POLICIES + ('llm',)
DEFAULT_PAYOFFS = dict(R=30, T=50, S=0, P=10)
# How long a /decide waits for an in-flight computation of the same decision
IN_FLIGHT_WAIT_SEC = 1800.0
# One batched GAME_INTERVIEW for a whole round
GAME_INTERVIEW_TIMEOUT_SEC = 900.0
# One RUN_ROUNDS burst of debate_rounds rounds
RUN_ROUNDS_TIMEOUT_SEC = 1800.0
INJECT_MODES = ('none', 'each', 'summary')
LABEL_UNITS = ('session', 'pair', 'agent')
GAMES = ('pd',) + tuple(GAME_REGISTRY)
NET_TOPOLOGIES = nc.NET_TOPOLOGIES
# A message that fails this many times aborts its conversation
NET_MESSAGE_ATTEMPTS = 2
# Runaway guard: more waves than this cannot happen with max_load * turns bounded
NET_MAX_WAVES = 60


@dataclass
class BridgeSettings:
    """Per-session knobs. Fault injection is for Phase 2 robustness tests only."""
    policy: str = 'random'
    seed: int = 0
    game: str = 'pd'                  # pd | pgg | beauty | trust | ultimatum
    game_params: Dict[str, Any] = field(default_factory=dict)  # overrides games.py defaults
    inject_delay_sec: float = 0.0
    inject_error_rate: float = 0.0
    # llm policy
    simulation_id: str = ''
    simulation_dir: str = ''          # overrides simulation_id (tests)
    platform: str = 'twitter'
    include_feed: bool = True
    feed_exclude_own: bool = False    # drop the agent's own posts from its decision feed
    num_rounds: int = 10
    default_choice: str = COOPERATE   # used when the answer cannot be parsed
    no_think: str = 'auto'            # auto (qwen3 only) | on | off
    # What agents see for the internal A (cooperate) / B (defect) (NOTES.md #39, #40)
    label_scheme: str = 'symbols'     # letters | symbols
    label_randomize: bool = True      # mapping and listing order drawn at random
    # session | pair | agent: who shares one random mapping (NOTES.md #43).
    # The pair chat needs partners to share labels, so session or pair.
    label_unit: str = 'session'
    swap_labels: bool = False         # letters, fixed: agents see A/B swapped
    payoffs: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_PAYOFFS))
    # debate phase (llm policy)
    debate_rounds: int = 0            # 0 = no-debate treatment
    debate_players_only: bool = True  # only game agents can be active in debate rounds
    # The simulated clock advances with every debate round, so time-of-day
    # activation leaves debates empty by midday / night (NOTES.md #31)
    debate_ignore_hours: bool = True
    debate_min_active: int = 2
    inject_results: str = 'each'      # none | each | summary
    announcer_agent_id: int = -1      # poster for 'summary'; -1 = lowest agent id
    opening_post: str = ''            # topic seeding before round 1
    opening_agent_id: int = -1        # -1 = announcer
    repeat_opening: bool = False      # re-post opening_post before every debate phase (NOTES.md #34)
    # internal state: Likert item before round 1 and after the last round
    belief_survey: bool = False
    comprehension_check: bool = False  # payoff-table quiz before round 1 (NOTES.md #39)
    belief_statement: str = DEFAULT_BELIEF_STATEMENT
    # pair chat (llm policy): private messages between partners before each decision
    chat_turns: int = 0               # messages per pair per round; 0 = no chat
    chat_memory_rounds: int = 3       # earlier rounds of chat shown in prompts; -1 = all
    chat_max_chars: int = 400
    # network chat (llm policy, PD / pgg, NOTES.md #54): one-to-one talk with
    # graph neighbours who are not the agent's partner / group members
    net_topology: str = 'none'        # none | er | ba | ws | ring
    net_mean_degree: float = 4.0      # er: round(N*k/2) edges; ba: m=max(1,round(k/2)); ws/ring: k even >= 2
    net_ws_p: float = 0.1
    net_seed: int = -1                # -1 = seed; graph and lambdas never depend on the session code
    net_exclude_partners: bool = True  # no edge to the PD partner / pgg group members (#51)
    net_contact_mean: float = 1.0     # mu: mean conversations started per agent and round
    net_contact_dispersion: float = 0.5  # r of the gamma lambda; <= 0: lambda = mu for all
    net_lambda_assign: str = 'random'  # random | degree (largest lambda to highest degree)
    net_max_initiate: int = 3
    net_max_load: int = 4             # initiated + received per agent and round
    net_turns: int = 2                # messages per conversation
    net_memory_rounds: int = 2        # earlier rounds of conversations shown; -1 = all
    net_max_convs_in_prompt: int = 8
    net_identity: str = 'profile'     # profile | anon ("Participant 7")
    # session-wide symbol mapping, but each agent lists the options in its own
    # order: cancels position bias (#49) within one session
    label_order_per_agent: bool = False


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
    # agent_id -> partner agent_id, from /configure
    agents: Dict[int, int] = field(default_factory=dict)
    # round_number -> agent_id -> outcome oTree recorded
    outcomes: Dict[int, Dict[int, Dict[str, Any]]] = field(default_factory=dict)
    # round_number -> pair (lower id, higher id) -> [{"agent_id", "text"}]
    chats: Dict[int, Dict[tuple, List[Dict[str, Any]]]] = field(default_factory=dict)
    # games other than pd: agent_id -> all members of its group (including itself)
    groups: Dict[int, List[int]] = field(default_factory=dict)
    # agent_id -> 1 (first mover / simultaneous) or 2 (second mover)
    roles: Dict[int, int] = field(default_factory=dict)
    # sequential games: round_number -> agent_id -> first movers' recorded decisions
    stage_outcomes: Dict[int, Dict[int, Dict[str, Any]]] = field(default_factory=dict)
    # network chat: {neighbors, lambdas, names, exclusions, ...} from configure, else None
    network: Optional[Dict[str, Any]] = None
    # round_number -> conversations {conv_id, index, round_number, initiator,
    # responder, messages: [{agent_id, text}], aborted}
    net_convs: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)


class InjectedFailure(RuntimeError):
    """Raised on purpose by fault injection."""


class UnknownSession(LookupError):
    """The session was never configured, e.g. the bridge restarted mid-run.

    Serving it with default settings would silently turn an llm run into a
    random-policy run (NOTES.md #23), so callers must see an error.
    """


def _coerce(current, value):
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if isinstance(current, dict):
        return dict(value)
    return type(current)(value)


class ExperimentBridge:
    def __init__(self, data_dir: str = EXPERIMENT_DATA_DIR):
        self._data_dir = data_dir
        self._sessions: Dict[str, SessionState] = {}
        self._lock = threading.Lock()

    # -- sessions ------------------------------------------------------------

    def _session(self, session_code: str, create: bool = False) -> SessionState:
        state = self._sessions.get(session_code)
        if state is None:
            if not create:
                raise UnknownSession(f"session {session_code} is not configured (bridge restarted?)")
            state = SessionState(session_code=session_code)
            self._sessions[session_code] = state
        return state

    def configure(self, session_code: str, agents: Optional[List[Dict[str, int]]] = None,
                  **kwargs) -> Dict[str, Any]:
        """
        Args:
            agents: [{"agent_id", "partner_agent_id"}]; with policy "llm" this
                starts prefetching round 1
        """
        policy = kwargs.get('policy')
        if policy is not None and policy not in POLICIES:
            raise ValueError(f"unknown policy: {policy} (expected one of {POLICIES})")
        scheme = kwargs.get('label_scheme')
        if scheme is not None and scheme not in LABEL_SCHEMES:
            raise ValueError(f"unknown label_scheme: {scheme} (expected one of {LABEL_SCHEMES})")
        unit = kwargs.get('label_unit')
        if unit is not None and unit not in LABEL_UNITS:
            raise ValueError(f"unknown label_unit: {unit} (expected one of {LABEL_UNITS})")
        game = kwargs.get('game')
        if game is not None and game not in GAMES:
            raise ValueError(f"unknown game: {game} (expected one of {GAMES})")
        topology = kwargs.get('net_topology')
        if topology is not None and topology not in NET_TOPOLOGIES:
            raise ValueError(f"unknown net_topology: {topology} (expected one of {NET_TOPOLOGIES})")
        inject = kwargs.get('inject_results')
        if inject is not None and inject not in INJECT_MODES:
            raise ValueError(f"unknown inject_results: {inject} (expected one of {INJECT_MODES})")
        with self._lock:
            state = self._session(session_code, create=True)
            for key, value in kwargs.items():
                if value is not None and hasattr(state.settings, key):
                    setattr(state.settings, key, _coerce(getattr(state.settings, key), value))
            if agents:
                if state.settings.game != 'pd':
                    state.groups = {int(a['agent_id']): [int(m) for m in a['group_agent_ids']] for a in agents}
                    state.roles = {int(a['agent_id']): int(a.get('role', 1)) for a in agents}
                    state.agents = {a: -1 for a in state.groups}  # no partner
                else:
                    state.agents = {int(a['agent_id']): int(a['partner_agent_id']) for a in agents}
            game_conflict = state.settings.game != 'pd' and (
                state.settings.chat_turns > 0 or state.settings.inject_results == 'summary')
            if game_conflict:
                del self._sessions[session_code]
                raise ValueError(f"game {state.settings.game!r} supports neither chat_turns > 0 "
                                 "nor inject_results 'summary'")
            if state.settings.chat_turns > 0 and state.settings.label_unit == 'agent':
                # partners would name the options with different symbols. Drop
                # the session so later calls fail visibly (409) instead of
                # running a half-configured one.
                del self._sessions[session_code]
                raise ValueError("chat_turns > 0 needs label_unit 'session' or 'pair', not 'agent'")
            net_error = self._network_conflict(state) if state.settings.net_topology != 'none' else None
            if net_error:
                del self._sessions[session_code]
                raise ValueError(net_error)
            settings = asdict(state.settings)
            if settings['policy'] == 'llm':
                self._simulation_dir(state.settings)  # validate early
            network = None
            if state.settings.net_topology != 'none' and state.agents:
                try:
                    network = self._build_network(state)
                except ValueError:
                    del self._sessions[session_code]
                    raise
                state.network = network
        if network is not None:
            self._write_json(session_code, 'network.json', network['file'])
            self._log(session_code, {'event': 'network_built', 'topology': state.settings.net_topology,
                                     'stats': network['file']['stats']})
        self._log(session_code, {'event': 'configure', 'settings': settings,
                                 'n_agents': len(state.agents),
                                 'labels': self._labels_summary(session_code, state)})
        if settings['policy'] == 'llm' and state.agents:
            def opening():
                if state.settings.comprehension_check:
                    self.comprehension_check(session_code)
                if state.settings.belief_survey:
                    self.belief_survey(session_code, 'pre')
                self._debate_phase(session_code, state.settings, 0, [])
            self.prefetch(session_code, 1, agent_ids=self._first_movers(state), before=opening)
        return {**settings, 'n_agents': len(state.agents)}

    # -- games other than pd ----------------------------------------------------

    @staticmethod
    def _game(settings: BridgeSettings):
        spec = GAME_REGISTRY[settings.game]
        return spec, spec.params(settings.game_params)

    @staticmethod
    def _first_movers(state: SessionState) -> List[int]:
        return sorted(a for a in state.agents if state.roles.get(a, 1) == 1)

    def _stage_info(self, state: SessionState, settings: BridgeSettings, round_number: int, agent_id: int):
        if settings.game == 'pd' or state.roles.get(agent_id, 1) != 2:
            return None
        spec, p = self._game(settings)
        with self._lock:
            got = dict(state.stage_outcomes.get(round_number, {}))
        return spec.stage_info(p, got, agent_id, state.groups.get(agent_id, [agent_id]))

    def stage_complete(self, session_code: str, round_number: int, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Sequential games: every first mover has decided. Starts the second
        movers' prefetch. Idempotent per round."""
        with self._lock:
            state = self._session(session_code)
            if round_number in state.stage_outcomes:
                return {'accepted': False, 'duplicate': True, 'round_number': round_number}
            state.stage_outcomes[round_number] = {int(o['agent_id']): o for o in outcomes if 'agent_id' in o}
            second = sorted(a for a in state.agents if state.roles.get(a, 1) == 2)
        self._log(session_code, {'event': 'stage_complete', 'round_number': round_number,
                                 'n_outcomes': len(outcomes)})
        prefetched = self.prefetch(session_code, round_number, agent_ids=second) if second else 0
        return {'accepted': True, 'duplicate': False, 'round_number': round_number,
                'prefetched_second_movers': prefetched}

    def _labels(self, session_code: str, settings: BridgeSettings,
                agent_id: Optional[int] = None) -> Labels:
        key = session_code
        if settings.label_unit == 'agent' and agent_id is not None:
            key = f"{session_code}:agent{agent_id}"
        elif settings.label_unit == 'pair' and agent_id is not None:
            state = self._sessions.get(session_code)
            partner = state.agents.get(agent_id, agent_id) if state else agent_id
            key = f"{session_code}:pair{min(agent_id, partner)}-{max(agent_id, partner)}"
        base = make_labels(key, scheme=settings.label_scheme,
                           randomize=settings.label_randomize, swap=settings.swap_labels,
                           seed=settings.seed)
        if (settings.label_unit == 'session' and settings.label_order_per_agent
                and agent_id is not None):
            # same symbols for everybody, own listing order (NOTES.md #49, #54)
            digest = hashlib.sha256(f"order:{settings.seed}:{session_code}:{agent_id}".encode()).hexdigest()
            order = random.Random(int(digest[:16], 16)).sample(list(base.order), 2)
            return Labels(base.shown, tuple(order))
        return base

    def _labels_summary(self, session_code: str, state: SessionState) -> Dict[str, Any]:
        if state.settings.label_unit in ('agent', 'pair') or state.settings.label_order_per_agent:
            return {str(a): self._labels(session_code, state.settings, a).to_dict()
                    for a in sorted(state.agents)}
        return self._labels(session_code, state.settings).to_dict()

    # -- network chat (NOTES.md #54) ---------------------------------------------

    @staticmethod
    def _network_conflict(state: SessionState) -> Optional[str]:
        s = state.settings
        if s.game not in ('pd', 'pgg'):
            return f"net_topology {s.net_topology!r} supports game 'pd' or 'pgg', not {s.game!r}"
        if s.chat_turns > 0:
            return "net_topology != 'none' needs chat_turns 0 (partners must not talk, #51)"
        if s.game == 'pd' and s.label_unit != 'session':
            return "net_topology != 'none' needs label_unit 'session' (everybody names the options alike)"
        if s.net_turns < 1:
            return "net_turns must be at least 1"
        if s.net_lambda_assign not in nc.LAMBDA_ASSIGNS:
            return f"unknown net_lambda_assign: {s.net_lambda_assign} (expected one of {nc.LAMBDA_ASSIGNS})"
        if s.net_identity not in nc.NET_IDENTITIES:
            return f"unknown net_identity: {s.net_identity} (expected one of {nc.NET_IDENTITIES})"
        return None

    def _build_network(self, state: SessionState) -> Dict[str, Any]:
        """Graph, lambdas and names for the session (caller holds the lock).
        Returns the in-memory state plus 'file', the network.json document."""
        s = state.settings
        ids = sorted(state.agents)
        seed = s.net_seed if s.net_seed >= 0 else s.seed
        if not s.net_exclude_partners:
            exclusions = {a: set() for a in ids}
        elif s.game == 'pd':
            exclusions = {a: ({state.agents[a]} if state.agents[a] in state.agents else set()) for a in ids}
        else:
            exclusions = {a: set(state.groups.get(a, [a])) - {a} for a in ids}
        net = nc.build_network(ids, exclusions, s.net_topology, s.net_mean_degree, s.net_ws_p, seed)
        degrees = {a: len(v) for a, v in net['neighbors'].items()}
        lambdas = nc.draw_lambdas(ids, s.net_contact_mean, s.net_contact_dispersion, seed,
                                  s.net_lambda_assign, degrees)
        sim_dir = self._simulation_dir(s) if s.policy == 'llm' else None
        names = nc.participant_names(sim_dir, ids, s.net_identity if s.policy == 'llm' else 'anon')
        doc = {
            'version': 1, 'session_code': state.session_code, 'game': s.game, 'topology': s.net_topology,
            'params': {'mean_degree_target': s.net_mean_degree, 'ba_m': net['params'].get('ba_m'),
                       'ws_k': net['params'].get('ws_k'), 'ws_p': s.net_ws_p,
                       'graph_seed': net['graph_seed'], 'seed': seed},
            'contact': {'mean': s.net_contact_mean, 'dispersion': s.net_contact_dispersion,
                        'max_initiate': s.net_max_initiate, 'max_load': s.net_max_load,
                        'turns': s.net_turns, 'lambda_assign': s.net_lambda_assign},
            'exclude_partners': s.net_exclude_partners,
            'nodes': [{'agent_id': a, 'name': names[a]['name'], 'display': names[a]['display'],
                       'degree': degrees[a], 'lambda': round(lambdas[a], 4),
                       'excluded': sorted(exclusions[a])} for a in ids],
            'edges': net['edges'],
            'excluded_edges_dropped': net['excluded_edges_dropped'],
            'repair_edges': net['repair_edges'],
            'stats': net['stats'], 'layout': net['layout'],
        }
        return {'neighbors': net['neighbors'], 'lambdas': lambdas, 'names': names,
                'exclusions': exclusions, 'seed': seed, 'file': doc}

    @staticmethod
    def _simulation_dir(settings: BridgeSettings) -> str:
        sim_dir = settings.simulation_dir or (
            os.path.join(Config.OASIS_SIMULATION_DATA_DIR, settings.simulation_id)
            if settings.simulation_id else '')
        if not sim_dir or not os.path.isdir(sim_dir):
            raise ValueError(f"llm policy needs an existing simulation_id/simulation_dir, got {sim_dir!r}")
        return sim_dir

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
                    return {**asdict(cached), 'cached': True,
                            **self._chat_field(state, round_number, agent_id)}
                in_flight = state.in_flight.get(key)
                if in_flight is None:
                    # This request computes the decision
                    in_flight = threading.Event()
                    state.in_flight[key] = in_flight
                    settings = BridgeSettings(**asdict(state.settings))
                    attempt = state.attempts.get(key, 0) + 1
                    state.attempts[key] = attempt
                    break
            # A retry (or the round prefetch) is already computing this
            # decision. Wait for it instead of computing twice, which would
            # be a second LLM call.
            in_flight.wait(timeout=IN_FLIGHT_WAIT_SEC)

        try:
            if settings.policy == 'llm':
                decision = self._llm_batch(state, settings, round_number, [agent_id],
                                           {agent_id: history or []})[agent_id]
            else:
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
        return {**asdict(decision), 'cached': False, **self._chat_field(state, round_number, agent_id)}

    def _chat_field(self, state: SessionState, round_number: int, agent_id: int) -> Dict[str, Any]:
        if state.settings.net_topology != 'none':
            # this agent's conversations of this round, read-only (caller may hold the lock)
            return {'network_chat': [
                dict(conv_id=c['conv_id'], other_agent_id=(c['responder'] if c['initiator'] == agent_id
                                                           else c['initiator']),
                     initiator=c['initiator'],
                     messages=[dict(agent_id=m['agent_id'], text=m['text']) for m in c['messages']])
                for c in state.net_convs.get(round_number, [])
                if agent_id in (c['initiator'], c['responder']) and c['messages']]}
        if state.settings.chat_turns <= 0:
            return {}
        pair = self._pair(agent_id, state.agents.get(agent_id, agent_id))
        messages = state.chats.get(round_number, {}).get(pair, [])  # caller may hold the lock
        return {'chat': [dict(agent_id=m['agent_id'], text=m['text']) for m in messages]}

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

        if settings.game == 'pgg':
            choice, reason = self._apply_pgg_policy(settings.policy, rng, history or [],
                                                    self._game(settings)[1]['endowment'])
        elif settings.game != 'pd':
            spec, p = self._game(settings)
            state = self._sessions[session_code]
            role = state.roles.get(agent_id, 1)
            stage = self._stage_info(state, settings, round_number, agent_id)
            choice = spec.auto_decision(p, role, stage) or spec.default(p, role, stage)
            reason = 'dummy: game default'
        else:
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

    @staticmethod
    def _apply_pgg_policy(policy: str, rng: random.Random, history: List[Dict[str, Any]], endowment: int):
        if policy == 'allc':
            return str(endowment), 'always contribute everything'
        if policy == 'alld':
            return '0', 'never contribute'
        if policy == 'tft':
            if not history or not history[-1].get('others'):
                return str(endowment), 'conditional: open with full contribution'
            others = history[-1]['others']
            return str(round(sum(others) / len(others))), 'conditional: match the others\' mean'
        return str(rng.randint(0, endowment)), 'random'

    # -- llm policy ------------------------------------------------------------

    def _history_from_outcomes(self, state: SessionState, agent_id: int, before_round: int):
        if state.settings.game != 'pd':
            spec, p = self._game(state.settings)
            return spec.history(p, state.outcomes, agent_id, state.groups.get(agent_id, [agent_id]),
                                before_round)
        history = []
        partner = state.agents.get(agent_id)
        for r in range(1, before_round):
            own = state.outcomes.get(r, {}).get(agent_id)
            other = state.outcomes.get(r, {}).get(partner)
            if own is None or other is None:
                continue
            history.append(dict(round_number=r, own=own['choice'], partner=other['choice'],
                                payoff=float(own.get('payoff') or 0)))
        return history

    def prefetch(self, session_code: str, round_number: int,
                 agent_ids: Optional[Iterable[int]] = None,
                 before: Optional[Callable[[], None]] = None) -> int:
        """Start computing every agent's decision for a round in the background.

        The in-flight slots are claimed before this returns, so a /decide that
        arrives while `before` (the debate phase) is still running waits for
        the post-debate decision instead of computing one without it.

        Returns the number of decisions this call took ownership of.
        """
        with self._lock:
            state = self._session(session_code)
            settings = BridgeSettings(**asdict(state.settings))
            if settings.policy != 'llm':
                return 0
            owned = {}
            for agent_id in (agent_ids or list(state.agents)):
                key = (round_number, agent_id)
                if key in state.decisions or key in state.in_flight:
                    continue
                owned[agent_id] = threading.Event()
                state.in_flight[key] = owned[agent_id]
                state.attempts[key] = state.attempts.get(key, 0) + 1
            histories = {a: self._history_from_outcomes(state, a, round_number) for a in owned}
        if not owned:
            return 0

        def run():
            if before is not None:
                try:
                    before()
                except Exception as e:  # noqa: BLE001 -- still decide, but record it
                    logger.error(f"Debate phase failed session={session_code} round={round_number}: {e}")
                    self._log(session_code, {'event': 'debate_failed', 'round_number': round_number,
                                             'error': str(e)})
            if settings.chat_turns > 0:
                try:
                    self._chat_phase(state, settings, round_number, histories)
                except Exception as e:  # noqa: BLE001 -- decide on whatever was said
                    logger.error(f"Pair chat failed session={session_code} round={round_number}: {e}")
                    self._log(session_code, {'event': 'chat_failed', 'round_number': round_number,
                                             'error': str(e)})
            if settings.net_topology != 'none' and state.network is not None:
                try:
                    self._network_phase(state, settings, round_number)
                except Exception as e:  # noqa: BLE001 -- decide on whatever was said
                    logger.error(f"Network chat failed session={session_code} round={round_number}: {e}")
                    self._log(session_code, {'event': 'network_failed', 'round_number': round_number,
                                             'error': str(e)})
            try:
                decisions = self._llm_batch(state, settings, round_number, list(owned), histories)
                with self._lock:
                    for agent_id, decision in decisions.items():
                        state.decisions[(round_number, agent_id)] = decision
                for agent_id, decision in decisions.items():
                    self._log(session_code, {'event': 'decide', 'round_number': round_number,
                                             'agent_id': agent_id, 'prefetch': True, **asdict(decision)})
            except Exception as e:  # noqa: BLE001 -- waiters fall back to computing themselves
                logger.error(f"Prefetch failed session={session_code} round={round_number}: {e}")
                self._log(session_code, {'event': 'prefetch_failed', 'round_number': round_number,
                                         'error': str(e)})
            finally:
                with self._lock:
                    for agent_id, event in owned.items():
                        state.in_flight.pop((round_number, agent_id), None)
                        event.set()

        threading.Thread(target=run, name=f"prefetch-{session_code}-r{round_number}", daemon=True).start()
        self._log(session_code, {'event': 'prefetch_started', 'round_number': round_number,
                                 'n_agents': len(owned)})
        return len(owned)

    def _llm_batch(self, state: SessionState, settings: BridgeSettings, round_number: int,
                   agent_ids: List[int], histories: Dict[int, List[Dict[str, Any]]]) -> Dict[int, Decision]:
        """Interview agents for one round; re-ask unparseable answers once, strictly.

        Raises on IPC failure (step-server down / timeout).
        """
        client = SimulationIPCClient(self._simulation_dir(settings))
        model = os.environ.get('LLM_MODEL_NAME', 'llm')
        no_think = wants_no_think(settings.no_think, model)
        labels = {a: self._labels(state.session_code, settings, a) for a in agent_ids}
        t0 = time.time()
        decisions: Dict[int, Decision] = {}
        pending = list(agent_ids)
        errors: Dict[int, str] = {}

        spec = p = None
        stages: Dict[int, Any] = {}
        if settings.game != 'pd':
            spec, p = self._game(settings)
            for a in list(pending):
                stages[a] = self._stage_info(state, settings, round_number, a)
                auto = spec.auto_decision(p, state.roles.get(a, 1), stages[a])
                if auto is not None:
                    decisions[a] = Decision(choice=auto, reason='no decision needed', source='auto',
                                            latency_sec=0.0)
                    pending.remove(a)

        for strict in (False, True):
            if not pending:
                break
            if spec is not None:
                interviews = [
                    dict(agent_id=a, prompt=spec.prompt(
                        p, round_number, settings.num_rounds, state.roles.get(a, 1), histories.get(a, []),
                        stages.get(a), include_feed=settings.include_feed, strict=strict, no_think=no_think,
                        network_chat=self._network_for(state, settings, a, round_number)))
                    for a in pending
                ]
            else:
                interviews = [
                    dict(agent_id=a, prompt=render_decision_prompt(
                        round_number, settings.num_rounds, settings.payoffs, histories.get(a, []),
                        labels[a], include_feed=settings.include_feed, strict=strict, no_think=no_think,
                        chat=self._chat_for(state, settings, a, round_number, include_current=True),
                        network_chat=self._network_for(state, settings, a, round_number)))
                    for a in pending
                ]
            response = client.send_game_interview(interviews, platform=settings.platform,
                                                   timeout=GAME_INTERVIEW_TIMEOUT_SEC,
                                                   exclude_own_posts=settings.feed_exclude_own)
            if response.status != CommandStatus.COMPLETED:
                raise RuntimeError(f"game_interview failed: {response.error}")
            answers = {int(a['agent_id']): a for a in response.result.get('answers', [])}
            prompts = {i['agent_id']: i['prompt'] for i in interviews}
            retry = []
            for agent_id in pending:
                answer = answers.get(agent_id, {})
                if spec is not None:
                    choice, reason, error = spec.parse(answer.get('response'), p,
                                                       state.roles.get(agent_id, 1), stages.get(agent_id))
                else:
                    choice, reason, error = parse_decision(answer.get('response'), labels[agent_id])
                if answer.get('error'):
                    error = answer['error']
                self._log_prompt(state.session_code, {
                    'round_number': round_number, 'agent_id': agent_id, 'strict': strict,
                    'prompt': prompts[agent_id],  # feed is substituted by the step-server
                    'feed_posts': answer.get('feed_posts'), 'feed': answer.get('feed'),
                    'response': answer.get('response'),
                    'parse_error': error,
                })
                if error is None:
                    decisions[agent_id] = Decision(
                        choice=choice, reason=reason,
                        source=f"llm:{model}" + (":strict" if strict else ""),
                        latency_sec=round(time.time() - t0, 3),
                    )
                else:
                    errors[agent_id] = error
                    retry.append(agent_id)
            pending = retry

        for agent_id in pending:
            decisions[agent_id] = Decision(
                choice=(spec.default(p, state.roles.get(agent_id, 1), stages.get(agent_id))
                        if spec is not None else settings.default_choice),
                reason=f"llm answer unusable: {errors.get(agent_id)}",
                source='llm_default',
                latency_sec=round(time.time() - t0, 3),
                missing=True,
            )
        return decisions

    # -- pair chat ---------------------------------------------------------------

    @staticmethod
    def _pair(agent_id: int, partner_id: int) -> tuple:
        return (min(agent_id, partner_id), max(agent_id, partner_id))

    def _chat_for(self, state: SessionState, settings: BridgeSettings, agent_id: int,
                  round_number: int, include_current: bool) -> List[Dict[str, Any]]:
        """This agent's pair chat, from its point of view: earlier rounds (at
        most chat_memory_rounds) and, if include_current, this round's."""
        if settings.chat_turns <= 0:
            return []
        pair = self._pair(agent_id, state.agents.get(agent_id, agent_id))
        with self._lock:
            rounds = [(r, list(state.chats[r][pair])) for r in sorted(state.chats)
                      if r < round_number and state.chats[r].get(pair)]
            current = list(state.chats.get(round_number, {}).get(pair, []))
        if settings.chat_memory_rounds >= 0:
            rounds = rounds[-settings.chat_memory_rounds:] if settings.chat_memory_rounds else []
        chat = [dict(round_number=r, messages=m) for r, m in rounds]
        if include_current and current:
            chat.append(dict(round_number=round_number, messages=current))
        return chat_view(chat, agent_id)

    def _chat_phase(self, state: SessionState, settings: BridgeSettings, round_number: int,
                    histories: Dict[int, List[Dict[str, Any]]]) -> None:
        """Partners exchange chat_turns messages before this round's decision.

        One batched GAME_INTERVIEW per turn: in every pair one partner speaks,
        seeing everything said before. The opener alternates between rounds.
        A message that cannot be read is re-asked once, then skipped.
        """
        session_code = state.session_code
        client = SimulationIPCClient(self._simulation_dir(settings))
        model = os.environ.get('LLM_MODEL_NAME', 'llm')
        no_think = wants_no_think(settings.no_think, model)
        pairs = sorted({self._pair(a, p) for a, p in state.agents.items() if p in state.agents})
        with self._lock:
            transcripts = state.chats.setdefault(round_number, {})
            for pair in pairs:
                transcripts.setdefault(pair, [])
        t0 = time.time()
        n_ok = n_failed = 0
        for turn in range(settings.chat_turns):
            speakers = {}
            for low, high in pairs:
                opener, other = (low, high) if round_number % 2 == 1 else (high, low)
                speakers[opener if turn % 2 == 0 else other] = (low, high)
            pending = list(speakers)
            for attempt in (1, 2):
                if not pending:
                    break
                prompts = {}
                for a in pending:
                    with self._lock:
                        so_far = list(transcripts[speakers[a]])
                    prompts[a] = render_chat_prompt(
                        round_number, settings.num_rounds, settings.payoffs, histories.get(a, []),
                        self._labels(session_code, settings, a),
                        past_chat=self._chat_for(state, settings, a, round_number, include_current=False),
                        current=chat_view([dict(round_number=round_number, messages=so_far)], a)[0]['messages'],
                        no_think=no_think)
                response = client.send_game_interview(
                    [dict(agent_id=a, prompt=prompts[a]) for a in pending],
                    platform=settings.platform, timeout=GAME_INTERVIEW_TIMEOUT_SEC)
                if response.status != CommandStatus.COMPLETED:
                    raise RuntimeError(f"game_interview (chat) failed: {response.error}")
                answers = {int(x['agent_id']): x for x in response.result.get('answers', [])}
                retry = []
                for a in pending:
                    answer = answers.get(a, {})
                    text, error = parse_chat_message(answer.get('response'), settings.chat_max_chars)
                    error = answer.get('error') or error
                    if error is None:
                        with self._lock:
                            transcripts[speakers[a]].append(dict(agent_id=a, text=text))
                        n_ok += 1
                    elif attempt == 1:
                        retry.append(a)
                    else:
                        n_failed += 1
                    self._append(session_code, 'chat.jsonl', {
                        'round_number': round_number, 'turn': turn, 'attempt': attempt,
                        'agent_id': a, 'partner_agent_id': state.agents.get(a),
                        'message': text, 'parse_error': error,
                        'prompt': prompts[a], 'response': answer.get('response'),
                    })
                pending = retry
        self._log(session_code, {'event': 'chat_phase', 'round_number': round_number,
                                 'pairs': len(pairs), 'turns': settings.chat_turns,
                                 'messages': n_ok, 'failed': n_failed,
                                 'elapsed_sec': round(time.time() - t0, 1)})

    # -- network chat phase (NOTES.md #54) ---------------------------------------

    def _net_visible(self, state: SessionState, settings: BridgeSettings, agent_id: int,
                     round_number: int, exclude_conv: Optional[str] = None) -> List[Dict[str, Any]]:
        """Conversations this agent may see at round_number: rounds within
        net_memory_rounds before it and this round's so far, non-empty only,
        last net_max_convs_in_prompt. Copies, taken under the lock."""
        lo = 1 if settings.net_memory_rounds < 0 else round_number - settings.net_memory_rounds
        with self._lock:
            convs = [dict(c, messages=list(c['messages']))
                     for r in sorted(state.net_convs) if lo <= r <= round_number
                     for c in state.net_convs[r]
                     if agent_id in (c['initiator'], c['responder']) and c['messages']
                     and c['conv_id'] != exclude_conv]
        return convs[-settings.net_max_convs_in_prompt:]

    def _network_for(self, state: SessionState, settings: BridgeSettings, agent_id: int,
                     round_number: int) -> Optional[List[Dict[str, Any]]]:
        """The network_chat block of a decision prompt, from this agent's view."""
        if settings.net_topology == 'none' or state.network is None:
            return None
        return nc.conversation_view(self._net_visible(state, settings, agent_id, round_number),
                                    agent_id, state.network['names']) or None

    def _network_phase(self, state: SessionState, settings: BridgeSettings, round_number: int) -> None:
        """One-to-one conversations with graph neighbours before this round's decisions.

        Contacts are drawn first (sample_contacts), then the messages run in
        waves, one batched GAME_INTERVIEW each with every agent at most once,
        so a conversation's two messages are never written concurrently. A
        message that cannot be read stays pending for the next wave; after
        NET_MESSAGE_ATTEMPTS failures its conversation is aborted.
        """
        session_code = state.session_code
        net = state.network
        client = SimulationIPCClient(self._simulation_dir(settings))
        model = os.environ.get('LLM_MODEL_NAME', 'llm')
        no_think = wants_no_think(settings.no_think, model)
        names = net['names']
        with self._lock:
            histories = {a: self._history_from_outcomes(state, a, round_number) for a in sorted(state.agents)}
        drawn, record = nc.sample_contacts(net['neighbors'], net['lambdas'], round_number, net['seed'],
                                           settings.net_max_initiate, settings.net_max_load)
        convs = [dict(c, round_number=round_number, messages=[], aborted=False, fails=0) for c in drawn]
        with self._lock:
            state.net_convs[round_number] = convs
        self._append(session_code, 'network_contacts.jsonl', {
            'round_number': round_number,
            'agents': {str(a): r for a, r in record.items()},
            'conversations': [dict(conv_id=c['conv_id'], initiator=c['initiator'], responder=c['responder'])
                              for c in convs]})
        if settings.game == 'pd':
            rules = lambda a: dict(  # noqa: E731
                options=self._labels(session_code, settings, a).order,
                blocks=_option_blocks(settings.payoffs, self._labels(session_code, settings, a)))
        else:
            rules = lambda a: pg._rules(self._game(settings)[1])  # noqa: E731

        t0 = time.time()
        n_ok = n_failed = 0
        wave_sizes: List[int] = []
        while len(wave_sizes) < NET_MAX_WAVES:
            with self._lock:
                wave = nc.next_wave(convs, settings.net_turns)
            if not wave:
                break
            prompts, seen = {}, {}
            for conv, a in wave:
                other_id = conv['responder'] if a == conv['initiator'] else conv['initiator']
                earlier = nc.conversation_view(
                    self._net_visible(state, settings, a, round_number, exclude_conv=conv['conv_id']),
                    a, names)
                with self._lock:
                    so_far = list(conv['messages'])
                current = nc.conversation_view([dict(conv, messages=so_far)], a, names)
                history = histories.get(a, [])
                if settings.game == 'pd':
                    history = _shown_history(history, self._labels(session_code, settings, a))
                prompts[a] = render_network_chat_prompt(
                    settings.game, round_number, settings.num_rounds, rules(a), history,
                    dict(display=names[other_id]['display'], short=names[other_id]['short']),
                    earlier, current[0]['messages'] if current else [], no_think=no_think)
                seen[a] = [c['conv_id'] for c in earlier]
            assert len({a for _, a in wave}) == len(wave), 'an agent speaks twice in one wave'
            response = client.send_game_interview(
                [dict(agent_id=a, prompt=prompts[a]) for _, a in wave],
                platform=settings.platform, timeout=GAME_INTERVIEW_TIMEOUT_SEC)
            if response.status != CommandStatus.COMPLETED:
                raise RuntimeError(f"game_interview (network chat) failed: {response.error}")
            wave_sizes.append(len(wave))
            answers = {int(x['agent_id']): x for x in response.result.get('answers', [])}
            for conv, a in wave:
                answer = answers.get(a, {})
                text, error = parse_chat_message(answer.get('response'), settings.chat_max_chars)
                error = answer.get('error') or error
                turn = len(conv['messages'])
                attempt = conv['fails'] + 1
                with self._lock:
                    if error is None:
                        conv['messages'].append(dict(agent_id=a, text=text))
                        conv['fails'] = 0
                        n_ok += 1
                    else:
                        conv['fails'] += 1
                        n_failed += 1
                        if conv['fails'] >= NET_MESSAGE_ATTEMPTS:
                            conv['aborted'] = True
                self._append(session_code, 'network_chat.jsonl', {
                    'round_number': round_number, 'conv_id': conv['conv_id'], 'turn': turn,
                    'wave': len(wave_sizes) - 1, 'attempt': attempt, 'agent_id': a,
                    'other_agent_id': conv['responder'] if a == conv['initiator'] else conv['initiator'],
                    'initiator': conv['initiator'], 'message': text, 'parse_error': error,
                    'seen_conv_ids': seen[a], 'prompt': prompts[a], 'response': answer.get('response')})
        self._log(session_code, {
            'event': 'network_phase', 'round_number': round_number, 'conversations': len(convs),
            'messages': n_ok, 'failed': n_failed, 'aborted': sum(1 for c in convs if c['aborted']),
            'waves': len(wave_sizes), 'wave_sizes': wave_sizes, 'max_load': settings.net_max_load,
            'elapsed_sec': round(time.time() - t0, 1)})

    # -- debate phase (Phase 4) -------------------------------------------------

    @staticmethod
    def result_posts(settings: BridgeSettings, agents: Dict[int, int], round_number: int,
                     outcomes: List[Dict[str, Any]], labels: Optional[Labels]) -> List[Dict[str, Any]]:
        """Posts that write round results back into the social space.

        labels=None writes them in points only: with per-agent labels a post
        naming a label would mean different things to different readers.
        """
        if settings.inject_results == 'none' or not outcomes:
            return []
        by_agent = {int(o['agent_id']): o for o in outcomes}
        if settings.game != 'pd':  # 'each' only (configure rejects 'summary')
            spec, p = ExperimentBridge._game(settings)
            return [dict(agent_id=a, content=spec.result_post(p, round_number, o))
                    for a, o in sorted(by_agent.items())]
        if settings.inject_results == 'each':
            posts = []
            for agent_id, o in sorted(by_agent.items()):
                partner = by_agent.get(agents.get(agent_id, -1))
                own_points = int(float(o.get('payoff') or 0))
                if labels is None:
                    other_points = int(float(partner.get('payoff') or 0)) if partner else '?'
                    content = (f"Decision task, round {round_number}: I got {own_points} points, "
                               f"the other person got {other_points} points.")
                else:
                    other = labels.show(partner['choice'] if partner else '?')
                    content = (f"Decision task, round {round_number}: I chose Option {labels.show(o['choice'])}, "
                               f"the other person chose Option {other}. I got {own_points} points.")
                posts.append(dict(agent_id=agent_id, content=content))
            return posts
        # summary
        seen, both_a, both_b, mixed = set(), 0, 0, 0
        for agent_id, partner_id in agents.items():
            pair = frozenset((agent_id, partner_id))
            if pair in seen or agent_id not in by_agent or partner_id not in by_agent:
                continue
            seen.add(pair)
            choices = {by_agent[agent_id]['choice'], by_agent[partner_id]['choice']}
            if choices == {'A'}:
                both_a += 1
            elif choices == {'B'}:
                both_b += 1
            else:
                mixed += 1
        announcer = settings.announcer_agent_id if settings.announcer_agent_id >= 0 else min(agents)
        m = settings.payoffs
        if labels is None:
            return [dict(agent_id=announcer, content=(
                f"Decision task, round {round_number} results: {both_a} pairs both got {m['R']} points, "
                f"{both_b} pairs both got {m['P']} points, and in {mixed} pairs one person got "
                f"{m['T']} and the other {m['S']}."))]
        rate = sum(o['choice'] == 'A' for o in by_agent.values()) / len(by_agent)
        c, d = labels.show('A'), labels.show('B')
        # list the counts in the order options are shown to agents
        counts = sorted([(c, both_a), (d, both_b)], key=lambda x: labels.order.index(x[0]))
        return [dict(agent_id=announcer, content=(
            f"Decision task, round {round_number} results: "
            f"{counts[0][1]} pairs both chose Option {counts[0][0]}, "
            f"{counts[1][1]} pairs both chose Option {counts[1][0]}, {mixed} pairs split. "
            f"{rate:.0%} of participants chose Option {c}."))]

    def _debate_phase(self, session_code: str, settings: BridgeSettings, round_number: int,
                      outcomes: List[Dict[str, Any]]) -> None:
        """Between rounds: write results back, then let agents debate.

        round_number=0 is the opening phase before round 1.
        """
        state = self._session(session_code)
        client = SimulationIPCClient(self._simulation_dir(settings))
        opening = []
        if settings.opening_post and (round_number == 0 or settings.repeat_opening):
            poster = settings.opening_agent_id
            if poster < 0:
                poster = settings.announcer_agent_id if settings.announcer_agent_id >= 0 else min(state.agents)
            opening = [dict(agent_id=poster, content=settings.opening_post)]
        posts = opening
        if round_number > 0:
            shared = None if settings.label_unit != 'session' else self._labels(session_code, settings)
            posts = posts + self.result_posts(settings, state.agents, round_number, outcomes, shared)

        record: Dict[str, Any] = {'event': 'debate_phase', 'after_round': round_number,
                                  'posts': len(posts), 'debate_rounds': settings.debate_rounds}
        t0 = time.time()
        if posts:
            resp = client.send_inject_posts(posts, platform=settings.platform)
            if resp.status != CommandStatus.COMPLETED:
                raise RuntimeError(f"inject_post failed: {resp.error}")
            record['inject'] = resp.result
        if settings.debate_rounds > 0:
            resp = client.send_run_rounds(
                settings.debate_rounds, platform=settings.platform, timeout=RUN_ROUNDS_TIMEOUT_SEC,
                agent_ids=sorted(state.agents) if settings.debate_players_only else None,
                ignore_active_hours=settings.debate_ignore_hours,
                min_active=settings.debate_min_active)
            if resp.status != CommandStatus.COMPLETED:
                raise RuntimeError(f"run_rounds failed: {resp.error}")
            record['run_rounds'] = resp.result
        record['elapsed_sec'] = round(time.time() - t0, 1)
        self._log(session_code, record)

    # -- belief survey ---------------------------------------------------------

    def belief_survey(self, session_code: str, phase: str) -> Dict[str, Any]:
        """Ask every game agent the Likert item; append to beliefs.jsonl."""
        state = self._session(session_code)
        settings = BridgeSettings(**asdict(state.settings))
        model = os.environ.get('LLM_MODEL_NAME', 'llm')
        prompt = render_belief_prompt(settings.belief_statement, include_feed=settings.include_feed,
                                      no_think=wants_no_think(settings.no_think, model))
        client = SimulationIPCClient(self._simulation_dir(settings))
        t0 = time.time()
        response = client.send_game_interview(
            [dict(agent_id=a, prompt=prompt) for a in sorted(state.agents)],
            platform=settings.platform, timeout=GAME_INTERVIEW_TIMEOUT_SEC,
            exclude_own_posts=settings.feed_exclude_own)
        if response.status != CommandStatus.COMPLETED:
            raise RuntimeError(f"belief survey failed: {response.error}")
        scores = []
        for answer in response.result.get('answers', []):
            score, reason, error = parse_likert(answer.get('response'))
            if answer.get('error'):
                error = answer['error']
            scores.append(score)
            self._append(session_code, 'beliefs.jsonl', {
                'phase': phase, 'agent_id': answer.get('agent_id'), 'score': score,
                'reason': reason, 'parse_error': error, 'feed_posts': answer.get('feed_posts'),
                'feed': answer.get('feed'),
                'statement': settings.belief_statement, 'response': answer.get('response'),
            })
        valid = [x for x in scores if x is not None]
        summary = {'event': 'belief_survey', 'phase': phase, 'n': len(scores), 'valid': len(valid),
                   'mean': round(sum(valid) / len(valid), 3) if valid else None,
                   'elapsed_sec': round(time.time() - t0, 1)}
        self._log(session_code, summary)
        return summary

    def comprehension_check(self, session_code: str) -> Dict[str, Any]:
        """Ask every game agent two payoff questions; append to comprehension.jsonl."""
        state = self._session(session_code)
        settings = BridgeSettings(**asdict(state.settings))
        model = os.environ.get('LLM_MODEL_NAME', 'llm')
        labels = {a: self._labels(session_code, settings, a) for a in state.agents}
        no_think = wants_no_think(settings.no_think, model)
        if settings.game != 'pd':
            spec, p = self._game(settings)
            # expected answers can differ by role (sequential games)
            prompts = {a: spec.comprehension(p, state.roles.get(a, 1), no_think=no_think)
                       for a in state.agents}
        else:
            prompts = {a: render_comprehension_prompt(settings.payoffs, labels[a], no_think=no_think)
                       for a in state.agents}
        expected_by_role = {}
        for a, (_, exp) in prompts.items():
            expected_by_role.setdefault(state.roles.get(a, 1), exp)
        client = SimulationIPCClient(self._simulation_dir(settings))
        t0 = time.time()
        response = client.send_game_interview(
            [dict(agent_id=a, prompt=prompts[a][0]) for a in sorted(state.agents)],
            platform=settings.platform, timeout=GAME_INTERVIEW_TIMEOUT_SEC)
        if response.status != CommandStatus.COMPLETED:
            raise RuntimeError(f"comprehension check failed: {response.error}")
        n_correct = 0
        answers = response.result.get('answers', [])
        for answer in answers:
            parse = parse_game_comprehension if settings.game != 'pd' else parse_comprehension
            got, error = parse(answer.get('response'))
            expected = prompts[answer.get('agent_id')][1] if answer.get('agent_id') in prompts else None
            correct = got == expected
            n_correct += correct
            self._append(session_code, 'comprehension.jsonl', {
                'agent_id': answer.get('agent_id'), 'answer': got, 'expected': expected,
                'correct': correct, 'parse_error': error or answer.get('error'),
                'labels': (labels[answer.get('agent_id')].to_dict()
                           if settings.game == 'pd' and answer.get('agent_id') in labels else None),
                'response': answer.get('response'),
            })
        summary = {'event': 'comprehension_check', 'n': len(answers), 'correct': n_correct,
                   'expected': (expected_by_role if len(expected_by_role) > 1
                                else next(iter(expected_by_role.values()), None)), 'elapsed_sec': round(time.time() - t0, 1)}
        self._log(session_code, summary)
        return summary

    def _safe_survey(self, session_code: str, phase: str) -> None:
        try:
            self.belief_survey(session_code, phase)
        except Exception as e:  # noqa: BLE001 -- the game itself is already over
            logger.error(f"Belief survey failed session={session_code} phase={phase}: {e}")
            self._log(session_code, {'event': 'belief_survey_failed', 'phase': phase, 'error': str(e)})

    # -- barrier ---------------------------------------------------------------

    def round_complete(self, session_code: str, round_number: int, summary: Dict[str, Any]) -> Dict[str, Any]:
        """All players in the session finished the round. Idempotent per round.

        summary["outcomes"] ([{agent_id, choice, payoff, source, missing}]) is
        what oTree actually recorded and is authoritative: when a client timed
        out and used its default, the bridge's own cached decision was never
        applied. With the llm policy this starts prefetching the next round.
        Phase 4 runs the debate phase here, before the prefetch.
        """
        outcomes = summary.get('outcomes') or []
        with self._lock:
            state = self._session(session_code)
            settings = BridgeSettings(**asdict(state.settings))
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
            state.outcomes[round_number] = {int(o['agent_id']): o for o in outcomes if 'agent_id' in o}
            record = {**summary, 'decided_by_bridge': decided, 'mismatches': mismatches,
                      'received_at': time.time()}
            state.completed_rounds[round_number] = record
            next_round = round_number + 1
            prefetch_next = (state.settings.policy == 'llm' and state.agents
                             and next_round <= state.settings.num_rounds)
        self._log(session_code, {'event': 'round_complete', 'round_number': round_number, **record})
        if (settings.policy == 'llm' and settings.belief_survey
                and round_number == settings.num_rounds):
            threading.Thread(target=self._safe_survey, args=(session_code, 'post'),
                             name=f"survey-{session_code}-post", daemon=True).start()
        prefetched = 0
        if prefetch_next:
            prefetched = self.prefetch(
                session_code, next_round, agent_ids=self._first_movers(state),
                before=lambda: self._debate_phase(session_code, settings, round_number, outcomes))
        return {'accepted': True, 'duplicate': False, 'round_number': round_number,
                'decided_by_bridge': decided, 'n_outcomes': len(outcomes),
                'n_mismatches': len(mismatches), 'prefetched_next': prefetched}

    # -- state -----------------------------------------------------------------

    def get_state(self, session_code: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._sessions.get(session_code)
            if state is None:
                return None
            per_round: Dict[int, int] = {}
            missing_per_round: Dict[int, int] = {}
            for (r, _a), d in state.decisions.items():
                per_round[r] = per_round.get(r, 0) + 1
                if d.missing:
                    missing_per_round[r] = missing_per_round.get(r, 0) + 1
            return {
                'session_code': session_code,
                'settings': asdict(state.settings),
                'n_agents': len(state.agents),
                'decisions_per_round': dict(sorted(per_round.items())),
                'missing_per_round': dict(sorted(missing_per_round.items())),
                'in_flight': len(state.in_flight),
                'completed_rounds': sorted(state.completed_rounds),
                'injected_errors': state.injected_errors,
                'retried_requests': sum(1 for n in state.attempts.values() if n > 1),
            }

    # -- log -------------------------------------------------------------------

    def _log_dir(self, session_code: str) -> str:
        state = self._sessions.get(session_code)
        if state is not None and state.settings.policy == 'llm':
            try:
                return os.path.join(self._simulation_dir(state.settings), 'game', session_code)
            except ValueError:
                pass
        return os.path.join(self._data_dir, session_code)

    def _append(self, session_code: str, filename: str, record: Dict[str, Any]) -> None:
        try:
            session_dir = self._log_dir(session_code)
            os.makedirs(session_dir, exist_ok=True)
            record = {'ts': time.time(), **record}
            with open(os.path.join(session_dir, filename), 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError as e:
            logger.warning(f"Failed to write {filename} for {session_code}: {e}")

    def _write_json(self, session_code: str, filename: str, obj: Dict[str, Any]) -> None:
        """Atomic write (temp + fsync + os.replace), so a crash never leaves half a file."""
        try:
            session_dir = self._log_dir(session_code)
            os.makedirs(session_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=session_dir, prefix=f".{filename}.")
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(obj, f, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, os.path.join(session_dir, filename))
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except OSError as e:
            logger.warning(f"Failed to write {filename} for {session_code}: {e}")

    def _log(self, session_code: str, record: Dict[str, Any]) -> None:
        self._append(session_code, 'bridge_log.jsonl', record)

    def _log_prompt(self, session_code: str, record: Dict[str, Any]) -> None:
        """Every LLM answer, for reproducibility (plan.md §5)."""
        self._append(session_code, 'llm_answers.jsonl', record)


_bridge: Optional[ExperimentBridge] = None
_bridge_lock = threading.Lock()


def get_bridge() -> ExperimentBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = ExperimentBridge()
        return _bridge
