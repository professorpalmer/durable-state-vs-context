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
    # Replicated sweep with a uniform fixed-window protocol (harness/ksweep_rigorous.py
    # + aggregate_sweep.py). Each point is mean ± 95% CI (Student-t) over n quality-gated
    # replicates (base_starts==1, full window, >=8 window workers).
    agg = json.loads((ROOT / "results" / "sweep_aggregate.json").read_text())["aggregate"]
    cs = sorted(int(c) for c in agg)
    means = [agg[str(c)]["mean"] for c in cs]
    cis = [agg[str(c)]["ci95"] for c in cs]
    ns = [agg[str(c)]["n"] for c in cs]
    K = 11.0  # reference admission cap, read off the knee (C=12 K_eff≈11.9, C=16≈10.5)

    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    xs = [x * 0.25 for x in range(8, 144)]
    ax.plot(xs, [min(1.0, K / x) for x in xs], "--", color="#888",
            label=f"reference min(1, K/C), K={K:.0f}")
    ax.errorbar(cs, means, yerr=cis, fmt="o", color="#c0392b", ms=8, lw=0,
                elinewidth=1.8, capsize=4, ecolor="#c0392b", zorder=3,
                label="measured: mean ± 95% CI")
    for c, m, n in zip(cs, means, ns):
        ax.annotate(f"{100*m:.0f}%\n(n={n})", (c, m), textcoords="offset points",
                    xytext=(9, -2), fontsize=8, va="center")
    ax.axvspan(0, 12, color="#27ae60", alpha=0.05)
    ax.axvspan(16, 36, color="#c0392b", alpha=0.05)
    ax.text(6, 0.10, "below cap:\n~97–99%", fontsize=8, color="#1e8449", ha="center")
    ax.text(28, 0.62, "above cap:\ncollapsed plateau", fontsize=8, color="#922b21", ha="center")
    ax.set_xlabel("requested worker concurrency  (C)")
    ax.set_ylabel("worker success rate  (produced .ts)")
    ax.set_title("Concurrency ceiling: success collapses above ~12 sessions\n"
                 "(effective admission cap K≈10–12; n=5–10 reps/point, 240 s window)")
    ax.set_ylim(0, 1.08)
    ax.set_xlim(0, 36)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    p = FIGS / "concurrency_ceiling.png"
    fig.savefig(p, dpi=140)
    plt.close(fig)
    print("wrote", p)


def fig_backends() -> None:
    """The money figure: same orchestrator + durable state, two serving backends.
    Cursor collapses above its session cap; Claude Code sustains 100% to C=32 — so the
    cap is a property of the serving platform, not of durable state."""
    cur = json.loads((ROOT / "results" / "sweep_aggregate.json").read_text())["aggregate"]
    cla = json.loads((ROOT / "results" / "claude_concurrency_aggregate.json").read_text())["aggregate"]
    cc = sorted(int(c) for c in cur)
    cm = [cur[str(c)]["mean"] for c in cc]
    ce = [cur[str(c)]["ci95"] for c in cc]
    lc = sorted(int(c) for c in cla)
    lm = [cla[str(c)]["mean"] for c in lc]
    le = [cla[str(c)]["ci95"] for c in lc]

    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    ax.errorbar(lc, lm, yerr=le, fmt="s-", color="#27ae60", ms=7, lw=2, capsize=4,
                label="Claude Code (Anthropic API)")
    ax.errorbar(cc, cm, yerr=ce, fmt="o-", color="#c0392b", ms=7, lw=2, capsize=4,
                label="Cursor agent (Cursor API/SDK)")
    ax.axhspan(0, 0.0, color="none")
    ax.annotate("100% through C=32\n(no cap observed)", (24, 1.0),
                textcoords="offset points", xytext=(-4, -34), fontsize=8.5, color="#1e8449")
    ax.annotate("collapses above\nK≈10–12 sessions", (24, 0.28),
                textcoords="offset points", xytext=(6, 6), fontsize=8.5, color="#922b21")
    ax.set_xlabel("requested worker concurrency  (C)")
    ax.set_ylabel("worker success rate")
    ax.set_title("Same orchestrator + durable state, two backends:\n"
                 "the concurrency cap is platform-specific, not fundamental")
    ax.set_ylim(0, 1.08)
    ax.set_xlim(0, 36)
    ax.grid(alpha=0.3)
    ax.legend(loc="center left", fontsize=9)
    fig.tight_layout()
    p = FIGS / "concurrency_backends.png"
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
    fig_backends()
    fig_headroom()
