#!/usr/bin/env python3
"""Aggregate ALL replicated sweep profiles into per-C mean ± 95% CI.

Decoupled from collection so replicates can be added incrementally: it globs every
results/profiles/sweep/c{C}_r{rep}.jsonl, recomputes the uniform windowed success
metric, applies quality gates, and writes results/sweep_aggregate.json.

Quality gates (a run is admitted only if ALL hold):
  * base_build_start == 1            (no double-run contamination)
  * have_window == True              (the full steady-state window elapsed)
  * total worker_end in window >= 8  (enough samples for a per-run rate)
"""
from __future__ import annotations

import json
import re
import statistics as stats
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWEEP_DIR = ROOT / "results" / "profiles" / "sweep"
WINDOW = 240
MIN_WORKERS = 8

T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145}


def window_metrics(path: Path, window: int = WINDOW) -> dict:
    evs = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                evs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    base_starts = sum(1 for e in evs if e.get("event") == "base_build_start")
    bb = next((e for e in evs if e.get("event") == "base_build_end"), None)
    if not bb:
        return {"ok": 0, "total": 0, "fast_fail": 0, "rate": None,
                "base_starts": base_starts, "have_window": False}
    t0, hi = bb["t"], bb["t"] + window
    harvest = {(e.get("file"), e.get("attempt")): e for e in evs
               if e.get("event") == "harvest_end"}
    we = [e for e in evs if e.get("event") == "worker_end" and t0 <= e.get("t", -1) <= hi]
    ok = sum(1 for e in we
             if harvest.get((e.get("file"), e.get("attempt")), {}).get("got_ts"))
    ff = sum(1 for e in we
             if not harvest.get((e.get("file"), e.get("attempt")), {}).get("got_ts")
             and (e.get("worker_s") or 0) < 20)
    max_t = max((e.get("t", 0) for e in evs), default=0)
    total = len(we)
    return {"ok": ok, "total": total, "fast_fail": ff,
            "rate": (ok / total if total else None),
            "base_starts": base_starts, "have_window": max_t >= hi}


def main() -> int:
    runs, rejected = [], []
    for path in sorted(SWEEP_DIR.glob("c*_r*.jsonl")):
        m = re.match(r"c(\d+)_r(\d+)", path.stem)
        if not m:
            continue
        c, rep = int(m.group(1)), int(m.group(2))
        wm = window_metrics(path)
        rec = {"C": c, "rep": rep, **wm, "profile": path.name}
        admit = (wm["base_starts"] == 1 and wm["have_window"]
                 and wm["total"] >= MIN_WORKERS and wm["rate"] is not None)
        (runs if admit else rejected).append(rec)

    by_c: dict[int, list[float]] = {}
    for r in runs:
        by_c.setdefault(r["C"], []).append(r["rate"])

    agg = {}
    for c in sorted(by_c):
        xs = sorted(by_c[c])
        n = len(xs)
        mean = stats.mean(xs)
        sd = stats.stdev(xs) if n > 1 else 0.0
        sem = sd / (n ** 0.5) if n > 1 else 0.0
        ci = T975.get(n - 1, 2.0) * sem if n > 1 else 0.0
        agg[c] = {"n": n, "mean": round(mean, 4), "std": round(sd, 4),
                  "sem": round(sem, 4), "ci95": round(ci, 4),
                  "reps": [round(x, 4) for x in xs], "K_eff": round(mean * c, 2)}

    payload = {"window_s": WINDOW, "metric": "got_ts / worker_end (windowed)",
               "quality_gates": {"base_starts": 1, "have_window": True,
                                 "min_window_workers": MIN_WORKERS},
               "n_admitted": len(runs), "n_rejected": len(rejected),
               "rejected": rejected, "runs": runs, "aggregate": agg}
    out = ROOT / "results" / "sweep_aggregate.json"
    out.write_text(json.dumps(payload, indent=2))

    print(f"admitted {len(runs)} runs, rejected {len(rejected)}")
    for r in rejected:
        print(f"  REJECT C={r['C']} r={r['rep']}: base_starts={r['base_starts']} "
              f"have_window={r['have_window']} total={r['total']}")
    print(f"\n{'C':>4} {'n':>3} {'mean':>6} {'95%CI':>8} {'sd':>6} {'K_eff':>6}  reps")
    for c in sorted(agg):
        a = agg[c]
        reps = " ".join(f"{100*x:.0f}" for x in a["reps"])
        print(f"{c:>4} {a['n']:>3} {100*a['mean']:>5.0f}% ±{100*a['ci95']:>5.1f}% "
              f"{100*a['std']:>5.1f}% {a['K_eff']:>6}  [{reps}]")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
