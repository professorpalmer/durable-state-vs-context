#!/usr/bin/env python3
"""Rebuild canonical.jsonl from scratch by re-ingesting every completed trial tree
through the current hardened oracle, so the whole dataset shares one schema and one
oracle version (no legacy hand-curated records, no duplicates).

"Completed" = a raw TrialRecord with status in {scored, rescored} exists for the
trial_id (i.e. run_arm finished). Running trials have no such record and are skipped.
Run this only when no batch is actively writing canonical.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ingest as ingest_mod  # noqa: E402

PROFILES = {"express": HERE / "profiles" / "express.json",
            "jsdom": HERE / "profiles" / "jsdom.json"}


def completed_trial_ids(results_dir: Path) -> list[str]:
    ids: dict[str, None] = {}
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
            tid = r.get("trial_id")
            if tid and r.get("status") in ("scored", "rescored"):
                ids.setdefault(tid, None)
    return list(ids)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", default="/Users/cary/lwds/runs")
    ap.add_argument("--results-dir", default="/Users/cary/lwds/results")
    ap.add_argument("--canonical", default="/Users/cary/lwds/results/canonical.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    runs_root = Path(a.runs_root)
    results_dir = Path(a.results_dir)
    tids = completed_trial_ids(results_dir)
    print(f"[rebuild] {len(tids)} completed trial(s)")

    fresh = Path(a.canonical).with_suffix(".rebuilt.jsonl")
    if fresh.exists():
        fresh.unlink()
    ok = 0
    for tid in tids:
        meta = ingest_mod.parse_trial_name(tid)
        tdir = runs_root / tid
        if not meta or not tdir.is_dir():
            print(f"[skip] {tid} (no dir or unparseable)")
            continue
        profile_path = PROFILES.get(meta["target"])
        if not profile_path:
            print(f"[skip] {tid} (no profile for {meta['target']})")
            continue
        profile = json.loads(profile_path.read_text())
        try:
            rec = ingest_mod.ingest(tdir, profile, results_dir)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] {tid}: {e}")
            continue
        if not a.dry_run:
            ingest_mod.upsert(fresh, rec)
        ok += 1
        print(f"[ok  ] {rec['display']:12} {rec['arm']:9} seed{rec['seed']} "
              f"oracle={rec['oracle_ok']} hatch={rec['escape_hatches']} DRR={rec['DRR']}")
    if not a.dry_run and ok:
        fresh.replace(Path(a.canonical))
        print(f"[rebuild] wrote {ok} records -> {a.canonical}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
