#!/usr/bin/env python3
"""Run a list of trials sequentially, ingesting each into canonical.jsonl as it
finishes. Sequential trials (each with bounded internal worker parallelism) keep
total Cursor load controlled while still replicating the design across seeds and
scales. Resumable: a trial whose (target,stratum,arm,seed) is already in canonical
is skipped unless --force.

Example:
  run_batch.py --mirror .../jsdom --profile profiles/jsdom.json \
      --arms durable,stateless_rag --strata S:8,M:24 --seeds 1,2 \
      --timeout 1200 --max-workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PM_ROOT = "/Users/cary/Desktop/Puppetmaster"
sys.path.insert(0, str(HERE))
import ingest as ingest_mod  # noqa: E402

ARM_DISPLAY = {"monolith": "monolith", "durable": "durable", "stateless_rag": "rag"}


def _env() -> dict:
    e = dict(os.environ)
    e["PYTHONPATH"] = PM_ROOT
    return e


def _already_done(canonical: Path, target: str, stratum: str, arm: str, seed: int) -> bool:
    if not canonical.exists():
        return False
    disp_arm = ARM_DISPLAY[arm]
    for line in canonical.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if (r.get("target") == target and r.get("stratum") == stratum
                and r.get("arm") == disp_arm and r.get("seed", 0) == seed):
            return True
    return False


def run_one(mirror: str, profile_path: str, profile: dict, arm: str, size: int, stratum: str,
            seed: int, timeout: int, maxw: int, state_dir: str, runs_root: str,
            out: str, canonical: str) -> dict:
    cmd = ["python", "-u", str(HERE / "run_arm.py"), "--mirror", mirror, "--profile", profile_path,
           "--arm", arm, "--size", str(size), "--stratum", stratum, "--seed", str(seed),
           "--timeout", str(timeout), "--max-workers", str(maxw),
           "--state-dir", state_dir, "--runs-root", runs_root, "--out", out]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(HERE), env=_env(), capture_output=True, text=True)
    m = re.search(r'"trial_id":\s*"([^"]+)"', proc.stdout)
    tid = m.group(1) if m else None
    rec = {"arm": arm, "stratum": stratum, "seed": seed, "trial_id": tid,
           "rc": proc.returncode, "elapsed_s": round(time.time() - t0, 1)}
    if not tid:
        rec["error"] = (proc.stderr or proc.stdout or "")[-800:]
        return rec
    # ingest into canonical via hardened oracle (deterministic re-score)
    try:
        ing = ingest_mod.ingest(Path(runs_root) / tid, profile, Path(out).parent)
        ingest_mod.upsert(Path(canonical), ing)
        rec.update(oracle_ok=ing["oracle_ok"], conversion_complete=ing["conversion_complete"],
                   DRR=ing["DRR"], escape_hatches=ing["escape_hatches"],
                   failed_gates=ing["failed_gates"])
    except Exception as e:  # noqa: BLE001
        rec["ingest_error"] = str(e)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--arms", required=True, help="comma list: monolith,durable,stateless_rag")
    ap.add_argument("--strata", required=True, help="comma list NAME:SIZE, e.g. S:8,M:24")
    ap.add_argument("--seeds", required=True, help="comma list of ints")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--max-workers", type=int, default=4)
    ap.add_argument("--state-dir", default="/Users/cary/lwds/pm-state")
    ap.add_argument("--runs-root", default="/Users/cary/lwds/runs")
    ap.add_argument("--out", default="/Users/cary/lwds/results/trials.jsonl")
    ap.add_argument("--canonical", default="/Users/cary/lwds/results/canonical.jsonl")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--order", default="stratum", choices=["stratum", "seed"],
                    help="iterate cheapest-first (stratum) keeps small scopes early")
    a = ap.parse_args()

    profile = json.loads(Path(a.profile).read_text())
    target = profile["name"]
    strata = [(s.split(":")[0], int(s.split(":")[1])) for s in a.strata.split(",")]
    seeds = [int(x) for x in a.seeds.split(",")]
    arms = a.arms.split(",")

    plan = []
    for (stratum, size) in strata:           # cheapest scopes first
        for seed in seeds:
            for arm in arms:
                plan.append((arm, size, stratum, seed))

    print(f"[batch] {len(plan)} trials planned over target={target}")
    done = []
    for (arm, size, stratum, seed) in plan:
        if not a.force and _already_done(Path(a.canonical), target, stratum, arm, seed):
            print(f"[skip] {target}-{stratum}-{arm}-s{seed} already in canonical")
            continue
        print(f"[run ] {target}-{stratum}-{arm}-s{seed} ...", flush=True)
        rec = run_one(a.mirror, a.profile, profile, arm, size, stratum, seed,
                      a.timeout, a.max_workers, a.state_dir, a.runs_root, a.out, a.canonical)
        done.append(rec)
        print(f"[done] {json.dumps(rec)}", flush=True)
    print(f"[batch] complete: {len(done)} trials run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
