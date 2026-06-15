#!/usr/bin/env python3
"""Resume-capable durable conversion of a scope onto a persistent shared tree.

This is the durable arm factored so it can be (a) interrupted by killing its
process group and (b) resumed against the same on-disk tree, skipping modules
whose `.ts` already exists and is committed. After each dependency layer it
commits to git and appends a checkpoint, so an interruption leaves every
already-committed module recoverable — the structural property the monolith
(one terminal commit, one disposable transcript) cannot offer.

Run as its own session (`start_new_session=True`) so the resumability driver
can `killpg` exactly this worker subtree without touching sibling experiments.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import run_arm as R  # reuse worker plumbing; importing does not execute main()


def _converted_in_scope(tree: Path, scope: list[str]) -> set[str]:
    return {f for f in scope if (tree / (f[:-3] + ".ts")).is_file()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shared", required=True, help="persistent durable tree (created if absent)")
    ap.add_argument("--base", required=True, help="pristine provisioned base to seed from")
    ap.add_argument("--scope-json", required=True)
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--checkpoints", required=True)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--max-workers", type=int, default=3)
    a = ap.parse_args()

    sel = json.loads(Path(a.scope_json).read_text())
    shared, base = Path(a.shared), Path(a.base)
    runs_dir, state_dir = Path(a.runs_dir), Path(a.state_dir)
    ck = Path(a.checkpoints)
    direct = sel["direct_deps"]

    if not shared.exists():
        R._lightcopy(base, shared)
        R._sh(["git", "add", "-A"], shared, timeout=120)
        R._sh(["git", "-c", "user.email=lwds@local", "-c", "user.name=lwds", "commit",
               "-q", "-m", "durable: seed"], shared, timeout=120)

    converted = _converted_in_scope(shared, sel["scope"])
    t0 = time.time()

    def emit(layer_idx: int, just: set[str]) -> None:
        rec = {"t_rel": round(time.time() - t0, 1), "layer_idx": layer_idx,
               "n_committed": len(converted), "just_committed": sorted(just)}
        with ck.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()

    emit(-1, set())  # checkpoint at resume start (records how many already survived)

    for li, layer in enumerate(sel["layers"]):
        todo = [f for f in layer if f not in converted]
        if not todo:
            continue
        results = R._convert_files_parallel(
            seed_for=lambda f: shared, files=todo, runs_dir=runs_dir, state_dir=state_dir,
            model=None, timeout=a.timeout, max_workers=a.max_workers,
            converted_deps_of=lambda f: [d for d in direct.get(f, []) if d in converted],
            pristine=False)
        just: set[str] = set()
        for f in todo:
            if results[f]["ts"]:
                R._apply_conversion(shared, f, results[f]["ts"])
                converted.add(f)
                just.add(f)
        R._sh(["git", "add", "-A"], shared, timeout=120)
        R._sh(["git", "-c", "user.email=lwds@local", "-c", "user.name=lwds", "commit",
               "-q", "-m", f"durable: layer {li} (+{len(just)})"], shared, timeout=120)
        emit(li, just)

    print(json.dumps({"done": True, "n_committed": len(converted), "scope_size": len(sel["scope"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
