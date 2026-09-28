"""
Step-server package for the oTree <-> MiroFish integration.

- ipc_protocol.py        : schema definitions for the 3 new IPC commands
                           (RUN_ROUNDS, INJECT_POST, GET_STATE), kept free of
                           any import on run_parallel_simulation.py or the
                           Flask app so either side of the process boundary
                           can import it cheaply.
- round_state.py         : resumable per-platform round-counter state and the
                           crash-safe RUN_ROUNDS lock file.
- step_server_handler.py : ExperimentIPCHandler, which serves the new commands
                           on top of ParallelIPCHandler and advances rounds via
                           run_parallel_simulation.step_round().

Entry point: backend/scripts/run_experiment_env.py.
See docs/otree-integration/phase0-mirofish.md for the design.
"""
