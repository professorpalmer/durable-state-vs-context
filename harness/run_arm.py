#!/usr/bin/env python3
"""Run one arm of the durable-state vs. transcript study on one scope.

This is the orchestrator. It builds a provisioned base checkout, runs the chosen
state-architecture arm over a size-controlled scope, scores the result with the
unforgeable oracle, computes Discovery Reuse Rate, and appends a TrialRecord.

The arms differ in exactly one dimension — how intermediate state flows between
bounded workers — implemented on one shared substrate (PM `cursor --implement`,
which edits the checkout in place):

  * monolith (C1)      one worker, the whole scope, one context window.
  * durable  (T)       per dependency-layer workers seeded from the SHARED
                       evolving tree, so each worker inherits prior conversions
                       (accumulation ON). Within a layer, workers run in parallel.
  * stateless_rag (R)  per-file workers seeded from the PRISTINE base + CodeGraph
                       retrieval, patches merged at the end (accumulation OFF;
                       reuse available only by re-derivation from the original).

Worker parallelism uses a thread pool of subprocess calls — that is the
"gas it" path and the genuine Puppetmaster stress test.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
# Frozen Puppetmaster engine pinned at commit af67d35 (see .pm-engine/.PINNED_COMMIT).
# The research spawns workers from this snapshot, NOT the live PM dev tree, so the
# experiment is immune to in-progress Puppetmaster edits (reproducible engine).
PM_ROOT = Path("/Users/cary/lwds/.pm-engine")
sys.path.insert(0, str(HERE))
import select_scope  # noqa: E402
import provision as provision_mod  # noqa: E402
from schema import TrialRecord, ArmStep, append_record  # noqa: E402
from drr import discovery_reuse  # noqa: E402

ORACLE = HERE.parent / "oracle" / "run_oracle.py"


# ---- optional phase profiler (env-gated, zero overhead when off) ----
# Set LWDS_PROFILE=/path/to/profile.jsonl to record per-phase timing for the
# durable wall-clock breakdown (setup / worker / harvest / commit / barrier).
# Purpose: decide whether the orchestration tax — rsync lightcopy, per-layer git
# commits, layer-barrier idle — is worth attacking (COW worktrees, dataflow
# scheduling) or whether the wall-clock is dominated by irreducible LLM time.
_PROF_PATH = os.environ.get("LWDS_PROFILE")
_PROF_LOCK = threading.Lock()
_PROF_T0 = time.time()


def prof(event: str, **fields: Any) -> None:
    if not _PROF_PATH:
        return
    line = json.dumps({"t": round(time.time() - _PROF_T0, 3), "event": event, **fields})
    with _PROF_LOCK:
        with open(_PROF_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _sh(cmd: list[str], cwd: Path, timeout: int, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e["PYTHONPATH"] = str(PM_ROOT)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=str(cwd), env=e, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout, check=False)


def _lightcopy(src: Path, dst: Path) -> None:
    """Copy a checkout cheaply: all tracked/working files except node_modules,
    which is symlinked (read-only sharing) so tsc/tests run without re-install."""
    if dst.exists():
        shutil.rmtree(dst)
    subprocess.run(["rsync", "-a", "--exclude", "node_modules", f"{src}/", f"{dst}/"], check=True)
    nm = src / "node_modules"
    if nm.exists():
        (dst / "node_modules").symlink_to(nm)


def make_base(mirror: Path, profile: dict, runs_dir: Path) -> Path:
    """Clone from the local mirror, install, apply the held-constant scaffold,
    commit it. The pristine starting point shared by every arm."""
    base = runs_dir / "base"
    if base.exists():
        shutil.rmtree(base)
    subprocess.run(["git", "clone", "--quiet", str(mirror), str(base)], check=True)
    _sh(["npm", "install", "--no-audit", "--no-fund"], base, timeout=1200)
    provision_mod.provision(base, profile, install=True)
    if profile["scaffold"].get("package_main_after"):
        pkg = json.loads((base / "package.json").read_text())
        pkg["main"] = profile["scaffold"]["package_main_after"]
        (base / "package.json").write_text(json.dumps(pkg, indent=2) + "\n")
    _sh(["git", "add", "-A"], base, timeout=120)
    _sh(["git", "-c", "user.email=lwds@local", "-c", "user.name=lwds", "commit", "-q",
         "-m", "scaffold: held-constant TS toolchain"], base, timeout=120)
    return base


# ---- worker prompts (identical objective; differ only in available state) ----

def _common_rules() -> str:
    return (
        "Constraints (graded by a machine oracle): (1) the project must type-check under "
        "`tsc --strict --noEmit`; (2) the existing test suite must stay green (do NOT edit tests); "
        "(3) ZERO escape hatches in converted source — forbidden: `: any`, `as any`, `<any>`, "
        "`any[]`, `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`; model real types instead. "
        "Do NOT modify tsconfig.json or package.json. Do NOT run npm install."
    )


def _prompt_one_file(target_file: str, converted_deps: list[str], pristine: bool) -> str:
    ts = target_file[:-3] + ".ts"
    if pristine:
        state = (
            "You may read any file in the repository and use the code graph for context, but "
            "this file's in-scope dependencies are still JavaScript — infer their shapes from source."
        )
    else:
        deps = ", ".join(d[:-3] + ".ts" for d in converted_deps) or "(none)"
        state = (
            "The following in-scope dependencies have ALREADY been converted to TypeScript in this "
            f"tree and EXPORT real types you should import and reuse: {deps}. Build on them — do not "
            "re-derive their types."
        )
    return (
        f"Convert exactly one file from JavaScript to strict TypeScript: `{target_file}` -> `{ts}`. "
        f"Create `{ts}` (delete `{target_file}`); do not convert or modify any other source file. "
        f"{state} {_common_rules()} "
        f"Verify with `npx tsc --strict --noEmit` before finishing."
    )


def _prompt_monolith(scope: list[str]) -> str:
    listing = "\n".join(f"  - {f} -> {f[:-3]}.ts" for f in scope)
    return (
        "Migrate this repository's in-scope JavaScript modules to strict TypeScript, all in one pass. "
        f"Convert exactly these {len(scope)} files (create .ts, delete .js):\n{listing}\n"
        f"{_common_rules()} Verify `npx tsc --strict --noEmit` is clean AND the test suite passes "
        "before finishing."
    )


# Worker serving backend: "cursor" (default, Cursor agent) or "claude" (Claude Code
# via Anthropic API). Set once in main() from --backend so every arm dispatches
# through the same platform. Used to test whether the concurrency cap is platform-
# specific (Cursor session cap) rather than a property of durable state.
BACKEND = "cursor"


def _run_worker(prompt: str, wk: Path, state_dir: Path, model: Optional[str], timeout: int) -> dict:
    if BACKEND == "claude":
        cmd = ["python", "-m", "puppetmaster", "--state-dir", str(state_dir), "claude", prompt,
               "--cwd", str(wk), "--timeout-seconds", str(timeout)]
    else:
        cmd = ["python", "-m", "puppetmaster", "--state-dir", str(state_dir), "cursor", prompt,
               "--cwd", str(wk), "--implement", "--timeout-seconds", str(timeout)]
    if model:
        cmd += ["--model", model]
    started = time.time()
    try:
        proc = _sh(cmd, wk, timeout=timeout + 180)
        rc, err = proc.returncode, ""
    except subprocess.TimeoutExpired:
        rc, err = None, "worker wall-timeout"
    return {"rc": rc, "duration_s": round(time.time() - started, 1), "error": err}


def _harvest(wk: Path, src_js: str) -> Optional[str]:
    ts = wk / (src_js[:-3] + ".ts")
    return ts.read_text(encoding="utf-8", errors="replace") if ts.is_file() else None


def _apply_conversion(tree: Path, src_js: str, ts_content: str) -> None:
    (tree / (src_js[:-3] + ".ts")).write_text(ts_content, encoding="utf-8")
    js = tree / src_js
    if js.exists():
        js.unlink()


# ---- arms ----

def arm_monolith(base: Path, scope: list[str], runs_dir: Path, state_dir: Path,
                 model: Optional[str], timeout: int) -> tuple[Path, list[ArmStep], int]:
    wk = runs_dir / "C1"
    _lightcopy(base, wk)
    res = _run_worker(_prompt_monolith(scope), wk, state_dir, model, timeout)
    step = ArmStep(label="monolith", scope=scope, status="done" if res["rc"] == 0 else "error",
                   duration_s=res["duration_s"], model=model, note=res["error"])
    return wk, [step], len(scope)


def _convert_files_parallel(seed_for: Any, files: list[str], runs_dir: Path, state_dir: Path,
                            model: Optional[str], timeout: int, max_workers: int,
                            converted_deps_of: Any, pristine: bool, retries: int = 2) -> dict[str, dict]:
    """Convert a set of files in parallel; each on its own lightcopy. Returns
    {file: {ts, step}}. `seed_for(file)` yields the tree to copy for that file.

    A worker that yields no `.ts` (transient timeout/error) is retried up to
    `retries` times. For the decomposed arms this is the cheap, targeted recovery
    that durable state makes possible — re-run only the failed unit, not the scope."""
    out: dict[str, dict] = {}

    def task(f: str) -> tuple[str, dict]:
        attempts = 0
        last = {"rc": None, "duration_s": 0.0, "error": ""}
        total_s = 0.0
        ts = None
        while attempts <= retries and ts is None:
            wk = runs_dir / ("wk_" + f.replace("/", "_"))
            prof("setup_start", file=f, attempt=attempts)
            _lightcopy(seed_for(f), wk)
            prof("worker_start", file=f, attempt=attempts)
            last = _run_worker(_prompt_one_file(f, converted_deps_of(f), pristine), wk, state_dir, model, timeout)
            prof("worker_end", file=f, attempt=attempts, rc=last["rc"], worker_s=last["duration_s"])
            ts = _harvest(wk, f)
            prof("harvest_end", file=f, attempt=attempts, got_ts=bool(ts))
            shutil.rmtree(wk, ignore_errors=True)
            total_s += last["duration_s"]
            attempts += 1
        return f, {"ts": ts, "attempts": attempts, "step": ArmStep(
            label=f, scope=[f], status="done" if ts else "error",
            duration_s=round(total_s, 1), model=model,
            note=(last["error"] + (f" (attempts={attempts})" if attempts > 1 else "")).strip())}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for fut in as_completed([ex.submit(task, f) for f in files]):
            f, r = fut.result()
            out[f] = r
    return out


def arm_durable(base: Path, sel: dict, runs_dir: Path, state_dir: Path,
                model: Optional[str], timeout: int, max_workers: int) -> tuple[Path, list[ArmStep], int]:
    shared = runs_dir / "T"
    _lightcopy(base, shared)
    direct = sel["direct_deps"]
    steps: list[ArmStep] = []
    converted: set[str] = set()
    for li, layer in enumerate(sel["layers"]):
        prof("layer_start", layer=li, width=len(layer))
        results = _convert_files_parallel(
            seed_for=lambda f: shared, files=layer, runs_dir=runs_dir, state_dir=state_dir,
            model=model, timeout=timeout, max_workers=max_workers,
            converted_deps_of=lambda f: [d for d in direct.get(f, []) if d in converted],
            pristine=False)
        for f in layer:
            if results[f]["ts"]:
                _apply_conversion(shared, f, results[f]["ts"])
                converted.add(f)
            steps.append(results[f]["step"])
        prof("commit_start", layer=li, width=len(layer))
        _sh(["git", "add", "-A"], shared, timeout=120)
        _sh(["git", "-c", "user.email=lwds@local", "-c", "user.name=lwds", "commit", "-q",
             "-m", f"durable: layer of {len(layer)} module(s)"], shared, timeout=120)
        prof("commit_end", layer=li)
    return shared, steps, 1  # durable peak working set ~= one module at a time


def arm_stateless_rag(base: Path, sel: dict, runs_dir: Path, state_dir: Path,
                      model: Optional[str], timeout: int, max_workers: int) -> tuple[Path, list[ArmStep], int]:
    scope = sel["scope"]
    results = _convert_files_parallel(
        seed_for=lambda f: base, files=scope, runs_dir=runs_dir, state_dir=state_dir,
        model=model, timeout=timeout, max_workers=max_workers,
        converted_deps_of=lambda f: [], pristine=True)
    merged = runs_dir / "R"
    _lightcopy(base, merged)
    steps = []
    for f in scope:
        if results[f]["ts"]:
            _apply_conversion(merged, f, results[f]["ts"])
        steps.append(results[f]["step"])
    return merged, steps, 1


# ---- scoring ----

def _normalize_tree(tree: Path, scope: list[str]) -> None:
    """Make the converted .ts the file that actually runs: for every in-scope
    module that has a .ts, delete any residual original. Node resolves .js before
    .ts, so a leftover original would silently shadow the migration and the tests
    would exercise un-converted source (a hollow pass). Applied identically to
    every arm so all are scored on their .ts truly running."""
    for src in scope:
        stem = re.sub(r"\.(js|cjs|mjs)$", "", src)
        if (tree / (stem + ".ts")).is_file() and (tree / src).is_file():
            (tree / src).unlink()


def score(tree: Path, sel: dict) -> dict:
    _normalize_tree(tree, sel["scope"])
    spec_path = tree / ".lwds" / "oracle_spec.json"
    spec = json.loads(spec_path.read_text())
    spec["require_converted"] = sel["scope"]            # hard conversion-complete gate
    spec_path.write_text(json.dumps(spec, indent=2))
    _sh(["npm", "install", "--no-audit", "--no-fund"], tree, timeout=900)
    proc = _sh(["python", str(ORACLE), "--repo", str(tree), "--spec", str(spec_path)],
               tree, timeout=3600)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "harness_error": (proc.stdout or proc.stderr or "")[-1500:]}


ARMS = {"monolith": arm_monolith, "durable": arm_durable, "stateless_rag": arm_stateless_rag}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run one arm on one size-controlled scope.")
    ap.add_argument("--mirror", required=True, help="local clone of the target at the pinned commit")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--arm", required=True, choices=list(ARMS))
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--stratum", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default="cursor", choices=["cursor", "claude"],
                    help="worker serving platform (cursor agent or Claude Code)")
    ap.add_argument("--timeout", type=int, default=900, help="per-worker timeout seconds")
    ap.add_argument("--max-workers", type=int, default=6)
    ap.add_argument("--state-dir", default="/Users/cary/lwds/pm-state")
    ap.add_argument("--runs-root", default="/Users/cary/lwds/runs")
    ap.add_argument("--out", default="/Users/cary/lwds/results/trials.jsonl")
    args = ap.parse_args(argv)

    global BACKEND
    BACKEND = args.backend

    mirror = Path(args.mirror).resolve()
    profile = json.loads(Path(args.profile).read_text())
    trial_id = f"{profile['name']}-{args.stratum}-{args.arm}-s{args.seed}-{uuid.uuid4().hex[:6]}"
    runs_dir = Path(args.runs_root) / trial_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    state_dir = Path(args.state_dir)

    sel = select_scope.select(mirror, profile, args.size, args.seed)
    (runs_dir / "scope.json").write_text(json.dumps(sel, indent=2))
    rec = TrialRecord(trial_id=trial_id, target=profile["name"], stratum=args.stratum,
                      arm=args.arm, seed=args.seed, scope_size=sel["scope_size"],
                      scope=sel["scope"], model=args.model, status="running")
    t0 = time.time()
    prof("base_build_start", trial=trial_id, scope=sel["scope_size"],
         n_layers=sel["n_layers"], max_layer_width=sel["max_layer_width"], max_workers=args.max_workers)
    base = make_base(mirror, profile, runs_dir)
    prof("base_build_end")

    fn = ARMS[args.arm]
    prof("arm_start", arm=args.arm)
    if args.arm == "monolith":
        tree, steps, peak = fn(base, sel["scope"], runs_dir, state_dir, args.model, args.timeout)
    else:
        tree, steps, peak = fn(base, sel, runs_dir, state_dir, args.model, args.timeout, args.max_workers)
    prof("arm_end", arm=args.arm)

    prof("score_start")
    verdict = score(tree, sel)
    prof("score_end")
    drr = discovery_reuse(tree, sel)

    rec.steps = steps
    rec.n_worker_invocations = len(steps)
    rec.peak_scope_in_one_context = peak if args.arm != "monolith" else sel["scope_size"]
    rec.wall_clock_s = round(time.time() - t0, 1)
    rec.oracle_ok = bool(verdict.get("ok"))
    rec.gates_passed = bool(verdict.get("gates_passed"))
    rec.escape_hatches = (verdict.get("escape_hatches") or {}).get("total")
    rec.oracle_verdict = verdict
    rec.discovery_reuse_rate = drr["rate"]
    rec.n_modules_consuming_prior_artifact = drr["numerator"]
    rec.n_modules_with_inscope_dep = drr["denominator"]
    rec.reuse_by_dependency_depth = drr["by_depth"]
    rec.status = "scored"
    rec.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    append_record(Path(args.out), rec)
    print(json.dumps({"trial_id": trial_id, "arm": args.arm, "scope_size": sel["scope_size"],
                      "oracle_ok": rec.oracle_ok, "gates_passed": rec.gates_passed,
                      "escape_hatches": rec.escape_hatches, "DRR": rec.discovery_reuse_rate,
                      "n_workers": rec.n_worker_invocations, "wall_s": rec.wall_clock_s,
                      "tree": str(tree)}, indent=2))
    return 0 if rec.oracle_ok else 1


if __name__ == "__main__":
    sys.exit(main())
