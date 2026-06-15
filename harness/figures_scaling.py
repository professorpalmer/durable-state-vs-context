#!/usr/bin/env python3
"""Two figures for the scaling story:

  1. concurrency_ceiling.png — worker success rate vs requested concurrency, with
     the min(1, K/C) session-cap fit. Points are computed from the real clean
     probe profiles (C=16, C=32) plus the C=4 capstone anchor.
  2. headroom_vs_scale.png — dependency critical-path as a %% of total work
     (shrinks with repo size) and the dataflow speedup headroom (grows), vs scope.
     Values from harness/scaling_headroom.py over the size-sweep.

Stdlib + matplotlib.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path("/Users/cary/lwds")
PROF = ROOT / "results" / "profiles"
FIGS = ROOT / "figures"


def _success_rate(profile: Path) -> tuple[int, int]:
    ev = [json.loads(l) for l in profile.read_text().splitlines() if l.strip()]
    assert sum(1 for e in ev if e["event"] == "base_build_start") == 1, \
        f"{profile.name}: not a single clean run"
    we = [e for e in ev if e["event"] == "worker_end"]
    he = {(e.get("file"), e.get("attempt")): e for e in ev if e["event"] == "harvest_end"}
    got = sum(1 for e in we if he.get((e.get("file"), e.get("attempt")), {}).get("got_ts"))
    return got, len(we)


def fig_ceiling() -> None:
    pts = [(4, 1.0, "364/364 (capstone)")]  # C=4 anchor: capstone first pass converted all
    for c, prof in [(16, "probe_c16_v2.jsonl"), (32, "sweep_c32_clean.jsonl")]:
        got, n = _success_rate(PROF / prof)
        pts.append((c, got / n, f"{got}/{n}"))

    cs = [p[0] for p in pts]
    rates = [p[1] for p in pts]
    # fit K from the two over-cap points
    K = sum(c * r for c, r, _ in pts if c >= 16) / sum(1 for c, _, _ in pts if c >= 16)

    fig, ax = plt.subplots(figsize=(6.2, 4.2))
    xs = list(range(2, 40))
    ax.plot(xs, [min(1.0, K / x) for x in xs], "--", color="#888",
            label=f"min(1, K/C),  K≈{K:.1f} sessions")
    ax.plot(cs, rates, "o-", color="#c0392b", lw=2, ms=9, label="measured (clean single run)")
    for c, r, lab in pts:
        dy = -14 if c == 4 else 8
        ax.annotate(lab, (c, r), textcoords="offset points", xytext=(8, dy), fontsize=8)
    ax.axhspan(0, 0.5, color="#c0392b", alpha=0.05)
    ax.set_xlabel("requested worker concurrency  (C)")
    ax.set_ylabel("worker success rate  (produced .ts)")
    ax.set_title("Concurrency ceiling: usable parallelism caps at K≈10–11 sessions")
    ax.set_ylim(0, 1.05)
    ax.set_xlim(0, 36)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    p = FIGS / "concurrency_ceiling.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print("wrote", p)


def fig_headroom() -> None:
    # from scaling_headroom.py over the durable size-sweep (cleanest seed per size)
    # (scope, critical-path % of total work, dataflow headroom x)
    rows = [(8, 45.0, 2.2), (24, 20.9, 4.8), (60, 20.3, 4.9), (364, 4.6, 21.6)]
    scope = [r[0] for r in rows]
    depthpct = [r[1] for r in rows]
    headroom = [r[2] for r in rows]

    fig, ax1 = plt.subplots(figsize=(6.2, 4.2))
    ax1.set_xscale("log")
    ax1.plot(scope, depthpct, "o-", color="#2980b9", lw=2, ms=8,
             label="critical path (% of total work)")
    ax1.set_xlabel("repository scope  (in-scope modules, log scale)")
    ax1.set_ylabel("critical path as % of total work", color="#2980b9")
    ax1.tick_params(axis="y", labelcolor="#2980b9")
    ax1.set_ylim(0, 50)
    for x, y in zip(scope, depthpct):
        ax1.annotate(f"{y:.0f}%", (x, y), textcoords="offset points", xytext=(4, 6),
                     fontsize=8, color="#2980b9")

    ax2 = ax1.twinx()
    ax2.plot(scope, headroom, "s--", color="#27ae60", lw=2, ms=7,
             label="dataflow speedup headroom (×)")
    ax2.set_ylabel("dataflow speedup headroom (×)", color="#27ae60")
    ax2.tick_params(axis="y", labelcolor="#27ae60")
    ax2.set_ylim(0, 24)
    for x, y in zip(scope, headroom):
        ax2.annotate(f"{y:.1f}×", (x, y), textcoords="offset points", xytext=(4, -12),
                     fontsize=8, color="#27ae60")

    ax1.set_title("Parallel headroom grows with repo size\n(work parallelizes; critical path does not)")
    ax1.set_xticks(scope)
    ax1.set_xticklabels([str(s) for s in scope])
    ax1.grid(alpha=0.3)
    fig.tight_layout()
    p = FIGS / "headroom_vs_scale.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print("wrote", p)


if __name__ == "__main__":
    FIGS.mkdir(exist_ok=True)
    fig_ceiling()
    fig_headroom()
