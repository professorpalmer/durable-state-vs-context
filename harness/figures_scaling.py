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
    # 5-point clean sweep (C=24 sampled twice to expose run-to-run variance).
    # Each profile is a single, contamination-checked full-scope durable run.
    probes = [
        (8, "ksweep_c8.jsonl"),
        (12, "ksweep_c12.jsonl"),
        (16, "probe_c16_v2.jsonl"),
        (24, "ksweep_c24_run1.jsonl"),
        (24, "ksweep_c24.jsonl"),
        (32, "sweep_c32_clean.jsonl"),
    ]
    pts = []
    for c, prof in probes:
        got, n = _success_rate(PROF / prof)
        pts.append((c, got / n, f"{got}/{n}"))

    cs = [p[0] for p in pts]
    rates = [p[1] for p in pts]
    # Reference (NOT a fit): effective session cap from the points that bracket the
    # knee + the far-over-cap point. The measured points scatter around it because
    # the throttle is stochastic — we show the reference to read off the cap, not to
    # claim the data obey it.
    K = 11.0

    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    xs = [x * 0.25 for x in range(8, 144)]
    ax.plot(xs, [min(1.0, K / x) for x in xs], "--", color="#888",
            label=f"reference min(1, K/C), K={K:.0f}")
    ax.scatter(cs, rates, color="#c0392b", s=80, zorder=3,
               label="measured success rate (clean single run)")
    for c, r, lab in pts:
        ax.annotate(lab, (c, r), textcoords="offset points", xytext=(7, 6), fontsize=8)
    # highlight the two C=24 samples as a variance bar
    c24 = [r for c, r, _ in pts if c == 24]
    ax.plot([24, 24], [min(c24), max(c24)], color="#c0392b", lw=1, alpha=0.5)
    ax.axvspan(0, 12, color="#27ae60", alpha=0.05)
    ax.axvspan(16, 36, color="#c0392b", alpha=0.05)
    ax.text(6, 0.12, "below cap:\n>90%", fontsize=8, color="#1e8449", ha="center")
    ax.text(28, 0.78, "above cap: noisy\n~25–35% plateau", fontsize=8, color="#922b21", ha="center")
    ax.set_xlabel("requested worker concurrency  (C)")
    ax.set_ylabel("worker success rate  (produced .ts)")
    ax.set_title("Concurrency ceiling: success collapses above ~12 sessions\n"
                 "(effective platform cap K≈10–12; over-cap rate is stochastic)")
    ax.set_ylim(0, 1.05)
    ax.set_xlim(0, 36)
    ax.grid(alpha=0.3)
    ax.legend(loc="center right", fontsize=9)
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
