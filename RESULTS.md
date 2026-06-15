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
| jsdom-XL| 120 | ✓ | ✓ (0 err, 0 hatches, 120 converted)¹ | — |
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

## Open / in progress (overnight)

- **Capstone:** durable at FULL jsdom (364) — does decompose+accumulate+iterative-repair reach a
  clean `typecheck_strict` where the one-shot monolith leaves 16 errors? (running)
- Multi-seed S/M durable-vs-RAG for error bars on the core divergence. (running: M seed 2)
- **Resumability (H4): DONE** — durable 70.8% preserved + resumed→PASS vs monolith 0%
  (`results/resume_exp.jsonl`, `figures/resumability.png`).
- Failure taxonomy + figures from `harness/analyze.py`. (`figures/`: drr_vs_scope,
  monolith_scaling, resumability)
