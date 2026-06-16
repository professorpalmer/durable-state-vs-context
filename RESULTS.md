# Results — honest running summary

_Last updated: 2026-06-15 (overnight autonomous run). Multi-seed at S/M; single seed at L+ unless noted._

## Headline (calibrated)

1. **The naive "single context breaks at repository scale" thesis did NOT hold up to 240 modules.** A single agentic worker (`monolith`) cleanly migrated **240 interdependent jsdom modules** (strict typecheck + tests + 0 hatches). Reason: a modern agentic coder *navigates the filesystem on demand* — it is already a reasoner over external state, not a prompt that crams the working set. "Context length" was not the binding constraint at moderate scale.
2. **But the single context DOES crack at full-repository scale.** At the **full jsdom `lib/` tree (364 modules)** the monolith converts everything and gets tests green with 0 hatches, but **fails `typecheck_strict` with 16 residual errors** (TS2571 "object is of type 'unknown'", TS2322 assignability), concentrated in the hardest module (`XMLHttpRequest-impl`). It used the *safe* escape (`unknown`, not `any`) but ran out of capacity to globally narrow types. This is the first honest degradation of the one-shot architecture — a quality (type-correctness) failure, not a conversion failure.
   - **Capstone — durable at the same 364 scale (VERIFIED CLEAN).** Durable decomposition converted all 364 modules (364 workers, ~3.2 h) and its *raw* tree had **only 5 type errors vs the monolith's 16**. Honestly, durable does not eliminate conflicts entirely: its residue is bounded *intra-layer* — sibling modules converted in the **same** dependency layer are blind to each other (they see only *prior* layers), so two CSS rule siblings redeclared a shared type (`TS2451`/`TS2300`). This is the *same conflict class* RAG suffers, but **bounded to a layer** rather than global. **Targeted iterative repair** then re-ran only the 2 conflicting modules (now able to see each other on the shared tree): **3 iterations, 4 repair-worker calls, 258 s (~4.3 min) → 0 errors, `tsc --strict --noEmit` verified CLEAN.** The decomposition is what makes that repair cheap and possible; the one-shot monolith — 16 errors inside a single 364-module context — has no seam to re-run against. **Net capstone: at full-repo scale the monolith FAILS strict typecheck and cannot self-repair, while durable reaches CLEAN for ~4 min of targeted re-work.**
3. **Durable accumulation beats stateless retrieval (RAG).** When work IS decomposed into parallel workers, the stateless-RAG arm — whose workers cannot see each other's discoveries — produces code that does not even compile (duplicate block-scoped declarations across independently-converted files; `typecheck_strict` FAIL at **every** S seed 0/3 and at L), while the durable arm (shared evolving tree) does not (PASS at S, L, XL). Same scope, same model, same tools — only **accumulation** differs. This is the clean, mechanism-grounded divergence.
4. **Resumability is a structural durable edge (H4, measured).** At a hard interrupt after an equal wall budget (~1200s) on jsdom-M(24): the **durable arm preserved 70.8% of work** (17/24 modules as consistent, type-checking committed checkpoints) and **resumed to a full oracle PASS**; the **monolith preserved 0%** — its 24-file partial tree fails the oracle, nothing was committed, and 0 modules are recoverable on restart. The monolith cannot expose intermediate consistent state by construction.
5. **The defensible contribution is narrower than "state beats context":** it is *conflict-free parallel decomposition via accumulated state* + *resumable consistent checkpoints* — dimensions where the monolith is structurally incapable, not merely slower.

## Primary outcome = `typecheck_strict` (static, unconfounded)

The headline correctness axis is **strict static type-checking** (`tsc --noEmit`, no `any`/`@ts-ignore` past budget). It is deterministic, runtime-free, and identical across arms and scales. The runtime test gate (`tests_api`) is valid except on any trial whose scope includes jsdom's codegen-coupled `namespaces.js` helper — there it is **confounded** (see Threats below), so those comparisons are made on `typecheck_strict` + `escape_hatches`, the gates jsdom's build system cannot perturb.

## Outcome table (hardened oracle)

Verdicts on the **primary static axis** (`typecheck_strict` + `escape_hatches`); ✓=clean,
✗=fails. "post-repair" = clean after targeted iterative repair (worker calls / wall noted).

| target | scope | monolith | durable | stateless-RAG |
| --- | --- | --- | --- | --- |
| express | 7   | ✓ | ✓ | ✓ |
| jsdom-S | 8   | ✓ (s1,s2) | ✓ (s1,s2) | ✗ 0/3 (s0,s1,s2 — TS2451 redeclaration) |
| jsdom-M | 24  | ✓ (s1,s2) | ✓ (s1,s2) | ✗ 0/2 (typecheck) |
| jsdom-L | 60  | ✓ | ✓ all seeds (s1: post-repair 2 calls/101s; s2: raw clean) | ✗ 0/3 (TS2451 conflicts) |
| jsdom-XL| 120 | ✓ | ✓ (0 err, 0 hatches, 120 converted)¹ | ✗ (≈290 err; TS2451 ×122 — conflicts grow with scope) |
| jsdom-XXL| 240 | ✓ | — | — |
| jsdom-FULL| 364 | **✗ (16 errors, no repair seam)** | **✓ post-repair (raw 5 → 4 calls/258s → 0)**¹ | — |

¹ Runtime gate (`tests_api`) confounded when scope includes `namespaces.js` (see Threats);
static axis is clean. At FULL the monolith fails the static axis outright (16 errors) and has
no decomposition seam to repair; durable reaches a verified-clean `tsc --strict --noEmit`.

S/M/L durable+monolith passes are honest on *all* gates (conversion-complete, strict typecheck,
full test suite, 0 hatches) except where the `namespaces.js` runtime confound applies, in which
case the static axis is reported. RAG **never** reaches a clean typecheck on jsdom at any scale.

## Mechanism — Discovery Reuse Rate (DRR)

Fraction of dependent in-scope modules whose converted `.ts` consumes a *type*
exported by an already-converted in-scope dependency (a persisted discovery).

| target | monolith | durable | stateless-RAG |
| --- | --- | --- | --- |
| express | 0.75 | 0.75 | 0.25 |
| jsdom-S | 0.17 | 0.00 | 0.00 |

DRR cleanly separates accumulation from retrieve-and-forget on express (ESM, explicit
type imports). On jsdom it is a weak discriminator because jsdom's CommonJS style rarely
annotates with sibling *types* — there the mechanism surfaces as the **failure taxonomy**
(RAG's cross-file declaration conflicts) rather than DRR.

## Validity work (why these numbers are trustworthy)

The pilot exposed and we fixed three oracle-validity bugs before trusting any jsdom number:
- **Hollow passes:** an arm that created `X.ts` but left `X.js` was silently tested on the
  un-migrated JavaScript (Node resolves `.js` first). Now a residual in-scope `.js` is a hard
  FAIL (`conversion_complete` gate) and the harness normalizes every arm's tree before scoring.
- **Subprocess `.ts` loading:** a test that spawns a fresh `node` process couldn't load `.ts`
  and unfairly failed any arm that actually deleted its `.js`. The oracle now propagates the
  tsx loader via `NODE_OPTIONS` to subprocesses.
- **Over-broad hatch counting:** `any`/`@ts-ignore` were counted across all `.ts` under `lib/`,
  including an ambient shim for *out-of-scope generated* code. Now hatches are counted **only in
  the in-scope converted modules** (the actual deliverable). This flipped mono-XXL(240) from a
  false FAIL (20 out-of-scope hatches) to its true PASS (0 in-scope hatches).

Conversions are decoupled from scoring; all trees are re-scored by the current oracle
(`harness/rescore.py` / `harness/ingest.py`) so the dataset is internally consistent.

## Threats to validity (honest)

- **jsdom build-system coupling (scope-correlated, not scale-correlated).** jsdom's
  `npm run prepare` runs `scripts/webidl/convert.js`, which `require()`s
  `lib/jsdom/living/helpers/namespaces.js` **by literal `.js` path**. **Whenever that one
  helper is in a trial's scope** and is converted to `.ts`, jsdom's own IDL codegen fails
  (`Cannot find module namespaces.js`), failing the *runtime* gate `tests_api`. We confirmed
  this is *exactly* correlated with scope membership: L-durable-s2 (namespaces.js ∈ scope) hit
  the confound and L-durable-s1 (namespaces.js ∉ scope) did not — both at the same L(60) scale.
  It is more likely at larger scope only because BFS eventually reaches the helper; it is a
  *scope-membership* artifact, arm-independent, not a scale effect. We therefore treat
  `typecheck_strict` + `escape_hatches` as the primary, build-agnostic outcome and flag
  `tests_api` as confounded on any trial whose scope contains `namespaces.js`. A codegen-baked
  harness (run `prepare` on the pristine tree pre-conversion, exclude `scripts/` and generated
  wrappers from scope) removes the confound; logged as future work rather than silently dropped.
- **Cursor-SDK token counts are unreliable** (implausibly low); we report wall-clock, worker
  count, and DRR instead of token deltas as the cost axis.

## Concurrency ceiling (parallelism soak) — durable's speed is platform-bound, not design-bound

We ran a **replicated concurrency sweep** on durable FULL(364) on the frozen engine to test the
"just send 20× workers" intuition. Every point uses an *identical* protocol — a fixed 240 s
steady-state window, each run contamination-checked (`base_build_start == 1`) and quality-gated
for a complete window (`harness/ksweep_rigorous.py` + `aggregate_sweep.py`). Success = worker
produced a `.ts`. Mean ± 95% CI (Student-t) over **n=5–10 replicates** per point (34 runs
admitted, 1 incomplete-window run rejected):

| concurrency | success (mean ± 95% CI) | n | K_eff | failure mode |
| --- | --- | --- | --- | --- |
| C=8  | 97% ± 5.7% | 10 | 7.8 | occasional non-throttle conversion miss |
| C=12 | 99% ± 2.0% | 5 | 11.9 | — (at the cap) |
| C=16 | 66% ± 2.7% | 5 | 10.5 | fast (<20s) no-diff no-ops |
| C=24 | 28% ± 4.3% | 5 | 6.8 | heavy throttle |
| C=32 | 19% ± 8.1% | 9 | 6.1 | heavy throttle |

Success is high (~97–99%) below ~12 concurrent sessions and **collapses monotonically** through a
sharp knee. Reading the **effective admission cap** off the knee gives **K ≈ 10–12** (C=12
K_eff=11.9, C=16 K_eff=10.5). Above the cap the rate falls *below* a `min(1, K/C)` reference —
retry churn on throttled sessions inflates the denominator, so the collapsed regime is *steeper*
than 1/C (K_eff drops to ~6 at C=24/32). The Cursor API/SDK sustains on the order of ~10
concurrent agent sessions; excess workers are throttled into fast no-edit returns (<20s vs
~90–170s for a real conversion) that the `require_diff` gate rejects and the harness retries.

> **Methodology note / correction.** An earlier *single-run* probe per point (mixed stop-rules)
> showed a spurious non-monotonicity (C=24 appearing below C=32). The replicated, uniform-window
> sweep above eliminated it — the artifact was in the measurement, not the system. This is why
> the curve is now monotone with tight CIs.

This is a **Cursor-platform session cap, not a Puppetmaster orchestration failure** — PM spawned,
leased, and retried every worker correctly, and durable state + retry absorbed the throttle (the
run still converges, just inefficiently, wasting API calls on doomed attempts).

### Second backend confirms the cap is platform-specific (not fundamental)

We reproduced the concurrency probe on a **second serving backend — Claude Code (Anthropic API) —
under the same frozen orchestrator** (`harness/claude_concurrency_probe.py`: launch exactly C
workers simultaneously, each isolated on its own tree and PM state-dir, success = produced the
`.ts`). The cap **vanishes**:

| concurrency | Cursor (success ± 95% CI) | Claude Code (success ± 95% CI) |
| --- | --- | --- |
| C=4  | — | 100% ± 0.0% (n=3) |
| C=8  | 97% ± 5.7% | **100% ± 0.0%** (n=3) |
| C=16 | 66% ± 2.7% | **100% ± 0.0%** (n=3) |
| C=24 | 28% ± 4.3% | **100% ± 0.0%** (n=3) |
| C=32 | 19% ± 8.1% | **100% ± 0.0%** (n=3) |

252 Claude workers, **0 fast-fails**, 2 transient rate-limit messages that still completed after
backoff. Where Cursor collapses (C=16→66%, C=24→28%, C=32→19%), Claude sustains **100% to C=32**
(`figures/concurrency_backends.png`). Same orchestrator, same durable state, different backend → the
admission cap is a property of the **serving platform**, not of durable state or Puppetmaster.
This moves the platform-specificity claim from *argued* to *demonstrated*.

**Implication for the scaling thesis.** The dataflow headroom is real (21.6× at FULL; critical
path = 4.6% of total work) but only ~K≈10 of it is *usable* at once on this platform. At the
practical ceiling, durable wall ≈ work / 10.6 ≈ 1.1 h vs the monolith's 0.83 h → durable is
~1.3× *slower* on wall-clock while being the only arm that reaches a clean strict typecheck at
full-repo scale. So the honest claim is: **durable wins on correctness/capacity and on
*theoretical* parallel headroom that grows with repo size; raw wall-clock parity is gated by the
platform's concurrent-session limit, addressable below — not by durable state's design.**

**Scale-emergent headroom (measured, size-sweep).** Dependency critical path as a fraction of
total work *shrinks* with repo size — 45% (8 mod) → 31% (24) → 20% (60) → **4.6% (364)** — i.e.
the parallelizable fraction climbs ~55% → ~95% as the DAG broadens (max layer width 5 → 78). The
bigger the repo, the more headroom durable exposes; the binding constraint is then how much of it
the platform lets you spend.

### Puppetmaster improvement opportunities surfaced by this soak
1. **Adaptive concurrency / admission control.** PM should cap concurrent dispatch near the
   observed session ceiling instead of launching `max_workers` that mostly fast-fail. A
   token-bucket limiter keyed to backpressure (a burst of <Ns no-diff returns = throttle signal)
   would stop wasting API calls and retries beyond K.
2. **Classify throttle vs. genuine no-op.** A sub-N-second `require_diff` failure should be tagged
   as a *backpressure/throttle* event and re-queued without counting against the task's quality
   budget, rather than treated identically to a real failed conversion.
3. **Dataflow scheduling** (separate lever): release a module the instant its deps are done
   instead of waiting on full-layer barriers. Lowers work-on-critical-path (theoretical floor
   0.55 h < monolith 0.83 h) but does *not* raise the session cap — pairs with (1).

## Third advantage axis: re-queries cost zero tokens (state-as-asset, made literal)

The defining property of an *asset* (vs a *prompt*) is that reuse cost → 0. Durable state has
this property exactly: once a discovery is **materialized as an artifact**, recalling it is a
SQLite read — **zero LLM invocations**. Demonstrated live on the frozen engine: recalling the
full structured result of a completed conversion job (gate verdict, changed files, strict-typecheck
outcome, *and* the provenance that it reused `css-values.ts`) took **<0.5 s and zero LLM calls**
(`puppetmaster artifacts <job_id>`).

**Measured reuse rate (DRR) — the empirical rate of zero-marginal-cost re-queries:**

| scope | DRR | modules reusing a prior materialized artifact |
| --- | --- | --- |
| 8 | 29–57% | 2–4 / 7 |
| 24 | 9–27% | 2–6 / ~23 |
| 60 | 19–28% | 10–16 / ~56 |
| 364 (capstone) | **29%** | **86 / 293** |

Each reuse is a discovery whose derivation was paid **once** and consumed free thereafter.

**Cross-arm cost of a re-query (reported in LLM *invocations*, our reliable metric — sidesteps the
unreliable Cursor-SDK token counts):**

| arm | re-query of a prior discovery | why |
| --- | --- | --- |
| **Durable** | **0 invocations** | artifact read (SQLite SELECT) |
| Transcript | retain in-context (ongoing per-turn cost, window-capped → fails at scale) **or** ≥1 (re-derive) | discoveries are transient text |
| RAG | ≥1 invocation every time | re-retrieve + re-reason; nothing accumulates |

This is a **third independent advantage**, alongside (a) correctness/capacity at full-repo scale
and (b) resumability (H4) — all three flow from the same root: *discoveries are durable system
objects, not disposable prompt text*. DB analogy: a converted `.ts` + its exported types is a
**materialized view** over a module; consumers `SELECT` it instead of recomputing.

**Honest boundary.** "Zero" is for *recall* of an already-materialized result. A re-query that
needs genuinely new synthesis still pays the synthesis — but it starts from the materialized
artifact (no re-derivation of the base discovery), so it is strictly cheaper than transcript
(must re-derive) or RAG (must re-retrieve + re-reason). Storage is bytes (our `state.sqlite` is
~1 GB), not tokens.

## Open / in progress (overnight)

- **Capstone:** durable at FULL jsdom (364) — does decompose+accumulate+iterative-repair reach a
  clean `typecheck_strict` where the one-shot monolith leaves 16 errors? (running)
- Multi-seed S/M durable-vs-RAG for error bars on the core divergence. (running: M seed 2)
- **Resumability (H4): DONE** — durable 70.8% preserved + resumed→PASS vs monolith 0%
  (`results/resume_exp.jsonl`, `figures/resumability.png`).
- Failure taxonomy + figures from `harness/analyze.py`. (`figures/`: drr_vs_scope,
  monolith_scaling, resumability)
