#!/usr/bin/env python3
"""Deterministic intra-scope dependency DAG for a migration scope.

The durable-state arm converts modules in dependency order, giving each bounded
worker only its own module plus the *already-converted* signatures of its direct
dependencies. That ordering is the heart of the treatment, so it must be
deterministic, transparent, and independent of any model — hence a plain
import/require parse over the in-scope source, not an LLM and not an opaque graph
export. (CodeGraph is used elsewhere for context injection and the audit; here we
want a reproducible topological layering anyone can re-derive.)

Output: layers of files. Files in the same layer have no unconverted in-scope
dependency on each other, so the durable arm can convert a whole layer in
parallel. Cycles (jsdom has them) are collapsed into a single co-converted layer
so the harness never deadlocks.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

# require('./x') / require("../y") and ESM import/export ... from './x'
_REQUIRE = re.compile(r"""require\(\s*['"](\.[^'"]+)['"]\s*\)""")
_ESM_FROM = re.compile(r"""\bfrom\s+['"](\.[^'"]+)['"]""")
_BARE_IMPORT = re.compile(r"""\bimport\s+['"](\.[^'"]+)['"]""")
_DYNAMIC = re.compile(r"""import\(\s*['"](\.[^'"]+)['"]\s*\)""")

_RESOLVE_SUFFIXES = ["", ".js", ".ts", ".cjs", ".mjs", "/index.js", "/index.ts", "/index.cjs"]


def _norm(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def _resolve(repo: Path, importer: str, spec: str, scope: set[str]) -> Optional[str]:
    base = (repo / importer).resolve().parent
    target = (base / spec)
    for suffix in _RESOLVE_SUFFIXES:
        candidate = Path(str(target) + suffix)
        if candidate.is_file():
            try:
                rel = _norm(repo, candidate)
            except ValueError:
                return None
            return rel if rel in scope else None
    return None


def parse_deps(repo: Path, rel_file: str, scope: set[str]) -> set[str]:
    text = (repo / rel_file).read_text(encoding="utf-8", errors="replace")
    specs: set[str] = set()
    for pat in (_REQUIRE, _ESM_FROM, _BARE_IMPORT, _DYNAMIC):
        specs.update(pat.findall(text))
    deps: set[str] = set()
    for spec in specs:
        resolved = _resolve(repo, rel_file, spec, scope)
        if resolved and resolved != rel_file:
            deps.add(resolved)
    return deps


def build_graph(repo: Path, scope: Iterable[str]) -> dict[str, set[str]]:
    scope_set = set(scope)
    return {f: parse_deps(repo, f, scope_set) for f in sorted(scope_set)}


def topo_layers(graph: dict[str, set[str]]) -> list[list[str]]:
    """Kahn-style layering; collapse any residual cycle into one co-converted layer."""
    remaining = {n: set(d & graph.keys()) for n, d in graph.items()}
    layers: list[list[str]] = []
    done: set[str] = set()
    while remaining:
        ready = sorted(n for n, deps in remaining.items() if deps <= done)
        if not ready:
            # Cycle: emit everything left as one layer (deterministic by name).
            ready = sorted(remaining)
        layers.append(ready)
        done.update(ready)
        for n in ready:
            remaining.pop(n, None)
    return layers


def analyze(repo: Path, scope: list[str]) -> dict:
    graph = build_graph(repo, scope)
    layers = topo_layers(graph)
    edge_count = sum(len(d) for d in graph.values())
    return {
        "repo": str(repo),
        "scope_size": len(scope),
        "edges": edge_count,
        "layers": layers,
        "n_layers": len(layers),
        "max_layer_width": max((len(l) for l in layers), default=0),
        "direct_deps": {f: sorted(d) for f, d in graph.items()},
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the intra-scope conversion DAG.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--scope-file", help="newline-delimited repo-relative paths; '-' for stdin")
    ap.add_argument("--scope", nargs="*", default=[], help="explicit repo-relative paths")
    args = ap.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    scope = list(args.scope)
    if args.scope_file:
        raw = sys.stdin.read() if args.scope_file == "-" else Path(args.scope_file).read_text(encoding="utf-8")
        scope += [l.strip() for l in raw.splitlines() if l.strip()]
    scope = sorted(set(scope))
    if not scope:
        print(json.dumps({"error": "empty scope"}))
        return 2
    print(json.dumps(analyze(repo, scope), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
