#!/usr/bin/env python3
"""Interruption / resumability experiment (H4).

Claim under test: durable state makes repository-scale work *recoverable* across
interruption, while a single-transcript agent (monolith) does not. The mechanism
is commit granularity — durable persists each completed dependency layer as a git
commit (a durable artifact); the monolith persists nothing until one terminal
commit and keeps its reasoning only in a disposable transcript.

Procedure (one scope, same provisioned base for both arms):
  1. Durable: spawn the resume-capable runner as its own process group. When it
     has committed >= ceil(scope/2) modules, kill the whole group (hard SIGKILL,
     simulating a crash). Record committed-and-surviving modules. Then RESUME from
     the same on-disk tree and run the oracle: a PASS proves the survivors were
     real and only the remainder had to be done.
  2. Monolith: lightcopy the same base, spawn one monolith worker, kill it at the
     same wall-clock budget. It has committed nothing -> 0 recoverable modules; a
     restart must redo the entire scope. We score the partial tree to show it is
     not a usable checkpoint.

Output: results/resume_exp.json with work_preserved_fraction (durable vs monolith)
and proof that the durable tree resumes to an oracle PASS.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Frozen PM engine (pinned af67d35) — isolated from the live PM dev tree. See run_arm.py.
PM_ROOT = Path("/Users/cary/lwds/.pm-engine")
sys.path.insert(0, str(HERE))
import select_scope  # noqa: E402
import run_arm as R  # noqa: E402


def _env() -> dict:
    e = dict(os.environ)
    e["PYTHONPATH"] = str(PM_ROOT)
    return e


def _killpg(p: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        p.wait(timeout=30)
    except Exception:
        pass


def _committed_ts(tree: Path, scope: list[str]) -> int:
    """Count in-scope modules whose .ts exists AND is committed (in git HEAD)."""
    tracked = R._sh(["git", "ls-files"], tree, timeout=120).stdout.splitlines()
    tracked_set = set(tracked)
    return sum(1 for f in scope if (f[:-3] + ".ts") in tracked_set)


def run(target_mirror: Path, profile: dict, size: int, stratum: str,
        runs_root: Path, state_dir: Path, timeout: int, max_workers: int) -> dict:
    sel = select_scope.select(target_mirror, profile, size, 0)
    scope = sel["scope"]
    n = len(scope)
    n_layers = len(sel["layers"])
    if n_layers < 2:
        raise SystemExit(f"scope {stratum} has {n_layers} layer(s); resumability needs >=2")

    trial = f"{profile['name']}-{stratum}-resume-{int(time.time())}"
    runs_dir = runs_root / trial
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "scope.json").write_text(json.dumps(sel, indent=2))
    base = R.make_base(target_mirror, profile, runs_dir)

    kill_threshold = max(1, math.ceil(n / 2))
    shared = runs_dir / "T"
    ckpt = runs_dir / "checkpoints.jsonl"
    ckpt.write_text("")

    # ---- 1a. durable run, interrupted ----
    cmd = ["python", "-u", str(HERE / "resume_runner.py"),
           "--shared", str(shared), "--base", str(base),
           "--scope-json", str(runs_dir / "scope.json"),
           "--runs-dir", str(runs_dir), "--state-dir", str(state_dir),
           "--checkpoints", str(ckpt), "--timeout", str(timeout),
           "--max-workers", str(max_workers)]
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(HERE), env=_env(), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    killed_at = None
    committed_at_kill = 0
    while True:
        if proc.poll() is not None:  # finished before threshold (small scope) -> no interruption
            committed_at_kill = _committed_ts(shared, scope)
            break
        if ckpt.exists() and ckpt.stat().st_size:
            last = [json.loads(l) for l in ckpt.read_text().splitlines() if l.strip()]
            ncom = last[-1]["n_committed"] if last else 0
            if ncom >= kill_threshold:
                _killpg(proc)
                killed_at = round(time.time() - t0, 1)
                committed_at_kill = _committed_ts(shared, scope)
                break
        time.sleep(5)
    durable_kill_budget_s = killed_at if killed_at is not None else round(time.time() - t0, 1)

    # ---- 1b. resume durable from the surviving on-disk tree ----
    resume_proc = subprocess.run(cmd, cwd=str(HERE), env=_env(),
                                 capture_output=True, text=True, timeout=timeout * (n + 4))
    committed_after_resume = _committed_ts(shared, scope)
    verdict_durable = R.score(shared, sel)

    # ---- 2. monolith, interrupted at the same wall budget ----
    c1 = runs_dir / "C1"
    R._lightcopy(base, c1)
    mono_cmd = ["python", "-m", "puppetmaster", "--state-dir", str(state_dir), "cursor",
                R._prompt_monolith(scope), "--cwd", str(c1), "--implement",
                "--timeout-seconds", str(timeout)]
    mp = subprocess.Popen(mono_cmd, cwd=str(c1), env=_env(), start_new_session=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    budget = max(durable_kill_budget_s, 60)
    waited = 0.0
    while waited < budget and mp.poll() is None:
        time.sleep(5)
        waited += 5
    mono_finished = mp.poll() is not None
    if not mono_finished:
        _killpg(mp)
    mono_committed = _committed_ts(c1, scope)  # monolith never commits intermediate -> 0
    mono_partial_ts = sum(1 for f in scope if (c1 / (f[:-3] + ".ts")).is_file())
    R._normalize_tree(c1, scope)
    verdict_mono_partial = R.score(c1, sel)

    result = {
        "target": profile["name"], "stratum": stratum, "scope_size": n, "n_layers": n_layers,
        "kill_threshold_committed": kill_threshold,
        "durable": {
            "kill_budget_s": durable_kill_budget_s,
            "committed_at_kill": committed_at_kill,
            "work_preserved_fraction": round(committed_at_kill / n, 4),
            "committed_after_resume": committed_after_resume,
            "resume_completed_scope": committed_after_resume == n,
            "oracle_ok_after_resume": bool(verdict_durable.get("ok")),
            "checkpoints": [json.loads(l) for l in ckpt.read_text().splitlines() if l.strip()],
        },
        "monolith": {
            "kill_budget_s": round(waited, 1),
            "finished_within_budget": mono_finished,
            "committed_at_kill": mono_committed,
            "work_preserved_fraction": round(mono_committed / n, 4),
            "uncommitted_partial_ts": mono_partial_ts,
            "partial_tree_oracle_ok": bool(verdict_mono_partial.get("ok")),
            "redo_modules_on_restart": n if not mono_finished else 0,
        },
        "headline": {
            "durable_work_preserved": round(committed_at_kill / n, 4),
            "monolith_work_preserved": round(mono_committed / n, 4),
        },
        "tree_durable": str(shared),
    }
    return result


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--stratum", required=True)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--max-workers", type=int, default=3)
    ap.add_argument("--state-dir", default="/Users/cary/lwds/pm-state")
    ap.add_argument("--runs-root", default="/Users/cary/lwds/runs")
    ap.add_argument("--out", default="/Users/cary/lwds/results/resume_exp.jsonl")
    a = ap.parse_args()
    profile = json.loads(Path(a.profile).read_text())
    res = run(Path(a.mirror).resolve(), profile, a.size, a.stratum,
              Path(a.runs_root), Path(a.state_dir), a.timeout, a.max_workers)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        fh.write(json.dumps(res) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
