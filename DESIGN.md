# Durable State vs. Shared Transcript for Long-Window Software Tasks

**A pre-registered, controlled study.**

- Status: `DRAFT — pre-registration` (freeze before first scored run)
- Version: 0.1.0
- Last updated: 2026-06-15
- Artifact home: this directory (graduates to a standalone public artifact repo at camera-ready)

> Pre-registration discipline: the protocol, arms, metrics, and analysis below are
> fixed *before* any scored run. Pilot runs (clearly labeled) may refine the
> harness but do not count toward results. Any post-hoc deviation is logged in
> `DEVIATIONS.md` with a reason.

---

## 1. Motivation and the gap

Long-horizon coding agents are evaluated today by varying the **model** under a
**fixed scaffold**, then attributing success to the model (usually: "long context
helps"). Two current benchmarks make the blind spot explicit:

- **SWE-Bench Pro** (Sep 2025): enterprise, multi-file, long-horizon tasks; even
  GPT-5 resolves ≤ 23.3% pass@1. Conclusion is about *model* capability under one
  unified scaffold.
- **NL2Repo-Bench** (Dec 2025): whole-repository generation; reports that ~180
  interaction turns accumulate **~90K tokens of conversation history**, and
  explicitly calls for *"mechanisms for maintaining global coherence over long
  development trajectories... beyond larger context windows."*

No published study **isolates the state architecture** — i.e., holds the model
fixed and varies only *how intermediate state is managed*. That is the gap.

**Thesis (state architecture, not context length).** The field has largely
treated repository-scale agent failure as a *context-window* problem, with the
assumed remedy being a bigger window (4k → 128k → 1M → …). We argue it is
primarily a **state-architecture** problem. The model should not carry the entire
working set in a transient prompt at all; the working set should live in
**durable external state** — discoveries accumulated as reusable, re-fetchable
artifacts — with the model acting as a *navigator and reasoner over that state*
rather than its container. This mirrors how databases scaled: not by giving every
process infinite RAM, but with indexes, caches, durable storage, and query
planners, turning the process into a *coordinator over state*.

Stated testably (and *not* as "we solved context windows"):

> **Repository-scale reasoning performance is limited primarily by state
> architecture, not by nominal context length.**

Operational prediction: a model running a **bounded** effective context with
durable-state decomposition (T) matches or beats the **same** model forced to
hold the working set in one transient transcript (C1/C2) — and, with model and
window held constant, T succeeds at working-set sizes where the transcript fails.
The binding constraint is the architecture, not the window. This directly answers
NL2Repo's open call for "mechanisms... beyond larger context windows."

**What durable state buys over retrieval (the "isn't this just RAG?" answer).**
RAG retrieves context and *throws it away every turn*. Durable state instead
**accumulates**: discoveries persist as typed artifacts, decomposition spreads
the working set across bounded workers, follow-ups reuse prior results without
re-derivation (zero-token recall), and a persistent world-model of the repo
compounds across steps. The claim is therefore *accumulation > retrieve-and-
forget*, and it is isolated by a dedicated control (arm **R**, §3.3): identical
decomposition and CodeGraph retrieval, but **no cross-worker accumulation**. If
durable-state (T) beats stateless-retrieval (R) at the same context budget, the
gain is persistence, not retrieval.

**Motivating real-world deployment (private, telemetry only).** A production
legacy **Adobe Flex → React/TypeScript** enterprise migration was executed via
durable-state orchestration: 292 durable jobs across 149 worktrees / 305 agent
sessions over ~9 days, 271/292 (92.8%) complete, driving an E2E suite from
180 pass / 43 fail to **223 pass / 0 fail**, with 883 golden-master parity
fixtures and an honestly-tracked residual-debt baseline (49 drifted, 890
escape-hatch fallbacks), and 286 zero-token follow-up reads from durable state.
This is the *motivation* (an existence proof at scale on proprietary code), **not**
the controlled result. The controlled result is the public experiment specified
here.

---

## 2. Hypotheses

- **H1 (headline — architecture > nominal context length).** With the model and
  its window held **constant**, durable-state decomposition (T) achieves a higher
  oracle-pass rate than shared-transcript (C1/C2), and the advantage **increases
  monotonically with task size**. The bridge metric is
  `peak_working_set_in_one_context`: T's stays bounded/flat as scope grows while
  the transcript's grows with scope and the transcript's pass-rate collapses once
  it exceeds the window — evidence the binding constraint is the *architecture*,
  not the window size.
- **H2 (accumulation > retrieval — the anti-RAG test).** At the **same** context
  budget and the **same** decomposition + retrieval, durable-state (T) beats
  stateless-retrieval (R). Isolates *persistent accumulation* from *retrieve-and-
  forget*; this is the contrast that answers "isn't this just RAG?".
- **H3 (mechanism).** Shared-transcript failures are dominated by
  context-overflow / lost-in-the-middle / regression of previously-correct work;
  durable-state keeps per-subtask context bounded and avoids these modes.
- **H4 (durability).** Under mid-run interruption, durable-state resumes at
  near-zero extra cost to completion; shared-transcript must replay/restart.
- **H5 (robustness, secondary — confound disclosed).** Giving the transcript arm a
  **larger-window model** postpones but does not eliminate its collapse: durable-
  state at a *smaller* window still matches or beats transcript at the *larger*
  window. Reported as secondary because swapping window also swaps model
  capability — the confound is stated, not hidden.

A result that **refutes** H1/H2 (no divergence, transcript wins, or T ≈ R) is
publishable and will be reported as such. The experiment is designed to be
falsifiable.

---

## 3. Design

### 3.0 Claim structure (the spine)

> **Repository scale** is the independent variable.
> **State architecture** is the treatment.
> **Discovery reuse rate** is the mechanism.
> **Task success / typecheck / tests** are the outcomes.

Everything below is in service of that four-line structure: vary scale, swap only
the state architecture, measure the reuse mechanism, score with an unforgeable
oracle. The contribution is not "a new retrieval method" but **a different
computational model for repository-scale agent reasoning — state as a durable
asset, not a transient prompt.**

### 3.1 Independent variable — **repository scope**, not token budget
The axis is **how much interrelated code must be reasoned about at once**,
operationalized as the **number of in-scope, dependency-connected modules** in a
single migration, swept across strata S/M/L/XL (jsdom: ~8 / ~24 / ~60 / ~120
modules; see `harness/select_scope.py`). Total required-context tokens are
*reported alongside* but are **not** the manipulated variable.

This is a deliberate methodological choice. We do **not** dial nominal context
budgets (e.g. 200k vs 1M) as the headline, because changing a model's window
also changes its capability — an unavoidable confound. Holding the model and its
window *fixed* and growing **repository scope** isolates state architecture
cleanly *and* matches the question practitioners actually ask ("can it handle a
million-line repo?"). The bigger-window-model comparison survives only as the
secondary, confound-disclosed robustness check **H5**.

Expected qualitative pattern (the figure we are trying to produce):

| Repo scope | Transcript (C1/C2) | RAG (R) | Durable (T) |
|------------|--------------------|---------|-------------|
| **Small** (fits one window) | ≈ | ≈ | ≈ |
| **Medium** | starts degrading | better | best |
| **Large** (≫ window) | fails rapidly | mixed (no cross-worker coherence) | stable |

### 3.2 Held constant (confound controls)
Base model; the toolset available to the agent (read/write files, run the oracle,
run tests); the oracle; the budget policy; the repository + pinned commit. The
**only** thing that differs across arms is state management.

### 3.3 Arms — a state-architecture spectrum (model pinned across all)

The arms are not an arbitrary list; they walk one axis — *how intermediate state
is managed* — from "all in a transient prompt" to "all in durable external
store." Each adjacent pair isolates exactly one mechanism.

| Arm | State architecture | Decomp | Retrieval | **Accumulation** | Role |
|-----|--------------------|:------:|:---------:|:----------------:|------|
| **C1** — Shared-transcript | whole working set in one growing prompt | ✗ | ✗ | in-context (lossful at scale) | naive control |
| **C2** — Transcript + compaction | C1 + periodic lossy summarization of history | ✗ | ✗ | in-context, compressed | **strong control** (pre-empts "just summarize") |
| **R** — Stateless retrieval (RAG) | per-subtask CodeGraph retrieval, **pristine tree each worker**, patches merged at end | ✓ | ✓ | **none across workers** | **anti-RAG control** (H2) |
| **T** — Durable-state | decompose → bounded context → typed artifacts persisted + re-fetched; workers build on the **shared evolving tree** (Puppetmaster) | ✓ | ✓ | **full (persistent)** | treatment |
| **C3** — Prior-art memory (stretch) | LangGraph checkpointer and/or MemGPT/Letta | ✓ | ✓ | external (named) | positions vs. named work |

The two decisive contrasts fall straight out of the table:
- **T vs C1/C2** (same model + window, vary architecture across the size-sweep)
  → *architecture beats nominal context length* (H1).
- **T vs R** (same decomposition + retrieval, toggle accumulation only)
  → *persistent accumulation beats retrieve-and-forget* (H2; the "just RAG?" kill).

**Fairness rules (mandatory).**
1. Identical model and identical tools in every arm. C1/C2 can read any file and
   run the oracle on demand — they simply lack durable external state +
   decomposition. R gets the *same* decomposition and retrieval as T — it is
   denied only cross-worker persistence. We compare *architectures*, not
   *capabilities*.
2. C2 must be a genuine best-effort transcript manager (real compaction), not a
   strawman. The headline claim is "durable-state beats a *competent*
   transcript," so C2 is the bar that matters.
3. R must be a genuine best-effort RAG agent (real CodeGraph retrieval per
   subtask), so the anti-RAG result is "durable beats *competent* RAG."

### 3.4 Model (pinned)
- **Primary:** `claude-opus-4.8` (frontier, long context) — the **steel-man**:
  if durable-state wins even when the transcript arm runs a frontier long-context
  model with compaction, the claim is maximally strong.
- **Replication:** one mid-tier model (cheaper sweep; transcript pressure appears
  sooner) — demonstrates the effect is **not model-specific**.
- Auto-routing is **OFF** for the core comparison (architecture is the only
  variable). Puppetmaster's task-aware router is a *separate* contribution,
  measured in its own ablation (§7), never mixed into the core arms.

---

## 4. Task and oracle

### 4.1 Primary task: strict JS → TypeScript migration
Convert an in-scope set of JavaScript modules to TypeScript such that the project
type-checks under strict mode, all existing tests still pass, and the build
succeeds — **without** disabling the type system.

Rationale: the oracle is unforgeable and machine-checkable; types propagate across
the module graph (real cross-file coupling → genuine long-window pressure, not
embarrassingly parallel); the size axis is continuous; and it mirrors the private
case study's "legacy → typed modern" shape.

### 4.2 The oracle (pass = ALL of)
1. **Type-check:** `tsc --strict --noEmit` → **0 errors**.
2. **Tests:** the repo's existing suite is **green** (no fewer passing than the
   pre-migration baseline; 0 new failures).
3. **Build:** the repo's build command succeeds.
4. **Anti-cheat / quality:** escape hatches are **counted and capped**. Forbidden
   (lint-enforced) or budgeted: `any`, `as any`, `@ts-ignore`, `@ts-expect-error`,
   implicit-any suppressions, `tsconfig` strictness downgrades. Residual count is
   reported as a **quality metric** (public analogue of the private run's "890
   fallbacks"). A run that "passes" only by spraying `any` does **not** pass.

### 4.3 Replication task: untyped Python → `mypy --strict`
Add type annotations to an untyped Python module set until `mypy --strict` is
clean and `pytest` is green. Anti-cheat: budget/forbid `# type: ignore`,
`Any`, and `cast(...)`. Demonstrates the effect is not a JS/`tsc` artifact.

---

## 5. Targets

Verified this session: license, primary language, presence of a runnable test
suite + CI.

- **Flagship (size-sweep): `jsdom/jsdom`** — MIT, JavaScript, large
  interdependent web-standards surface, substantial suite. Best repo for the
  within-repo scaling curve.
- **Flagship-small (hero example): `expressjs/express`** — MIT, pure JavaScript,
  "super-high test coverage", GH Actions CI. Clean and bounded.
- **Corpus (generalizability, addresses n=1):** stratified sample of ~8
  MIT/permissive, still-JavaScript repos with green CI suites, across size strata.
  Selection procedure is itself reported (license ∈ permissive; primary lang =
  JS; runnable test suite + CI; not already TS). Commits pinned in `corpus.lock`.

Structure: **deep size-sweep on `jsdom`** (the divergence figure) + **corpus
breadth** (statistical generalizability) + **`express`** as the readable example.

---

## 6. Protocol

- **Isolation:** each trial runs in a clean container at the pinned commit; the
  oracle is executed fresh; no state leaks between trials.
- **Stochasticity:** k = 3–5 seeds per (repo, size-stratum, arm); report mean ±
  95% CI.
- **Budget:** generous cap on **both** tokens and wall-clock. Report
  `success@budget` *and* `tokens-to-success`. (Equal success at 5× tokens is a
  result.)
- **Interruption sub-experiment (H3):** kill each arm at ~50% progress; measure
  whether/at-what-cost it resumes to oracle-pass.
- **Freeze:** tag this doc `v1.0.0 (registered)` before the first scored run.

---

## 7. Metrics and analysis

- **Primary figure:** oracle-pass rate vs. task size, one curve per arm, with CIs.
  H1 predicts the transcript curve declines past the effective window while the
  durable-state curve stays flat → a widening gap.
- **Secondary:** wall-clock; `peak_working_set_in_one_context` (the H1 bridge
  metric); **escape-hatch count** (quality); partial credit (% modules converted,
  % tests fixed); worker-invocation count. (Token-to-success is reported when
  available but the Cursor SDK's token accounting is unreliable/estimated, so it
  is *not* a primary metric — see §8.)
- **Mechanism for T-vs-R (H2) — Discovery Reuse Rate (DRR).** The number that
  *explains* a durable-vs-RAG win instead of asserting it. **DRR = fraction of
  successfully-converted modules whose produced `.ts` imports and uses a
  type/interface exported by an earlier-converted in-scope dependency** (i.e.
  consumes a *persisted discovery* rather than re-deriving it). Statically
  computed from the final tree + the dependency-layer order (`harness/dag.py`).
  Precise structural claim (no overclaim): **in the stateless-RAG arm, discovery
  reuse is architecturally unavailable *except through the original repository /
  index*** — a later worker can only *re-derive* a prior finding from the static
  original sources, never *consume* a prior worker's produced artifact; **in the
  durable arm, reuse is explicit and measurable through persisted artifacts.**
  Prediction: R's DRR stays low and flat (reuse only via re-derivation) and
  integration breaks where cross-module types must agree; T's DRR is positive and
  **rises with dependency depth**. A claim like "42% of T's successful conversions
  consumed a type discovered and persisted by an earlier task — which R could at
  best re-derive from scratch, not inherit" is the mechanistic core of the paper.
- **Mechanism (H3):** failure-mode taxonomy, counted per arm — `context_overflow`,
  `lost_in_middle_regression`, `nonterminating_loop`, `budget_exhausted`,
  `oracle_red_typecheck`, `oracle_red_tests`, `gave_up`.
- **Durability (H3):** resume-success rate and extra-cost-to-finish per arm.
- **Routing ablation (separate):** with architecture = T fixed, compare pinned
  model vs. Puppetmaster auto-routing on cost and success — the router as its own
  contribution, never folded into the core arms.

**Statistics:** mixed-effects model with arm + log(size) + arm×size as fixed
effects and repo as a random effect; the arm×size interaction is the H1 test.

---

## 8. Threats to validity

- **Construct** (oracle-green ≠ good code): mitigated by the anti-cheat
  escape-hatch metric + human spot-check of a random sample for spec drift.
- **Internal** (LLM stochasticity; confounds): seeds + CIs; pinned model;
  identical tools across arms.
- **External** (single repo / language): stratified corpus + Python replication.
- **Strawman** (weak control): C2 is a real compaction-augmented transcript.
- **Contamination** (repos in pretraining): report it; the *comparative* claim is
  robust to contamination because all arms share the same (possibly contaminated)
  model — contamination cannot explain an *architecture* gap.

---

## 9. Reproducibility / artifact

Public release: oracle harness (`oracle/`), arm harnesses, `corpus.lock` (pinned
commits), per-trial raw logs + JSON verdicts, analysis notebooks, and seeds.
Proprietary deployment data never ships — only the aggregate telemetry quoted in
§1.

---

## 10. Venue & timeline

- **Venue:** ICSE-SEIP / FSE-Industry / EMSE (controlled study + experience
  report), arXiv preprint first.
- **Timeline:** pilot (1 repo, 1 stratum, all arms) in days; full sweep + corpus
  in a few weeks; Python replication after JS result lands.

---

## 11. Open decisions (resolve before freeze)

- Exact size strata per flagship (depends on `jsdom` module graph).
- Whether C3 (LangGraph/MemGPT) is in v1 or deferred to "future work".
- Budget ceilings (set from pilot burn rates).
- Corpus final membership (run the selection procedure, pin commits).
