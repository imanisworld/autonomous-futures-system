"""Frozen equal-time treatments for the MNQ account-admission overlay.

Research only. This module does not score P&L, does not import runtime code,
and does not change frozen capacity arbitration. The default overlay stays
strict-before. A caller opts into the frozen pairs below.

The 45 ``EXIT_BEFORE_CANDIDATE_PROVEN`` pairs are the Asia same-bar cases in
``research/artifacts/pr915-reachable-order-audit-5a9f14b.json``. The two
``DISTINCT_REQUEST_ORDER_UNKNOWN`` pairs are the unresolved cross-timeframe
payloads. Primary mode keeps those two busy. The sensitivity mode treats only
those two as exit-first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

EXIT_BEFORE_CANDIDATE_PROVEN = "EXIT_BEFORE_CANDIDATE_PROVEN"
DISTINCT_REQUEST_ORDER_UNKNOWN = "DISTINCT_REQUEST_ORDER_UNKNOWN"
UNKNOWN_ORDER_BUSY_FIRST = "UNKNOWN_ORDER_BUSY_FIRST"
UNKNOWN_ORDER_EXIT_FIRST = "UNKNOWN_ORDER_EXIT_FIRST"

MODE_PRIMARY = "primary"
MODE_SENSITIVITY = "unknown_order_exit_first"

EXIT_FIRST_TREATMENTS = frozenset(
    {
        EXIT_BEFORE_CANDIDATE_PROVEN,
        UNKNOWN_ORDER_EXIT_FIRST,
    }
)

_AUDIT_CLASSIFICATIONS = frozenset(
    {
        EXIT_BEFORE_CANDIDATE_PROVEN,
        DISTINCT_REQUEST_ORDER_UNKNOWN,
    }
)

FROZEN_PROVEN_COUNT = 45
FROZEN_UNKNOWN_COUNT = 2

_ROOT = Path(__file__).resolve().parents[1]
FROZEN_AUDIT_PATH = _ROOT / "research/artifacts/pr915-reachable-order-audit-5a9f14b.json"


class EqualTimeOrderError(ValueError):
    """Fail-closed rejection of an equal-time override map."""


@dataclass(frozen=True)
class EqualTimeOverride:
    timestamp: str
    exiting_source: str
    candidate_source: str
    audit_classification: str
    treatment: str


@dataclass(frozen=True)
class EqualTimeOrder:
    mode: str
    pairs: tuple[EqualTimeOverride, ...]

    def __post_init__(self) -> None:
        index: dict[tuple[str, str], str] = {}
        exits: dict[str, str] = {}
        candidates: dict[str, str] = {}
        for pair in self.pairs:
            key = (pair.exiting_source, pair.candidate_source)
            if key in index:
                raise EqualTimeOrderError(f"duplicate equal-time override {key}")
            if pair.exiting_source in exits:
                raise EqualTimeOrderError(
                    f"conflicting equal-time override for exiting source {pair.exiting_source}"
                )
            if pair.candidate_source in candidates:
                raise EqualTimeOrderError(
                    f"conflicting equal-time override for candidate source {pair.candidate_source}"
                )
            index[key] = pair.treatment
            exits[pair.exiting_source] = pair.candidate_source
            candidates[pair.candidate_source] = pair.exiting_source
        object.__setattr__(self, "_index", index)

    def treatment_for(self, exiting_source: str, candidate_source: str) -> Optional[str]:
        return self._index.get((exiting_source, candidate_source))

    def require_sources(self, source_ids: Iterable[str]) -> None:
        known = set(source_ids)
        for pair in self.pairs:
            if pair.exiting_source not in known:
                raise EqualTimeOrderError(
                    f"equal-time override references unknown source id {pair.exiting_source}"
                )
            if pair.candidate_source not in known:
                raise EqualTimeOrderError(
                    f"equal-time override references unknown source id {pair.candidate_source}"
                )


def _treatment(classification: str, mode: str) -> str:
    if classification == EXIT_BEFORE_CANDIDATE_PROVEN:
        return EXIT_BEFORE_CANDIDATE_PROVEN
    if classification == DISTINCT_REQUEST_ORDER_UNKNOWN:
        if mode == MODE_PRIMARY:
            return UNKNOWN_ORDER_BUSY_FIRST
        if mode == MODE_SENSITIVITY:
            return UNKNOWN_ORDER_EXIT_FIRST
    raise EqualTimeOrderError(
        f"unsupported equal-time classification {classification!r} for mode {mode!r}"
    )


def _source(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EqualTimeOrderError(f"equal-time override {label} is missing")
    return value


def compile_equal_time_order(
    rows: Iterable[dict[str, object]],
    *,
    mode: str,
    known_source_ids: Optional[Iterable[str]] = None,
) -> EqualTimeOrder:
    """Build a pair map. Duplicate, conflicting, or unknown ids fail closed."""

    if mode not in {MODE_PRIMARY, MODE_SENSITIVITY}:
        raise EqualTimeOrderError(f"unknown equal-time mode {mode!r}")
    known = None if known_source_ids is None else set(known_source_ids)
    pairs: list[EqualTimeOverride] = []
    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EqualTimeOrderError("equal-time override row must be an object")
        exiting = _source(row.get("exiting_source"), "exiting_source")
        candidate = _source(row.get("candidate_source"), "candidate_source")
        timestamp = _source(row.get("timestamp"), "timestamp")
        classification = _source(row.get("classification"), "classification")
        if classification not in _AUDIT_CLASSIFICATIONS:
            raise EqualTimeOrderError(
                f"unknown equal-time classification {classification!r}"
            )
        if exiting == candidate:
            raise EqualTimeOrderError(
                f"equal-time override lists the same source on both sides: {exiting}"
            )
        if known is not None and exiting not in known:
            raise EqualTimeOrderError(
                f"equal-time override references unknown source id {exiting}"
            )
        if known is not None and candidate not in known:
            raise EqualTimeOrderError(
                f"equal-time override references unknown source id {candidate}"
            )
        treatment = _treatment(classification, mode)
        key = (exiting, candidate)
        if key in seen and seen[key] != treatment:
            raise EqualTimeOrderError(f"conflicting equal-time override {key}")
        if key in seen:
            raise EqualTimeOrderError(f"duplicate equal-time override {key}")
        seen[key] = treatment
        pairs.append(
            EqualTimeOverride(
                timestamp=timestamp,
                exiting_source=exiting,
                candidate_source=candidate,
                audit_classification=classification,
                treatment=treatment,
            )
        )
    return EqualTimeOrder(mode=mode, pairs=tuple(pairs))


def load_frozen_equal_time_order(
    mode: str,
    *,
    known_source_ids: Optional[Iterable[str]] = None,
    audit_path: Path = FROZEN_AUDIT_PATH,
) -> EqualTimeOrder:
    """Load the frozen 45 + 2 audit pairs. Any other shape fails closed."""

    rows = json.loads(audit_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise EqualTimeOrderError("frozen equal-time audit must be a list")
    order = compile_equal_time_order(
        rows,
        mode=mode,
        known_source_ids=known_source_ids,
    )
    proven = sum(
        pair.audit_classification == EXIT_BEFORE_CANDIDATE_PROVEN for pair in order.pairs
    )
    unknown = sum(
        pair.audit_classification == DISTINCT_REQUEST_ORDER_UNKNOWN
        for pair in order.pairs
    )
    if (
        proven != FROZEN_PROVEN_COUNT
        or unknown != FROZEN_UNKNOWN_COUNT
        or len(order.pairs) != FROZEN_PROVEN_COUNT + FROZEN_UNKNOWN_COUNT
    ):
        raise EqualTimeOrderError(
            "frozen equal-time audit must contain exactly 45 proven pairs and 2 unknown pairs"
        )
    return order
