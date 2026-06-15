#!/usr/bin/env python3
"""Discovery Reuse Rate (DRR) — the mechanism metric for the T-vs-R contrast.

A converted module "reuses a discovery" when its produced ``.ts`` imports a
*type* (interface / type alias / class / enum / `import type`) that an
**earlier-converted in-scope dependency** exports — i.e. it consumes a persisted
artifact rather than re-deriving the dependency's shape. We measure this purely
statically from the final tree plus the dependency layering, so it is identical
and unforgeable across arms.

Interpretation (no overclaim): in the durable arm reuse is *explicit* (the dep's
types exist in the shared tree to import); in the stateless-RAG arm reuse is
architecturally available only by re-derivation from the original index, so we
expect DRR to stay low/flat there and rise with dependency depth in durable.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# `export interface X` / `export type X` / `export class X` / `export enum X`
_EXPORT_TYPE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:abstract\s+)?(?:interface|type|enum|class)\s+([A-Za-z0-9_]+)"
)
# `export type { A, B }` re-exports
_EXPORT_TYPE_BLOCK = re.compile(r"\bexport\s+type\s*\{([^}]*)\}")
# ESM import statements (single or namespace/default/named clause) with a source
_IMPORT = re.compile(r"\bimport\s+(type\s+)?([^;'\"]+?)\s+from\s+['\"]([^'\"]+)['\"]", re.DOTALL)
# TypeScript import-equals form: `import X = require('./dep')`
_IMPORT_EQ = re.compile(r"\bimport\s+([A-Za-z0-9_$]+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)")
# CommonJS form jsdom favours: `const X = require('./dep')` / `const { a, b } = require('./dep')`
_REQUIRE_CONST = re.compile(
    r"\b(?:const|let|var)\s+(\{[^}]*\}|[A-Za-z0-9_$]+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _stem(path: str) -> str:
    for ext in (".ts", ".js", ".cjs", ".mjs"):
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def _exported_types(ts_path: Path) -> set[str]:
    if not ts_path.is_file():
        return set()
    text = ts_path.read_text(encoding="utf-8", errors="replace")
    names = set(_EXPORT_TYPE.findall(text))
    for block in _EXPORT_TYPE_BLOCK.findall(text):
        names.update(n.strip().split(" as ")[0].strip() for n in block.split(",") if n.strip())
    return names


class _Import:
    __slots__ = ("is_type", "names", "locals", "source")

    def __init__(self, is_type: bool, names: set[str], locals_: set[str], source: str):
        self.is_type = is_type        # whole-statement `import type ...`
        self.names = names            # original exported names (for type-name matching)
        self.locals = locals_         # local bindings (default/namespace/named alias)
        self.source = source


def _parse_clause(clause: str) -> tuple[set[str], set[str]]:
    """Return (exported_names, local_bindings) for an import clause."""
    names: set[str] = set()
    locals_: set[str] = set()
    block = re.search(r"\{([^}]*)\}", clause)
    if block:
        for part in block.group(1).split(","):
            part = re.sub(r"^\s*type\s+", "", part.strip())   # inline `{ type X }`
            if not part:
                continue
            orig, _, alias = part.partition(" as ")
            names.add(orig.strip())
            locals_.add((alias or orig).strip())
    # default / namespace bindings live outside the braces
    head = clause[: block.start()] if block else clause
    for tok in head.replace("*", " ").split(","):
        tok = re.sub(r"^\s*as\s+", "", tok.strip())
        if re.fullmatch(r"[A-Za-z0-9_$]+", tok):
            locals_.add(tok)
    return names, locals_


def _imports(ts_path: Path) -> list[_Import]:
    text = ts_path.read_text(encoding="utf-8", errors="replace")
    out: list[_Import] = []
    for is_type, clause, source in _IMPORT.findall(text):
        names, locals_ = _parse_clause(clause)
        out.append(_Import(bool(is_type.strip()), names, locals_, source))
    for local, source in _IMPORT_EQ.findall(text):
        out.append(_Import(False, set(), {local}, source))
    for binding, source in _REQUIRE_CONST.findall(text):
        names, locals_ = _parse_clause(binding) if binding.startswith("{") else (set(), {binding})
        out.append(_Import(False, names, locals_, source))
    return out


def _used_as_type(name: str, text: str) -> bool:
    """Heuristic: is `name` referenced in a type position (annotation, generic
    argument, `as`, extends/implements, union/intersection, `typeof`)?"""
    n = re.escape(name)
    patterns = (
        rf":\s*{n}\b", rf"<\s*{n}\b", rf"\bas\s+{n}\b", rf"\bextends\s+{n}\b",
        rf"\bimplements\s+{n}\b", rf"\|\s*{n}\b", rf"&\s*{n}\b", rf"\btypeof\s+{n}\b",
    )
    return any(re.search(p, text) for p in patterns)


def _resolves_to(importer_rel: str, source: str, dep_rel: str) -> bool:
    if not source.startswith("."):
        return False
    importer_dir = Path(importer_rel).parent
    resolved = (importer_dir / source).as_posix()
    return _stem(resolved) == _stem(dep_rel)


def discovery_reuse(tree: Path, sel: dict[str, Any]) -> dict[str, Any]:
    direct: dict[str, list[str]] = sel["direct_deps"]
    layers: list[list[str]] = sel["layers"]
    depth_of = {f: i for i, layer in enumerate(layers) for f in layer}

    numerator = 0
    denominator = 0
    by_depth: dict[str, int] = {}
    per_file: dict[str, bool] = {}

    # Pre-compute each dependency's exported type names from its produced .ts.
    exported: dict[str, set[str]] = {}
    for f, deps in direct.items():
        for d in deps:
            if d not in exported:
                exported[d] = _exported_types(tree / (_stem(d) + ".ts"))

    for f, deps in direct.items():
        inscope_deps = [d for d in deps if d != f]
        if not inscope_deps:
            continue
        denominator += 1
        f_ts = tree / (_stem(f) + ".ts")
        reused = False
        if f_ts.is_file():
            text = f_ts.read_text(encoding="utf-8", errors="replace")
            imports = _imports(f_ts)
            for d in inscope_deps:
                for imp in imports:
                    if not _resolves_to(f, imp.source, d):
                        continue
                    if imp.is_type or (imp.names & exported.get(d, set())) or \
                            any(_used_as_type(loc, text) for loc in imp.locals):
                        reused = True
                        break
                if reused:
                    break
        per_file[f] = reused
        if reused:
            numerator += 1
            k = str(depth_of.get(f, -1))
            by_depth[k] = by_depth.get(k, 0) + 1

    rate = (numerator / denominator) if denominator else None
    return {
        "rate": round(rate, 4) if rate is not None else None,
        "numerator": numerator,
        "denominator": denominator,
        "by_depth": by_depth,
        "per_file": per_file,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compute Discovery Reuse Rate for a scored tree.")
    ap.add_argument("--tree", required=True, help="the converted result tree")
    ap.add_argument("--scope", required=True, help="scope.json from select_scope")
    args = ap.parse_args(argv)
    sel = json.loads(Path(args.scope).read_text())
    print(json.dumps(discovery_reuse(Path(args.tree).resolve(), sel), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
