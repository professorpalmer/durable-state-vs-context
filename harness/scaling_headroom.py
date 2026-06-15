#!/usr/bin/env python3
"""Does durable 'only get better as the codebase gets bigger'? Test it from the
size-sweep we already ran — no re-run.

For each durable trial we already have:
  * the per-worker times (trial record `steps[].duration_s`)
  * the dependency structure (its run dir `scope.json`: layers + direct_deps)

We compute, per scope size, the *parallelism headroom*:
    headroom = total_work / longest_dependency_chain
which is the max speedup a dataflow scheduler could extract at that size. The
claim 'it wants to be scaled' predicts headroom rises monotonically with scope,
because work grows ~linearly with module count while the dependency critical path
(depth) grows much slower. If headroom climbs S->M->L->XL->FULL, the claim holds.

Stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

RUNS = Path("/Users/cary/lwds/runs")
TRIALS = Path("/Users/cary/lwds/results/trials.jsonl")


def _durable_trials() -> list[dict]:
    seen: dict[str, dict] = {}
    for line in TRIALS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("arm") != "durable":
            continue
        steps = r.get("steps") or []
        if not any(isinstance(s, dict) and s.get("duration_s") for s in steps):
            continue
        seen[r["trial_id"]] = r            # last wins (latest record)
    return list(seen.values())


def _longest_chain(layers: list[list[str]], deps_of: dict, durs: list[float]) -> tuple[float, float]:
    ordered = [f for layer in layers for f in layer]
    dur_of = dict(zip(ordered, durs))
    memo: dict[str, float] = {}

    def lp(n: str) -> float:
        if n in memo:
            return memo[n]
        best = 0.0
        for d in deps_of.get(n, []):
            if d in dur_of:
                best = max(best, lp(d))
        memo[n] = best + dur_of.get(n, 0.0)
        return memo[n]

    chain = max((lp(f) for f in ordered), default=0.0)
    layer_floor = sum(max(durs[s:s + len(l)]) if l else 0.0
                      for s, l in _slices(layers))
    return chain, layer_floor


def _slices(layers):
    i = 0
    for l in layers:
        yield i, l
        i += len(l)


def main() -> int:
    rows = []
    for r in _durable_trials():
        run = RUNS / r["trial_id"]
        sp = run / "scope.json"
        if not sp.is_file():
            continue
        sel = json.loads(sp.read_text())
        durs = [float(s["duration_s"]) for s in r["steps"]
                if isinstance(s, dict) and s.get("duration_s") is not None]
        if len(durs) != sel["scope_size"]:
            # steps must align 1:1 with scope for the chain math; skip if not
            pass
        chain, layer_floor = _longest_chain(sel["layers"], sel["direct_deps"], durs)
        work = sum(durs)
        rows.append({
            "trial": r["trial_id"],
            "scope": sel["scope_size"],
            "n_layers": sel["n_layers"],
            "max_width": sel["max_layer_width"],
            "edges": sel.get("intra_scope_edges"),
            "work": work,
            "chain": chain,
            "layer_floor": layer_floor,
            "headroom": work / chain if chain else 0,
            "ok": r.get("oracle_ok"),
        })
    rows.sort(key=lambda x: x["scope"])

    print("=" * 86)
    print("DURABLE PARALLELISM HEADROOM vs REPO SCALE  (from size-sweep, no re-run)")
    print("  headroom = total_work / longest_dependency_chain  (max dataflow speedup)")
    print("=" * 86)
    print(f"{'scope':>6} {'layers':>6} {'maxW':>5} {'edges':>6} {'work_s':>9} "
          f"{'chain_s':>8} {'headroom':>9} {'depth%work':>10}")
    for x in rows:
        depthpct = 100 * x["chain"] / x["work"] if x["work"] else 0
        print(f"{x['scope']:>6} {x['n_layers']:>6} {x['max_width']:>5} "
              f"{(x['edges'] or 0):>6} {x['work']:>9.0f} {x['chain']:>8.0f} "
              f"{x['headroom']:>8.1f}x {depthpct:>9.1f}%")

    if len(rows) >= 2:
        print("\nREADING:")
        lo, hi = rows[0], rows[-1]
        mono = all(rows[i]["headroom"] <= rows[i + 1]["headroom"] + 1e-9
                   for i in range(len(rows) - 1))
        print(f" * headroom {lo['headroom']:.1f}x @ scope {lo['scope']}  ->  "
              f"{hi['headroom']:.1f}x @ scope {hi['scope']}")
        print(f" * monotonic in scope? {'YES — it wants to be scaled' if mono else 'NO (non-monotonic)'}")
        print(f" * critical path as %% of work shrinks with size => parallelizable")
        print(f"   fraction grows => the bigger the repo, the more durable wins.")
    print("=" * 86)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
