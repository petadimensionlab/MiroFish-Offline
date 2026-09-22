#!/usr/bin/env python3
"""MiroFish-Offline command-line interface.

Drives the same workflow as the web UI (http://localhost:3000) by calling the
Flask backend REST API (default http://localhost:5001). Start the backend first:

    npm run backend          # or: cd backend && uv run python run.py

Global options (before the subcommand): --base-url, --json, --quiet,
--timeout, --interval.

Examples:
    python scripts/mirofish_cli.py pipeline \\
        --requirement "Public reaction to this policy?" \\
        --file press_release.pdf --max-rounds 50 --report-out report.md

    python scripts/mirofish_cli.py sim-status --simulation-id sim_xxxx
    python scripts/mirofish_cli.py interview --simulation-id sim_xxxx \\
        --agent-id 0 --prompt "Why did you post that?"
"""

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

DEFAULT_BASE_URL = os.environ.get("MIROFISH_API_BASE", "http://localhost:5001")
TERMINAL_RUN_STATES = {"completed", "failed", "stopped"}


class CliError(Exception):
    pass


def encode_multipart(fields, files):
    boundary = uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    for field, file_path in files:
        path = Path(file_path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
            f'filename="{path.name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode("utf-8")
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class ApiClient:
    def __init__(self, base_url, timeout):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _open(self, method, path, body=None, headers=None):
        url = f"{self.base_url}{path}"
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        req = request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except error.HTTPError as exc:
            detail = self._error_detail(exc.read().decode("utf-8", "replace"))
            raise CliError(f"{method} {path} failed ({exc.code}): {detail}") from exc
        except error.URLError as exc:
            raise CliError(
                f"Cannot reach {url}: {exc.reason}. Is the backend running?"
            ) from exc
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8"))
        return raw

    @staticmethod
    def _error_detail(raw):
        try:
            payload = json.loads(raw)
            return payload.get("error") or payload.get("message") or raw
        except json.JSONDecodeError:
            return raw

    def _unwrap(self, result, path):
        if not isinstance(result, dict):
            raise CliError(f"Unexpected non-JSON response from {path}")
        if result.get("success") is False:
            raise CliError(result.get("error") or result.get("message") or "request failed")
        return result

    def get(self, path):
        return self._unwrap(self._open("GET", path), path)

    def post(self, path, payload=None):
        body = json.dumps(payload or {}).encode("utf-8")
        result = self._open("POST", path, body, {"Content-Type": "application/json"})
        return self._unwrap(result, path)

    def post_multipart(self, path, fields, files):
        body, content_type = encode_multipart(fields, files)
        result = self._open("POST", path, body, {"Content-Type": content_type})
        return self._unwrap(result, path)

    def download(self, path, out_path):
        raw = self._open("GET", path)
        if isinstance(raw, dict):
            raise CliError(raw.get("error") or "download failed")
        Path(out_path).write_bytes(raw)
        return out_path


def wait_for(fetch, done, describe, timeout, interval, quiet, label="completion"):
    deadline = time.monotonic() + timeout
    data = None
    while True:
        data = fetch()
        if not quiet:
            print(f"  {describe(data)}", file=sys.stderr)
        if done(data):
            return data
        if time.monotonic() >= deadline:
            raise CliError(
                f"timed out after {timeout:.0f}s waiting for {label}; "
                "increase --max-wait (see /tmp/mirofish-backend.log)"
            )
        time.sleep(interval)


def output(args, data, lines):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for line in lines:
            print(line)


def parse_entity_types(value):
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def platform_to_flags(platform):
    if platform == "twitter":
        return True, False
    if platform == "reddit":
        return False, True
    return True, True


def default_project_name(files, explicit=None):
    if explicit:
        return explicit
    stem = Path(files[0]).stem if files else "project"
    return f"{stem} ({datetime.now().strftime('%Y-%m-%d %H:%M')})"


def op_ontology(client, requirement, files, name, context):
    fields = {
        "simulation_requirement": requirement,
        "project_name": name or "Unnamed Project",
    }
    if context:
        fields["additional_context"] = context
    response = client.post_multipart(
        "/api/graph/ontology/generate", fields, [("files", f) for f in files]
    )
    return response["data"]


def op_build(client, project_id, chunk_size, chunk_overlap, wait, args, force=False) -> tuple[str, dict | None]:
    payload = {"project_id": project_id}
    if chunk_size:
        payload["chunk_size"] = chunk_size
    if chunk_overlap:
        payload["chunk_overlap"] = chunk_overlap
    if force:
        payload["force"] = True
    response = client.post("/api/graph/build", payload)
    task_id = response["data"]["task_id"]
    if not wait:
        return task_id, None
    task = wait_for(
        fetch=lambda: client.get(f"/api/graph/task/{task_id}")["data"],
        done=lambda data: data.get("status") in ("completed", "failed"),
        describe=lambda data: f"graph build {data.get('progress', 0)}% {data.get('message', '')}",
        timeout=args.max_wait,
        interval=args.interval,
        quiet=args.quiet,
        label="graph build",
    )
    if task.get("status") == "failed":
        raise CliError(f"graph build failed: {task.get('error')}")
    project = client.get(f"/api/graph/project/{project_id}")["data"]
    return task_id, project


def op_create(client, project_id, graph_id, platform):
    twitter, reddit = platform_to_flags(platform)
    payload = {"project_id": project_id, "enable_twitter": twitter, "enable_reddit": reddit}
    if graph_id:
        payload["graph_id"] = graph_id
    response = client.post("/api/simulation/create", payload)
    return response["data"]


def op_prepare(client, simulation_id, entity_types, use_llm, parallel, force, wait, args):
    payload = {
        "simulation_id": simulation_id,
        "use_llm_for_profiles": use_llm,
        "parallel_profile_count": parallel,
        "force_regenerate": force,
    }
    if entity_types:
        payload["entity_types"] = entity_types
    response = client.post("/api/simulation/prepare", payload)
    data = response["data"]
    if data.get("already_prepared") or not wait:
        return data
    task_id = data["task_id"]
    task = wait_for(
        fetch=lambda: client.post(
            "/api/simulation/prepare/status",
            {"task_id": task_id, "simulation_id": simulation_id},
        )["data"],
        done=lambda item: item.get("status") in ("completed", "failed", "ready"),
        describe=lambda item: f"prepare {item.get('progress', 0)}% {item.get('message', '')}",
        timeout=args.max_wait,
        interval=args.interval,
        quiet=args.quiet,
        label="simulation preparation",
    )
    if task.get("status") == "failed":
        raise CliError(f"prepare failed: {task.get('error')}")
    return task


def op_start(client, simulation_id, platform, max_rounds, graph_memory, force, follow, args):
    if platform == "both":
        platform = "parallel"
    payload = {
        "simulation_id": simulation_id,
        "platform": platform,
        "enable_graph_memory_update": graph_memory,
        "force": force,
    }
    if max_rounds:
        payload["max_rounds"] = max_rounds
    response = client.post("/api/simulation/start", payload)
    data = response["data"]
    if not follow:
        return data
    state = wait_for(
        fetch=lambda: client.get(f"/api/simulation/{simulation_id}/run-status")["data"],
        done=lambda item: item.get("runner_status") in TERMINAL_RUN_STATES,
        describe=lambda item: (
            f"run {item.get('runner_status')} round "
            f"{item.get('current_round', 0)}/{item.get('total_rounds', 0)} "
            f"({item.get('progress_percent', 0)}%)"
        ),
        timeout=args.max_wait,
        interval=args.interval,
        quiet=args.quiet,
        label="simulation run",
    )
    if state.get("runner_status") == "failed":
        raise CliError(f"simulation failed: {state.get('error') or 'see simulation log'}")
    return state


def op_report(client, simulation_id, force, wait, args):
    response = client.post(
        "/api/report/generate",
        {"simulation_id": simulation_id, "force_regenerate": force},
    )
    data = response["data"]
    if data.get("already_generated") or not wait:
        return data
    task_id = data.get("task_id")
    if not task_id:
        return data
    task = wait_for(
        fetch=lambda: client.post(
            "/api/report/generate/status",
            {"task_id": task_id, "simulation_id": simulation_id},
        )["data"],
        done=lambda item: item.get("status") in ("completed", "failed")
        or item.get("already_completed"),
        describe=lambda item: f"report {item.get('progress', 0)}% {item.get('message', '')}",
        timeout=args.max_wait,
        interval=args.interval,
        quiet=args.quiet,
        label="report generation",
    )
    if task.get("status") == "failed":
        raise CliError(f"report generation failed: {task.get('error')}")
    report = client.get(f"/api/report/by-simulation/{simulation_id}")["data"]
    return report


def list_projects(client, limit):
    return client.get("/api/graph/project/list?" + urlencode({"limit": limit}))["data"]


def fetch_simulations(client, project_id):
    return client.get("/api/simulation/list?" + urlencode({"project_id": project_id}))["data"]


def simulation_report_id(client, simulation_id):
    try:
        data = client.get(f"/api/report/check/{simulation_id}")["data"]
    except CliError:
        return None
    return data.get("report_id") if data.get("has_report") else None


def project_row(index, project):
    created = (project.get("created_at") or "")[:16].replace("T", " ")
    name = (project.get("name") or "")[:30]
    files = len(project.get("files") or [])
    return (
        f"{index:>2}  {created:<16}  {project.get('status', ''):<18}  "
        f"{name:<30}  {files:>5}  {project.get('project_id')}"
    )


def next_action(project, simulations, client):
    status = project.get("status")
    if status == "graph_building":
        return ("build", {"force": True})
    if not project.get("graph_id"):
        return ("build", {"force": False})
    if not simulations:
        return ("create", {})
    sim = simulations[0]
    simulation_id = sim["simulation_id"]
    if simulation_report_id(client, simulation_id):
        return ("report-ready", {"simulation_id": simulation_id})
    sim_status = sim.get("status")
    if sim_status in ("created", "preparing", "failed"):
        return ("prepare", {"simulation_id": simulation_id})
    if sim_status in ("ready", "stopped", "paused"):
        return ("start", {"simulation_id": simulation_id})
    if sim_status == "completed":
        return ("report", {"simulation_id": simulation_id})
    return ("status", {"simulation_id": simulation_id})


def format_action(project, action):
    kind, params = action
    project_id = project["project_id"]
    if kind == "build":
        force = " --force" if params.get("force") else ""
        return f"./scripts/run_cli.sh build --project-id {project_id}{force}"
    if kind == "create":
        return f"./scripts/run_cli.sh sim-create --project-id {project_id}"
    if kind == "prepare":
        return f"./scripts/run_cli.sh sim-prepare --simulation-id {params['simulation_id']}"
    if kind == "start":
        return f"./scripts/run_cli.sh sim-start --simulation-id {params['simulation_id']} --follow"
    if kind == "report":
        return f"./scripts/run_cli.sh report-generate --simulation-id {params['simulation_id']}"
    if kind == "report-ready":
        return f"./scripts/run_cli.sh report-download --report-id {params.get('report_id') or 'REPORT_ID'} --out report.md"
    return f"./scripts/run_cli.sh sim-status --simulation-id {params['simulation_id']}"


def run_action(client, project, action, args):
    kind, params = action
    if kind == "build":
        _, built = op_build(client, project["project_id"], None, None, True, args, params.get("force", False))
        print(f"graph_id: {built.get('graph_id') if built else '-'}")
    elif kind == "create":
        print(f"simulation_id: {op_create(client, project['project_id'], project.get('graph_id'), 'both')['simulation_id']}")
    elif kind == "prepare":
        print(f"status: {op_prepare(client, params['simulation_id'], None, True, getattr(args, 'parallel_profiles', 24), False, True, args).get('status')}")
    elif kind == "start":
        print(f"runner_status: {op_start(client, params['simulation_id'], 'parallel', getattr(args, 'max_rounds', None), False, False, True, args).get('runner_status')}")
    elif kind == "report":
        print(f"report_id: {op_report(client, params['simulation_id'], False, True, args).get('report_id')}")
    elif kind == "status":
        data = client.get(f"/api/simulation/{params['simulation_id']}/run-status")["data"]
        print(f"runner_status: {data.get('runner_status')} round {data.get('current_round')}/{data.get('total_rounds')}")
    else:
        print("report is ready; use report-get / report-download")


def resolve_action(client, project, simulations):
    action = next_action(project, simulations, client)
    if action[0] == "report-ready":
        return (action[0], {"simulation_id": action[1]["simulation_id"],
                            "report_id": simulation_report_id(client, action[1]["simulation_id"])})
    return action


def print_project_detail(client, project, simulations, args):
    ontology = project.get("ontology") or {}
    files = project.get("files") or []
    if args.json:
        print(json.dumps({"project": project, "simulations": simulations}, ensure_ascii=False, indent=2))
        return
    print(f"project_id : {project.get('project_id')}")
    print(f"name       : {project.get('name')}")
    print(f"status     : {project.get('status')}")
    print(f"created    : {project.get('created_at')}")
    print(f"requirement: {project.get('simulation_requirement')}")
    print(f"graph_id   : {project.get('graph_id')}")
    print(f"ontology   : {len(ontology.get('entity_types', []))} entity types, {len(ontology.get('edge_types', []))} edge types")
    print("files      : " + (", ".join(f.get("filename", "?") for f in files) or "-"))
    if simulations:
        print("simulations:")
        for sim in simulations:
            report_id = simulation_report_id(client, sim["simulation_id"]) or "-"
            print(f"  - {sim['simulation_id']}  status={sim.get('status')}  report={report_id}")
    else:
        print("simulations: -")
    action = resolve_action(client, project, simulations)
    print("next step  : " + format_action(project, action))


def cmd_projects(client, args):
    projects = list_projects(client, getattr(args, "limit", 20))
    if args.json:
        print(json.dumps(projects, ensure_ascii=False, indent=2))
        return 0
    print(f"{'#':>2}  {'CREATED':<16}  {'STATUS':<18}  {'NAME':<30}  {'FILES':>5}  PROJECT_ID")
    for index, project in enumerate(projects, 1):
        print(project_row(index, project))
    return 0


def cmd_project(client, args):
    project = client.get(f"/api/graph/project/{args.project_id}")["data"]
    print_project_detail(client, project, fetch_simulations(client, args.project_id), args)
    return 0


def run_until_done(client, project_id, args):
    for step in range(1, 21):
        project = client.get(f"/api/graph/project/{project_id}")["data"]
        simulations = fetch_simulations(client, project_id)
        action = resolve_action(client, project, simulations)
        if action[0] == "report-ready":
            print(f"[done] {project_id} is complete")
            print("next step: " + format_action(project, action))
            return 0
        print(f"[{step}] {format_action(project, action)}")
        run_action(client, project, action, args)
    raise CliError("resume did not finish after 20 steps; run `project --project-id ...` to inspect")


def cmd_resume(client, args):
    if not args.project_id and args.index is None:
        return cmd_projects(client, args)
    if args.project_id:
        project_id = args.project_id
    else:
        projects = list_projects(client, args.index)
        if args.index < 1 or args.index > len(projects):
            raise CliError(f"--index out of range (1..{len(projects)})")
        project_id = projects[args.index - 1]["project_id"]

    if args.run and not getattr(args, "once", False):
        return run_until_done(client, project_id, args)

    project = client.get(f"/api/graph/project/{project_id}")["data"]
    simulations = fetch_simulations(client, project_id)
    action = resolve_action(client, project, simulations)
    print(f"project: {project_id}  ({project.get('name')})  status={project.get('status')}")
    print("next step:")
    print("  " + format_action(project, action))
    if args.run:
        run_action(client, project, action, args)
    return 0


def cmd_simulations(client, args):
    params = f"?{urlencode({'project_id': args.project_id})}" if args.project_id else ""
    data = client.get(f"/api/simulation/list{params}")["data"]
    output(args, data, [f"{s['simulation_id']}  {s.get('status', ''):<12} {s.get('project_id', '')}" for s in data])
    return 0


def cmd_ontology(client, args):
    name = default_project_name(args.file, args.name)
    data = op_ontology(client, args.requirement, args.file, name, args.context)
    ontology = data.get("ontology", {})
    output(args, data, [
        f"project_id: {data['project_id']}",
        f"entity_types: {len(ontology.get('entity_types', []))}",
        f"edge_types: {len(ontology.get('edge_types', []))}",
    ])
    return 0


def cmd_build(client, args):
    task_id, project = op_build(client, args.project_id, args.chunk_size, args.chunk_overlap, not args.no_wait, args, args.force)
    if project:
        lines = [
            f"graph_id: {project.get('graph_id')}",
            f"status: {project.get('status')}",
        ]
        if project.get("graph_id"):
            try:
                graph = client.get(f"/api/graph/data/{project['graph_id']}")["data"]
                lines.append(f"nodes: {graph.get('node_count')}  edges: {graph.get('edge_count')}")
            except CliError:
                pass
        output(args, project, lines)
    else:
        output(args, {"task_id": task_id, "status": "started"}, [
            f"graph build task started: {task_id} (use --wait to block)",
        ])
    return 0


def cmd_sim_create(client, args):
    data = op_create(client, args.project_id, args.graph_id, args.platform)
    output(args, data, [f"simulation_id: {data.get('simulation_id')}", f"status: {data.get('status')}"])
    return 0


def cmd_sim_prepare(client, args):
    data = op_prepare(
        client, args.simulation_id, parse_entity_types(args.entity_types),
        not args.no_llm_profiles, args.parallel_profiles, args.force, not args.no_wait, args,
    )
    output(args, data, [f"status: {data.get('status')}", f"message: {data.get('message', '')}"])
    return 0


def cmd_sim_start(client, args):
    data = op_start(
        client, args.simulation_id, args.platform, args.max_rounds,
        args.graph_memory, args.force, args.follow, args,
    )
    output(args, data, [
        f"runner_status: {data.get('runner_status')}",
        f"rounds: {data.get('current_round', 0)}/{data.get('total_rounds', 0)}",
    ])
    return 0


def cmd_sim_status(client, args):
    data = client.get(f"/api/simulation/{args.simulation_id}/run-status")["data"]
    output(args, data, [
        f"runner_status: {data.get('runner_status')}",
        f"rounds: {data.get('current_round', 0)}/{data.get('total_rounds', 0)}",
        f"actions: {data.get('total_actions_count', 0)}",
    ])
    return 0


def cmd_sim_stop(client, args):
    data = client.post("/api/simulation/stop", {"simulation_id": args.simulation_id})["data"]
    output(args, data, [f"runner_status: {data.get('runner_status')}"])
    return 0


def cmd_sim_close(client, args):
    data = client.post("/api/simulation/close-env", {"simulation_id": args.simulation_id})["data"]
    output(args, data, [f"message: {data.get('message', 'environment closed')}"])
    return 0


def cmd_sim_config(client, args):
    data = client.get(f"/api/simulation/{args.simulation_id}/config")["data"]
    output(args, data, [json.dumps(data, ensure_ascii=False, indent=2)])
    return 0


def cmd_profiles(client, args):
    data = client.get(
        f"/api/simulation/{args.simulation_id}/profiles?platform={args.platform}"
    )["data"]
    output(args, data, [f"count: {data.get('count', 0)}"])
    return 0


def cmd_interview(client, args):
    payload = {"simulation_id": args.simulation_id, "agent_id": args.agent_id, "prompt": args.prompt}
    if args.platform:
        payload["platform"] = args.platform
    data = client.post("/api/simulation/interview", payload)["data"]
    output(args, data, [json.dumps(data.get("result", data), ensure_ascii=False, indent=2)])
    return 0


def cmd_interview_all(client, args):
    payload = {"simulation_id": args.simulation_id, "prompt": args.prompt}
    if args.platform:
        payload["platform"] = args.platform
    data = client.post("/api/simulation/interview/all", payload)["data"]
    output(args, data, [json.dumps(data, ensure_ascii=False, indent=2)])
    return 0


def cmd_interview_history(client, args):
    payload = {"simulation_id": args.simulation_id, "limit": args.limit}
    if args.platform:
        payload["platform"] = args.platform
    if args.agent_id is not None:
        payload["agent_id"] = args.agent_id
    data = client.post("/api/simulation/interview/history", payload)["data"]
    output(args, data, [json.dumps(data, ensure_ascii=False, indent=2)])
    return 0


def cmd_report_generate(client, args):
    data = op_report(client, args.simulation_id, args.force, not args.no_wait, args)
    output(args, data, [
        f"report_id: {data.get('report_id')}",
        f"status: {data.get('status')}",
    ])
    return 0


def cmd_report_get(client, args):
    if not args.report_id and not args.simulation_id:
        raise CliError("provide --report-id or --simulation-id")
    if args.report_id:
        data = client.get(f"/api/report/{args.report_id}")["data"]
    else:
        data = client.get(f"/api/report/by-simulation/{args.simulation_id}")["data"]
    output(args, data, [json.dumps(data, ensure_ascii=False, indent=2)])
    return 0


def cmd_report_download(client, args):
    out = client.download(f"/api/report/{args.report_id}/download", args.out)
    print(f"saved: {out}")
    return 0


def cmd_report_chat(client, args):
    history = json.loads(args.history) if args.history else []
    data = client.post(
        "/api/report/chat",
        {"simulation_id": args.simulation_id, "message": args.message, "chat_history": history},
    )["data"]
    output(args, data, [data.get("response", "")])
    return 0


def cmd_pipeline(client, args):
    print("[1/5] ontology generation...", file=sys.stderr)
    project = op_ontology(
        client, args.requirement, args.file, default_project_name(args.file, args.name), args.context
    )
    project_id = project["project_id"]
    print(f"      project_id={project_id}", file=sys.stderr)

    print("[2/5] graph build...", file=sys.stderr)
    _, built_project = op_build(client, project_id, None, None, True, args)
    project = built_project or {}
    graph_id = project.get("graph_id")
    print(f"      graph_id={graph_id}", file=sys.stderr)

    print("[3/5] simulation create + prepare...", file=sys.stderr)
    simulation = op_create(client, project_id, graph_id, args.platform)
    simulation_id = simulation["simulation_id"]
    op_prepare(
        client, simulation_id, parse_entity_types(args.entity_types),
        True, args.parallel_profiles, False, True, args,
    )
    print(f"      simulation_id={simulation_id}", file=sys.stderr)

    print("[4/5] running simulation...", file=sys.stderr)
    op_start(client, simulation_id, args.platform, args.max_rounds, False, False, True, args)

    result = {"project_id": project_id, "graph_id": graph_id, "simulation_id": simulation_id}
    if not args.skip_report:
        print("[5/5] generating report...", file=sys.stderr)
        report = op_report(client, simulation_id, False, True, args)
        result["report_id"] = report.get("report_id")
        if args.report_out and report.get("report_id"):
            client.download(f"/api/report/{report['report_id']}/download", args.report_out)
            result["report_path"] = args.report_out

    if args.interview_agent is not None:
        data = client.post(
            "/api/simulation/interview",
            {
                "simulation_id": simulation_id,
                "agent_id": args.interview_agent,
                "prompt": args.interview_prompt or "What is your overall view on this?",
            },
        )["data"]
        result["interview"] = data.get("result", data)

    output(args, result, [json.dumps(result, ensure_ascii=False, indent=2)])
    return 0


def add_common(parser):
    parser.add_argument("--base-url", dest="base_url", default=argparse.SUPPRESS,
                        help=f"Backend base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="Print raw JSON results")
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="Suppress polling progress output")
    parser.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="HTTP timeout seconds (default: 300)")
    parser.add_argument("--max-wait", type=float, default=argparse.SUPPRESS,
                        help="Per-step polling timeout seconds (default: 7200)")
    parser.add_argument("--interval", type=float, default=argparse.SUPPRESS,
                        help="Polling interval seconds (default: 5)")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    add_common(common)

    parser = argparse.ArgumentParser(
        description="MiroFish-Offline CLI (drives the backend REST API)",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, help_text):
        return sub.add_parser(name, parents=[common], help=help_text)

    projects = command("projects", "List projects (newest first)")
    projects.add_argument("--limit", type=int, default=20)
    projects.set_defaults(func=cmd_projects)

    project_cmd = command("project", "Show one project with its simulations and next step")
    project_cmd.add_argument("--project-id", required=True)
    project_cmd.set_defaults(func=cmd_project)

    resume = command("resume", "Show (or run) the next step for a project")
    resume.add_argument("--project-id")
    resume.add_argument("--index", type=int, help="1-based row number from `projects`")
    resume.add_argument("--limit", type=int, default=20)
    resume.add_argument("--run", action="store_true", help="Run the remaining steps until done")
    resume.add_argument("--once", action="store_true", help="With --run, execute only one step")
    resume.add_argument("--max-rounds", type=int, help="Cap simulation rounds when starting/resuming")
    resume.add_argument("--parallel-profiles", type=int, default=24,
                        help="Concurrent persona generations during prepare (default: 24)")
    resume.set_defaults(func=cmd_resume)

    simulations = command("simulations", "List simulations")
    simulations.add_argument("--project-id")
    simulations.set_defaults(func=cmd_simulations)

    ontology = command("ontology", "Upload documents and generate an ontology")
    ontology.add_argument("--requirement", required=True)
    ontology.add_argument("--file", action="append", required=True)
    ontology.add_argument("--name")
    ontology.add_argument("--context")
    ontology.set_defaults(func=cmd_ontology)

    build = command("build", "Build the knowledge graph for a project")
    build.add_argument("--project-id", required=True)
    build.add_argument("--chunk-size", type=int)
    build.add_argument("--chunk-overlap", type=int)
    build.add_argument("--force", action="store_true", help="Rebuild even if building/completed")
    build.add_argument("--no-wait", action="store_true")
    build.set_defaults(func=cmd_build)

    create = command("sim-create", "Create a simulation for a project")
    create.add_argument("--project-id", required=True)
    create.add_argument("--graph-id")
    create.add_argument("--platform", choices=["twitter", "reddit", "both"], default="both")
    create.set_defaults(func=cmd_sim_create)

    prepare = command("sim-prepare", "Prepare agent profiles and simulation config")
    prepare.add_argument("--simulation-id", required=True)
    prepare.add_argument("--entity-types", help="Comma-separated entity types")
    prepare.add_argument("--no-llm-profiles", action="store_true")
    prepare.add_argument("--parallel-profiles", type=int, default=5)
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--no-wait", action="store_true")
    prepare.set_defaults(func=cmd_sim_prepare)

    start = command("sim-start", "Start / resume a simulation")
    start.add_argument("--simulation-id", required=True)
    start.add_argument("--platform", choices=["parallel", "twitter", "reddit"], default="parallel")
    start.add_argument("--max-rounds", type=int)
    start.add_argument("--graph-memory", action="store_true")
    start.add_argument("--force", action="store_true")
    start.add_argument("--follow", action="store_true", help="Block until the run finishes")
    start.set_defaults(func=cmd_sim_start)

    status = command("sim-status", "Show simulation run status")
    status.add_argument("--simulation-id", required=True)
    status.set_defaults(func=cmd_sim_status)

    stop = command("sim-stop", "Stop a running simulation")
    stop.add_argument("--simulation-id", required=True)
    stop.set_defaults(func=cmd_sim_stop)

    close = command("sim-close", "Gracefully close a simulation environment")
    close.add_argument("--simulation-id", required=True)
    close.set_defaults(func=cmd_sim_close)

    config = command("sim-config", "Print the generated simulation config")
    config.add_argument("--simulation-id", required=True)
    config.set_defaults(func=cmd_sim_config)

    profiles = command("profiles", "List generated agent profiles")
    profiles.add_argument("--simulation-id", required=True)
    profiles.add_argument("--platform", choices=["twitter", "reddit"], default="reddit")
    profiles.set_defaults(func=cmd_profiles)

    interview = command("interview", "Interview a single agent")
    interview.add_argument("--simulation-id", required=True)
    interview.add_argument("--agent-id", type=int, required=True)
    interview.add_argument("--prompt", required=True)
    interview.add_argument("--platform", choices=["twitter", "reddit"])
    interview.set_defaults(func=cmd_interview)

    interview_all = command("interview-all", "Interview every agent with one question")
    interview_all.add_argument("--simulation-id", required=True)
    interview_all.add_argument("--prompt", required=True)
    interview_all.add_argument("--platform", choices=["twitter", "reddit"])
    interview_all.set_defaults(func=cmd_interview_all)

    history = command("interview-history", "Show past interview responses")
    history.add_argument("--simulation-id", required=True)
    history.add_argument("--agent-id", type=int)
    history.add_argument("--platform", choices=["twitter", "reddit"])
    history.add_argument("--limit", type=int, default=100)
    history.set_defaults(func=cmd_interview_history)

    report = command("report-generate", "Generate the simulation report")
    report.add_argument("--simulation-id", required=True)
    report.add_argument("--force", action="store_true")
    report.add_argument("--no-wait", action="store_true")
    report.set_defaults(func=cmd_report_generate)

    report_get = command("report-get", "Print a report (by id or simulation)")
    report_get.add_argument("--report-id")
    report_get.add_argument("--simulation-id")
    report_get.set_defaults(func=cmd_report_get)

    report_dl = command("report-download", "Download a report markdown file")
    report_dl.add_argument("--report-id", required=True)
    report_dl.add_argument("--out", required=True)
    report_dl.set_defaults(func=cmd_report_download)

    report_chat = command("report-chat", "Ask the report agent a question")
    report_chat.add_argument("--simulation-id", required=True)
    report_chat.add_argument("--message", required=True)
    report_chat.add_argument("--history", help="JSON array of prior {role, content} messages")
    report_chat.set_defaults(func=cmd_report_chat)

    pipeline = command("pipeline", "Run ontology -> graph -> prepare -> simulate -> report")
    pipeline.add_argument("--requirement", required=True)
    pipeline.add_argument("--file", action="append", required=True)
    pipeline.add_argument("--name")
    pipeline.add_argument("--context")
    pipeline.add_argument("--platform", choices=["twitter", "reddit", "both"], default="both")
    pipeline.add_argument("--entity-types")
    pipeline.add_argument("--parallel-profiles", type=int, default=5)
    pipeline.add_argument("--max-rounds", type=int)
    pipeline.add_argument("--skip-report", action="store_true")
    pipeline.add_argument("--report-out")
    pipeline.add_argument("--interview-agent", type=int)
    pipeline.add_argument("--interview-prompt")
    pipeline.set_defaults(func=cmd_pipeline)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.base_url = getattr(args, "base_url", None) or DEFAULT_BASE_URL
    args.json = getattr(args, "json", False)
    args.quiet = getattr(args, "quiet", False)
    args.timeout = getattr(args, "timeout", 300.0)
    args.max_wait = getattr(args, "max_wait", 7200.0)
    args.interval = getattr(args, "interval", 5.0)

    client = ApiClient(args.base_url, args.timeout)
    try:
        return args.func(client, args)
    except (CliError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
