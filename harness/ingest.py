#!/usr/bin/env python3
"""Rescore a completed trial tree through the hardened oracle and upsert a fully
attributed record into results/canonical.jsonl (the honest, reproducible dataset).

Conversions are expensive and immutable; scoring is cheap and deterministic, so the
canonical dataset is always (re)derived from the on-disk trees by the *current*
oracle — never hand-edited. Wall-clock and worker-count come from the raw
TrialRecord emitted by run_arm at conversion time, matched by trial_id.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rescore as RS  # noqa: E402

SUB = {"monolith": "C1", "durable": "T", "stateless_rag": "R"}
ARM_DISPLAY = {"monolith": "monolith", "durable": "durable", "stateless_rag": "rag"}
TRIAL_RE = re.compile(r"^(.+)-(S|M|L|XL|XXL|FULL)-(monolith|durable|stateless_rag)-s(\d+)-([0-9a-f]+)$")


def parse_trial_name(name: str) -> dict | None:
    m = TRIAL_RE.match(name)
    if not m:
        return None
    return {"target": m.group(1), "stratum": m.group(2), "arm": m.group(3),
            "seed": int(m.group(4)), "hash": m.group(5)}


def _raw_record(results_dir: Path, trial_id: str) -> dict:
    for fp in results_dir.glob("*.jsonl"):
        if fp.name == "canonical.jsonl":
            continue
        for line in fp.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("trial_id") == trial_id:
                return r
    return {}


def ingest(trial_dir: Path, profile: dict, results_dir: Path) -> dict:
    meta = parse_trial_name(trial_dir.name)
    if not meta:
        raise SystemExit(f"cannot parse trial name: {trial_dir.name}")
    sub = SUB[meta["arm"]]
    scored = RS.rescore(trial_dir, sub, profile)
    raw = _raw_record(results_dir, trial_dir.name)
    display = meta["target"] if meta["stratum"] == "FULL" else f"{meta['target']}-{meta['stratum']}"
    return {
        "trial": trial_dir.name,
        "target": meta["target"],
        "stratum": meta["stratum"],
        "display": display,
        "arm": ARM_DISPLAY[meta["arm"]],
        "arm_raw": meta["arm"],
        "seed": meta["seed"],
        "scope_size": scored["scope_size"],
        "oracle_ok": scored["oracle_ok"],
        "gates_passed": scored["gates_passed"],
        "conversion_complete": scored["conversion_complete"],
        "escape_hatches": scored["escape_hatches"],
        "DRR": scored["DRR"],
        "drr_detail": scored["drr_detail"],
        "failed_gates": [g["name"] for g in scored["failed_gates"]],
        "failed_gate_detail": scored["failed_gates"],
        "wall_clock_s": raw.get("wall_clock_s"),
        "n_worker_invocations": raw.get("n_worker_invocations"),
        "peak_scope_in_one_context": raw.get("peak_scope_in_one_context"),
    }


def upsert(canonical: Path, rec: dict) -> None:
    recs = []
    if canonical.exists():
        for line in canonical.read_text().splitlines():
            if line.strip():
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    recs = [r for r in recs if not (r.get("target") == rec["target"]
            and r.get("stratum") == rec["stratum"] and r.get("arm") == rec["arm"]
            and r.get("seed", 0) == rec.get("seed", 0))]
    recs.append(rec)
    order = {"monolith": 0, "durable": 1, "rag": 2}
    recs.sort(key=lambda r: (r.get("target", ""), r.get("scope_size", 0), order.get(r.get("arm"), 9)))
    canonical.write_text("".join(json.dumps(r) + "\n" for r in recs))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial-dir", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--results-dir", default="/Users/cary/lwds/results")
    ap.add_argument("--canonical", default="/Users/cary/lwds/results/canonical.jsonl")
    args = ap.parse_args(argv)
    profile = json.loads(Path(args.profile).read_text())
    rec = ingest(Path(args.trial_dir).resolve(), profile, Path(args.results_dir))
    upsert(Path(args.canonical), rec)
    print(json.dumps({k: rec[k] for k in ("display", "arm", "oracle_ok",
          "conversion_complete", "escape_hatches", "DRR", "scope_size",
          "wall_clock_s", "n_worker_invocations", "failed_gates")}, indent=2))
    return 0 if rec["oracle_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
