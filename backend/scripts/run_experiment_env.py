"""
Experiment step-server for the oTree <-> MiroFish integration.

Unlike run_parallel_simulation.py, which runs every round up front and then
sits in wait mode, this server executes ZERO debate rounds at startup. It
brings up the OASIS environments (plus the round-0 initial posts), then
serves IPC commands so the caller (the oTree bridge) owns round advancement:

  run_rounds(k)   advance k debate rounds (via rps.step_round)
  inject_post     publish a CREATE_POST as a given agent (e.g. game results)
  get_state       round counters / busy flag
  interview / batch_interview / close_env   inherited from ParallelIPCHandler

Bootstrap and per-round logic are the functions extracted from
run_parallel_simulation.py by refactor R1 (setup_platform_env,
publish_initial_posts, step_round); nothing is re-implemented here.

Usage:
    python run_experiment_env.py --config path/to/simulation_config.json
        [--twitter-only | --reddit-only] [--start-hour H]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, ".."))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import run_parallel_simulation as rps  # noqa: E402
from action_logger import SimulationLogManager  # noqa: E402
from experiment.step_server_handler import ExperimentIPCHandler  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Experiment step-server (oTree integration)")
    parser.add_argument("--config", required=True, help="Configuration file path (simulation_config.json)")
    parser.add_argument("--twitter-only", action="store_true")
    parser.add_argument("--reddit-only", action="store_true")
    parser.add_argument(
        "--start-hour", type=int, default=None,
        help="Simulated hour the first debate round runs at. Round 0 is hour 0, where "
             "default active_hours (8-22) leave every agent idle; see NOTES.md #1.",
    )
    args = parser.parse_args()

    # Share the shutdown event with run_parallel_simulation's signal handling
    rps._shutdown_event = asyncio.Event()
    rps.setup_signal_handlers(asyncio.get_running_loop())

    if not os.path.exists(args.config):
        print(f"Error: Configuration file does not exist: {args.config}")
        sys.exit(1)

    config = rps.load_config(args.config)
    simulation_dir = os.path.dirname(args.config) or "."

    rps.init_logging_for_simulation(simulation_dir)
    log_manager = SimulationLogManager(simulation_dir)
    loggers = {
        "twitter": log_manager.get_twitter_logger(),
        "reddit": log_manager.get_reddit_logger(),
    }

    platforms = []
    if not args.reddit_only:
        platforms.append("twitter")
    if not args.twitter_only:
        platforms.append("reddit")

    sims = {}
    initial_rowids = {}
    for platform in platforms:
        label = platform.capitalize()

        def log_info(msg, label=label):
            log_manager.info(f"[{label}] {msg}")

        sim = await rps.setup_platform_env(platform, config, simulation_dir, log_info)
        if sim.env is None:
            continue
        loggers[platform].log_simulation_start(config)
        _, initial_rowids[platform] = await rps.publish_initial_posts(
            sim, config, loggers[platform], log_info,
            allow_multiple_per_agent=(platform == "reddit"),
        )
        sims[platform] = sim

    if not sims:
        log_manager.info("No platform could be started, exiting")
        sys.exit(1)

    handler = ExperimentIPCHandler(
        simulation_dir=simulation_dir,
        twitter_env=sims["twitter"].env if "twitter" in sims else None,
        twitter_agent_graph=sims["twitter"].agent_graph if "twitter" in sims else None,
        reddit_env=sims["reddit"].env if "reddit" in sims else None,
        reddit_agent_graph=sims["reddit"].agent_graph if "reddit" in sims else None,
    )
    handler.attach_config(config, sims=sims, action_loggers=loggers)
    handler.twitter_state.last_rowid = initial_rowids.get("twitter", 0)
    handler.reddit_state.last_rowid = initial_rowids.get("reddit", 0)

    if args.start_hour is not None:
        minutes_per_round = handler.twitter_state.minutes_per_round
        start_round = (args.start_hour * 60) // minutes_per_round
        for state in (handler.twitter_state, handler.reddit_state):
            state.round_num = start_round

    handler.update_status("alive")
    log_manager.info("Step server ready - commands: run_rounds, inject_post, get_state, "
                     "interview, batch_interview, close_env")

    try:
        while not rps._shutdown_event.is_set():
            if not await handler.process_commands():
                break
            try:
                await asyncio.wait_for(rps._shutdown_event.wait(), timeout=0.5)
                break
            except asyncio.TimeoutError:
                pass
    finally:
        handler.update_status("stopped")
        for platform, sim in sims.items():
            await sim.env.close()
            log_manager.info(f"[{platform.capitalize()}] Environment closed")


if __name__ == "__main__":
    asyncio.run(main())
