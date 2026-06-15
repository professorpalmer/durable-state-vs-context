#!/usr/bin/env python3
"""Hardened, language-agnostic oracle for the durable-state vs. transcript study.

The oracle is the *unforgeable* success criterion for one migration trial. It is
deliberately config-driven (see ``specs/``) so the identical scoring logic runs
across every repository, every arm (durable-state / transcript / ...), and both
task languages (TS, Python) — no per-arm scoring drift.

A trial PASSES iff:
  1. every required ``gate`` command succeeds (or meets its parsed threshold), and
  2. the total count of forbidden "escape hatches" is within ``budget``.

Escape-hatch accounting is what stops an agent from gaming a type oracle by
spraying ``any`` / ``# type: ignore``: those count against the budget, and the
residual count is reported as a quality metric regardless of pass/fail.

Output: a single JSON verdict on stdout (and to ``--out`` if given). Exit code is
0 on PASS, 1 on FAIL, 2 on harness/usage error — so it composes in shell and CI.

Stdlib only; no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional


def _load_spec(spec_path: Path) -> dict[str, Any]:
    try:
        return json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"could not read oracle spec {spec_path}: {exc}")
        raise  # unreachable; satisfies type checkers


def _die(message: str) -> None:
    print(json.dumps({"ok": False, "harness_error": message}), file=sys.stdout)
    sys.exit(2)


def _run_gate(gate: dict[str, Any], repo: Path, default_timeout: int,
              base_env: dict[str, str]) -> dict[str, Any]:
    """Run one gate command and decide pass/fail.

    A gate passes when its exit code is 0, unless ``pass_on`` requests a parsed
    metric (e.g. ``tsc`` error count == 0 scraped from output), which lets us
    score tools that exit non-zero for benign reasons.
    """
    name = gate["name"]
    cmd = gate["cmd"]
    cwd = repo / gate["cwd"] if gate.get("cwd") else repo
    timeout = int(gate.get("timeout_seconds", default_timeout))
    env = dict(base_env)
    env.update({str(k): str(v) for k, v in gate.get("env", {}).items()})
    started = time.time()

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=isinstance(cmd, str),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        returncode: Optional[int] = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode, timed_out = None, True
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr or "" if isinstance(exc.stderr, str) else ""
    except OSError as exc:
        return {
            "name": name, "passed": False, "error": f"could not run: {exc}",
            "duration_s": round(time.time() - started, 2),
        }

    pass_on = gate.get("pass_on", "returncode_zero")
    metric: Optional[int] = None
    if pass_on == "metric_zero":
        metric = _count_matches(gate["metric_pattern"], (stdout or "") + "\n" + (stderr or ""))
        passed = (metric == 0) and not timed_out
    else:  # returncode_zero
        passed = (returncode == 0) and not timed_out

    return {
        "name": name,
        "passed": bool(passed),
        "returncode": returncode,
        "timed_out": timed_out,
        "metric": metric,
        "duration_s": round(time.time() - started, 2),
        "stdout_tail": (stdout or "")[-2000:],
        "stderr_tail": (stderr or "")[-2000:],
    }


def _count_matches(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.MULTILINE))


def _iter_files(repo: Path, include_globs: list[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in include_globs:
        for path in repo.glob(pattern):
            if path.is_file():
                seen.setdefault(path.resolve(), None)
    return list(seen.keys())


def _count_escape_hatches(repo: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Count forbidden type-system escape hatches across the in-scope files.

    Heuristic by design: a regex over source over-counts (`any` inside comments
    or identifiers like `company`). The patterns target *type positions* to keep
    false positives low, and the authoritative path is a lint ``count_cmd`` when
    provided (e.g. eslint ``no-explicit-any`` --format json). Either way the
    residual is reported, never silently zeroed.
    """
    cfg = spec.get("escape_hatches")
    if not cfg:
        return {"total": 0, "by_pattern": {}, "budget": 0, "within_budget": True, "method": "none"}

    budget = int(cfg.get("budget", 0))

    count_cmd = cfg.get("count_cmd")
    if count_cmd:
        try:
            completed = subprocess.run(
                count_cmd, cwd=str(repo), shell=isinstance(count_cmd, str),
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=int(cfg.get("timeout_seconds", 600)), check=False,
            )
            total = int((completed.stdout or "0").strip().splitlines()[-1])
            return {
                "total": total, "by_pattern": {}, "budget": budget,
                "within_budget": total <= budget, "method": "count_cmd",
            }
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            return {
                "total": None, "by_pattern": {}, "budget": budget,
                "within_budget": False, "method": "count_cmd",
                "error": f"count_cmd failed: {exc}",
            }

    patterns: dict[str, str] = cfg.get("patterns", {})
    # Scope-restrict to the deliverable: when the trial declares which modules must
    # be converted (require_converted), only those in-scope .ts files count toward
    # the hatch budget. Hatches in out-of-scope files (e.g. an ambient shim the
    # agent writes to declare genuinely-untyped *generated* code outside the scope)
    # are a boundary artifact common to every arm, not a quality signal of the
    # migration. Falls back to include_globs when no scope is declared.
    scope = spec.get("require_converted")
    if scope:
        files = [repo / (f[:-3] + ".ts") for f in scope if (repo / (f[:-3] + ".ts")).is_file()]
        scope_method = "regex_in_scope"
    else:
        files = _iter_files(repo, cfg.get("include_globs", []))
        scope_method = "regex"
    by_pattern: dict[str, int] = {name: 0 for name in patterns}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, pattern in patterns.items():
            by_pattern[name] += _count_matches(pattern, text)
    total = sum(by_pattern.values())
    return {
        "total": total, "by_pattern": by_pattern, "budget": budget,
        "within_budget": total <= budget, "method": scope_method,
        "files_scanned": len(files),
    }


def _check_conversion(repo: Path, scope: list[str]) -> dict[str, Any]:
    """Hard anti-cheat gate: every in-scope source file must actually be
    migrated. The converted ``.ts`` must exist and the original ``.js`` must be
    gone — a leftover ``.js`` shadows the ``.ts`` at runtime (Node resolves
    ``.js`` first), so the tests would silently exercise the un-migrated source
    and the conversion would be hollow. Residual originals therefore FAIL."""
    missing_ts: list[str] = []
    residual_js: list[str] = []
    for src in scope:
        stem = re.sub(r"\.(js|cjs|mjs)$", "", src)
        if not (repo / (stem + ".ts")).is_file():
            missing_ts.append(stem + ".ts")
        if (repo / src).is_file():
            residual_js.append(src)
    passed = not missing_ts and not residual_js
    return {
        "name": "conversion_complete", "passed": passed, "required": True,
        "n_scope": len(scope), "missing_ts": missing_ts[:20],
        "residual_js": residual_js[:20],
    }


def evaluate(repo: Path, spec: dict[str, Any], default_timeout: int) -> dict[str, Any]:
    base_env = dict(os.environ)
    base_env.update({str(k): str(v) for k, v in spec.get("env", {}).items()})

    gates_result = [_run_gate(g, repo, default_timeout, base_env) for g in spec.get("gates", [])]
    required = [
        g for g, cfg in zip(gates_result, spec.get("gates", []))
        if cfg.get("required", True)
    ]
    gates_ok = all(g["passed"] for g in required)

    conversion: Optional[dict[str, Any]] = None
    scope = spec.get("require_converted")
    if scope:
        conversion = _check_conversion(repo, scope)
        gates_result.append(conversion)
        gates_ok = gates_ok and conversion["passed"]

    hatches = _count_escape_hatches(repo, spec)
    hatches_ok = bool(hatches.get("within_budget"))

    return {
        "ok": gates_ok and hatches_ok,
        "spec": spec.get("name", "unnamed"),
        "language": spec.get("language"),
        "repo": str(repo),
        "gates_passed": gates_ok,
        "conversion_complete": conversion["passed"] if conversion else None,
        "escape_hatches_within_budget": hatches_ok,
        "gates": gates_result,
        "escape_hatches": hatches,
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Hardened oracle for one migration trial.")
    parser.add_argument("--repo", required=True, help="path to the repo checkout under test")
    parser.add_argument("--spec", required=True, help="path to an oracle spec JSON (see specs/)")
    parser.add_argument("--out", help="optional path to write the JSON verdict")
    parser.add_argument("--default-timeout", type=int, default=1800,
                        help="per-gate timeout (seconds) when the gate omits one")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        _die(f"--repo is not a directory: {repo}")
    spec = _load_spec(Path(args.spec).expanduser())

    verdict = evaluate(repo, spec, args.default_timeout)
    rendered = json.dumps(verdict, indent=2)
    print(rendered)
    if args.out:
        Path(args.out).expanduser().write_text(rendered + "\n", encoding="utf-8")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
