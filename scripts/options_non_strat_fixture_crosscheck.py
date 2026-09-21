"""Cross-check logged real-trade fixture candidates against ns-v0.1 events.

This is a coverage audit only. A match says the observer would have named the
same broad structural family somewhere in the fixture's recorded window. It
does not prove the original trade plan, option expectancy, or promotion.

The mapping below is intentionally sparse. Only fixtures whose existing,
hand-authored evidence names a setup family that ns-v0.1 can detect are
eligible for a mechanical family match. Everything else stays explicitly
untestable instead of being reverse-engineered from outcome candles.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.non_strat_coverage import OBSERVER_VERSION  # noqa: E402
from options_manager.validation.fixture_status import (  # noqa: E402
    FixtureCandidate,
    build_fixture_candidate_inventory,
)

DEFAULT_SQLITE = ROOT / "logs" / "options_non_strat_coverage.sqlite"

# Frozen before looking at ns-v0.1 outcomes.
#
# EBAY explicitly records a long PDL-reclaim entry. In ns-v0.1 the mechanical
# predicate "low < PDL and close > PDL" is currently named
# PDL_REJECTION_LONG; the semantic alias below prevents that naming mismatch
# from becoming a false negative.
#
# HOOD names support-hold continuation/pullback-reclaim, but that family is
# deliberately absent from ns-v0.1 until a frozen planned-level source exists.
EXPECTATIONS: dict[str, dict[str, Any]] = {
    "EBAY": {
        "expected_families": ("PDL_REJECTION_LONG",),
        "semantic_setup": "PDL_RECLAIM_LONG",
        "basis": "fixture evidence explicitly says waited for a PDL-reclaim before entry",
    },
    "HOOD": {
        "expected_families": (),
        "semantic_setup": "SUPPORT_HOLD_CONTINUATION_OR_PULLBACK_RECLAIM",
        "basis": "fixture names the family but planned support source is not proven",
        "blocked_reason": "generic_planned_level_source_unproven",
    },
    "AMD": {
        "expected_families": (),
        "semantic_setup": "PREMARKET_RECLAIM",
        "basis": "fixture trigger is explicitly premarket",
        "blocked_reason": "ns-v0.1_regular_session_only",
    },
}


@dataclass(frozen=True)
class CrosscheckRow:
    ticker: str
    fixture_window: str
    fixture_status: str
    semantic_setup: str | None
    expected_families: tuple[str, ...]
    observed_families: tuple[str, ...]
    matched_events: int
    result: str
    reason: str
    edge_claim_allowed: bool = False

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


def _parse_window(value: str) -> tuple[str | None, str | None]:
    raw = (value or "").strip()
    if not raw or raw.lower() == "unknown":
        return None, None
    parts = [part.strip() for part in raw.split("/") if part.strip()]
    try:
        if len(parts) == 1:
            day = date.fromisoformat(parts[0]).isoformat()
            return day, day
        if len(parts) == 2:
            start = date.fromisoformat(parts[0]).isoformat()
            end = date.fromisoformat(parts[1]).isoformat()
            if end < start:
                return None, None
            return start, end
    except ValueError:
        return None, None
    return None, None


def _events_for(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT row_json
           FROM non_strat_events
           WHERE observer_version=?
             AND symbol=?
             AND session_date BETWEEN ? AND ?
           ORDER BY session_date, bar_start, family""",
        (OBSERVER_VERSION, ticker, start, end),
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def evaluate_fixture(
    candidate: FixtureCandidate,
    events: Sequence[dict[str, Any]],
) -> CrosscheckRow:
    ticker = candidate.ticker.upper()
    expectation = EXPECTATIONS.get(ticker)
    observed_families = tuple(sorted({str(row.get("family") or "") for row in events if row.get("family")}))

    if expectation is None:
        return CrosscheckRow(
            ticker=ticker,
            fixture_window=candidate.window,
            fixture_status=candidate.status.value,
            semantic_setup=None,
            expected_families=(),
            observed_families=observed_families,
            matched_events=0,
            result="UNTESTABLE",
            reason="fixture_has_no_proven_setup_family_for_ns-v0.1",
        )

    semantic_setup = str(expectation["semantic_setup"])
    expected = tuple(str(value) for value in expectation.get("expected_families", ()))
    blocked = expectation.get("blocked_reason")
    if blocked:
        return CrosscheckRow(
            ticker=ticker,
            fixture_window=candidate.window,
            fixture_status=candidate.status.value,
            semantic_setup=semantic_setup,
            expected_families=expected,
            observed_families=observed_families,
            matched_events=0,
            result="OUT_OF_SCOPE",
            reason=str(blocked),
        )

    matched = [row for row in events if str(row.get("family") or "") in expected]
    if matched:
        return CrosscheckRow(
            ticker=ticker,
            fixture_window=candidate.window,
            fixture_status=candidate.status.value,
            semantic_setup=semantic_setup,
            expected_families=expected,
            observed_families=observed_families,
            matched_events=len(matched),
            result="COVERAGE_MATCH",
            reason="expected_family_observed_in_fixture_window",
        )
    return CrosscheckRow(
        ticker=ticker,
        fixture_window=candidate.window,
        fixture_status=candidate.status.value,
        semantic_setup=semantic_setup,
        expected_families=expected,
        observed_families=observed_families,
        matched_events=0,
        result="NO_COVERAGE_MATCH",
        reason="expected_family_not_observed_in_loaded_fixture_window",
    )


def crosscheck(
    conn: sqlite3.Connection,
    candidates: dict[str, FixtureCandidate] | None = None,
) -> list[CrosscheckRow]:
    inventory = candidates or build_fixture_candidate_inventory()
    rows: list[CrosscheckRow] = []
    for ticker, candidate in sorted(inventory.items()):
        start, end = _parse_window(candidate.window)
        if start is None or end is None:
            rows.append(
                CrosscheckRow(
                    ticker=ticker,
                    fixture_window=candidate.window,
                    fixture_status=candidate.status.value,
                    semantic_setup=(
                        str(EXPECTATIONS[ticker]["semantic_setup"])
                        if ticker in EXPECTATIONS
                        else None
                    ),
                    expected_families=tuple(
                        str(value)
                        for value in EXPECTATIONS.get(ticker, {}).get(
                            "expected_families", ()
                        )
                    ),
                    observed_families=(),
                    matched_events=0,
                    result="UNTESTABLE",
                    reason="fixture_window_not_machine_parseable",
                )
            )
            continue

        events = _events_for(conn, ticker=ticker, start=start, end=end)
        row = evaluate_fixture(candidate, events)
        if not events and row.result == "NO_COVERAGE_MATCH":
            row = CrosscheckRow(
                **{
                    **row.to_row(),
                    "result": "DATA_MISSING",
                    "reason": "no_ns-v0.1_events_loaded_for_fixture_window",
                }
            )
        rows.append(row)
    return rows


def summary(rows: Sequence[CrosscheckRow]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.result] = counts.get(row.result, 0) + 1
    return {
        "observer_version": OBSERVER_VERSION,
        "fixture_count": len(rows),
        "counts": counts,
        "edge_claim_allowed": False,
        "interpretation": (
            "coverage-only regression; matches do not validate the recalled trade "
            "plan, strategy expectancy, or option execution"
        ),
        "rows": [row.to_row() for row in rows],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check logged fixture candidates against ns-v0.1 events"
    )
    parser.add_argument(
        "--sqlite",
        default=str(DEFAULT_SQLITE),
        help="ns-v0.1 dedicated sqlite path",
    )
    parser.add_argument("--json", dest="json_path", help="optional output JSON path")
    args = parser.parse_args(argv)

    path = Path(args.sqlite)
    if not path.exists():
        print(f"missing observer database: {path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = crosscheck(conn)
    finally:
        conn.close()

    report = summary(rows)
    for row in rows:
        print(
            f"{row.ticker:5s} {row.result:18s} "
            f"setup={row.semantic_setup or '-'} matches={row.matched_events} "
            f"reason={row.reason}"
        )
    print(json.dumps(report["counts"], sort_keys=True))

    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())