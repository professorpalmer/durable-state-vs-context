#!/usr/bin/env python3
"""Targeted recovery of unconverted in-scope modules on an existing durable tree.

When a decomposed run leaves a few modules unconverted (a transient worker failure),
durable state lets us re-run *only those units* seeded from the already-converted
tree — not the whole scope. This is the cheap-targeted-recovery property the
monolith lacks (its only retry is redoing the entire scope in a fresh context).

Records the recovery cost (modules recovered, worker invocations) so the paper can
state: "durable repaired k/N modules with k worker-calls; a monolith would re-run N."
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_arm as R  # noqa: E402


def missing_modules(tree: Path, scope: list[str]) -> list[str]:
    return [f for f in scope if (tree / f).is_file() and not (tree / (f[:-3] + ".ts")).is_file()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial-dir", required=True)
    ap.add_argument("--sub", default="T")
    ap.add_argument("--state-dir", default="/Users/cary/lwds/pm-state")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--max-workers", type=int, default=3)
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()

    trial_dir = Path(a.trial_dir).resolve()
    tree = trial_dir / a.sub
    sel = json.loads((trial_dir / "scope.json").read_text())
    scope, direct = sel["scope"], sel["direct_deps"]
    converted = {f for f in scope if (tree / (f[:-3] + ".ts")).is_file()}
    todo = missing_modules(tree, scope)
    if not todo:
        print(json.dumps({"trial": trial_dir.name, "missing": 0, "note": "already complete"}))
        return 0

    print(f"[patch] {trial_dir.name}: recovering {len(todo)}/{len(scope)} modules: {todo}", flush=True)
    t0 = time.time()
    results = R._convert_files_parallel(
        seed_for=lambda f: tree, files=todo, runs_dir=trial_dir, state_dir=Path(a.state_dir),
        model=None, timeout=a.timeout, max_workers=a.max_workers,
        converted_deps_of=lambda f: [d for d in direct.get(f, []) if d in converted],
        pristine=False, retries=a.retries)
    recovered = []
    for f in todo:
        if results[f]["ts"]:
            R._apply_conversion(tree, f, results[f]["ts"])
            converted.add(f)
            recovered.append(f)
    R._sh(["git", "add", "-A"], tree, timeout=120)
    R._sh(["git", "-c", "user.email=lwds@local", "-c", "user.name=lwds", "commit", "-q",
           "-m", f"durable: targeted recovery (+{len(recovered)})"], tree, timeout=120)
    out = {
        "trial": trial_dir.name,
        "scope_size": len(scope),
        "missing_before": len(todo),
        "recovered": len(recovered),
        "still_missing": sorted(set(todo) - set(recovered)),
        "recovery_worker_invocations": len(todo),  # only the failed units, not the scope
        "monolith_equivalent_redo": len(scope),     # a single-context retry would redo all
        "recovery_wall_s": round(time.time() - t0, 1),
    }
    print(json.dumps(out, indent=2))
    return 0 if not out["still_missing"] else 1


if __name__ == "__main__":
    sys.exit(main())
