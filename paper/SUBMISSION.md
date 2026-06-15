# Submission guide — arXiv → Hugging Face Papers

This is the exact, foolproof path to get this paper onto arXiv and then onto the
Hugging Face Papers feed. HF Papers is built **on top of** arXiv — it only indexes
papers that already have an arXiv ID — so **arXiv is the gate**.

## 0. What's in this folder
- `paper.md` — source of truth (Markdown); embeds all 5 figures from `../figures/`.
- `paper.tex` — standalone LaTeX (pandoc `-s`, arxiv-header inlined, 5 figures embedded).
- `paper.pdf` — built PDF, 12 pages, 5 figures (fallback: arXiv accepts PDF-only submissions).
- `arxiv-header.tex` — LaTeX preamble (unicode glyph mapping); inlined into `paper.tex` and `build.sh`.
- `build.sh` — reproduces `paper.pdf` (`brew install pandoc tectonic` first).
- `make_arxiv.sh` — **produces the self-contained `arxiv-submission.tar.gz`** (rewrites figure
  paths to a flat `figures/` subdir and verifies it compiles standalone). **This is the arXiv upload.**

## 1. arXiv metadata (copy-paste)

**Title:** State, Not Tokens: Repository-Scale Agent Reasoning Is Bound by State Architecture

**Authors:** *(arXiv requires real author names — pseudonyms violate policy. Set your real name; GitHub/HF handle is `professorpalmer`.)*

**Primary category:** `cs.SE` (Software Engineering)
**Cross-list:** `cs.AI`, `cs.LG`

**Comments field:** `12 pages, 5 figures. Code, data, and reproduction harness: https://github.com/professorpalmer/durable-state-vs-context ; trial records + concurrency sweeps: https://huggingface.co/datasets/CaryPalmer/durable-vs-context-trials`

**License (recommend):** CC BY 4.0 — maximizes HF visibility and reuse.

**Abstract (arXiv field; ~1900 chars, fits the form):**

> The agent community has largely treated repository-scale forgetting as a context-window
> problem: bigger windows (8k to 1M) are expected to yield better whole-repo reasoning. We
> argue this is a misdiagnosis. Using a hard, machine-checkable task — strict
> JS-to-TS migration of real OSS repositories under an unforgeable oracle
> (strict tsc, immutable tests, mandatory .js-to-.ts replacement, zero escape-hatches) — we
> vary a single axis: how state flows between bounded workers. Three arms hold model, tools,
> and oracle constant: a single-context monolith; a durable arm that accumulates each completed
> dependency layer as a committed artifact on a shared evolving tree; and a stateless-RAG arm
> whose per-file workers retrieve context but never see each other's results. We find: (1) a
> single modern agentic worker already scales far past the naive context thesis — cleanly
> migrating up to 240 interdependent modules by navigating the filesystem on demand — but it
> cracks at the full 364-module tree by capacity (residual strict-type errors), not window
> overflow; (2) when work is decomposed for parallelism, durable
> accumulation strictly dominates stateless retrieval — RAG's independent workers emit code
> that does not compile (TS2451 redeclaration conflicts appear only in RAG); and (3) durable
> state confers two properties no single transcript can: interruption-resumable consistent
> checkpoints, and zero-cost re-query of a materialized discovery (a database read, not
> an LLM call). The three fail distinctly: RAG by conflict, monolith by capacity, durable by
> neither. Finally, the only cap on realized parallel speed is the platform,
> not state: a replicated sweep caps usable Cursor concurrency at K~10-12 sessions, yet the same
> orchestrator on a second backend (Claude Code) sustains 100% at 32-way — the cap is
> platform-specific, not architectural. The contribution is a reframing: state is an asset, not
> a prompt.

## 2. Submit to arXiv
1. Create / log in at https://arxiv.org with the email you'll use for HF claiming (**use the same email**).
2. **Endorsement caveat (honest):** a first-ever submission to `cs.*` usually needs an
   *endorsement* from an established author, or it goes through moderation. This is the one
   step neither of us can shortcut — budget a few days. (Some accounts are auto-endorsed; you
   may get lucky.)
3. New submission → run `bash paper/make_arxiv.sh` and upload the resulting
   `paper/arxiv-submission.tar.gz` (it bundles `paper.tex` + the 5 figures in a flat `figures/`
   subdir, and is verified to compile standalone with tectonic = 12 pages). If AutoTeX fails,
   fall back to uploading `paper.pdf` (PDF-only is allowed).
4. Fill the metadata from §1. Submit. You'll get an arXiv ID like `2606.XXXXX`.

## 3. Index on Hugging Face Papers (trivial once on arXiv)
1. Visit `https://huggingface.co/papers/{arxiv-id}` (or `https://huggingface.co/papers/submit`).
   If not yet indexed, the page offers to index it → it lands on the Daily Papers feed.
2. **Claim authorship:** click your name in the author list → "claim authorship". The Hub
   auto-matches on email (must match arXiv); an admin verifies (can take a few days).
3. **Link artifacts** (this is what makes the page look credible and rank): add the arXiv/HF
   paper URL to the GitHub README and to the live HF dataset
   (`https://huggingface.co/datasets/CaryPalmer/durable-vs-context-trials`) so they back-link to
   the paper page. The repo README and dataset card already link *out* to each other.

## 4. Honest reality check on "getting on the feed"
Indexing is automatic; *trending* on Daily Papers depends on community upvotes, which we can't
manufacture. What we control — and have — is a real arXiv paper, a public reproduction repo, a
linked dataset, honest framing, and an unforgeable oracle. That's the credible-submission bar.
