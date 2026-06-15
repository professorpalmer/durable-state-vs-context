# Results — honest running summary

_Last updated: 2026-06-15. Single seed unless noted; statistics in progress._

## Headline (calibrated)

1. **The naive "single context breaks at repository scale" thesis did NOT hold up to 120 modules.** A single agentic worker (`monolith`) cleanly migrated **120 interdependent jsdom modules** (strict typecheck + 574 tests + 0 hatches). Reason: a modern agentic coder *navigates the filesystem on demand* — it is already a reasoner over external state, not a prompt that crams the working set. "Context length" was not the binding constraint.
2. **Durable accumulation beats stateless retrieval (RAG).** When work IS decomposed into parallel workers, the stateless-RAG arm — whose workers cannot see each other's discoveries — produces code that does not even compile (e.g. duplicate block-scoped declarations across independently-converted files), while the durable arm (shared evolving tree) does not. This is the one clean, mechanism-grounded divergence so far.
3. **The defensible contribution is narrower than "state beats context":** it is *conflict-free parallel decomposition via accumulated state* + (to be measured) *resumability and zero-re-derivation follow-ups* — dimensions where the monolith is structurally incapable, not merely slower.

## Outcome table (hardened oracle, single seed)

| target | scope | monolith | durable | stateless-RAG |
| --- | --- | --- | --- | --- |
| express | 7   | PASS | PASS | PASS |
| jsdom-S | 8   | PASS | PASS | **FAIL** (TS2451 redeclaration) |
| jsdom-L | 60  | PASS | _re-scoring_ | _re-scoring_ |
| jsdom-XL| 120 | **PASS** | _crawling_ | — |

All passes are honest: conversion-complete, strict typecheck, full test suite, 0 escape hatches.

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

The pilot exposed and we fixed two oracle-validity bugs before trusting any jsdom number:
- **Hollow passes:** an arm that created `X.ts` but left `X.js` was silently tested on the
  un-migrated JavaScript (Node resolves `.js` first). Now a residual in-scope `.js` is a hard
  FAIL (`conversion_complete` gate) and the harness normalizes every arm's tree before scoring.
- **Subprocess `.ts` loading:** a test that spawns a fresh `node` process couldn't load `.ts`
  and unfairly failed any arm that actually deleted its `.js`. The oracle now propagates the
  tsx loader via `NODE_OPTIONS` to subprocesses.

Conversions are decoupled from scoring; all trees are re-scored by the current oracle
(`harness/rescore.py`) so the dataset is internally consistent.

## Open / in progress

- Complete L/XL durable + L RAG; multi-seed S/M/L for statistics.
- **Breaking-point search:** does the monolith EVER break (full jsdom 364; a 2nd large repo)?
- **Resumability (H4):** interrupt monolith vs durable mid-run; the monolith loses a ~46-min
  single shot, durable resumes from committed layers. (Structural durable edge.)
- **Follow-up (H5):** extend an existing conversion; measure re-derivation cost.
