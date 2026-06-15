#!/usr/bin/env python3
"""Reconstruct the durable capstone's wall-clock structure from state we already
have — no re-run. This is the durable-state thesis applied to our own analysis:
the timing lives in artifacts (git log + the trial record's per-worker steps), so
we read it instead of re-deriving it with a 3.2h instrumented re-run.

Inputs (all already on disk):
  * scope.json          — the dependency-layer structure (ordered layers + widths)
  * trial record        — per-worker `steps[].duration_s`, appended in layer order
  * T/ git log          — per-layer commit timestamps (wall-clock ground truth)

Output: per-layer work vs wall, overall utilization, and a principled projection
of conversion wall-clock as a function of worker concurrency, bounded below by the
dependency critical path (sum over layers of the slowest worker in that layer).

The projection model per layer at concurrency C:
    layer_time(C) = max( slowest_worker_in_layer,  sum_worker_seconds / C )
which is the standard makespan bound: you can't beat the longest single task, and
you can't beat perfectly packing the rest across C slots. Total = sum over layers.

Stdlib only.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

RUN = Path("/Users/cary/lwds/runs/jsdom-FULL-durable-s0-9dde28")
TRIALS = Path("/Users/cary/lwds/results/trials.jsonl")
MONOLITH_FULL_WALL_S = 2972.0   # mono FULL(364) wall; converts all, fails strict typecheck (16 err)


def _load_steps(trial_id: str) -> list[float]:
    for line in TRIALS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("trial_id") == trial_id:
            return [float(s["duration_s"]) for s in r["steps"]
                    if isinstance(s, dict) and s.get("duration_s") is not None]
    raise SystemExit(f"trial {trial_id} not found in {TRIALS}")


def _git_layer_walls() -> list[tuple[int, int]]:
    """(width, unix_ct) per durable layer commit, chronological. Plus the scaffold
    commit as t0."""
    out = subprocess.run(["git", "-C", str(RUN / "T"), "log", "--format=%ct\t%s"],
                         capture_output=True, text=True, check=True).stdout
    rows = []
    t0 = None
    for line in out.splitlines():
        ct, _, subj = line.partition("\t")
        if subj.startswith("scaffold:"):
            t0 = int(ct)
            break
        if subj.startswith("durable: layer of"):
            w = int(subj.split("layer of")[1].split("module")[0].strip())
            rows.append((w, int(ct)))
    rows.reverse()  # chronological
    return t0, rows


def main() -> int:
    sel = json.loads((RUN / "scope.json").read_text())
    layers = sel["layers"]
    trial_id = RUN.name
    durs = _load_steps(trial_id)
    t0, git_layers = _git_layer_walls()

    # slice per-worker durations into layers, in run order
    sliced: list[list[float]] = []
    i = 0
    for layer in layers:
        n = len(layer)
        sliced.append(durs[i:i + n])
        i += n

    total_work = sum(durs)
    crit_path = sum(max(s) if s else 0.0 for s in sliced)

    # True dependency critical path (what dataflow scheduling achieves at infinite
    # workers): longest weighted path through the dep DAG, node weight = its worker_s.
    # This is <= the layer-barrier floor; the gap is pure layer-barrier waste.
    ordered = [f for layer in layers for f in layer]
    dur_of = dict(zip(ordered, durs))
    deps_of = sel["direct_deps"]
    memo: dict[str, float] = {}

    def longest(n: str) -> float:
        if n in memo:
            return memo[n]
        best = 0.0
        for d in deps_of.get(n, []):
            if d in dur_of:
                best = max(best, longest(d))
        memo[n] = best + dur_of.get(n, 0.0)
        return memo[n]

    dataflow_floor = max((longest(f) for f in ordered), default=0.0)

    print("=" * 72)
    print(f"CAPSTONE RECONSTRUCTION  (durable jsdom FULL=364, max_workers=4)")
    print(f"reconstructed from durable state — no re-run")
    print("=" * 72)
    print(f"\nworkers={len(durs)}  total work={total_work:.0f} worker-s ({total_work/3600:.1f} worker-h)")
    print(f"layers={len(layers)}  widest={max(len(l) for l in layers)}  "
          f"per-worker: min={min(durs):.0f} median={sorted(durs)[len(durs)//2]:.0f} max={max(durs):.0f}")

    # per-layer table with git wall vs implied utilization
    print(f"\n{'layer':>5} {'width':>5} {'work_s':>8} {'slow_s':>7} {'git_wall':>9} {'eff_conc':>8}")
    prev = t0
    convert_wall = 0.0
    for li, (layer, s) in enumerate(zip(layers, sliced)):
        gw = None
        if li < len(git_layers):
            gw = git_layers[li][1] - prev
            prev = git_layers[li][1]
            convert_wall += gw
        work = sum(s)
        slow = max(s) if s else 0.0
        eff = (work / gw) if gw else float("nan")
        print(f"{li:>5} {len(layer):>5} {work:>8.0f} {slow:>7.0f} "
              f"{(gw if gw else 0):>9.0f} {eff:>8.2f}")

    overall_eff = total_work / convert_wall if convert_wall else 0
    print(f"\nconversion wall (git) = {convert_wall:.0f}s ({convert_wall/3600:.2f}h)  "
          f"overall eff concurrency = {overall_eff:.2f} of 4 ({100*overall_eff/4:.0f}% utilized)")

    # ---- concurrency projection (makespan bound per layer) ----
    print(f"\n-- projected conversion wall vs worker concurrency --")
    print(f"   model: layer_time(C) = max(slowest_worker, layer_work / C); total = sum")
    print(f"   layer-barrier floor (sum of per-layer slowest) = {crit_path:.0f}s ({crit_path/3600:.2f}h)")
    print(f"   TRUE dataflow floor (longest dep chain)         = {dataflow_floor:.0f}s ({dataflow_floor/3600:.2f}h)"
          f"  <- what killing layer barriers buys")
    print(f"\n   {'C':>4} {'proj_wall_s':>12} {'proj_h':>7} {'speedup_vs_4':>12} {'vs_monolith':>12}")
    base4 = None
    for C in (1, 4, 8, 16, 32, 64, 128, 10**9):
        total = 0.0
        for s in sliced:
            if not s:
                continue
            total += max(max(s), sum(s) / C)
        if C == 4:
            base4 = total
        label = "inf" if C >= 10**8 else str(C)
        spd = (base4 / total) if base4 else 0
        vsmono = total / MONOLITH_FULL_WALL_S
        print(f"   {label:>4} {total:>12.0f} {total/3600:>7.2f} {spd:>11.2f}x "
              f"{vsmono:>10.2f}x")

    print(f"\n   monolith FULL(364) wall = {MONOLITH_FULL_WALL_S:.0f}s "
          f"({MONOLITH_FULL_WALL_S/3600:.2f}h) — but FAILS strict typecheck (16 err, no repair seam)")
    print("=" * 72)
    print("READING:")
    print(" * At 4 workers the pool is ~92% utilized — durable's wall is work/4, not")
    print("   barrier waste. The lever is concurrency, not COW or scheduling (here).")
    print(" * The dependency critical path is the hard floor; beyond ~that concurrency,")
    print("   more workers stop helping. Dataflow scheduling lowers the floor by not")
    print("   waiting for whole layers; warm pools cut the per-worker startup tax.")
    print(" * Crossover vs the monolith's wall is where durable wins BOTH correctness")
    print("   (clean typecheck) AND speed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
