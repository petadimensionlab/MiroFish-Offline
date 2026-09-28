"""
Experiment API routes (oTree bridge)
Called by oTree bots: /decide per agent per round, /round_complete once per round.
"""

import traceback
from flask import request, jsonify

from . import experiment_bp
from ..services.experiment_bridge import get_bridge, InjectedFailure, UnknownSession
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.experiment')


def _require(data, *keys):
    missing = [k for k in keys if data.get(k) is None]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")


@experiment_bp.route('/configure', methods=['POST'])
def configure():
    """
    Set the decision policy and fault injection for an oTree session

    Request body:
        session_code (required), policy ('random'|'allc'|'alld'|'tft'|'llm'), seed,
        inject_delay_sec, inject_error_rate
        llm policy: simulation_id (or simulation_dir), platform, include_feed,
        num_rounds, default_choice, payoffs {R,T,S,P},
        agents [{agent_id, partner_agent_id}] (starts prefetching round 1)
        debate phase: debate_rounds, inject_results ('none'|'each'|'summary'),
        announcer_agent_id, opening_post, opening_agent_id
    """
    try:
        data = request.get_json(silent=True) or {}
        _require(data, 'session_code')
        settings = get_bridge().configure(
            data['session_code'],
            agents=data.get('agents'),
            **{k: v for k, v in data.items() if k not in ('session_code', 'agents')},
        )
        return jsonify({"success": True, "data": settings})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Failed to configure experiment: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@experiment_bp.route('/decide', methods=['POST'])
def decide():
    """
    Get one agent's choice for one round

    Request body:
        session_code, round_number, agent_id (required)
        history: [{"round_number", "own", "partner", "payoff"}] (optional)

    Returns:
        {"choice": "A"|"B", "reason", "source", "latency_sec", "missing", "cached"}
    """
    try:
        data = request.get_json(silent=True) or {}
        _require(data, 'session_code', 'round_number', 'agent_id')
        result = get_bridge().decide(
            data['session_code'],
            int(data['round_number']),
            int(data['agent_id']),
            history=data.get('history'),
        )
        return jsonify({"success": True, "data": result})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except UnknownSession as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except InjectedFailure as e:
        return jsonify({"success": False, "error": str(e)}), 503
    except Exception as e:
        logger.error(f"Failed to decide: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@experiment_bp.route('/round_complete', methods=['POST'])
def round_complete():
    """
    Notify that every player in the session finished a round (idempotent per round)

    Request body:
        session_code, round_number (required), plus any summary fields
    """
    try:
        data = request.get_json(silent=True) or {}
        _require(data, 'session_code', 'round_number')
        summary = {k: v for k, v in data.items() if k not in ('session_code', 'round_number')}
        result = get_bridge().round_complete(data['session_code'], int(data['round_number']), summary)
        return jsonify({"success": True, "data": result})
    except UnknownSession as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Failed to record round completion: {e}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@experiment_bp.route('/state/<session_code>', methods=['GET'])
def get_state(session_code: str):
    state = get_bridge().get_state(session_code)
    if state is None:
        return jsonify({"success": False, "error": f"unknown session: {session_code}"}), 404
    return jsonify({"success": True, "data": state})
