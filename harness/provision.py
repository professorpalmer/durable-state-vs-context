#!/usr/bin/env python3
"""Apply an *identical* TypeScript scaffold to a fresh repo checkout.

This is the experiment's fairness seam. Every arm (durable-state / transcript /
transcript+compaction) receives a byte-for-byte identical starting point: the
same strict ``tsconfig.json``, the same dev-dependency pins, the same test
loader wiring, and the same machine-checkable oracle spec. The *only* thing an
arm gets to vary is the migration work itself — never the rules it is graded by.

Concretely, provisioning a checkout:
  1. installs the TS toolchain dev-deps (typescript, tsx, @types/node) pinned by
     the target profile, so ``tsc`` and the on-the-fly TS test loader exist;
  2. writes a strict ``tsconfig.json`` from the profile (agents may not weaken
     it — the oracle re-reads ``--strict`` from the CLI, not the file);
  3. rewires the package test script to register the TS loader *first*, so the
     existing JS test suite transparently loads converted ``.ts`` source;
  4. renders a repo-specific oracle spec (correct in-scope globs) into
     ``.lwds/oracle_spec.json`` inside the checkout.

Crucially it does NOT convert any source — that is the arm's job. After
provisioning, the still-JS baseline test suite must stay green (the loader is a
no-op for ``.js``); ``--validate-baseline`` asserts exactly that, which is how we
prove the scaffold itself never moved the goalposts.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
ORACLE_SPECS = HERE.parent / "oracle" / "specs"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _run(cmd: list[str], cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _install_dev_deps(checkout: Path, deps: list[str]) -> dict[str, Any]:
    if not deps:
        return {"installed": [], "skipped": True}
    cmd = ["npm", "install", "--no-audit", "--no-fund", "--save-dev", *deps]
    started = time.time()
    proc = _run(cmd, checkout, timeout=1200)
    return {
        "installed": deps,
        "returncode": proc.returncode,
        "duration_s": round(time.time() - started, 1),
        "stderr_tail": (proc.stderr or "")[-1500:] if proc.returncode != 0 else "",
    }


def _write_tsconfig(checkout: Path, tsconfig: dict[str, Any]) -> None:
    _write_json(checkout / "tsconfig.json", tsconfig)


def _patch_test_script(checkout: Path, loader: str) -> dict[str, Any]:
    """Prepend a ``--require <loader>`` so the JS test suite can load TS source.

    Idempotent: re-provisioning never double-registers the loader.
    """
    pkg_path = checkout / "package.json"
    pkg = _load_json(pkg_path)
    scripts = pkg.setdefault("scripts", {})
    test_cmd = scripts.get("test", "")
    require_flag = f"--require {loader}"
    if not test_cmd:
        patched = f"mocha {require_flag}"
        changed = True
    elif require_flag in test_cmd:
        patched, changed = test_cmd, False
    else:
        # Insert the loader immediately after the test runner binary so it
        # registers before any other --require (e.g. a test env bootstrap).
        parts = test_cmd.split()
        insert_at = 1 if parts else 0
        parts[insert_at:insert_at] = [require_flag]
        patched = " ".join(parts)
        changed = True
    scripts["test"] = patched
    _write_json(pkg_path, pkg)
    return {"before": test_cmd, "after": patched, "changed": changed}


def _render_oracle_spec(checkout: Path, profile: dict[str, Any]) -> Path:
    base = _load_json(ORACLE_SPECS / profile["oracle_spec"])
    overrides = profile.get("oracle_overrides", {})
    # Shallow-merge per top-level key; escape_hatches merges one level deeper so
    # we can swap in repo-correct include_globs without dropping the patterns.
    for key, value in overrides.items():
        if key == "escape_hatches" and isinstance(value, dict):
            base.setdefault("escape_hatches", {}).update(value)
        else:
            base[key] = value
    out = checkout / ".lwds" / "oracle_spec.json"
    _write_json(out, base)
    return out


def _run_post_install(checkout: Path, cmds: list[list[str]]) -> list[dict[str, Any]]:
    """Run codegen/setup commands a repo needs before it is runnable.

    jsdom, for example, generates WebIDL wrappers, event sets, and CSS property
    tables via ``npm run prepare`` before any test can load. These run identically
    for every arm, so they belong to the held-constant scaffold, not the task.
    """
    results: list[dict[str, Any]] = []
    for cmd in cmds:
        started = time.time()
        proc = _run(cmd, checkout, timeout=1800)
        results.append({
            "cmd": cmd, "returncode": proc.returncode,
            "duration_s": round(time.time() - started, 1),
            "stderr_tail": (proc.stderr or "")[-1000:] if proc.returncode != 0 else "",
        })
    return results


def provision(checkout: Path, profile: dict[str, Any], install: bool = True) -> dict[str, Any]:
    scaffold = profile["scaffold"]
    report: dict[str, Any] = {
        "profile": profile["name"],
        "checkout": str(checkout),
        "provisioned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if install:
        report["deps"] = _install_dev_deps(checkout, scaffold.get("dev_dependencies", []))
    post_cmds = scaffold.get("post_install_cmds", [])
    if post_cmds:
        report["post_install"] = _run_post_install(checkout, post_cmds)
    # Ambient declarations for third-party deps that ship no @types (e.g. express's
    # `router`). Typing external deps is scaffolding, not the migration, so it is
    # identical for every arm and pre-baked rather than re-derived per worker.
    ambient = scaffold.get("ambient_decls", {})
    for rel, content in ambient.items():
        dest = checkout / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    report["ambient_decls"] = sorted(ambient.keys())
    _write_tsconfig(checkout, scaffold["tsconfig"])
    # Some targets (jsdom) keep their own test script (it runs WPT); the oracle
    # invokes the test gate directly with the TS loader instead of `npm test`.
    if scaffold.get("patch_test_script", True):
        report["test_script"] = _patch_test_script(checkout, scaffold["test_loader"])
    report["oracle_spec"] = str(_render_oracle_spec(checkout, profile))
    report["package_main_after"] = scaffold.get("package_main_after")
    _write_json(checkout / ".lwds" / "provision_report.json", report)
    return report


def validate_baseline(checkout: Path, profile: dict[str, Any]) -> dict[str, Any]:
    """Run the (still-JS) test suite after scaffolding to prove it stays green."""
    cmd = profile.get("baseline_test_cmd", ["npm", "test", "--silent"])
    started = time.time()
    proc = _run(cmd, checkout, timeout=2400)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "duration_s": round(time.time() - started, 1),
        "stdout_tail": (proc.stdout or "")[-1500:],
        "stderr_tail": (proc.stderr or "")[-1500:],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the identical TS scaffold to a checkout.")
    parser.add_argument("--checkout", required=True, help="path to a fresh repo checkout")
    parser.add_argument("--profile", required=True, help="path to a target profile JSON")
    parser.add_argument("--no-install", action="store_true", help="skip npm install (deps already present)")
    parser.add_argument("--validate-baseline", action="store_true",
                        help="after scaffolding, run the JS test suite and assert it stays green")
    args = parser.parse_args(argv)

    checkout = Path(args.checkout).expanduser().resolve()
    if not (checkout / "package.json").is_file():
        print(json.dumps({"ok": False, "error": f"no package.json in {checkout}"}))
        return 2
    profile = _load_json(Path(args.profile).expanduser().resolve())

    report = provision(checkout, profile, install=not args.no_install)
    if args.validate_baseline:
        report["baseline"] = validate_baseline(checkout, profile)

    print(json.dumps(report, indent=2))
    if args.validate_baseline and not report["baseline"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
