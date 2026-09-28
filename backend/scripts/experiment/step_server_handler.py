"""
ExperimentIPCHandler - Phase 0-B step-server prototype.

Extends run_parallel_simulation.ParallelIPCHandler (imported, not
modified) with 3 new IPC commands: RUN_ROUNDS, INJECT_POST, GET_STATE,
using the schema constants/types in experiment.ipc_protocol and the
resumable state objects in experiment.round_state.

Round execution delegates to run_parallel_simulation.step_round(), the
per-round function extracted from run_twitter_simulation() /
run_reddit_simulation() by refactor R1 (see
docs/otree-integration/phase0-mirofish.md). There is no longer a
hand-maintained copy of the round-loop body here.

Status: prototype/skeleton. Import-clean and logically wired, but not
exercised end-to-end against a live multi-round simulation as part of
Phase 0-B (only the mid-simulation INTERVIEW/CREATE_POST primitives it
builds on were verified live - see docs/otree-integration/phase0-mirofish.md
task 2).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_HERE)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Reused as-is from the existing script (module-level symbols; see the
# "importable as-is" inventory with line numbers in phase0-mirofish.md).
import run_parallel_simulation as rps  # noqa: E402

from experiment.ipc_protocol import (  # noqa: E402
    RUN_ROUNDS, INJECT_POST, GET_STATE, GAME_INTERVIEW, FEED_PLACEHOLDER,
)
from experiment.round_state import PlatformRoundState, RunRoundsLock  # noqa: E402


class ExperimentIPCHandler(rps.ParallelIPCHandler):
    """
    Drop-in replacement for ParallelIPCHandler that additionally serves
    RUN_ROUNDS / INJECT_POST / GET_STATE, and drains ALL pending command
    files per poll instead of one-per-tick.

    Why drain-all: the stock main() loop in run_parallel_simulation.py
    calls process_commands() once, then awaits `_shutdown_event.wait()`
    with a 0.5s timeout before looping again (run_parallel_simulation.py
    ~1613-1624). That means a second command file that lands while the
    first is already on disk still waits for the *next* tick even though
    both commands were ready. For an oTree barrier that fires a burst of
    RUN_ROUNDS + several GET_STATE/INJECT_POST calls close together, that
    is dead latency for no benefit. Draining removes it while preserving
    strict one-command-at-a-time execution (see "Serialization" in the
    design doc).
    """

    def __init__(self, simulation_dir: str, *args, **kwargs):
        super().__init__(simulation_dir, *args, **kwargs)
        self.twitter_state = PlatformRoundState(platform="twitter")
        self.reddit_state = PlatformRoundState(platform="reddit")
        self._run_rounds_lock_path = os.path.join(simulation_dir, ".run_rounds.lock")
        self._sims: Dict[str, "rps.PlatformSimulation"] = {}
        self._action_loggers: Dict[str, Any] = {}
        self._config: Optional[Dict[str, Any]] = None

    # -- config / setup ----------------------------------------------------

    def attach_config(
        self,
        config: Dict[str, Any],
        sims: Optional[Dict[str, "rps.PlatformSimulation"]] = None,
        action_loggers: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Must be called once after construction, before any RUN_ROUNDS command.

        Args:
            sims: {"twitter"|"reddit": PlatformSimulation} as returned by
                rps.setup_platform_env(). If omitted, built from the env /
                agent_graph passed to the constructor.
            action_loggers: optional {"twitter"|"reddit": PlatformActionLogger}
                so RUN_ROUNDS writes to <platform>/actions.jsonl like the
                stock runner does.
        """
        self._config = config
        self._action_loggers = action_loggers or {}
        time_config = config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        total_rounds = (total_hours * 60) // minutes_per_round

        for state in (self.twitter_state, self.reddit_state):
            state.minutes_per_round = minutes_per_round
            state.total_rounds = total_rounds
            state.status = "idle"

        if sims is not None:
            self._sims = dict(sims)
            return

        base_names = rps.get_agent_names_from_config(config)
        for platform, env, agent_graph in (
            ("twitter", self.twitter_env, self.twitter_agent_graph),
            ("reddit", self.reddit_env, self.reddit_agent_graph),
        ):
            if not env:
                continue
            sim = rps.PlatformSimulation()
            sim.env = env
            sim.agent_graph = agent_graph
            sim.db_path = os.path.join(self.simulation_dir, f"{platform}_simulation.db")
            names = dict(base_names)
            for agent_id, agent in agent_graph.get_agents():
                names.setdefault(agent_id, getattr(agent, "name", f"Agent_{agent_id}"))
            sim.agent_names = names
            self._sims[platform] = sim

    # -- command dispatch (overrides ParallelIPCHandler.process_commands) --

    async def process_commands(self) -> bool:
        seen = set()
        while True:
            command = self.poll_command()
            if not command:
                return True
            command_id = command.get("command_id")
            # send_response() only removes <command_id>.json; a command file
            # named otherwise is returned again by poll_command(), so stop
            # draining instead of spinning on it. See NOTES.md #5.
            if command_id in seen:
                return True
            seen.add(command_id)
            try:
                should_continue = await self._dispatch_one(command)
            except Exception as e:  # noqa: BLE001 -- keep the server alive, report to caller
                self.send_response(command_id, "failed", error=f"{type(e).__name__}: {e}")
                continue
            if not should_continue:
                return False

    async def _dispatch_one(self, command: Dict[str, Any]) -> bool:
        command_id = command.get("command_id")
        command_type = command.get("command_type")
        args = command.get("args", {})

        if command_type == RUN_ROUNDS:
            return await self.handle_run_rounds(command_id, args)
        if command_type == INJECT_POST:
            return await self.handle_inject_post(command_id, args)
        if command_type == GET_STATE:
            return await self.handle_get_state(command_id, args)
        if command_type == GAME_INTERVIEW:
            return await self.handle_game_interview(command_id, args)

        # Fall back to the 3 pre-existing command types, reusing the
        # parent class's own handlers verbatim (inherited, not copied).
        if command_type == rps.CommandType.INTERVIEW:
            await self.handle_interview(
                command_id, args.get("agent_id", 0), args.get("prompt", ""),
                args.get("platform"),
            )
            self._consume_trace()
            return True
        if command_type == rps.CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id, args.get("interviews", []), args.get("platform"),
            )
            self._consume_trace()
            return True
        if command_type == rps.CommandType.CLOSE_ENV:
            self.send_response(command_id, "completed", result={"message": "Environment will close"})
            return False

        self.send_response(command_id, "failed", error=f"Unknown command_type: {command_type}")
        return True

    # -- RUN_ROUNDS ----------------------------------------------------------

    async def handle_run_rounds(self, command_id: str, args: Dict[str, Any]) -> bool:
        existing_lock = RunRoundsLock.read(self._run_rounds_lock_path)
        if existing_lock is not None and not existing_lock.is_stale():
            self.send_response(
                command_id, "rejected",
                error=f"run_rounds already in progress (command_id={existing_lock.command_id})",
            )
            return True

        platform = args.get("platform", "both")
        k = int(args.get("rounds", 0))
        agent_ids = args.get("agent_ids")
        activation = dict(
            allowed_agent_ids={int(a) for a in agent_ids} if agent_ids else None,
            ignore_active_hours=bool(args.get("ignore_active_hours", False)),
            min_active=int(args.get("min_active", 0)),
        )
        if k <= 0:
            self.send_response(command_id, "failed", error="rounds must be >= 1")
            return True
        if self._config is None:
            self.send_response(command_id, "failed", error="attach_config() was not called before RUN_ROUNDS")
            return True

        lock = RunRoundsLock(command_id=command_id, platform=platform, rounds_requested=k, pid=os.getpid())
        lock.write(self._run_rounds_lock_path)
        try:
            result: Dict[str, Any] = {}
            if platform in ("twitter", "both") and self.twitter_env:
                result["twitter"] = await self._run_rounds_one_platform(
                    "twitter", self.twitter_env, self.twitter_state, k, activation,
                )
            if platform in ("reddit", "both") and self.reddit_env:
                result["reddit"] = await self._run_rounds_one_platform(
                    "reddit", self.reddit_env, self.reddit_state, k, activation,
                )
            if not result:
                self.send_response(command_id, "failed", error=f"platform unavailable: {platform}")
                return True
            self.send_response(command_id, "completed", result=result)
            return True
        finally:
            RunRoundsLock.remove(self._run_rounds_lock_path)

    async def _run_rounds_one_platform(
        self, platform: str, env, state: PlatformRoundState, k: int,
        activation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run up to k rounds via rps.step_round(), resumable via `state`."""
        sim = self._sims[platform]
        action_logger = self._action_loggers.get(platform)
        round_from = state.round_num
        actions_this_call = 0
        state.status = "running_rounds"
        # Rows written since the last command (e.g. interviews) are not debate actions
        self._consume_trace((platform,))

        try:
            for _ in range(k):
                if state.reached_end():
                    break
                actual_actions, state.last_rowid = await rps.step_round(
                    sim, self._config, state.round_num, state.minutes_per_round,
                    state.last_rowid, action_logger, **(activation or {}),
                )
                actions_this_call += len(actual_actions)
                state.total_actions += len(actual_actions)
                state.round_num += 1
        finally:
            state.status = "idle"

        result = state.to_dict()
        result.update({
            "rounds_requested": k,
            "rounds_executed": state.round_num - round_from,
            "round_from": round_from,
            "round_to": state.round_num,
            "actions_this_call": actions_this_call,
        })
        return result

    # -- trace watermark -------------------------------------------------------

    def _state_for(self, platform: str) -> PlatformRoundState:
        return self.twitter_state if platform == "twitter" else self.reddit_state

    def _consume_trace(self, platforms=("twitter", "reddit")) -> None:
        """Advance last_rowid past rows written outside RUN_ROUNDS (interviews,
        refreshes) so the next round does not log them. See NOTES.md #18."""
        for platform in platforms:
            sim = self._sims.get(platform)
            if sim is None:
                continue
            state = self._state_for(platform)
            _, state.last_rowid = rps.fetch_new_actions_from_db(
                sim.db_path, state.last_rowid, sim.agent_names,
            )

    # -- GAME_INTERVIEW -------------------------------------------------------

    async def handle_game_interview(self, command_id: str, args: Dict[str, Any]) -> bool:
        platform = args.get("platform", "twitter")
        sim = self._sims.get(platform)
        if sim is None:
            self.send_response(command_id, "failed", error=f"platform unavailable: {platform}")
            return True
        # Rows before this call belong to earlier commands; consume them first
        # so the watermark marks exactly where this batch's interviews start.
        self._consume_trace((platform,))
        start_rowid = self._state_for(platform).last_rowid

        # refresh() reads the rec table, which OASIS only rebuilds at the start
        # of env.step(); without this the feed misses every post made since
        # the last step, including the latest debate round (NOTES.md #20).
        if any(FEED_PLACEHOLDER in item.get("prompt", "") for item in args.get("interviews", [])):
            await sim.env.platform.update_rec_table()

        exclude_own = bool(args.get("exclude_own_posts", False))
        answers: Dict[int, Dict[str, Any]] = {}
        actions = {}
        for item in args.get("interviews", []):
            agent_id = int(item.get("agent_id"))
            prompt = item.get("prompt", "")
            answer: Dict[str, Any] = {"agent_id": agent_id, "response": None, "feed_posts": 0,
                                      "feed": None, "error": None}
            answers[agent_id] = answer
            try:
                agent = sim.agent_graph.get_agent(agent_id)
                if FEED_PLACEHOLDER in prompt:
                    posts = await agent.env.action.refresh()
                    if exclude_own and posts.get("success"):
                        # agents otherwise read their own posts back as evidence (NOTES.md #38)
                        posts["posts"] = [p for p in posts.get("posts") or [] if p.get("user_id") != agent_id]
                    if posts.get("success") and posts.get("posts"):
                        feed = json.dumps(posts["posts"], indent=2, ensure_ascii=False)
                        answer["feed_posts"] = len(posts["posts"])
                        # what the agent actually saw, for analysis (NOTES.md #37)
                        answer["feed"] = [
                            {k: p.get(k) for k in ("post_id", "user_id", "content", "num_likes")}
                            for p in posts["posts"]
                        ]
                    else:
                        feed = "(no posts)"
                    prompt = prompt.replace(FEED_PLACEHOLDER, feed)
                actions[agent] = rps.ManualAction(
                    action_type=rps.ActionType.INTERVIEW,
                    action_args={"prompt": prompt},
                )
            except Exception as e:  # noqa: BLE001 -- reported per agent
                answer["error"] = f"{type(e).__name__}: {e}"

        if actions:
            await sim.env.step(actions)

        # Read only interview rows written by this step. The stock
        # _get_interview_result() takes the agent's latest interview by
        # created_at, which silently returns a previous answer when this
        # interview failed to write a row (NOTES.md #19).
        conn = sqlite3.connect(sim.db_path)
        try:
            rows = conn.execute(
                "SELECT user_id, info FROM trace WHERE rowid > ? AND action = ? ORDER BY rowid",
                (start_rowid, rps.ActionType.INTERVIEW.value),
            ).fetchall()
        finally:
            conn.close()
        for user_id, info_json in rows:
            answer = answers.get(user_id)
            if answer is None:
                continue
            try:
                info = json.loads(info_json) if info_json else {}
                answer["response"] = info.get("response")
            except json.JSONDecodeError:
                answer["response"] = info_json
        for answer in answers.values():
            if answer["response"] is None and answer["error"] is None:
                answer["error"] = "no interview record written"

        self._consume_trace((platform,))
        self.send_response(command_id, "completed", result={
            "platform": platform, "answers": list(answers.values()),
        })
        return True

    # -- INJECT_POST ----------------------------------------------------------

    async def handle_inject_post(self, command_id: str, args: Dict[str, Any]) -> bool:
        """Publish posts as agents in ONE env.step per platform.

        args: {"posts": [{"agent_id", "content"}], "platform"} or the
        single-post form {"agent_id", "content", "platform"}. One step keeps
        the OASIS clock from advancing once per post (NOTES.md #8).
        """
        posts = args.get("posts")
        if posts is None:
            posts = [{"agent_id": args.get("agent_id"), "content": args.get("content", "")}]
        posts = [p for p in posts if p.get("agent_id") is not None and p.get("content")]
        platform = args.get("platform", "both")

        if not posts:
            self.send_response(command_id, "failed", error="agent_id and content are required")
            return True

        platforms_result: Dict[str, Any] = {}
        for plat, env, agent_graph, state in (
            ("twitter", self.twitter_env, self.twitter_agent_graph, self.twitter_state),
            ("reddit", self.reddit_env, self.reddit_agent_graph, self.reddit_state),
        ):
            if platform not in (plat, "both") or not env:
                continue
            try:
                actions: Dict[Any, List[Any]] = {}
                unknown = []
                for post in posts:
                    try:
                        agent = agent_graph.get_agent(int(post["agent_id"]))
                    except Exception:  # noqa: BLE001 -- reported below
                        unknown.append(post["agent_id"])
                        continue
                    actions.setdefault(agent, []).append(rps.ManualAction(
                        action_type=rps.ActionType.CREATE_POST,
                        action_args={"content": post["content"]},
                    ))
                self._consume_trace((plat,))
                if actions:
                    await env.step({a: (acts[0] if len(acts) == 1 else acts) for a, acts in actions.items()})
                # Consume the injected posts' trace rows now, so the next
                # RUN_ROUNDS does not log them as spontaneous agent posts.
                # See NOTES.md #9.
                sim = self._sims[plat]
                injected, state.last_rowid = rps.fetch_new_actions_from_db(
                    sim.db_path, state.last_rowid, sim.agent_names,
                )
                action_logger = self._action_loggers.get(plat)
                if action_logger:
                    for action_data in injected:
                        action_logger.log_action(
                            round_num=state.round_num,
                            agent_id=action_data["agent_id"],
                            agent_name=action_data["agent_name"],
                            action_type=action_data["action_type"],
                            action_args={**action_data["action_args"], "injected": True},
                        )
                platforms_result[plat] = {
                    "round_num": state.round_num, "posted": len(injected),
                    "unknown_agents": unknown, "error": None,
                }
            except Exception as e:  # mirrors ParallelIPCHandler's own defensive style
                platforms_result[plat] = {"round_num": state.round_num, "error": str(e)}

        if not platforms_result:
            self.send_response(command_id, "failed", error=f"platform unavailable: {platform}")
            return True

        result = {"requested": len(posts), "platforms": platforms_result}
        if len(posts) == 1:
            result["agent_id"] = posts[0]["agent_id"]
        self.send_response(command_id, "completed", result=result)
        return True

    # -- GET_STATE ----------------------------------------------------------

    async def handle_get_state(self, command_id: str, args: Dict[str, Any]) -> bool:
        platform = args.get("platform", "both")
        result: Dict[str, Any] = {}
        if platform in ("twitter", "both") and self.twitter_env:
            result["twitter"] = self.twitter_state.to_dict()
        if platform in ("reddit", "both") and self.reddit_env:
            result["reddit"] = self.reddit_state.to_dict()

        lock = RunRoundsLock.read(self._run_rounds_lock_path)
        result["server"] = {
            "busy": lock is not None,
            "current_command_id": lock.command_id if lock else None,
            "in_flight_since": (
                datetime.fromtimestamp(lock.started_at).isoformat() if lock else None
            ),
        }
        self.send_response(command_id, "completed", result=result)
        return True
