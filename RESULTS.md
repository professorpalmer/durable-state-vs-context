# Results — honest running summary

_Last updated: 2026-06-15 (overnight autonomous run). Multi-seed at S/M; single seed at L+ unless noted._

## Headline (calibrated)

1. **The naive "single context breaks at repository scale" thesis did NOT hold up to 240 modules.** A single agentic worker (`monolith`) cleanly migrated **240 interdependent jsdom modules** (strict typecheck + tests + 0 hatches). Reason: a modern agentic coder *navigates the filesystem on demand* — it is already a reasoner over external state, not a prompt that crams the working set. "Context length" was not the binding constraint at moderate scale.
2. **But the single context DOES crack at full-repository scale.** At the **full jsdom `lib/` tree (364 modules)** the monolith converts everything and gets tests green with 0 hatches, but **fails `typecheck_strict` with 16 residual errors** (TS2571 "object is of type 'unknown'", TS2322 assignability), concentrated in the hardest module (`XMLHttpRequest-impl`). It used the *safe* escape (`unknown`, not `any`) but ran out of capacity to globally narrow types. This is the first honest degradation of the one-shot architecture — a quality (type-correctness) failure, not a conversion failure.
3. **Durable accumulation beats stateless retrieval (RAG).** When work IS decomposed into parallel workers, the stateless-RAG arm — whose workers cannot see each other's discoveries — produces code that does not even compile (duplicate block-scoped declarations across independently-converted files; `typecheck_strict` FAIL at **every** S seed 0/3 and at L), while the durable arm (shared evolving tree) does not (PASS at S, L, XL). Same scope, same model, same tools — only **accumulation** differs. This is the clean, mechanism-grounded divergence.
4. **The defensible contribution is narrower than "state beats context":** it is *conflict-free parallel decomposition via accumulated state* + *resumability and targeted recovery* — dimensions where the monolith is structurally incapable, not merely slower.

## Primary outcome = `typecheck_strict` (static, unconfounded)

The headline correctness axis is **strict static type-checking** (`tsc --noEmit`, no `any`/`@ts-ignore` past budget). It is deterministic, runtime-free, and identical across arms and scales. The runtime test gate (`tests_api`) is valid at S/M/L but becomes **confounded at XL+ jsdom scope** — see Threats below — so XL/FULL comparisons are made on `typecheck_strict` + `escape_hatches`, the gates jsdom's build system cannot perturb.

## Outcome table (hardened oracle)

| target | scope | monolith | durable | stateless-RAG |
| --- | --- | --- | --- | --- |
| express | 7   | PASS | PASS | PASS |
| jsdom-S | 8   | PASS (s1,s2) | PASS (s1,s2) | **FAIL** (s0,s1,s2 — TS2451 redeclaration) |
| jsdom-M | 24  | PASS (s1) | _running_ | _queued_ |
| jsdom-L | 60  | PASS | PASS | **FAIL** (typecheck) |
| jsdom-XL| 120 | PASS | **PASS** typecheck (0 err); runtime confounded¹ | — |
| jsdom-XXL| 240 | PASS | — | — |
| jsdom-FULL| 364 | **FAIL** typecheck (16 err) | _capstone running_ | — |

¹ XL durable: `tsc --noEmit` clean (0 errors), 0 hatches, all 120 converted. `conversion_complete`/`tests_api` fail only because converting jsdom's `living/helpers/namespaces.js` breaks jsdom's own `prepare:convert-idl` codegen, which `require()`s that file by literal `.js` path. Build-system coupling artifact, not a durable-arm defect (see Threats).

S/M/L passes are honest on *all* gates: conversion-complete, strict typecheck, full test suite, 0 escape hatches.

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

- **jsdom build-system coupling at XL+ scope.** jsdom's `npm run prepare` runs
  `scripts/webidl/convert.js`, which `require()`s `lib/jsdom/living/helpers/namespaces.js`
  **by literal `.js` path**. Once that helper enters scope and is converted to `.ts`, jsdom's own
  IDL codegen fails (`Cannot find module namespaces.js`), which fails the *runtime* gates
  (`tests_api`, and a stale-`.js` reappearance hitting `conversion_complete`). This affects
  XL(120)+ scopes of jsdom for any arm; the monolith happened to be scored against an
  already-codegen'd tree. We therefore treat `typecheck_strict` + `escape_hatches` as the
  primary, build-agnostic outcome at XL+ and flag the runtime gate as confounded there. A
  codegen-baked harness (run `prepare` on the pristine tree pre-conversion, exclude `scripts/`
  and generated wrappers from scope) would remove the confound; logged as future work rather
  than silently dropped.
- **Cursor-SDK token counts are unreliable** (implausibly low); we report wall-clock, worker
  count, and DRR instead of token deltas as the cost axis.

## Open / in progress (overnight)

- **Capstone:** durable at FULL jsdom (364) — does decompose+accumulate+iterative-repair reach a
  clean `typecheck_strict` where the one-shot monolith leaves 16 errors? (running)
- Multi-seed S/M durable-vs-RAG for error bars on the core divergence. (running)
- **Resumability (H4):** interrupt monolith vs durable mid-run; the monolith loses its single
  shot, durable resumes from committed layers. (running)
- Failure taxonomy + figures from `harness/analyze.py`.
