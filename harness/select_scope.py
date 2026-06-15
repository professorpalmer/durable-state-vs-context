#!/usr/bin/env python3
"""Select a coherent, size-controlled migration scope from a target repo.

The size-sweep's independent variable is *how much interrelated code an agent
must reason about at once*. To vary that cleanly we grow a connected
neighborhood of the dependency graph around an anchor module via breadth-first
search (following both dependencies and dependents), so a size-N scope is a real
interlocking subsystem rather than N unrelated files. Selection is fully
deterministic given (repo, profile, size, seed) so every arm sees the identical
scope and the run is reproducible.

Candidate modules are restricted to git-*tracked* source (hand-written), which
elegantly excludes generated files (webidl wrappers, etc.) that are untracked
after the codegen step and must not be migrated.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dag import build_graph, topo_layers  # noqa: E402


def _tracked_source(repo: Path, globs: list[str]) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", *globs], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout
    return sorted(l.strip() for l in out.splitlines() if l.strip())


def _degree(graph: dict[str, set[str]]) -> dict[str, int]:
    deg = {n: len(d) for n, d in graph.items()}
    for n, deps in graph.items():
        for d in deps:
            deg[d] = deg.get(d, 0) + 1
    return deg


def select(repo: Path, profile: dict, size: int, seed: int = 0, anchor: Optional[str] = None) -> dict:
    globs = profile["source_globs"]
    pool = _tracked_source(repo, globs) if profile.get("source_tracked_only") else sorted(
        p.relative_to(repo).as_posix() for g in globs for p in repo.glob(g) if p.is_file()
    )
    pool_set = set(pool)
    graph = build_graph(repo, pool)                 # full-source dependency graph
    rev: dict[str, set[str]] = {n: set() for n in pool}
    for n, deps in graph.items():
        for d in deps:
            rev.setdefault(d, set()).add(n)

    deg = _degree(graph)
    if anchor is None:
        anchor = profile.get("scope_anchor")
    # Seed varies the anchor deterministically across the highest-degree hubs,
    # giving distinct-but-reproducible subsystems for replication seeds.
    if seed > 0 or anchor not in pool_set:
        hubs = sorted(pool, key=lambda f: (-deg.get(f, 0), f))
        anchor = hubs[seed % len(hubs)]

    visited: list[str] = []
    seen: set[str] = set()
    q: deque[str] = deque([anchor])
    seen.add(anchor)
    while q and len(visited) < size:
        node = q.popleft()
        visited.append(node)
        neighbors = sorted((graph.get(node, set()) | rev.get(node, set())) & pool_set)
        for nb in neighbors:
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    if len(visited) < size:  # disconnected: top up by degree then name
        for f in sorted(pool, key=lambda x: (-deg.get(x, 0), x)):
            if f not in seen:
                visited.append(f)
                seen.add(f)
            if len(visited) >= size:
                break

    scope = sorted(visited[:size])
    sub = {f: sorted(d & set(scope)) for f, d in build_graph(repo, scope).items()}
    layers = topo_layers(sub)
    return {
        "target": profile["name"],
        "anchor": anchor,
        "seed": seed,
        "requested_size": size,
        "scope_size": len(scope),
        "scope": scope,
        "intra_scope_edges": sum(len(d) for d in sub.values()),
        "layers": layers,
        "n_layers": len(layers),
        "max_layer_width": max((len(l) for l in layers), default=0),
        "direct_deps": sub,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Select a size-controlled migration scope.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--anchor", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    result = select(repo, profile, args.size, args.seed, args.anchor)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.out:
        Path(args.out).expanduser().write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
