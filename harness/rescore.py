#!/usr/bin/env python3
"""Re-score an already-converted trial tree with the current hardened oracle.

Conversions are the expensive part (real agent workers); scoring is cheap and
deterministic. When the oracle is hardened (e.g. the tsx-subprocess loader fix or
the conversion-complete gate), we re-score existing trees instead of paying to
regenerate them. This re-renders the oracle spec from the profile (picking up the
latest gates + env), normalizes the tree, runs the oracle, and recomputes DRR.

Usage:
  rescore.py --trial-dir RUNS/<trial> --sub <C1|T|R> --profile <profile.json> \
             [--arm ARM] [--patch RESULTS.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provision as provision_mod  # noqa: E402
import run_arm  # noqa: E402
from drr import discovery_reuse  # noqa: E402


def rescore(trial_dir: Path, sub: str, profile: dict) -> dict:
    tree = trial_dir / sub
    sel = json.loads((trial_dir / "scope.json").read_text())
    provision_mod._render_oracle_spec(tree, profile)        # refresh gates + env
    verdict = run_arm.score(tree, sel)                      # normalize + require_converted + oracle
    drr = discovery_reuse(tree, sel)
    return {
        "trial": trial_dir.name,
        "scope_size": sel["scope_size"],
        "oracle_ok": bool(verdict.get("ok")),
        "gates_passed": bool(verdict.get("gates_passed")),
        "conversion_complete": verdict.get("conversion_complete"),
        "escape_hatches": (verdict.get("escape_hatches") or {}).get("total"),
        "DRR": drr["rate"],
        "drr_detail": {k: drr[k] for k in ("numerator", "denominator", "by_depth")},
        "failed_gates": [
            {"name": g["name"], "tail": (g.get("stdout_tail") or g.get("stderr_tail") or "")[-160:],
             "residual_js": g.get("residual_js"), "missing_ts": g.get("missing_ts")}
            for g in verdict.get("gates", []) if not g.get("passed")
        ],
        "verdict": verdict,
        "sel": sel,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial-dir", required=True)
    ap.add_argument("--sub", required=True, help="arm subtree dir: C1 / T / R")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--arm")
    ap.add_argument("--patch", help="results jsonl to update in place by trial_id")
    args = ap.parse_args(argv)

    profile = json.loads(Path(args.profile).read_text())
    out = rescore(Path(args.trial_dir).resolve(), args.sub, profile)

    if args.patch:
        fp = Path(args.patch)
        recs = [json.loads(l) for l in fp.read_text().splitlines() if l.strip()] if fp.exists() else []
        tid = out["trial"]
        patched = False
        for r in recs:
            if r.get("trial_id") == tid:
                v, d = out["verdict"], out["drr_detail"]
                r.update(oracle_ok=out["oracle_ok"], gates_passed=out["gates_passed"],
                         escape_hatches=out["escape_hatches"], oracle_verdict=v,
                         discovery_reuse_rate=out["DRR"],
                         n_modules_consuming_prior_artifact=d["numerator"],
                         n_modules_with_inscope_dep=d["denominator"],
                         reuse_by_dependency_depth=d["by_depth"], status="rescored")
                patched = True
        if patched:
            fp.write_text("".join(json.dumps(r) + "\n" for r in recs))

    print(json.dumps({k: out[k] for k in
                      ("trial", "scope_size", "oracle_ok", "gates_passed",
                       "conversion_complete", "escape_hatches", "DRR", "drr_detail",
                       "failed_gates")}, indent=2))
    return 0 if out["oracle_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
