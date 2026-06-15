# Canonical task: strict JS → TypeScript migration

This is the **identical** task description handed to every arm (durable-state,
transcript, transcript+compaction). Only the *orchestration/state architecture*
differs between arms; the goal, constraints, and grading are constant. This file
is the single source of truth so the comparison is fair and the run is
reproducible.

## Objective

Migrate the in-scope JavaScript source modules of this repository to TypeScript
such that the project type-checks under `--strict`, the existing test suite
stays green, and no type-system escape hatches are introduced.

## In scope

Only the source modules listed in the trial's `SCOPE` (passed at runtime).
Convert each `*.js` in scope to `*.ts`. Do **not** modify the test suite's
assertions, weaken `tsconfig.json`, or edit files outside the scope except:

- `package.json` `main` may be pointed at the converted entry (`index.ts`).
- New `*.d.ts` declaration files may be added if genuinely needed.

## Hard constraints (these are graded by a machine oracle)

1. **Strict typecheck passes**: `npx tsc --strict --noEmit` exits 0.
2. **Tests pass**: `npm test` exits 0 (same suite, same assertions).
3. **Build passes** if a build script exists.
4. **Zero escape hatches** in converted source. Forbidden: `: any`, `as any`,
   `<any>`, `any[]`, `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`. Model real
   types (interfaces, generics, unions, `unknown` + narrowing) instead.
5. Do **not** weaken the provided `tsconfig.json`. The oracle passes `--strict`
   on the command line regardless of the file, so weakening it cannot help.

## Setup note for the worker

Dependencies may not be installed in this working tree. Run `npm install`
once before type-checking or testing.

## Definition of done

`node research/.../oracle/run_oracle.py --repo . --spec .lwds/oracle_spec.json`
returns `ok: true`. Aim for faithful, idiomatic types — the residual
escape-hatch count is recorded as a quality metric even when zero.
