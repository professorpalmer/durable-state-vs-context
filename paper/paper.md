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
filesystem on demand rather than cramming a working set into the prompt; (2) when
work is decomposed for parallelism, **durable accumulation strictly dominates
stateless retrieval** — RAG's independent workers emit code that does not even
compile, while durable does not; and (3) durable state confers a structural
property no single transcript can: **interruption-resumable, consistent
checkpoints**. The contribution is a reframing — *state is an asset, not a prompt*
— with controls that isolate which capability actually matters.

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
size strata (jsdom S/M/L/XL/XXL = 8/24/60/120/240 `[live]`). We argue over repo
size, not prompt tokens. Multiple seeds select different scope sets → generalization.

### 3.4 Metrics
Oracle pass; wall-clock; worker invocations; peak working set in one context;
escape-hatch count; **Discovery Reuse Rate (DRR)** = fraction of dependent
in-scope modules whose `.ts` consumes a type exported by an already-converted
in-scope dependency (a persisted discovery); failure taxonomy.

## 4. Results

### 4.1 The naive context thesis fails: navigation scales
| scope | monolith |
| --- | --- |
| 7 (express) | PASS |
| 8 | PASS |
| 60 | PASS (0 hatches, 41 min) |
| 120 | PASS (0 hatches, 46 min) |
| 240 | `[live]` |

A single context did not break where the context-length thesis predicts. The
agent is *already* a reasoner over external state (the filesystem); it pulls in
modules on demand. **This is itself a finding** and it reframes the contribution
away from "state beats context for a single agent."

### 4.2 Durable accumulation > stateless retrieval (the clean divergence)
| scope | durable | stateless-RAG |
| --- | --- | --- |
| express (7) | PASS (DRR 0.75) | PASS (DRR 0.25) |
| jsdom-S (8) | PASS | **FAIL** (TS2451 redeclaration + incomplete) |
| jsdom-M (24) | `[live]` | `[live]` |
| jsdom-L (60) | `[live]` | `[live]` |

Mechanism: RAG's independent workers, blind to each other, emit conflicting
top-level declarations and inconsistent shared types → the merged tree does not
type-check. Durable workers build on committed predecessors, so the tree stays
consistent. Multi-seed replication: `[live]`.

### 4.3 Discovery Reuse Rate
DRR cleanly separates accumulation from retrieve-and-forget where the codebase
annotates with sibling types (express: durable/monolith 0.75 vs RAG 0.25). On
jsdom's CommonJS style DRR is a weaker discriminator; there the mechanism surfaces
as the failure taxonomy (§4.2) rather than DRR. Honest about both.

### 4.4 Resumability (H4): the structural durable edge
At a hard mid-run interruption: durable preserves every committed layer (a
*consistent* checkpoint that type-checks) and resumes to an oracle PASS having
only redone the in-flight layer; the monolith's interrupted tree is an
inconsistent partial that fails the oracle — 0 known-good recoverable modules.
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
- One task family (migration); artifact == code. Generalization to
  reasoning-artifact tasks is future work.
- Oracle hardening history (hollow passes, subprocess `.ts` loading) — all trees
  re-scored by the final oracle for internal consistency.
- Single platform (Puppetmaster cursor workers); model routing held constant.

## 7. Conclusion
Repository-scale agent performance is primarily constrained by **state
architecture**, not nominal context length. The win, where it exists, comes from
treating discoveries as durable, consistent, reusable system objects.
