#!/usr/bin/env python3
"""Aggregate the canonical dataset into paper-ready tables + figures.

Reads:
  results/canonical.jsonl   one rescored record per (target, stratum, arm, seed)
  results/resume_exp.jsonl  interruption/resumability records (optional)
  results/followup_exp.jsonl follow-up/extension records (optional)

Writes:
  results/tables.md         markdown tables (outcome, scaling, DRR, resumability)
  figures/*.png             figures (only if matplotlib is importable)
"""

from __future__ import annotations

import json
import re
import statistics as stats
from collections import defaultdict
from pathlib import Path

RESULTS = Path("/Users/cary/lwds/results")
FIGS = Path("/Users/cary/lwds/figures")
ARMS = ["monolith", "durable", "rag"]


def _load(name: str) -> list[dict]:
    fp = RESULTS / name
    if not fp.exists():
        return []
    return [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]


def _dedup_canonical(recs: list[dict]) -> list[dict]:
    """Tolerate legacy hand-curated records (no 'stratum') alongside reproducible
    ingested records. Key on (display/target, scope_size, arm, seed); prefer the
    ingested record (has 'stratum'). Read-only, so it never races a live batch
    writing canonical.jsonl."""
    best: dict[tuple, dict] = {}
    for r in recs:
        key = (r.get("display") or r.get("target"), r.get("scope_size"), r.get("arm"), r.get("seed", 0))
        cur = best.get(key)
        if cur is None or ("stratum" in r and "stratum" not in cur):
            best[key] = r
    return list(best.values())


# Gates confounded by jsdom's webidl codegen at XL+ scope (see RESULTS threats).
_CONFOUNDED = {"tests_api", "conversion_complete"}


def _cell(rec: dict) -> str:
    if rec is None:
        return "—"
    ok = "PASS" if rec["oracle_ok"] else "FAIL"
    h = rec.get("escape_hatches")
    drr = rec.get("DRR")
    failed = rec.get("failed_gates") or []
    extra = "" if rec["oracle_ok"] else f" ({','.join(failed)})"
    # Primary static axis = typecheck_strict. Mark when it passes despite a
    # confounded runtime-gate failure (the honest XL+ jsdom situation).
    if not rec["oracle_ok"] and "typecheck_strict" not in failed and set(failed) <= _CONFOUNDED:
        extra += " — **typecheck CLEAN; runtime gate confounded**"
    return f"{ok} h={h} DRR={drr}{extra}"


def outcome_table(recs: list[dict]) -> str:
    # group by (target, scope_size); average over seeds by majority/first for display
    by_key: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in recs:
        by_key[(r["target"], r["scope_size"], r.get("display", r["target"]))][r["arm"]].append(r)
    rows = ["| scope | n | monolith | durable | stateless-RAG |", "| --- | --- | --- | --- | --- |"]
    for (target, n, display), arms in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        def pick(a):
            lst = arms.get(a)
            return lst[0] if lst else None
        rows.append(f"| {display} | {n} | {_cell(pick('monolith'))} | {_cell(pick('durable'))} | {_cell(pick('rag'))} |")
    return "\n".join(rows)


def scaling_table(recs: list[dict]) -> str:
    mono = sorted([r for r in recs if r["arm"] == "monolith"], key=lambda r: r["scope_size"])
    rows = ["| scope_size | monolith oracle | conv-complete | hatches | DRR | wall_s | workers |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in mono:
        rows.append(f"| {r['scope_size']} | {'PASS' if r['oracle_ok'] else 'FAIL'} | "
                    f"{r.get('conversion_complete')} | {r.get('escape_hatches')} | {r['DRR']} | "
                    f"{r.get('wall_clock_s')} | {r.get('n_worker_invocations')} |")
    return "\n".join(rows)


def drr_table(recs: list[dict]) -> str:
    by_key = defaultdict(dict)
    for r in recs:
        by_key[(r["target"], r["scope_size"], r.get("display", r["target"]))][r["arm"]] = r.get("DRR")
    rows = ["| scope | monolith | durable | stateless-RAG | durable−RAG |", "| --- | --- | --- | --- | --- |"]
    for (t, n, disp), d in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        dd, rr = d.get("durable"), d.get("rag")
        diff = round(dd - rr, 4) if isinstance(dd, (int, float)) and isinstance(rr, (int, float)) else "—"
        rows.append(f"| {disp} | {d.get('monolith','—')} | {d.get('durable','—')} | {d.get('rag','—')} | {diff} |")
    return "\n".join(rows)


TS_CODE = re.compile(r"TS(\d{3,5})")
TS_MEANING = {
    "2451": "cannot redeclare block-scoped variable (cross-file conflict)",
    "2300": "duplicate identifier",
    "2304": "cannot find name (missing/unreferenced type)",
    "2305": "module has no exported member",
    "2307": "cannot find module",
    "2322": "type not assignable",
    "2339": "property does not exist on type",
    "2345": "argument type not assignable",
    "2430": "interface incorrectly extends interface",
    "2551": "property does not exist (no near-match)",
    "2571": "object is of type 'unknown' (un-narrowed)",
    "2717": "subsequent property decls must have same type",
    "2739": "type missing required properties",
    "7006": "parameter implicitly has 'any' type",
    "7016": "could not find a declaration file for module",
}


def failure_taxonomy(recs: list[dict]) -> str:
    fails = [r for r in recs if not r.get("oracle_ok")]
    if not fails:
        return "_no failures recorded_"
    rows = ["| trial | arm | failed gates | TS error codes (top) |", "| --- | --- | --- | --- |"]
    code_tally: dict[str, int] = defaultdict(int)
    by_arm_codes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in fails:
        codes: dict[str, int] = defaultdict(int)
        for g in r.get("failed_gate_detail", []):
            for c in TS_CODE.findall(g.get("tail", "") or ""):
                codes[c] += 1
                code_tally[c] += 1
                by_arm_codes[r["arm"]][c] += 1
        top = ", ".join(f"TS{c}×{n}" for c, n in sorted(codes.items(), key=lambda kv: -kv[1])[:4]) or "—"
        disp = r.get("display", r.get("trial", "?"))
        rows.append(f"| {disp} | {r['arm']} | {','.join(r.get('failed_gates') or [])} | {top} |")
    rows.append("")
    rows.append("**Aggregate TS error codes across failures:**")
    rows.append("")
    rows.append("| code | meaning | count | arms |")
    rows.append("| --- | --- | --- | --- |")
    for c, n in sorted(code_tally.items(), key=lambda kv: -kv[1]):
        arms = ",".join(sorted(a for a in by_arm_codes if by_arm_codes[a].get(c)))
        rows.append(f"| TS{c} | {TS_MEANING.get(c, '?')} | {n} | {arms} |")
    return "\n".join(rows)


def resumability_table(rrecs: list[dict]) -> str:
    if not rrecs:
        return "_no resumability runs yet_"
    rows = ["| scope | durable work-preserved @ crash | monolith work-preserved @ crash | durable resumed→PASS |",
            "| --- | --- | --- | --- |"]
    for r in rrecs:
        d, m = r["durable"], r["monolith"]
        disp = f"{r['target']}-{r['stratum']} ({r['scope_size']})"
        rows.append(f"| {disp} | {d['work_preserved_fraction']*100:.0f}% ({d['committed_at_kill']}/{r['scope_size']}) | "
                    f"{m['work_preserved_fraction']*100:.0f}% ({m['committed_at_kill']}/{r['scope_size']}) | "
                    f"{d['oracle_ok_after_resume']} |")
    return "\n".join(rows)


def followup_table(frecs: list[dict]) -> str:
    if not frecs:
        return "_no follow-up runs yet_"
    rows = ["| scope→ext | arm | re-derivation reuse (DRR on ext) | ext oracle | workers on ext |",
            "| --- | --- | --- | --- | --- |"]
    for r in frecs:
        rows.append(f"| {r.get('label','?')} | {r.get('arm')} | {r.get('ext_DRR')} | "
                    f"{r.get('ext_oracle_ok')} | {r.get('ext_workers')} |")
    return "\n".join(rows)


def figures(recs: list[dict], rrecs: list[dict]) -> list[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    FIGS.mkdir(parents=True, exist_ok=True)
    made = []

    # DRR vs scope by arm
    fig, ax = plt.subplots(figsize=(6, 4))
    for arm, color in [("monolith", "#888"), ("durable", "#1f77b4"), ("rag", "#d62728")]:
        pts = sorted([(r["scope_size"], r["DRR"]) for r in recs
                      if r["arm"] == arm and r["target"] == "jsdom" and isinstance(r.get("DRR"), (int, float))])
        if pts:
            xs, ys = zip(*pts)
            ax.plot(xs, ys, marker="o", label=arm, color=color)
    ax.set_xlabel("repository scope (modules)"); ax.set_ylabel("Discovery Reuse Rate")
    ax.set_title("DRR vs scope (jsdom)"); ax.legend(); ax.grid(alpha=0.3)
    p = FIGS / "drr_vs_scope.png"; fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
    made.append(str(p))

    # monolith pass vs scope (scaling / breaking point)
    mono = sorted([r for r in recs if r["arm"] == "monolith" and r["target"] == "jsdom"],
                  key=lambda r: r["scope_size"])
    if mono:
        fig, ax = plt.subplots(figsize=(6, 4))
        xs = [r["scope_size"] for r in mono]; ys = [1 if r["oracle_ok"] else 0 for r in mono]
        ax.step(xs, ys, where="mid", marker="s", color="#444")
        ax.set_ylim(-0.1, 1.1); ax.set_yticks([0, 1]); ax.set_yticklabels(["FAIL", "PASS"])
        ax.set_xlabel("repository scope (modules)"); ax.set_title("Single-context monolith: pass vs scope (jsdom)")
        ax.grid(alpha=0.3)
        p = FIGS / "monolith_scaling.png"; fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
        made.append(str(p))

    # resumability bar
    if rrecs:
        fig, ax = plt.subplots(figsize=(6, 4))
        labels = [f"{r['target']}-{r['stratum']}" for r in rrecs]
        dur = [r["durable"]["work_preserved_fraction"] * 100 for r in rrecs]
        mon = [r["monolith"]["work_preserved_fraction"] * 100 for r in rrecs]
        x = range(len(labels)); w = 0.35
        ax.bar([i - w/2 for i in x], dur, w, label="durable", color="#1f77b4")
        ax.bar([i + w/2 for i in x], mon, w, label="monolith", color="#888")
        ax.set_xticks(list(x)); ax.set_xticklabels(labels)
        ax.set_ylabel("% work preserved at crash"); ax.set_title("Resumability: work surviving an interruption")
        ax.legend(); ax.grid(alpha=0.3, axis="y")
        p = FIGS / "resumability.png"; fig.tight_layout(); fig.savefig(p, dpi=140); plt.close(fig)
        made.append(str(p))
    return made


def main() -> int:
    recs = _dedup_canonical(_load("canonical.jsonl"))
    rrecs = _load("resume_exp.jsonl")
    frecs = _load("followup_exp.jsonl")
    md = [
        "# Tables (auto-generated by analyze.py)\n",
        f"_n canonical records: {len(recs)}; resumability runs: {len(rrecs)}; follow-up runs: {len(frecs)}_\n",
        "## Outcome (hardened oracle)\n", outcome_table(recs), "",
        "## Single-context scaling (breaking-point search)\n", scaling_table(recs), "",
        "## Discovery Reuse Rate\n", drr_table(recs), "",
        "## Failure taxonomy (mechanism)\n", failure_taxonomy(recs), "",
        "## Resumability (H4): work surviving an interruption\n", resumability_table(rrecs), "",
        "## Follow-up / extension (re-derivation cost)\n", followup_table(frecs), "",
    ]
    (RESULTS / "tables.md").write_text("\n".join(md))
    figs = figures(recs, rrecs)
    print("wrote results/tables.md")
    print(f"figures: {figs if figs else '(matplotlib unavailable — tables only)'}")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
