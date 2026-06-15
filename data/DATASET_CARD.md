---
license: cc-by-4.0
language:
- en
pretty_name: "Durable State vs Context — Repository-Scale Agent Trials"
tags:
- llm-agents
- code
- typescript
- software-engineering
- code-migration
- agent-orchestration
task_categories:
- other
configs:
- config_name: trials
  data_files: trials.jsonl
---

# Durable State vs Context — Repository-Scale Agent Trials

Machine-verified trial records from the paper **"State, Not Tokens: Repository-Scale
Agent Reasoning Is Bound by State Architecture."** Each record is one run of a
JavaScript→TypeScript migration of a real OSS repository (`express`, `jsdom`) under an
**unforgeable oracle**, graded by strict `tsc --strict --noEmit`, immutable test suites,
mandatory `.js`→`.ts` replacement, and a zero type-escape-hatch budget.

- **Code + reproduction harness:** https://github.com/professorpalmer/durable-state-vs-context
- **Paper:** *(arXiv ID pending — link will be added once indexed)*

## Why this dataset

The study varies a **single axis — how state flows between bounded agent workers** — with
model, tools, scaffold, and oracle held constant, across three arms:

| arm | state flow |
| --- | --- |
| `monolith` | one worker, whole scope, one context |
| `durable` | dependency-layer workers on a shared evolving tree; each inherits prior committed conversions |
| `rag` (stateless-RAG) | per-file workers on pristine trees + code-graph retrieval; never see each other's results |

The independent variable is **repository scope** (deterministic BFS over the dependency
graph: jsdom S/M/L/XL/XXL/FULL = 8/24/60/120/240/364 modules), not prompt tokens.

## `trials.jsonl` schema (one JSON object per line)

| field | meaning |
| --- | --- |
| `trial` | unique trial id |
| `target` | repository (`express`, `jsdom`) |
| `stratum` | size stratum (`S`/`M`/`L`/`XL`/`FULL`/…) |
| `scope_size` | number of in-scope modules |
| `arm` | `monolith` / `durable` / `rag` |
| `seed` | scope-selection seed (generalization across module subsets) |
| `oracle_ok` | overall PASS/FAIL under the hardened oracle |
| `gates_passed` / `failed_gates` / `failed_gate_detail` | per-gate breakdown (typecheck_strict, tests, conversion_complete, escape_hatches) |
| `conversion_complete` | all in-scope `.js` replaced by `.ts` (no runtime shadowing) |
| `escape_hatches` | count of forbidden `any`/`as any`/`@ts-ignore`/… (budget 0) |
| `DRR` / `drr_detail` | Discovery Reuse Rate — fraction of dependent modules consuming a prior worker's exported types |
| `n_worker_invocations` | agent worker calls used |
| `peak_scope_in_one_context` | largest module set held in a single context |
| `wall_clock_s` | end-to-end wall-clock seconds |

## `concurrency_sweep_aggregate.json` + `concurrency_sweep_profiles/`

A **replicated concurrency sweep** on full-scale (364-module) durable runs, used to localize the
**platform session cap**. Every run uses an identical protocol — a fixed 240 s steady-state
window — with n=5–10 replicates per concurrency C ∈ {8,12,16,24,32} and quality gates
(`base_build_start==1`, full window, ≥8 window workers; 34 admitted, 1 rejected). The aggregate
JSON has per-C mean ± 95% CI; the profiles dir has the raw per-worker event stream
(`base_build_*`, `layer_start`, `worker_start`, `worker_end` with `rc`/`worker_s`, `harvest_end`
with `got_ts`) for every replicate `c{C}_r{rep}.jsonl`.

Result (mean ± 95% CI): **C=8 97% ±5.7, C=12 99% ±2.0, C=16 66% ±2.7, C=24 28% ±4.3,
C=32 19% ±8.1** — a cleanly monotone collapse through a sharp knee, effective admission cap
**K≈10–12** (C=12 K_eff=11.9, C=16 K_eff=10.5). Above the cap the rate falls below a `min(1,K/C)`
reference (retry churn inflates the denominator).

## `claude_concurrency_aggregate.json` + `claude_concurrency_profiles/`

The **second-backend control** that proves the cap above is platform-specific, not fundamental.
The *same* frozen orchestrator runs **Claude Code (Anthropic API)** workers instead of Cursor
agents; a probe launches exactly C workers simultaneously (each isolated on its own tree + PM
state-dir, success = produced the `.ts`), C ∈ {4,8,16,24,32}, n=3. The aggregate JSON has per-C
mean ± 95% CI; `claude_concurrency_profiles/c{C}_r{rep}.json` has every worker's `got_ts`,
duration, and rate-limit signal.

Result: **100% success at every C through C=32** (252 workers, 0 fast-fails) — where the Cursor
backend collapses to 66%/28%/19% at C=16/24/32. Same orchestrator + durable state, different
serving platform → the admission cap is a property of the platform, not of durable state.

## Headline result

A single modern agentic worker scales much further than the naive context thesis predicts
(clean to 240 interdependent modules), but cracks at full repo scale by *capacity*, not
window. When work is decomposed, **durable accumulation strictly dominates stateless
retrieval** — RAG's blind workers emit code that does not compile (`TS2451` redeclaration
conflicts appear *only* in RAG). Durable additionally buys interruption-resumable consistent
checkpoints and zero-marginal-cost re-query. **State is an asset, not a prompt.**

## License
CC BY 4.0.
