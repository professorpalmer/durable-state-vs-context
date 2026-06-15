#!/usr/bin/env python3
"""Clean concurrency K-sweep: measure durable worker success rate vs requested
concurrency C, to firm up the min(1, K/C) session-cap fit.

Methodology (identical to the committed C=16 / C=32 probes):
  * one full-scope (364-module) durable run per C, isolated state-dir + fresh
    profile, run strictly single-instance and sequentially (never parallel —
    parallel runs contend on the Cursor API and contaminate the concurrency
    measurement, exactly the double-run bug we guard against here);
  * let layer 0 (max width 78) accumulate worker_end events at steady-state
    concurrency C, then kill the whole process group;
  * success = fraction of completed workers with rc==0 (produced a diff). The
    serving platform throttles excess sessions into fast (<20 s) no-edit
    returns, which show up as rc!=0.

We only need layer 0, so each probe is killed early (cheap).
"""
from __future__ import annotations
import json, os, signal, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MIRROR = str(ROOT / "targets" / "jsdom")
PROFILE = str(HERE / "profiles" / "jsdom.json")
RUN_ARM = str(HERE / "run_arm.py")
PROF_DIR = ROOT / "results" / "profiles"

TARGET_OK = 30           # stop once this many *genuine successes* (rc==0) land.
                         # NOT total worker_end: at high C the throttle churns out
                         # dozens of fast-fails in seconds, so a worker-count stop
                         # would halt before the slow real successes finish.
MAX_SECONDS = 16 * 60    # hard cap per probe
POLL = 12                # seconds between profile reads


def _running_run_arm() -> list[int]:
    out = subprocess.run(["pgrep", "-f", "run_arm.py"], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split()] if out.stdout.strip() else []


def _profile_stats(path: Path) -> dict:
    base_starts = worker_end = ok = layer_starts = 0
    fast_fail = 0
    if not path.is_file():
        return {"base_starts": 0, "worker_end": 0, "ok": 0, "layer_starts": 0, "fast_fail": 0}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = r.get("event")
        if ev == "base_build_start":
            base_starts += 1
        elif ev == "layer_start":
            layer_starts += 1
        elif ev == "worker_end":
            worker_end += 1
            if r.get("rc") == 0:
                ok += 1
            elif (r.get("worker_s") or 0) < 20:
                fast_fail += 1
    return {"base_starts": base_starts, "worker_end": worker_end, "ok": ok,
            "layer_starts": layer_starts, "fast_fail": fast_fail}


def run_probe(c: int) -> dict:
    stale = _running_run_arm()
    if stale:
        raise SystemExit(f"REFUSING to start C={c}: run_arm.py already running (pids {stale}). "
                         "Single-instance discipline — kill it first.")

    prof_path = PROF_DIR / f"ksweep_c{c}.jsonl"
    if prof_path.exists():
        prof_path.unlink()
    state_dir = str(ROOT / f"pm-state-ksweep-c{c}")
    runs_root = str(ROOT / f"runs-ksweep-c{c}")
    out = str(ROOT / "results" / f"ksweep_c{c}_trial.jsonl")

    env = dict(os.environ)
    env["LWDS_PROFILE"] = str(prof_path)
    env["PYTHONPATH"] = str(ROOT / ".pm-engine")  # pin frozen engine (run_batch parity)
    cmd = ["python", "-u", RUN_ARM, "--mirror", MIRROR, "--profile", PROFILE,
           "--arm", "durable", "--size", "364", "--stratum", "FULL", "--seed", "0",
           "--max-workers", str(c), "--state-dir", state_dir,
           "--runs-root", runs_root, "--out", out, "--timeout", "300"]
    print(f"\n=== C={c} :: launching (state={state_dir}) ===", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pgid = os.getpgid(proc.pid)
    t0 = time.time()
    last = {}
    try:
        while True:
            time.sleep(POLL)
            st = _profile_stats(prof_path)
            last = st
            el = time.time() - t0
            if st["base_starts"] > 1:
                print(f"  !! base_build_start={st['base_starts']} — CONTAMINATION, aborting C={c}", flush=True)
                break
            rate = (100 * st["ok"] / st["worker_end"]) if st["worker_end"] else 0
            print(f"  C={c} t={el:4.0f}s workers_done={st['worker_end']:3d} "
                  f"ok={st['ok']:3d} fast_fail={st['fast_fail']:3d} rate={rate:4.0f}% "
                  f"layers={st['layer_starts']}", flush=True)
            if proc.poll() is not None:
                print(f"  C={c} process exited on its own", flush=True)
                break
            if st["ok"] >= TARGET_OK:
                print(f"  C={c} reached {TARGET_OK} successes — stopping early", flush=True)
                break
            if st["layer_starts"] > 1:
                print(f"  C={c} layer 0 complete — stopping", flush=True)
                break
            if el > MAX_SECONDS:
                print(f"  C={c} hit time cap — stopping", flush=True)
                break
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            time.sleep(3)
        # belt-and-suspenders: reap any stray run_arm/worker procs for THIS probe
        subprocess.run(["pkill", "-9", "-f", f"pm-state-ksweep-c{c}"],
                       capture_output=True)
    # settle + final read
    time.sleep(2)
    st = _profile_stats(prof_path)
    rate = (st["ok"] / st["worker_end"]) if st["worker_end"] else 0
    result = {"C": c, "workers_done": st["worker_end"], "ok": st["ok"],
              "fast_fail": st["fast_fail"], "rate": round(rate, 4),
              "base_starts": st["base_starts"], "profile": str(prof_path)}
    print(f"  C={c} FINAL: ok={st['ok']}/{st['worker_end']} = {100*rate:.0f}%  "
          f"(fast_fail={st['fast_fail']}, base_starts={st['base_starts']})", flush=True)
    return result


def main() -> int:
    cs = [int(x) for x in sys.argv[1:]] or [8, 12, 24]
    results = []
    for c in cs:
        results.append(run_probe(c))
        time.sleep(5)  # let API/sessions drain between probes
    print("\n" + "=" * 60)
    print("K-SWEEP RESULTS (new), plus committed C=16/C=32 for context:")
    print(f"{'C':>4} {'ok':>5} {'done':>5} {'rate':>6}")
    for r in results:
        print(f"{r['C']:>4} {r['ok']:>5} {r['workers_done']:>5} {100*r['rate']:>5.0f}%")
    out = ROOT / "results" / "ksweep_summary.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
