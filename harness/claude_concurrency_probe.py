#!/usr/bin/env python3
"""Second-backend concurrency probe: does the K≈10–12 session cap reproduce on a
DIFFERENT serving platform (Claude Code via the Anthropic API)?

The Cursor sweep (ksweep_rigorous.py) measured worker success vs requested concurrency
C and found a hard admission cap (~10–12 simultaneous agent sessions) imposed by the
Cursor API/SDK, which durable state + retry then absorbs. If that cap is a property of
the *serving platform* and not of durable state, a second backend should show its own,
*different* admission behavior under the same orchestrator.

This probe isolates the admission cap cleanly:
  * for each concurrency C, launch EXACTLY C Claude Code workers simultaneously (one
    wave), each on its own temp git tree and its own Puppetmaster state-dir, so the
    only shared resource is the Anthropic account — any throttle is purely the API;
  * each worker does the same trivial, unique JS->TS conversion (cheap + fast, so the
    measurement is of *admission*, not task difficulty);
  * success = the worker produced the .ts. rate = successes / C, averaged over n reps,
    with 95% CI. A fast non-success (<25 s, no diff) is the platform throttling /
    rate-limiting the session, exactly analogous to the Cursor fast no-edit return.

Same orchestrator (frozen Puppetmaster engine, `claude` verb), different backend.
Requires ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import json
import os
import shutil
import statistics as stats
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PM_ENGINE = str(ROOT / ".pm-engine")
PROBE_DIR = ROOT / "results" / "profiles" / "claude_concurrency"
PER_WORKER_TIMEOUT = 200
FAST_FAIL_S = 25.0

T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228}

JS_TEMPLATE = """\
// unique module {uid}
function scale{uid}(values, factor) {{
  return values.map(function (v) {{ return v * factor + {uid}; }});
}}
function total{uid}(values) {{
  return values.reduce(function (a, b) {{ return a + b; }}, 0);
}}
module.exports = {{ scale{uid}: scale{uid}, total{uid}: total{uid} }};
"""

PROMPT = ("Convert mod.js to strict TypeScript: create mod.ts and delete mod.js. "
          "Add explicit parameter and return types; keep behavior identical. "
          "Do not modify any other file.")


def _make_worktree(parent: Path, uid: str) -> Path:
    wk = parent / f"wk_{uid}"
    wk.mkdir(parents=True)
    (wk / "mod.js").write_text(JS_TEMPLATE.format(uid=uid))
    subprocess.run(["git", "init", "-q"], cwd=wk, check=True)
    subprocess.run(["git", "add", "-A"], cwd=wk, check=True)
    subprocess.run(["git", "-c", "user.email=p@l", "-c", "user.name=p",
                    "commit", "-qm", "init"], cwd=wk, check=True)
    return wk


def _run_claude_worker(wk: Path, state_dir: Path) -> dict:
    cmd = ["python", "-m", "puppetmaster", "--state-dir", str(state_dir), "claude",
           PROMPT, "--cwd", str(wk), "--timeout-seconds", str(PER_WORKER_TIMEOUT)]
    env = dict(os.environ)
    env["PYTHONPATH"] = PM_ENGINE
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(wk), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=PER_WORKER_TIMEOUT + 120)
        rc = proc.returncode
        tail = (proc.stdout or "")[-300:] + (proc.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        rc, tail = None, "wall-timeout"
    dur = time.time() - t0
    got_ts = (wk / "mod.ts").is_file() and not (wk / "mod.js").is_file()
    # crude rate-limit signal from the worker tail
    rl = any(s in tail.lower() for s in ("rate limit", "429", "overloaded", "too many"))
    return {"got_ts": got_ts, "rc": rc, "dur_s": round(dur, 1),
            "fast_fail": (not got_ts and dur < FAST_FAIL_S), "rate_limit_msg": rl}


def run_wave(c: int, rep: int) -> dict:
    parent = Path(tempfile.mkdtemp(prefix=f"claudeprobe_c{c}_r{rep}_"))
    state_root = Path(tempfile.mkdtemp(prefix=f"claudestate_c{c}_r{rep}_"))
    try:
        worktrees = [_make_worktree(parent, f"{c}_{rep}_{i}") for i in range(c)]
        results = []
        # launch all C simultaneously
        with ThreadPoolExecutor(max_workers=c) as ex:
            futs = {ex.submit(_run_claude_worker, wk, state_root / f"st_{i}"): i
                    for i, wk in enumerate(worktrees)}
            for fut in as_completed(futs):
                results.append(fut.result())
        ok = sum(1 for r in results if r["got_ts"])
        ff = sum(1 for r in results if r["fast_fail"])
        rl = sum(1 for r in results if r["rate_limit_msg"])
        durs = sorted(r["dur_s"] for r in results)
        rec = {"C": c, "rep": rep, "ok": ok, "fast_fail": ff, "rate_limit_msgs": rl,
               "n": len(results), "rate": ok / len(results) if results else None,
               "median_dur_s": durs[len(durs) // 2] if durs else None,
               "workers": results}
        PROBE_DIR.mkdir(parents=True, exist_ok=True)
        (PROBE_DIR / f"c{c}_r{rep}.json").write_text(json.dumps(rec, indent=2))
        print(f"  C={c} r={rep}: ok={ok}/{len(results)} = {100*rec['rate']:.0f}%  "
              f"fast_fail={ff} rate_limit_msgs={rl} median={rec['median_dur_s']}s", flush=True)
        return rec
    finally:
        shutil.rmtree(parent, ignore_errors=True)
        shutil.rmtree(state_root, ignore_errors=True)


def aggregate(records: list[dict]) -> dict:
    by_c: dict[int, list[float]] = {}
    for r in records:
        if r["rate"] is not None:
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
                  "ci95": round(ci, 4), "reps": [round(x, 4) for x in xs],
                  "K_eff": round(mean * c, 2)}
    return agg


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set")
    reps = int(os.environ.get("PROBE_REPS", "3"))
    cs = [int(x) for x in (sys.argv[1:] or [4, 8, 16, 24, 32])]
    print(f"Claude concurrency probe: C={cs}, reps={reps}", flush=True)
    records = []
    for c in cs:
        for rep in range(1, reps + 1):
            records.append(run_wave(c, rep))
            (ROOT / "results" / "claude_probe_runs.json").write_text(json.dumps(records, indent=2))
            time.sleep(8)  # let the API settle between waves
    agg = aggregate(records)
    out = ROOT / "results" / "claude_concurrency_aggregate.json"
    out.write_text(json.dumps({"backend": "claude-code", "metric": "got_ts / C (one wave)",
                               "reps": reps, "runs": records, "aggregate": agg}, indent=2))
    print("\n" + "=" * 56)
    print("CLAUDE CONCURRENCY (mean ± 95% CI over reps):")
    print(f"{'C':>4} {'n':>3} {'mean':>6} {'95%CI':>8} {'K_eff':>6}  reps")
    for c in sorted(agg):
        a = agg[c]
        reps_txt = " ".join(f"{100*x:.0f}" for x in a["reps"])
        print(f"{c:>4} {a['n']:>3} {100*a['mean']:>5.0f}% ±{100*a['ci95']:>5.1f}% "
              f"{a['K_eff']:>6}  [{reps_txt}]", flush=True)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
