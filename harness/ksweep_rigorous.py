#!/usr/bin/env python3
"""Rigorous, replicated concurrency sweep with a uniform fixed-window protocol.

Why this exists: the first sweep reported a single run per concurrency and mixed
stop-rules across points, which produced a non-monotonic, untrustworthy curve. A
benchmark is only credible with (a) an identical measurement protocol at every point
and (b) replication with error bars. This harness delivers both.

Protocol (identical for every (C, replicate)):
  * one full-scope (364-module) durable run, isolated state-dir + fresh profile, run
    strictly single-instance and sequentially (parallel runs contend on the serving
    API and contaminate the measurement);
  * let the base build finish, then measure a fixed STEADY-STATE WINDOW of `window`
    seconds of layer-0 worker completions (events with profile-time
    t in [t_base_build_end, t_base_build_end + window]) — so low-C and high-C are
    sampled apples-to-apples and the window closes BEFORE teardown (no kill artifacts);
  * success = worker produced a .ts (harvest_end.got_ts), the same metric the figures
    use. The serving platform throttles excess sessions into fast (<20 s) no-edit
    returns, which appear as non-success worker_end events.

Aggregate: per-C mean, sample std, standard error, and 95% CI (Student-t) across the
replicates. Everything is persisted so the curve is fully reproducible.
"""
from __future__ import annotations

import json
import os
import signal
import statistics as stats
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MIRROR = str(ROOT / "targets" / "jsdom")
PROFILE_SPEC = str(HERE / "profiles" / "jsdom.json")
RUN_ARM = str(HERE / "run_arm.py")
SWEEP_DIR = ROOT / "results" / "profiles" / ("sweep" if os.environ.get("SWEEP_BACKEND", "cursor") == "cursor" else "sweep_claude")

BACKEND = os.environ.get("SWEEP_BACKEND", "cursor")  # "cursor" or "claude" worker platform
WINDOW = int(os.environ.get("SWEEP_WINDOW", "240"))  # steady-state window (s), uniform across all C
WALL_BUDGET = 150     # extra wall seconds beyond WINDOW to absorb base-build + startup
                      # (raised from 90: a slow cold base build was truncating the window)
POLL = 10

# Student-t 0.975 critical values by degrees of freedom (n-1), for 95% CI.
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
        7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def _running_run_arm() -> list[int]:
    out = subprocess.run(["pgrep", "-f", "run_arm.py"], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split()] if out.stdout.strip() else []


def _events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    evs = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evs.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return evs


def _window_metrics(path: Path, window: int) -> dict:
    """Success rate over the fixed steady-state window, with contamination guard."""
    evs = _events(path)
    base_starts = [e for e in evs if e.get("event") == "base_build_start"]
    bb_end = next((e for e in evs if e.get("event") == "base_build_end"), None)
    if not bb_end:
        return {"ok": 0, "total": 0, "fast_fail": 0, "rate": None,
                "base_starts": len(base_starts), "t_bb": None, "have_window": False}
    t0 = bb_end["t"]
    hi = t0 + window
    harvest = {(e.get("file"), e.get("attempt")): e
               for e in evs if e.get("event") == "harvest_end"}
    we = [e for e in evs
          if e.get("event") == "worker_end" and t0 <= e.get("t", -1) <= hi]
    ok = sum(1 for e in we
             if harvest.get((e.get("file"), e.get("attempt")), {}).get("got_ts"))
    fast_fail = sum(1 for e in we
                    if not harvest.get((e.get("file"), e.get("attempt")), {}).get("got_ts")
                    and (e.get("worker_s") or 0) < 20)
    total = len(we)
    # window is "complete" once the profile has progressed past t0+window
    max_t = max((e.get("t", 0) for e in evs), default=0)
    return {"ok": ok, "total": total, "fast_fail": fast_fail,
            "rate": (ok / total if total else None),
            "base_starts": len(base_starts), "t_bb": t0,
            "have_window": max_t >= hi}


def run_once(c: int, rep: int, window: int) -> dict:
    stale = _running_run_arm()
    if stale:
        raise SystemExit(f"REFUSING C={c} rep={rep}: run_arm.py already running "
                         f"(pids {stale}). Single-instance discipline.")

    tag = f"c{c}_r{rep}"
    prof_path = SWEEP_DIR / f"{tag}.jsonl"
    if prof_path.exists():
        prof_path.unlink()
    state_dir = str(ROOT / f"pm-state-sweep-{tag}")
    runs_root = str(ROOT / f"runs-sweep-{tag}")
    out = str(ROOT / "results" / f"sweep_{tag}_trial.jsonl")

    env = dict(os.environ)
    env["LWDS_PROFILE"] = str(prof_path)
    env["PYTHONPATH"] = str(ROOT / ".pm-engine")
    cmd = ["python", "-u", RUN_ARM, "--mirror", MIRROR, "--profile", PROFILE_SPEC,
           "--arm", "durable", "--size", "364", "--stratum", "FULL", "--seed", "0",
           "--backend", BACKEND, "--max-workers", str(c), "--state-dir", state_dir,
           "--runs-root", runs_root, "--out", out, "--timeout", "300"]
    print(f"\n=== C={c} rep={rep} :: launching ===", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pgid = os.getpgid(proc.pid)
    t_launch = time.time()
    deadline = t_launch + window + WALL_BUDGET
    m = {}
    try:
        while True:
            time.sleep(POLL)
            m = _window_metrics(prof_path, window)
            el = time.time() - t_launch
            if m["base_starts"] > 1:
                print(f"  !! C={c} r={rep} base_starts={m['base_starts']} — "
                      f"CONTAMINATION, aborting probe", flush=True)
                break
            r = m["rate"]
            rtxt = f"{100*r:3.0f}%" if r is not None else "  --"
            print(f"  C={c} r={rep} t={el:4.0f}s window_workers={m['total']:3d} "
                  f"ok={m['ok']:3d} ff={m['fast_fail']:3d} rate={rtxt} "
                  f"{'[full]' if m['have_window'] else ''}", flush=True)
            if proc.poll() is not None:
                print(f"  C={c} r={rep} process exited on its own", flush=True)
                break
            if m["have_window"] or el > deadline - t_launch:
                break
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            time.sleep(2)
        subprocess.run(["pkill", "-9", "-f", f"pm-state-sweep-{tag}"],
                       capture_output=True)
    time.sleep(2)
    m = _window_metrics(prof_path, window)
    res = {"C": c, "rep": rep, "ok": m["ok"], "total": m["total"],
           "fast_fail": m["fast_fail"], "rate": m["rate"],
           "base_starts": m["base_starts"], "have_window": m["have_window"],
           "profile": str(prof_path.relative_to(ROOT))}
    rtxt = f"{100*m['rate']:.0f}%" if m["rate"] is not None else "n/a"
    print(f"  C={c} r={rep} FINAL: ok={m['ok']}/{m['total']} = {rtxt} "
          f"(ff={m['fast_fail']}, base_starts={m['base_starts']}, "
          f"full_window={m['have_window']})", flush=True)
    # tidy ephemeral state immediately so 25 runs don't pile up disk
    subprocess.run(["rm", "-rf", state_dir, runs_root, out], capture_output=True)
    return res


def aggregate(results: list[dict]) -> dict:
    by_c: dict[int, list[float]] = {}
    for r in results:
        if r["rate"] is not None and r["total"] >= 5 and r["base_starts"] == 1:
            by_c.setdefault(r["C"], []).append(r["rate"])
    agg = {}
    for c in sorted(by_c):
        xs = by_c[c]
        n = len(xs)
        mean = stats.mean(xs)
        sd = stats.stdev(xs) if n > 1 else 0.0
        sem = sd / (n ** 0.5) if n > 1 else 0.0
        ci = T975.get(n - 1, 2.0) * sem if n > 1 else 0.0
        agg[c] = {"n": n, "mean": round(mean, 4), "std": round(sd, 4),
                  "sem": round(sem, 4), "ci95": round(ci, 4),
                  "reps": [round(x, 4) for x in xs],
                  "K_eff": round(mean * c, 2)}
    return agg


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    reps = int(os.environ.get("SWEEP_REPS", "5"))
    rep_start = int(os.environ.get("SWEEP_REP_START", "1"))
    cs = [int(x) for x in (sys.argv[1:] or [8, 12, 16, 24, 32])]
    print(f"Rigorous sweep: C={cs}, reps={rep_start}..{rep_start+reps-1}, window={WINDOW}s "
          f"(~{len(cs)*reps*(WINDOW+WALL_BUDGET)/3600:.1f}h)", flush=True)

    results: list[dict] = []
    for c in cs:
        for rep in range(rep_start, rep_start + reps):
            results.append(run_once(c, rep, WINDOW))
            (ROOT / "results" / "sweep_runs.json").write_text(json.dumps(results, indent=2))
            time.sleep(6)  # drain API sessions between probes

    agg = aggregate(results)
    payload = {"window_s": WINDOW, "reps": reps, "metric": "got_ts / worker_end (windowed)",
               "runs": results, "aggregate": agg}
    out = ROOT / "results" / "sweep_aggregate.json"
    out.write_text(json.dumps(payload, indent=2))

    print("\n" + "=" * 64)
    print("RIGOROUS SWEEP (mean ± 95% CI over replicates):")
    print(f"{'C':>4} {'n':>3} {'mean':>6} {'95%CI':>8} {'K_eff':>6}   reps")
    for c in sorted(agg):
        a = agg[c]
        reps_txt = " ".join(f"{100*x:.0f}" for x in a["reps"])
        print(f"{c:>4} {a['n']:>3} {100*a['mean']:>5.0f}% ±{100*a['ci95']:>5.1f}% "
              f"{a['K_eff']:>6} [{reps_txt}]", flush=True)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
