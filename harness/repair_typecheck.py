#!/usr/bin/env python3
"""Integration-repair pass for a decomposed (durable) tree.

Per-file workers each type-check their own file but never see the *integrated*
whole, so decomposition can leave cross-file type inconsistencies (e.g. a worker
redeclaring a shared impl type). A single-context monolith avoids this because it
self-corrects within one window. The fair durable analog is an iterative repair
loop over the shared tree — exactly what durable state makes cheap: run `tsc` on
the integrated tree, route each error to a worker that fixes only that file
(reusing already-converted dependency types), commit, repeat until clean.

Records repair cost (iterations, files touched, worker-calls) so the paper can
state durable's refine-in-place property quantitatively.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_arm as R  # noqa: E402

TSC_ERR = re.compile(r"^(.+?\.ts)\((\d+),(\d+)\):\s+error\s+(TS\d+):\s*(.*)$", re.MULTILINE)


def _typecheck_errors(tree: Path) -> dict[str, list[str]]:
    proc = R._sh(["npx", "tsc", "--strict", "--noEmit"], tree, timeout=1200)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    by_file: dict[str, list[str]] = {}
    for m in TSC_ERR.finditer(out):
        rel, line, col, code, msg = m.groups()
        by_file.setdefault(rel, []).append(f"{rel}({line},{col}): {code}: {msg}")
    return by_file


def _repair_prompt(ts_file: str, errors: list[str], converted_deps: list[str]) -> str:
    errs = "\n".join(errors[:25])
    deps = ", ".join(d[:-3] + ".ts" for d in converted_deps) or "(its in-scope deps are already .ts)"
    return (
        f"The TypeScript file `{ts_file}` does not type-check under `tsc --strict --noEmit`. "
        f"Fix ONLY this file so these errors are resolved:\n{errs}\n"
        f"Its already-converted in-scope dependencies export real types you should import and "
        f"reuse instead of redeclaring: {deps}. "
        f"{R._common_rules()} Introduce NO escape hatches. Verify with `npx tsc --strict --noEmit`."
    )


def repair(trial_dir: Path, sub: str, state_dir: Path, timeout: int, max_workers: int,
           max_iters: int) -> dict:
    tree = trial_dir / sub
    sel = json.loads((trial_dir / "scope.json").read_text())
    scope_ts = {f[:-3] + ".ts" for f in sel["scope"]}
    direct = sel["direct_deps"]
    ts_to_js = {f[:-3] + ".ts": f for f in sel["scope"]}
    converted = {f for f in sel["scope"] if (tree / (f[:-3] + ".ts")).is_file()}

    iters = []
    t0 = time.time()
    for it in range(max_iters):
        errs = _typecheck_errors(tree)
        in_scope_bad = {f: e for f, e in errs.items() if f in scope_ts}
        if not in_scope_bad:
            iters.append({"iter": it, "in_scope_error_files": 0, "clean": True})
            break
        targets = list(in_scope_bad)

        def repair_one(tsf: str) -> tuple[str, str | None]:
            jsf = ts_to_js[tsf]
            wk = trial_dir / ("rk_" + tsf.replace("/", "_"))
            R._lightcopy(tree, wk)
            deps = [d for d in direct.get(jsf, []) if d in converted]
            R._run_worker(_repair_prompt(tsf, in_scope_bad[tsf], deps), wk, state_dir, None, timeout)
            fixed_ts = (wk / tsf).read_text(encoding="utf-8", errors="replace") if (wk / tsf).is_file() else None
            shutil.rmtree(wk, ignore_errors=True)
            return tsf, fixed_ts

        fixed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for fut in as_completed([ex.submit(repair_one, t) for t in targets]):
                tsf, new_ts = fut.result()
                if new_ts:
                    (tree / tsf).write_text(new_ts, encoding="utf-8")
                    fixed += 1
        R._sh(["git", "add", "-A"], tree, timeout=120)
        R._sh(["git", "-c", "user.email=lwds@local", "-c", "user.name=lwds", "commit", "-q",
               "-m", f"durable: typecheck repair iter {it} ({len(targets)} files)"], tree, timeout=120)
        iters.append({"iter": it, "in_scope_error_files": len(targets),
                      "files": targets[:20], "reworked": fixed})

    final = _typecheck_errors(tree)
    final_bad = sorted(f for f in final if f in scope_ts)
    return {
        "trial": trial_dir.name,
        "scope_size": len(sel["scope"]),
        "iterations": iters,
        "iters_used": len(iters),
        "repair_worker_invocations": sum(i.get("in_scope_error_files", 0) for i in iters),
        "final_in_scope_error_files": len(final_bad),
        "clean": len(final_bad) == 0,
        "repair_wall_s": round(time.time() - t0, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial-dir", required=True)
    ap.add_argument("--sub", default="T")
    ap.add_argument("--state-dir", default="/Users/cary/lwds/pm-state")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--max-workers", type=int, default=3)
    ap.add_argument("--max-iters", type=int, default=3)
    a = ap.parse_args()
    out = repair(Path(a.trial_dir).resolve(), a.sub, Path(a.state_dir),
                 a.timeout, a.max_workers, a.max_iters)
    print(json.dumps(out, indent=2))
    return 0 if out["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
