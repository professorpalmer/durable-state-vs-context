#!/usr/bin/env python3
"""Bucket a durable-arm phase profile (LWDS_PROFILE jsonl) into the numbers that
decide where the wall-clock goes — and therefore which optimization is worth it.

Two complementary views:

  A. Worker-seconds (concurrency-independent): of all the compute the worker pool
     must chew, what fraction is rsync lightcopy (setup) vs PM+LLM (worker) vs
     harvest. setup% IS the copy-on-write payoff ceiling — COW deletes that slice
     of pool load, so at fixed concurrency it cuts ~that fraction of parallel wall.

  B. Wall-clock decomposition: base-build / convert / per-layer commit / score,
     plus effective concurrency (worker-seconds / convert-wall) vs the worker cap.
     The gap between effective and max concurrency is the layer-barrier loss that
     dataflow scheduling would recover.

Startup-vs-edit is reported as a proxy: the min worker_s across all workers is the
fixed PM+SDK spin-up floor; everything above it is reasoning/edit.

Stdlib only. Usage: python profile_report.py <profile.jsonl> [--max-workers N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median


def _load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _span(rows: list[dict], start_ev: str, end_ev: str) -> float | None:
    s = next((r["t"] for r in rows if r["event"] == start_ev), None)
    e = next((r["t"] for r in rows if r["event"] == end_ev), None)
    return round(e - s, 1) if (s is not None and e is not None) else None


def _pct(part: float, whole: float) -> str:
    return f"{100.0 * part / whole:5.1f}%" if whole else "  n/a"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--max-workers", type=int, default=None,
                    help="override; otherwise read from base_build_start")
    args = ap.parse_args(argv)

    rows = _load(Path(args.profile))
    if not rows:
        print("empty profile")
        return 1

    meta = next((r for r in rows if r["event"] == "base_build_start"), {})
    max_workers = args.max_workers or meta.get("max_workers") or 0

    # ---- pair per-worker phases by (file, attempt) ----
    setup_start: dict[tuple, float] = {}
    worker_start: dict[tuple, float] = {}
    worker_end: dict[tuple, dict] = {}
    harvest_end: dict[tuple, float] = {}
    for r in rows:
        ev = r["event"]
        if ev in ("setup_start", "worker_start", "worker_end", "harvest_end"):
            key = (r.get("file"), r.get("attempt"))
            if ev == "setup_start":
                setup_start[key] = r["t"]
            elif ev == "worker_start":
                worker_start[key] = r["t"]
            elif ev == "worker_end":
                worker_end[key] = r
            elif ev == "harvest_end":
                harvest_end[key] = r["t"]

    setup_s = worker_s = harvest_s = 0.0
    worker_durs: list[float] = []
    n_workers = ok_workers = 0
    for key, wend in worker_end.items():
        n_workers += 1
        if setup_start.get(key) is not None and worker_start.get(key) is not None:
            setup_s += worker_start[key] - setup_start[key]
        d = float(wend.get("worker_s") or 0.0)
        worker_s += d
        worker_durs.append(d)
        if worker_start.get(key) is not None and harvest_end.get(key) is not None:
            harvest_s += max(0.0, harvest_end[key] - wend["t"])
        if wend.get("rc") == 0:
            ok_workers += 1

    pool_seconds = setup_s + worker_s + harvest_s  # serial compute the pool chews

    # ---- per-layer wall + commit ----
    layer_start: dict[int, float] = {}
    commit_start: dict[int, float] = {}
    commit_end: dict[int, float] = {}
    widths: dict[int, int] = {}
    for r in rows:
        if r["event"] == "layer_start":
            layer_start[r["layer"]] = r["t"]
            widths[r["layer"]] = r.get("width", 0)
        elif r["event"] == "commit_start":
            commit_start[r["layer"]] = r["t"]
        elif r["event"] == "commit_end":
            commit_end[r["layer"]] = r["t"]

    convert_wall = commit_total = 0.0
    layer_rows = []
    for li in sorted(layer_start):
        ls = layer_start[li]
        cs = commit_start.get(li)
        ce = commit_end.get(li)
        cw = (cs - ls) if cs is not None else None      # parallel convert portion
        ct = (ce - cs) if (cs is not None and ce is not None) else None
        if cw is not None:
            convert_wall += cw
        if ct is not None:
            commit_total += ct
        layer_rows.append((li, widths.get(li, 0), cw, ct))

    base_build_s = _span(rows, "base_build_start", "base_build_end")
    arm_s = _span(rows, "arm_start", "arm_end")
    score_s = _span(rows, "score_start", "score_end")
    total_wall = rows[-1]["t"]

    eff_conc = (pool_seconds / convert_wall) if convert_wall else 0.0

    print("=" * 64)
    print(f"DURABLE PHASE PROFILE  ({args.profile})")
    print(f"trial={meta.get('trial')}  scope={meta.get('scope')}  "
          f"layers={meta.get('n_layers')}  max_layer_width={meta.get('max_layer_width')}  "
          f"max_workers={max_workers}")
    print(f"workers={n_workers} ({ok_workers} rc=0)")
    print("=" * 64)

    print("\n-- View A: worker-seconds (what the pool must chew; concurrency-free) --")
    print(f"  rsync lightcopy (setup) : {setup_s:9.1f}s  {_pct(setup_s, pool_seconds)}   <- COW payoff ceiling")
    print(f"  PM + LLM (worker)       : {worker_s:9.1f}s  {_pct(worker_s, pool_seconds)}")
    print(f"  harvest (read .ts)      : {harvest_s:9.1f}s  {_pct(harvest_s, pool_seconds)}")
    print(f"  pool-seconds total      : {pool_seconds:9.1f}s")
    if worker_durs:
        lo = min(worker_durs)
        print(f"\n  worker_s distribution   : min={lo:.1f}  median={median(worker_durs):.1f}  max={max(worker_durs):.1f}")
        edit_est = worker_s - lo * len(worker_durs)
        print(f"  startup floor (min)     ~ {lo:.1f}s fixed PM+SDK spin-up per worker")
        print(f"  est. fixed-startup tax  : {lo * len(worker_durs):9.1f}s  {_pct(lo * len(worker_durs), worker_s)} of worker time"
              f"  (warm-pool payoff ceiling)")
        print(f"  est. reasoning/edit     : {edit_est:9.1f}s  {_pct(edit_est, worker_s)} of worker time  (irreducible)")

    print("\n-- View B: wall-clock decomposition --")
    if base_build_s is not None:
        print(f"  base build (clone+install+scaffold) : {base_build_s:9.1f}s  {_pct(base_build_s, total_wall)}")
    if arm_s is not None:
        print(f"  arm (convert+commit)                : {arm_s:9.1f}s  {_pct(arm_s, total_wall)}")
        print(f"     - parallel convert               : {convert_wall:9.1f}s")
        print(f"     - per-layer git commit (serial)  : {commit_total:9.1f}s  {_pct(commit_total, arm_s)}")
    if score_s is not None:
        print(f"  score (npm install + oracle)        : {score_s:9.1f}s  {_pct(score_s, total_wall)}")
    print(f"  total wall                          : {total_wall:9.1f}s")

    print("\n-- Concurrency / barrier --")
    print(f"  effective concurrency   : {eff_conc:5.2f}  of {max_workers} workers"
          f"  ({_pct(eff_conc, max_workers).strip()} utilization)")
    print(f"  barrier loss            : {(1 - eff_conc / max_workers) * 100 if max_workers else 0:4.1f}%"
          f"  of convert-wall lost to layer barriers + scheduling")
    print(f"     -> dataflow scheduling recovers up to this; a perfect pool would")
    print(f"        finish convert in ~{pool_seconds / max_workers:.0f}s vs actual {convert_wall:.0f}s" if max_workers else "")

    print("\n-- widest / slowest layers (barrier hot spots) --")
    hot = sorted([r for r in layer_rows if r[2] is not None], key=lambda r: -(r[2] or 0))[:8]
    print(f"  {'layer':>5} {'width':>5} {'convert_s':>10} {'commit_s':>9}")
    for li, w, cw, ct in hot:
        print(f"  {li:>5} {w:>5} {cw or 0:>10.1f} {ct or 0:>9.1f}")

    print("\n-- verdict heuristics --")
    cow = 100.0 * setup_s / pool_seconds if pool_seconds else 0
    print(f"  COW worktrees worth it?      {'YES' if cow >= 8 else 'marginal'}  (rsync = {cow:.1f}% of pool-seconds)")
    print(f"  dataflow scheduling worth it? {'YES' if max_workers and eff_conc / max_workers < 0.8 else 'marginal'}"
          f"  (utilization = {_pct(eff_conc, max_workers).strip()})")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
