"""Audit real logged option-trade fixture windows against ns-v0.1.

This is evidence-only. It does not assume that a raw ns-v0.1 event was the
operator's original setup. It asks only: during each logged trade window, which
of the frozen non-Strat structural events were actually present in causal 5m
bars?

Usage:
    python scripts/options_non_strat_fixture_audit.py --dry-run
    python scripts/options_non_strat_fixture_audit.py --ticker EBAY
    python scripts/options_non_strat_fixture_audit.py --out logs/non_strat_fixture_audit.json

Candidates with an unknown window are reported as DATA_BLOCKED rather than
inventing dates. SPXW is also blocked because this observer is equity-bar
specific and the fixture inventory identifies it as 0DTE scalp noise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import AlpacaBarProvider, CONSOLIDATED_FEED  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5, missing_bar_starts, session_bars  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.non_strat_coverage import (  # noqa: E402
    OBSERVER_VERSION,
    NonStratEvent,
    observe_session,
)
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402
from options_manager.validation.fixture_status import (  # noqa: E402
    FixtureCandidate,
    build_fixture_candidate_inventory,
)
from scripts.options_coverage_observer import fetch_all, sessions_through  # noqa: E402


@dataclass(frozen=True)
class FixtureAuditRow:
    ticker: str
    window: str
    fixture_status: str
    status: str
    reason: str
    sessions_observed: int
    events: int
    families: dict[str, int]
    event_rows: tuple[dict[str, Any], ...]

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


def parse_window(value: str) -> tuple[date, date] | None:
    text = (value or "").strip()
    if not text or text.lower() == "unknown":
        return None
    if "/" not in text:
        return None
    left, right = [piece.strip() for piece in text.split("/", 1)]
    try:
        start = date.fromisoformat(left)
        end = date.fromisoformat(right)
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


def fixture_testability(candidate: FixtureCandidate) -> tuple[bool, str]:
    window = parse_window(candidate.window)
    if window is None:
        return False, "fixture_window_unknown"
    if candidate.ticker.upper() == "SPXW":
        return False, "index_option_fixture_not_equity_bar_observer"
    return True, ""


def _session_slice(bars, session):
    return session_bars(bars, MINUTE_5, session.open, session.close)


def _complete(bars, session) -> bool:
    return not missing_bar_starts(
        list(bars),
        MINUTE_5,
        session.open,
        session.close,
        through=session.close,
    )


def _family_counts(events: Sequence[NonStratEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.family] = counts.get(event.family, 0) + 1
    return dict(sorted(counts.items()))


async def audit_candidate(
    candidate: FixtureCandidate,
    provider: AlpacaBarProvider,
) -> FixtureAuditRow:
    ok, reason = fixture_testability(candidate)
    if not ok:
        return FixtureAuditRow(
            ticker=candidate.ticker,
            window=candidate.window,
            fixture_status=candidate.status.value,
            status="DATA_BLOCKED",
            reason=reason,
            sessions_observed=0,
            events=0,
            families={},
            event_rows=(),
        )

    start_day, end_day = parse_window(candidate.window)  # type: ignore[misc]
    sessions = []
    cursor = start_day - timedelta(days=8)
    while cursor <= end_day:
        session = nyse_session_for(cursor)
        if session is not None:
            sessions.append(session)
        cursor += timedelta(days=1)
    target_sessions = [s for s in sessions if start_day <= s.date <= end_day]
    if not target_sessions:
        return FixtureAuditRow(
            ticker=candidate.ticker,
            window=candidate.window,
            fixture_status=candidate.status.value,
            status="DATA_BLOCKED",
            reason="no_nyse_sessions_in_window",
            sessions_observed=0,
            events=0,
            families={},
            event_rows=(),
        )

    first = sessions[0]
    last = target_sessions[-1]
    fetched, errors = await fetch_all(
        provider,
        [candidate.ticker.upper(), "SPY", "QQQ"],
        MINUTE_5,
        first.open,
        last.close,
    )
    if errors:
        return FixtureAuditRow(
            ticker=candidate.ticker,
            window=candidate.window,
            fixture_status=candidate.status.value,
            status="DATA_BLOCKED",
            reason="provider:" + ";".join(f"{k}={v}" for k, v in sorted(errors.items())),
            sessions_observed=0,
            events=0,
            families={},
            event_rows=(),
        )

    ticker_bars = fetched.get(candidate.ticker.upper(), [])
    spy_bars = fetched.get("SPY", [])
    qqq_bars = fetched.get("QQQ", [])
    events: list[NonStratEvent] = []
    observed = 0

    for target in target_sessions:
        earlier = [s for s in sessions if s.date < target.date]
        if not earlier:
            continue
        prior = earlier[-1]
        current = _session_slice(ticker_bars, target)
        previous = _session_slice(ticker_bars, prior)
        if not current or not previous or not _complete(current, target) or not _complete(previous, prior):
            continue

        spy_session = _session_slice(spy_bars, target)
        qqq_session = _session_slice(qqq_bars, target)
        spy_history = [b for b in spy_bars if b.start_utc < target.open]
        qqq_history = [b for b in qqq_bars if b.start_utc < target.open]
        history = [b for b in ticker_bars if b.start_utc < target.open]

        events.extend(
            observe_session(
                symbol=candidate.ticker,
                session_date=target.date.isoformat(),
                prior_session_bars=previous,
                session_bars=current,
                history_bars=history,
                spy_history=spy_history,
                spy_session=spy_session,
                qqq_history=qqq_history,
                qqq_session=qqq_session,
            )
        )
        observed += 1

    if observed == 0:
        return FixtureAuditRow(
            ticker=candidate.ticker,
            window=candidate.window,
            fixture_status=candidate.status.value,
            status="DATA_BLOCKED",
            reason="no_complete_fixture_sessions",
            sessions_observed=0,
            events=0,
            families={},
            event_rows=(),
        )

    return FixtureAuditRow(
        ticker=candidate.ticker,
        window=candidate.window,
        fixture_status=candidate.status.value,
        status="OBSERVED",
        reason="",
        sessions_observed=observed,
        events=len(events),
        families=_family_counts(events),
        event_rows=tuple(event.to_row() for event in events),
    )


async def run(args: argparse.Namespace) -> int:
    inventory = build_fixture_candidate_inventory()
    if args.ticker:
        key = args.ticker.strip().upper()
        if key not in inventory:
            print(f"unknown fixture ticker: {key}", file=sys.stderr)
            return 2
        selected = [inventory[key]]
    else:
        selected = list(inventory.values())

    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        print("Alpaca credentials not configured", file=sys.stderr)
        return 2
    provider = AlpacaBarProvider(
        base_url=os.environ.get(
            "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
        ).rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        feed=CONSOLIDATED_FEED,
    )

    rows = [await audit_candidate(candidate, provider) for candidate in selected]
    payload = {
        "observer_version": OBSERVER_VERSION,
        "purpose": "logged_fixture_window_coverage_only_not_setup_attribution",
        "rows": [row.to_row() for row in rows],
    }

    for row in rows:
        print(
            f"{row.ticker:5s} {row.window:23s} {row.status:12s} "
            f"sessions={row.sessions_observed:2d} events={row.events:4d} "
            f"families={','.join(row.families) if row.families else '-'}"
        )
        if row.reason:
            print(f"  reason: {row.reason}")

    if args.out and not args.dry_run:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out}")
    elif args.dry_run:
        print("(dry run: no output file written)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit logged option fixture windows against ns-v0.1"
    )
    parser.add_argument("--ticker", help="Audit one tracked fixture ticker")
    parser.add_argument("--out", help="Optional JSON output path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
