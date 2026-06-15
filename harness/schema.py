#!/usr/bin/env python3
"""Trial record schema for the durable-state vs. transcript study.

One ``TrialRecord`` is the atomic unit of evidence: a single (target, stratum,
arm, seed) run, its machine-checked oracle verdict, and the cost it took to get
there. Records are append-only JSON lines so a partially-completed sweep is never
lost and analysis can stream them. Keeping this in one tiny, dependency-free
module means the harness, the arm runners, and the analysis all agree on the
shape of a result.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class ArmStep:
    """One bounded agent invocation within an arm (a single worker/job)."""
    label: str
    job_id: Optional[str] = None
    scope: list[str] = field(default_factory=list)
    status: str = "pending"
    duration_s: Optional[float] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    model: Optional[str] = None
    note: str = ""


@dataclass
class TrialRecord:
    trial_id: str
    target: str
    stratum: str
    # State-architecture spectrum (§3.3): the treatment is "durable"; controls are
    # "monolith" (C1), "monolith_compaction" (C2), and "stateless_rag" (R, the
    # anti-RAG control: same decomposition + retrieval, no cross-worker persistence).
    arm: str                      # "durable" | "monolith" | "monolith_compaction" | "stateless_rag"
    seed: int
    scope_size: int
    scope: list[str] = field(default_factory=list)
    model: Optional[str] = None

    # Outcome (filled by the oracle)
    oracle_ok: Optional[bool] = None
    gates_passed: Optional[bool] = None
    escape_hatches: Optional[int] = None
    oracle_verdict: dict[str, Any] = field(default_factory=dict)

    # Cost / process
    steps: list[ArmStep] = field(default_factory=list)
    wall_clock_s: Optional[float] = None
    total_tokens_in: Optional[int] = None
    total_tokens_out: Optional[int] = None
    total_cost_usd: Optional[float] = None
    n_worker_invocations: Optional[int] = None
    peak_scope_in_one_context: Optional[int] = None   # H1 bridge metric: max modules a single agent had to hold at once

    # Discovery Reuse Rate (§7, the H2 mechanism). DRR = fraction of converted
    # modules whose produced .ts consumes a type/interface exported by an
    # earlier-converted in-scope dependency (a *persisted discovery*), vs merely
    # re-derivable from the original index. For R this is reuse that is
    # architecturally unavailable except via re-derivation; for T it is explicit.
    discovery_reuse_rate: Optional[float] = None
    n_modules_consuming_prior_artifact: Optional[int] = None
    n_modules_with_inscope_dep: Optional[int] = None   # denominator: modules that *could* reuse
    reuse_by_dependency_depth: dict[str, int] = field(default_factory=dict)

    # Failure taxonomy (filled by analysis): context_overflow | regression |
    # escape_hatch_cheat | incomplete | infra | none
    failure_mode: Optional[str] = None

    status: str = "created"       # created | running | scored | error
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    finished_at: Optional[str] = None
    error: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "TrialRecord":
        data = json.loads(line)
        steps = [ArmStep(**s) for s in data.pop("steps", [])]
        rec = cls(**{k: v for k, v in data.items() if k in {f.name for f in dataclasses.fields(cls)}})
        rec.steps = steps
        return rec


def append_record(path: Path, rec: TrialRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(rec.to_json() + "\n")


def load_records(path: Path) -> list[TrialRecord]:
    if not path.is_file():
        return []
    return [TrialRecord.from_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
