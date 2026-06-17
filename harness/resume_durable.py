#!/usr/bin/env python3
"""Resume a durable arm from an existing committed shared tree.

The durable arm commits each completed dependency layer to its shared tree
(`runs/<trial>/T`). If a run is interrupted (here: an API wall mid-layer), the
committed layers are a consistent, resumable checkpoint — exactly the property
the paper measures in the resumability section. This driver reuses the harness
internals to continue from that checkpoint: it reads which in-scope modules are
already converted in the tree, skips fully-converted layers, converts only the
remaining modules (seeding each worker from the shared tree so it inherits prior
conversions), commits per layer, then scores and writes a TrialRecord.

Usage:
  python resume_durable.py --tree <runs/.../T> --scope-json <runs/.../scope.json>
      --backend claude --model claude-sonnet-4-6 --max-workers 10 --timeout 900
      --out results/claude_XL_durable.jsonl --stratum XL --seed 0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import run_arm
from schema import TrialRecord, append_record
from drr import discovery_reuse


def converted_in_tree(tree: Path, scope: list[str]) -> set[str]:
    """In-scope modules whose .ts exists (and .js is gone) in the shared tree."""
    done: set[str] = set()
    for js in scope:
        ts = tree / (js[:-3] + ".ts") if js.endswith(".js") else None
        if ts and ts.exists() and not (tree / js).exists():
            done.add(js)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Resume a durable arm from a committed tree.")
    ap.add_argument("--tree", required=True, help="existing shared tree (runs/<trial>/T)")
    ap.add_argument("--scope-json", required=True)
    ap.add_argument("--backend", default="claude", choices=["cursor", "claude"])
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--state-dir", default="/Users/cary/lwds/pm-state")
    ap.add_argument("--out", default="/Users/cary/lwds/results/claude_XL_durable.jsonl")
    ap.add_argument("--stratum", default="XL")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    run_arm.BACKEND = args.backend
    shared = Path(args.tree).resolve()
    sel = json.loads(Path(args.scope_json).read_text())
    runs_dir = shared.parent
    state_dir = Path(args.state_dir)
    direct = sel["direct_deps"]

    converted = converted_in_tree(shared, sel["scope"])
    print(f"resume: {len(converted)}/{sel['scope_size']} modules already converted in {shared}")

    steps: list = []
    t0 = time.time()
    for li, layer in enumerate(sel["layers"]):
        remaining = [f for f in layer if f not in converted]
        if not remaining:
            print(f"  layer {li}: complete ({len(layer)} modules) — skip")
            continue
        print(f"  layer {li}: converting {len(remaining)}/{len(layer)} remaining modules")
        run_arm.prof("resume_layer_start", layer=li, remaining=len(remaining))
        results = run_arm._convert_files_parallel(
            seed_for=lambda f: shared, files=remaining, runs_dir=runs_dir, state_dir=state_dir,
            model=args.model, timeout=args.timeout, max_workers=args.max_workers,
            converted_deps_of=lambda f: [d for d in direct.get(f, []) if d in converted],
            pristine=False)
        for f in remaining:
            if results[f]["ts"]:
                run_arm._apply_conversion(shared, f, results[f]["ts"])
                converted.add(f)
            steps.append(results[f]["step"])
        run_arm._sh(["git", "add", "-A"], shared, timeout=120)
        run_arm._sh(["git", "-c", "user.email=lwds@local", "-c", "user.name=lwds",
                     "commit", "-q", "-m", f"durable(resumed): layer {li} of {len(remaining)} module(s)"],
                    shared, timeout=120)
        run_arm.prof("resume_layer_end", layer=li, converted_total=len(converted))

    print(f"resume: conversion done ({len(converted)}/{sel['scope_size']}); scoring...")
    verdict = run_arm.score(shared, sel)
    drr = discovery_reuse(shared, sel)

    trial_id = f"{sel['target']}-{args.stratum}-durable-s{args.seed}-resume-{uuid.uuid4().hex[:6]}"
    rec = TrialRecord(trial_id=trial_id, target=sel["target"], stratum=args.stratum,
                      arm="durable", seed=args.seed, scope_size=sel["scope_size"],
                      scope=sel["scope"], model=args.model, status="running")
    rec.steps = steps
    rec.n_worker_invocations = len(steps)
    rec.peak_scope_in_one_context = 1
    rec.wall_clock_s = round(time.time() - t0, 1)
    rec.oracle_ok = bool(verdict.get("ok"))
    rec.gates_passed = bool(verdict.get("gates_passed"))
    rec.escape_hatches = (verdict.get("escape_hatches") or {}).get("total")
    rec.oracle_verdict = verdict
    rec.discovery_reuse_rate = drr["rate"]
    rec.n_modules_consuming_prior_artifact = drr["numerator"]
    rec.n_modules_with_inscope_dep = drr["denominator"]
    rec.reuse_by_dependency_depth = drr["by_depth"]
    rec.status = "scored"
    rec.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    append_record(Path(args.out), rec)
    print(json.dumps({"trial_id": trial_id, "arm": "durable", "scope_size": sel["scope_size"],
                      "backend": args.backend, "model": args.model,
                      "oracle_ok": rec.oracle_ok, "gates_passed": rec.gates_passed,
                      "escape_hatches": rec.escape_hatches, "DRR": rec.discovery_reuse_rate,
                      "n_workers_this_resume": rec.n_worker_invocations,
                      "resume_wall_s": rec.wall_clock_s, "tree": str(shared)}, indent=2))
    return 0 if rec.oracle_ok else 1


if __name__ == "__main__":
    sys.exit(main())
