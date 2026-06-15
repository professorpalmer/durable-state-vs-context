# Durable State vs. Context Length for Repository-Scale Agent Reasoning

A controlled study of whether repository-scale agent performance is limited by
**state architecture** or by **nominal context length** — using a hard,
machine-checkable task (strict JavaScript→TypeScript migration of real OSS
repositories) and an unforgeable oracle.

> **Status: active research. Findings are reported honestly, including results
> that cut against the original hypothesis.** See `RESULTS.md` for the current,
> calibrated read of the evidence.

**Artifacts.** Paper: [`paper/paper.pdf`](paper/paper.pdf). Trial records + concurrency
sweeps (Cursor + Claude second backend) as a public dataset:
[`CaryPalmer/durable-vs-context-trials`](https://huggingface.co/datasets/CaryPalmer/durable-vs-context-trials).

## The question

The field has treated agent forgetting as a *context-window* problem (bigger
context → better). An alternative framing: the working set should live in
**durable external state**, with the model acting as a navigator/reasoner over
that state rather than the container of it. We test which framing the data
supports, with explicit controls that isolate the variable.

## Headline findings (current — see `RESULTS.md` / `paper/paper.md`)

The data supports the state-architecture framing, with calibrated honesty:

- **The naive context thesis is refuted, then reframed.** A single navigating agent
  cleanly migrates up to **240** interdependent modules — it does *not* break where the
  context-window thesis predicts. It *does* crack at the full **364**-module tree, but by
  **capacity** (16 residual strict-type errors, no repair seam), not by window overflow.
- **Durable accumulation > stateless retrieval.** Same model/tools/decomposition — only
  accumulation differs. RAG's blind workers emit code that won't compile (`TS2451`
  redeclaration conflicts appear *only* in RAG); durable does not. At full scale durable
  reaches a **clean** `tsc --strict` tree (raw 5 errors → targeted repair → 0) where the
  monolith has no seam.
- **Three advantages, one root** (*discoveries are durable objects, not prompt text*):
  (1) conflict-free parallel decomposition; (2) interruption-resumable checkpoints
  (durable preserves 70.8% + resumes→PASS vs monolith 0%); (3) **zero-marginal-cost
  re-query** — recalling a materialized discovery is a SQLite read, not an LLM call.
- **Honest limit (and its resolution):** parallel *headroom* grows with repo size (critical
  path falls to **4.6%** of total work at full scale, 21.6× theoretical), but on the Cursor
  backend *usable* concurrency is capped at an effective **K≈10–12** sessions (replicated n=5–10
  sweep, mean ± 95% CI; success collapses monotonically above the cap). A **second backend
  (Claude Code) sustains 100% to C=32** under the same orchestrator — so the cap is a property of
  the **serving platform, not durable state** (demonstrated, not just argued). Closing the Cursor
  wall-clock gap is future work (admission control + dataflow scheduling).

## Arms (one substrate, one dimension varied: how state flows between workers)

| arm | decomposition | retrieval | accumulation |
| --- | --- | --- | --- |
| `monolith` (C1)      | none (one worker, whole scope) | implicit (agent navigates files) | n/a |
| `durable` (T)        | per dependency-layer workers on a **shared evolving tree** | yes | **yes** |
| `stateless_rag` (R)  | per-file workers on **pristine** trees + code-graph retrieval, merged | yes | **no** |

All arms run on the same platform (Puppetmaster `cursor --implement` workers),
the same held-constant TypeScript scaffold, and the same hardened oracle.

## The oracle (unforgeable success criterion)

A trial PASSES iff, on the converted tree:
1. `tsc --strict --noEmit` is clean,
2. the repo's real test suite stays green (tests are immutable),
3. **conversion is complete** — every in-scope `.js` is gone and replaced by a
   `.ts` (a leftover `.js` shadows the `.ts` at runtime → hollow pass → FAIL), and
4. zero type-system escape hatches (`any`, `as any`, `@ts-ignore`, …; budget 0).

The oracle propagates the tsx loader to spawned subprocesses so partially-migrated
trees load `.ts` at runtime. See `oracle/run_oracle.py`.

## Independent variable: repository **scope** (not token budget)

Scope is selected deterministically by BFS over the intra-repo dependency graph
from a fixed anchor, in size strata (jsdom S/M/L/XL/XXL/FULL =
8/24/60/120/240/364 modules, where FULL is the entire `lib/` tree), so we argue over
*repository size* rather than prompt tokens.

## Layout

```
harness/     orchestrator (run_arm.py), scope selection, dependency DAG,
             provisioning, DRR analyzer, trial schema, re-scorer
oracle/      hardened, config-driven success oracle + specs
results/     canonical trial records (JSONL) — the evidence
figures/     generated figures
paper/       paper draft
DESIGN.md    full experimental design + hypotheses
RESULTS.md   honest running summary of findings
```

## Reproducibility

Targets are pinned to exact commits (`*.pin.json`). Each trial provisions a fresh
checkout, runs the arm, and is scored by the same oracle. Re-scoring an existing
tree (`harness/rescore.py`) is deterministic and cheap, decoupled from the
expensive agent conversions.
