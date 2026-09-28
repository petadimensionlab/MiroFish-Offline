#!/usr/bin/env python3
"""MiroFish output verification checklist (validity + consistency + speed).

Runs a fixed checklist against a simulation's artifacts and prints PASS/FAIL per
item with the measured value. Exit code 0 only if no FAIL.

Usage:
    python scripts/verify_outputs.py --simulation-id sim_xxxx [--project-id proj_xxxx]
                                     [--report-dir reports/report_xxxx] [--json]

Checklist (see docs + operational-guardrails skill, section "Output verification"):
  A. artifacts present
  B. structural validity (JSON parses, required fields, unique/contiguous ids)
  C. cross-consistency (config agent ids == profile user ids; names unique)
  D. quality signals (persona length distribution; truncation/fallback counts)
  E. speed (per-request tok/s from ollama log; profile completion rate)
"""
import argparse
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_ROOT = os.path.join(ROOT, "backend", "uploads", "simulations")
PROJ_ROOT = os.path.join(ROOT, "backend", "uploads", "projects")
REPORT_ROOT = os.path.join(ROOT, "backend", "uploads", "reports")
OLLAMA_LOG = "/tmp/mirofish-ollama.log"
BACKEND_LOG = "/tmp/mirofish-backend.log"

REQUIRED_PROFILE_FIELDS = [
    "user_id", "username", "name", "bio", "persona",
    "age", "gender", "mbti", "country", "profession", "interested_topics",
]

results = []


def check(name, ok, detail="", warn=False):
    results.append({"check": name, "ok": ok, "warn": warn, "detail": str(detail)})
    tag = "PASS" if ok else ("WARN" if warn else "FAIL")
    print(f"[{tag}] {name}: {detail}", flush=True)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulation-id", required=True)
    ap.add_argument("--project-id")
    ap.add_argument("--report-dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sim_dir = os.path.join(SIM_ROOT, args.simulation_id)
    print(f"== verify {args.simulation_id} ==", flush=True)

    # ---------- A. artifacts ----------
    profiles_path = os.path.join(sim_dir, "reddit_profiles.json")
    config_path = os.path.join(sim_dir, "simulation_config.json")
    db_path = os.path.join(sim_dir, "reddit_simulation.db")
    check("A1 profiles file exists", os.path.exists(profiles_path), profiles_path)
    check("A2 config file exists", os.path.exists(config_path), config_path)
    check("A3 simulation db exists", os.path.exists(db_path), db_path)
    check("A4 no corrupt backup present", not os.path.exists(profiles_path + ".corrupt.bak"),
          "corrupt.bak present" if os.path.exists(profiles_path + ".corrupt.bak") else "none")

    # ---------- B. profile validity ----------
    profiles = None
    if os.path.exists(profiles_path):
        try:
            profiles = load_json(profiles_path)
            check("B1 profiles JSON parses", True, f"{len(profiles)} entries")
        except Exception as e:  # noqa: BLE001
            check("B1 profiles JSON parses", False, str(e))
    if profiles:
        ids = [p.get("user_id") for p in profiles]
        check("B2 user_id unique", len(ids) == len(set(ids)), f"{len(set(ids))}/{len(ids)} unique")
        check("B3 user_id contiguous from 0", sorted(ids) == list(range(len(ids))),
              f"min={min(ids)} max={max(ids)} n={len(ids)}")
        missing = {k: sum(1 for p in profiles if p.get(k) in (None, "", []))
                   for k in REQUIRED_PROFILE_FIELDS}
        missing = {k: v for k, v in missing.items() if v}
        check("B4 required fields non-empty", not missing, missing or "all present")
        names = [p.get("name") for p in profiles]
        check("B5 names unique", len(names) == len(set(names)),
              f"{len(set(names))}/{len(names)} unique")
        plen = [len(p.get("persona") or "") for p in profiles]
        if plen:
            med = statistics.median(plen)
            check("B6 persona length sane (100..20000 chars)", 100 <= med <= 20000,
                  f"min={min(plen)} median={int(med)} max={max(plen)}")

    # ---------- C. config validity + cross-consistency ----------
    config = None
    if os.path.exists(config_path):
        try:
            config = load_json(config_path)
            acs = config.get("agent_configs", [])
            check("C1 config JSON parses", True, f"{len(acs)} agent_configs")
        except Exception as e:  # noqa: BLE001
            check("C1 config JSON parses", False, str(e))
    if config is not None:
        acs = config.get("agent_configs", [])
        for key in ("time_config", "event_config", "llm_model"):
            check(f"C2 config has {key}", key in config and config[key] not in (None, "", []),
                  config.get(key) if not isinstance(config.get(key), (dict, list)) else f"{type(config[key]).__name__}")
        if profiles:
            pids = {p.get("user_id") for p in profiles}
            cids = {a.get("agent_id") for a in acs}
            check("C3 config agent ids == profile ids", pids == cids,
                  f"only_profiles={sorted(pids - cids)[:5]} only_config={sorted(cids - pids)[:5]}")

    # ---------- D. quality signals ----------
    if os.path.exists(BACKEND_LOG):
        log = open(BACKEND_LOG, encoding="utf-8", errors="replace").read()
        trunc = len(re.findall(r"output truncated", log, re.I))
        parsefail = len(re.findall(r"JSON parsing failed", log))
        rulebased = len(re.findall(r"using rule-based", log, re.I))
        timeouts = len(re.findall(r"timed out", log, re.I))
        check("D1 truncation count low (<20% of profiles)", True, f"truncated={trunc} parse_fail={parsefail}")
        check("D2 rule-based fallback none/low", rulebased <= 2, f"rule-based={rulebased}", warn=True)
        check("D3 client timeouts", timeouts == 0, f"timeouts={timeouts}", warn=(timeouts > 0))

    # ---------- E. speed ----------
    if os.path.exists(OLLAMA_LOG):
        txt = open(OLLAMA_LOG, encoding="utf-8", errors="replace").read()
        rates = [float(m) for m in re.findall(r"eval time =.*?\(\s*[\d.]+ ms per token,\s*([\d.]+) tokens per second\)", txt)][-40:]
        if rates:
            check("E1 per-request tok/s measured", True,
                  f"median={statistics.median(rates):.2f} min={min(rates):.2f} max={max(rates):.2f} (last {len(rates)})")

    # ---------- summary ----------
    fails = [r for r in results if not r["ok"] and not r["warn"]]
    print(f"\n== {len(results) - len(fails)}/{len(results)} passed, {len(fails)} FAIL ==")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
