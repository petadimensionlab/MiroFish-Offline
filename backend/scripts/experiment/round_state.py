"""
Resumable per-platform round state for the step-server design.

Today, run_twitter_simulation()/run_reddit_simulation() in
run_parallel_simulation.py hold round_num, last_rowid, total_actions and
start_time as plain local variables inside one unbroken `for round_num in
range(total_rounds):` loop (see run_parallel_simulation.py:1228-1288 for
Twitter, and the equivalent Reddit block). That works for "run everything,
then sit in wait-mode" but cannot survive being re-entered across many
separate RUN_ROUNDS(k) IPC calls.

This module defines the state object those loops would need to read from
and write back into, if the loop body were extracted into a step method
(see docs/otree-integration/phase0-mirofish.md, refactor item R1).
Nothing here imports run_parallel_simulation.py or oasis/camel, so it is
safe to import from either side of the process boundary and from tests.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class PlatformRoundState:
    """Mirrors the local variables in run_{twitter,reddit}_simulation()."""

    platform: str                     # "twitter" | "reddit"
    round_num: int = 0                # rounds completed so far (post-init-posts)
    total_rounds: int = 0              # from time_config, set once at init
    minutes_per_round: int = 30
    last_rowid: int = 0                 # sqlite trace rowid watermark, see
                                          # fetch_new_actions_from_db() in
                                          # run_parallel_simulation.py:657
    total_actions: int = 0
    status: str = "initializing"        # initializing|idle|running_rounds|closed

    def simulated_minutes(self) -> int:
        return self.round_num * self.minutes_per_round

    def simulated_hour(self) -> int:
        return (self.simulated_minutes() // 60) % 24

    def simulated_day(self) -> int:
        return self.simulated_minutes() // (60 * 24) + 1

    def reached_end(self) -> bool:
        return self.round_num >= self.total_rounds

    def to_dict(self) -> dict:
        d = asdict(self)
        d["simulated_hour"] = self.simulated_hour()
        d["simulated_day"] = self.simulated_day()
        d["reached_end"] = self.reached_end()
        return d


@dataclass
class RunRoundsLock:
    """
    Crash-safe marker for "a RUN_ROUNDS burst is in flight".

    In-memory guards (an asyncio.Lock on the handler instance) are not
    enough on their own: if the simulation process is killed mid-burst and
    supervised back up, a fresh ParallelIPCHandler starts with a clean
    in-memory Lock and would happily accept a new RUN_ROUNDS even though
    the previous one never sent a response and the caller may still be
    waiting/retrying. A lock *file* (written before the first env.step of
    a burst, removed after the last) survives that restart and lets a new
    process detect + report the stale state instead of silently racing.

    This dataclass is the (de)serialization shape for that file; it does
    not itself do any file I/O policy beyond read/write/remove, so the
    step-server can decide staleness (e.g. mtime older than N seconds with
    no matching live PID) using whatever policy fits.
    """

    command_id: str
    platform: str
    rounds_requested: int
    pid: int
    started_at: float = field(default_factory=time.time)

    def write(self, path: str) -> None:
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f)
        os.replace(tmp_path, path)  # atomic on POSIX

    @classmethod
    def read(cls, path: str) -> Optional["RunRoundsLock"]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**data)
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    @staticmethod
    def remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def is_stale(self, max_age_sec: float = 120.0) -> bool:
        """
        Heuristic only: a burst of k rounds against a local Ollama model
        can legitimately take minutes. Callers should prefer checking PID
        liveness (os.kill(pid, 0)) over age where possible; age is the
        fallback for cross-machine / containerized deployments where the
        PID is not in the same namespace.
        """
        return (time.time() - self.started_at) > max_age_sec
