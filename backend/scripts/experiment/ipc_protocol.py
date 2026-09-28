"""
Schema definitions for the 3 new step-server IPC commands.

Deliberately standalone (stdlib only) so it can be imported by:
  - the simulation process (run_experiment_env.py / a future patched
    run_parallel_simulation.py), and
  - the Flask side (a future patched app/services/simulation_ipc.py)
without pulling in camel/oasis, Flask, or run_parallel_simulation.py's
import-time side effects.

This is a design artifact for Phase 0-B: today, CommandType is defined
twice in the real codebase (app/services/simulation_ipc.py:24 and
run_parallel_simulation.py:210) and the two are kept in sync by hand.
A real implementation should have both sides import CommandType (and
ideally the args/result TypedDicts below) from a single shared module
like this one instead of maintaining two copies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict


# ---------------------------------------------------------------------------
# Command type constants
#
# These 3 are ADDITIVE to the existing set (interview / batch_interview /
# close_env). Existing command handling is untouched.
# ---------------------------------------------------------------------------

RUN_ROUNDS = "run_rounds"
INJECT_POST = "inject_post"
GET_STATE = "get_state"
GAME_INTERVIEW = "game_interview"

NEW_COMMAND_TYPES = (RUN_ROUNDS, INJECT_POST, GET_STATE, GAME_INTERVIEW)

Platform = Literal["twitter", "reddit", "both"]


# ---------------------------------------------------------------------------
# RUN_ROUNDS
# ---------------------------------------------------------------------------

class RunRoundsArgs(TypedDict, total=False):
    platform: Platform          # default "both"
    rounds: int                 # k, required, >= 1


class PlatformRoundResult(TypedDict):
    rounds_requested: int
    rounds_executed: int        # may be < requested if total_rounds reached
    round_from: int              # round_num before this call (exclusive start)
    round_to: int                 # round_num after this call
    simulated_hour: int
    simulated_day: int
    total_rounds: int
    actions_this_call: int
    total_actions: int
    reached_end: bool           # True if total_rounds was hit mid-call


class RunRoundsResult(TypedDict, total=False):
    twitter: PlatformRoundResult
    reddit: PlatformRoundResult


# ---------------------------------------------------------------------------
# INJECT_POST
# ---------------------------------------------------------------------------

class InjectPostArgs(TypedDict, total=False):
    agent_id: int                # required
    content: str                  # required
    platform: Platform           # default "both"
    write_to_memory: bool        # default True -> also call
                                   # GraphMemoryUpdater.add_activity_from_dict


class PlatformInjectResult(TypedDict, total=False):
    post_id: Optional[int]
    round_num: int
    error: Optional[str]


class InjectPostResult(TypedDict, total=False):
    agent_id: int
    platforms: Dict[str, PlatformInjectResult]
    memory_written: bool


# ---------------------------------------------------------------------------
# GET_STATE
# ---------------------------------------------------------------------------

class GetStateArgs(TypedDict, total=False):
    platform: Platform           # default "both"


class PlatformState(TypedDict):
    round_num: int
    total_rounds: int
    simulated_hour: int
    simulated_day: int
    total_actions: int
    last_rowid: int
    status: str                  # "initializing" | "idle" | "running_rounds" | "closed"


class ServerState(TypedDict):
    busy: bool
    current_command_type: Optional[str]
    current_command_id: Optional[str]
    in_flight_since: Optional[str]  # ISO timestamp


class GetStateResult(TypedDict, total=False):
    twitter: PlatformState
    reddit: PlatformState
    server: ServerState


# ---------------------------------------------------------------------------
# Rejection shape used when a second RUN_ROUNDS arrives while one is
# already in flight (see phase0-mirofish.md "Serialization" section).
# This is carried in IPCResponse.error / IPCResponse.result, status="rejected".
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GAME_INTERVIEW
#
# Batch interview for game decisions. Unlike batch_interview, each prompt may
# contain FEED_PLACEHOLDER, replaced per agent with the posts that agent sees
# on refresh: OASIS interviews only see the agent's own ChatAgent memory,
# which is empty for agents never activated in a debate round (NOTES.md #10).
# Interview trace rows are consumed so they never reach actions.jsonl.
# ---------------------------------------------------------------------------

FEED_PLACEHOLDER = "{{FEED}}"


class GameInterviewItem(TypedDict):
    agent_id: int
    prompt: str


class GameInterviewArgs(TypedDict, total=False):
    platform: Literal["twitter", "reddit"]   # default "twitter"
    interviews: List[GameInterviewItem]


class GameInterviewAnswer(TypedDict, total=False):
    agent_id: int
    response: Optional[str]
    feed_posts: int                 # number of posts embedded in the prompt
    error: Optional[str]


class GameInterviewResult(TypedDict):
    platform: str
    answers: List[GameInterviewAnswer]


REJECTED = "rejected"


@dataclass
class RunRoundsRejection:
    reason: str = "run_rounds already in progress"
    in_flight_command_id: Optional[str] = None
    in_flight_since: Optional[str] = None
    retry_hint: str = "poll get_state or retry after in_flight_command completes"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason": self.reason,
            "in_flight_command_id": self.in_flight_command_id,
            "in_flight_since": self.in_flight_since,
            "retry_hint": self.retry_hint,
        }
