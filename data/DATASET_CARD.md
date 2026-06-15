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
- config_name: concurrency_c8
  data_files: concurrency_probe_c8.jsonl
- config_name: concurrency_c12
  data_files: concurrency_probe_c12.jsonl
- config_name: concurrency_c16
  data_files: concurrency_probe_c16.jsonl
- config_name: concurrency_c24
  data_files: concurrency_probe_c24.jsonl
- config_name: concurrency_c32
  data_files: concurrency_probe_c32.jsonl
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

## `concurrency_probe_c{8,12,16,24,32}.jsonl`

Per-worker timing events from a clean **5-point concurrency sweep** on full-scale (364-module)
durable runs, used to localize the **platform session cap**. Event stream: `base_build_start`,
`layer_start`, `worker_start`, `worker_end` (with `rc` and `worker_s`). A worker with `rc==0`
(or a `harvest_end` with `got_ts`) produced a diff; throttled sessions return fast (<20 s) with
no diff. Measured success rates: C=8 91%, C=12 96%, C=16 70%, C=24 ~26% (two samples: 29%, 23%),
C=32 34%. The over-cap rate is **stochastic** — C=24 reproducibly fell below C=32 — so we report
an effective session cap K≈10–12 rather than a clean `min(1, K/C)` law.

## Headline result

A single modern agentic worker scales much further than the naive context thesis predicts
(clean to 240 interdependent modules), but cracks at full repo scale by *capacity*, not
window. When work is decomposed, **durable accumulation strictly dominates stateless
retrieval** — RAG's blind workers emit code that does not compile (`TS2451` redeclaration
conflicts appear *only* in RAG). Durable additionally buys interruption-resumable consistent
checkpoints and zero-marginal-cost re-query. **State is an asset, not a prompt.**

## License
CC BY 4.0.
