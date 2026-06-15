# State, Not Tokens: Repository-Scale Agent Reasoning Is Bound by State Architecture

**Working draft — professorpalmer. Numbers marked `[live]` are still being collected;
this draft reports only what the hardened oracle has actually verified.**

## Abstract

The agent community has largely treated repository-scale forgetting as a
*context-window* problem: bigger windows (8k → 128k → 1M) are expected to yield
better whole-repo reasoning. We argue this is a misdiagnosis. Using a hard,
machine-checkable task — strict JavaScript→TypeScript migration of real OSS
repositories under an unforgeable oracle (strict `tsc`, immutable test suites,
mandatory `.js`→`.ts` replacement, zero type-escape-hatches) — we vary a single
axis: **how state flows between bounded workers**. Three arms hold model, tools,
scaffold, and oracle constant: a single-context *monolith*, a *durable* arm that
accumulates each completed dependency layer as a committed artifact on a shared
evolving tree, and a *stateless-RAG* arm whose per-file workers retrieve context
but never see each other's results. We find: (1) a single modern agentic worker
already scales much further than the naive context thesis predicts — it cleanly
migrates **120** interdependent jsdom modules `[live: 240]` by navigating the
filesystem on demand rather than cramming a working set into the prompt — cleanly
migrating up to **240** interdependent jsdom modules — but it **does crack at the
full 364-module tree**, leaving residual strict-type errors on the hardest module
(a *capacity* failure, not a conversion failure); (2) when work is decomposed for
parallelism, **durable accumulation strictly dominates stateless retrieval** —
RAG's independent workers emit code that does not even compile (`TS2451`
redeclaration conflicts appear *only* in RAG), while durable does not; and (3)
durable state confers a structural property no single transcript can:
**interruption-resumable, consistent checkpoints**. A failure taxonomy shows three
architectures fail in three distinct ways — RAG by *conflict*, monolith by
*capacity*, durable by neither. The contribution is a reframing — *state is an
asset, not a prompt* — with controls that isolate which capability actually matters.

## 1. Introduction

- The misdiagnosis: context-length arms race vs. state architecture.
- The database analogy: processes became coordinators over durable state
  (indexes, caches, query planners) rather than containers of state.
- The reviewer's question we must answer: *what does durable state buy that
  retrieval alone does not?* Our answer is mechanistic, not rhetorical:
  conflict-free parallel decomposition + resumable checkpoints, measured.

## 2. Related work

- Long-context LMs; lost-in-the-middle; context compaction.
- RAG / code-graph retrieval for coding agents.
- Agent memory / scratchpads / external stores.
- SWE-bench-style repo tasks (we differ: controlled state axis, unforgeable oracle).

## 3. Experimental design

### 3.1 Task and oracle
Strict JS→TS migration. Oracle PASS iff: `tsc --strict --noEmit` clean; immutable
test suite green; **conversion-complete** (no in-scope `.js` left to shadow a `.ts`
at runtime); zero escape hatches (`any`, `as any`, `@ts-ignore`, … budget 0). The
oracle propagates the tsx loader to spawned subprocesses so partial trees load
`.ts` at runtime. Why this task: success is verifiable and *adversarially hard to
fake* — partial credit and hidden hollow passes are eliminated by construction.

### 3.2 The single varied axis: state flow
- **monolith (C1):** one worker, whole scope, one context.
- **durable (T):** dependency-layer workers on a shared evolving tree; each
  inherits prior conversions (accumulation ON). Per-layer git commit = checkpoint.
- **stateless-RAG (R):** per-file workers on pristine trees + code-graph retrieval,
  merged at the end (accumulation OFF; reuse only by re-derivation).

### 3.3 Independent variable: repository scope (not tokens)
Deterministic BFS over the intra-repo dependency graph from a fixed anchor, in
size strata (jsdom S/M/L/XL/XXL/FULL = 8/24/60/120/240/364, where FULL is the
entire `lib/` tree). We argue over repo size, not prompt tokens. Multiple seeds
select different scope sets → generalization.

### 3.4 Metrics
Oracle pass; wall-clock; worker invocations; peak working set in one context;
escape-hatch count; **Discovery Reuse Rate (DRR)** = fraction of dependent
in-scope modules whose `.ts` consumes a type exported by an already-converted
in-scope dependency (a persisted discovery); failure taxonomy.

## 4. Results

### 4.1 The naive context thesis fails at moderate scale — but the single context *does* crack at full-repo scale
| scope (jsdom modules) | monolith | note |
| --- | --- | --- |
| 7 (express) | PASS | |
| 8 | PASS | |
| 24 | PASS | |
| 60 | PASS | 0 hatches, 41 min |
| 120 | PASS | 0 hatches, 46 min |
| 240 | PASS | 0 hatches, 26 min |
| **364 (full `lib/`)** | **FAIL** | converts all, tests green, 0 hatches, but **16 strict-type errors** |

A single context did **not** break where the context-length thesis predicts: the
agent is *already* a reasoner over external state (the filesystem), pulling in
modules on demand rather than cramming a working set into the prompt. It cleanly
migrated up to **240** interdependent modules. **This is itself a finding** and it
reframes the contribution away from "state beats context for a single agent."

But at the **full 364-module `lib/` tree the one-shot architecture degrades**: it
still converts everything and gets the test suite green with zero escape hatches,
yet leaves **16 residual strict-type errors** (`TS2322` assignability ×8, `TS2571`
"object is of type 'unknown'" ×7), concentrated in the single hardest module
(`XMLHttpRequest-impl`). Notably it failed *safely* — it reached for `unknown`,
not `any` — but ran out of capacity to globally narrow types at full scale. This
is a **capacity** failure (correctness under a single global view), categorically
different from RAG's failure mode below.

### 4.2 Durable accumulation > stateless retrieval (the clean divergence)
| scope | durable | stateless-RAG |
| --- | --- | --- |
| express (7) | PASS (DRR 0.75) | PASS (DRR 0.25) |
| jsdom-S (8) | PASS (seeds 1,2) | **FAIL** (seeds 0,1,2 — 0/3) |
| jsdom-M (24) | `[live]` | `[live]` |
| jsdom-L (60) | PASS | **FAIL** (typecheck) |
| jsdom-XL (120) | PASS (typecheck, 0 hatches)¹ | — |

Same scope, same model, same tools — only **accumulation** differs. RAG fails at
*every* small-scope seed (0/3 at S) and at L; durable passes. ¹At XL the durable
runtime gate is confounded by jsdom's own build system (§6); we report the
unconfounded static axis (`tsc` clean, 0 hatches, all 120 converted).

### 4.3 Failure taxonomy: three architectures, three distinct failure modes
The mechanism is clearest in *how* each arm fails (TS error codes across all
failing trials):

| failure mode | arm | signature codes | reading |
| --- | --- | --- | --- |
| **conflict** | stateless-RAG | `TS2451` redeclare ×10, `TS2717`, `TS2430`, `TS2739` | blind workers emit colliding top-level declarations / inconsistent shared types → merged tree won't compile |
| **capacity** | monolith (364) | `TS2322` ×8, `TS2571` un-narrowed `unknown` ×7 | global view, but can't maintain strict correctness on the hardest module at full scale |
| (none) | durable | — | shared state removes conflicts; decomposition bounds per-worker complexity |

`TS2451` ("cannot redeclare block-scoped variable") appears **only** in the RAG
arm and is the direct fingerprint of missing shared state. The monolith's `unknown`/
assignability errors appear **only** under one global context at full scale. Durable
avoids both by construction. This is the paper's central mechanistic claim.

### 4.4 Discovery Reuse Rate
DRR cleanly separates accumulation from retrieve-and-forget where the codebase
annotates with sibling types (express: durable/monolith 0.75 vs RAG 0.25). On
jsdom's CommonJS style DRR is a weaker, noisier discriminator; there the mechanism
surfaces as the failure taxonomy (§4.3) rather than DRR. We report both honestly
rather than cherry-picking the favorable metric.

### 4.5 Resumability (H4): the structural durable edge
At a hard mid-run interruption: durable preserves every committed layer (a
*consistent* checkpoint that type-checks) and resumes to an oracle PASS having
only redone the in-flight layer; the monolith's interrupted tree is an
inconsistent partial that fails the oracle — 0 known-good recoverable modules.
Measured: durable committed 17 modules across 6 layers before interrupt and
resumed from that committed state; the monolith loses its entire single shot.
Work-preserved-at-crash: durable `[live]%` vs monolith 0%.

## 5. Discussion
- What durable state buys (measured): conflict-free decomposition; resumable
  consistent checkpoints. What it does **not** buy for this task: raw single-agent
  navigation is already strong, and for code-artifact tasks follow-up reuse is
  filesystem-available to any navigating agent (we did not claim otherwise).
- Implication: the durable advantage is an *orchestration/coordination* property,
  realized when one context is insufficient or work must survive interruption /
  parallelize — not a universal "context is solved" claim.

## 6. Threats to validity
- **jsdom build-system coupling at XL+ scope.** jsdom's `npm run prepare` runs
  `scripts/webidl/convert.js`, which `require()`s `lib/jsdom/living/helpers/
  namespaces.js` by *literal `.js` path*. Once that helper is converted to `.ts`,
  jsdom's own IDL codegen fails, which fails the *runtime* gate (`tests_api`) and
  resurfaces stale `.js`. This is a target build artifact, not a durable-arm
  defect; we therefore make XL/FULL comparisons on the **static** axis
  (`typecheck_strict` + `escape_hatches`), which jsdom's build cannot perturb, and
  flag the runtime gate as confounded there. A codegen-baked harness (run `prepare`
  on the pristine tree pre-conversion, exclude `scripts/` from scope) removes the
  confound; logged as future work rather than silently dropped.
- One task family (migration); artifact == code. Generalization to
  reasoning-artifact tasks is future work.
- Oracle hardening history (hollow passes, subprocess `.ts` loading, over-broad
  hatch counting) — all trees re-scored by the final oracle for internal
  consistency; the hatch fix flipped mono-240 from a false FAIL to its true PASS.
- Cursor-SDK token counts are unreliable (implausibly low); we report wall-clock,
  worker count, and DRR as the cost axes instead of token deltas.
- Single platform (Puppetmaster cursor workers); model routing held constant.

## 7. Conclusion
Repository-scale agent performance is primarily constrained by **state
architecture**, not nominal context length. A single modern agentic context
scales much further than the context-length arms race assumes (clean to 240
modules by navigating the filesystem), but it does eventually crack at full-repo
scale — and it cracks by *capacity* (un-narrowed types under one global view),
not by running out of window. When work is decomposed for parallelism or
resilience, *how state flows between workers* becomes decisive: stateless
retrieval fails by *conflict* (colliding declarations its blind workers cannot
reconcile), while durable accumulation fails by neither, additionally buying
interruption-resumable consistent checkpoints. The win comes from treating
discoveries as durable, consistent, reusable system objects — *state is an asset,
not a prompt*.
