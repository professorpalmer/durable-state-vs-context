# Durable State vs. Context Length for Repository-Scale Agent Reasoning

A controlled study of whether repository-scale agent performance is limited by
**state architecture** or by **nominal context length** — using a hard,
machine-checkable task (strict JavaScript→TypeScript migration of real OSS
repositories) and an unforgeable oracle.

> **Status: active research. Findings are reported honestly, including results
> that cut against the original hypothesis.** See `RESULTS.md` for the current,
> calibrated read of the evidence.

## The question

The field has treated agent forgetting as a *context-window* problem (bigger
context → better). An alternative framing: the working set should live in
**durable external state**, with the model acting as a navigator/reasoner over
that state rather than the container of it. We test which framing the data
supports, with explicit controls that isolate the variable.

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
from a fixed anchor, in size strata (jsdom S/M/L/XL = 8/24/60/120 modules), so we
argue over *repository size* rather than prompt tokens.

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

## Reproationality

Targets are pinned to exact commits (`*.pin.json`). Each trial provisions a fresh
checkout, runs the arm, and is scored by the same oracle. Re-scoring an existing
tree (`harness/rescore.py`) is deterministic and cheap, decoupled from the
expensive agent conversions.
