"""options_manager/validation/fixtures.py

Real-setup validation fixtures — Increment 23. Runs manually-authored
RealSetupFixture entries (base.py) through the existing advisory-only
row-building and scanning path, then pairs each fixture's scan verdict
with its recorded real-world outcome, without ever fetching anything.
Every fixture case here is a fixed, hardcoded value set -- never loaded
from a file, network call, or live data source.

Every case in _FIXTURE_CASE_BUILDERS below is currently a placeholder:
synthetic, deliberately-constructed values meant only to prove this
package's wiring end-to-end. None of them is a real historical trade --
each one's `provenance` is "placeholder", and its id/notes say so
explicitly. Replacing these with real user-supplied setups (provenance
"user_supplied") is the next step this increment exists to enable; this
module does not perform that replacement itself.

This module performs no I/O of any kind: no candle fetch, no
option-chain fetch, no market-data fetch, no broker call, no order
placement, no execution, no alert sending, no file access at runtime. It
does not implement GEX/gamma or Signa logic, and does not import
replay/replay_engine.py, the live context.market_context loader,
alert_ranker, options_companion, execution, webhook, broker systems, or
risk/risk_engine.py.
"""

from __future__ import annotations

from options_manager.scanner import ScanReport, WatchlistRow, scan_watchlist_strat_212
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

from .base import (
    RealSetupFixture,
    RealSetupValidationEntry,
    RealSetupValidationSummary,
    classify_real_setup_outcome,
)

_PLACEHOLDER_NOTE = (
    "PLACEHOLDER — manual validation placeholder, requires replacement "
    "with a real user-supplied setup."
)
_PLACEHOLDER_OUTCOME_NOTE = (
    "PLACEHOLDER — synthetic stand-in only, not a real trade."
)


def _build_watchlist_row(fixture: RealSetupFixture) -> WatchlistRow:
    """Pure translation of one RealSetupFixture's setup-packet fields
    into a WatchlistRow. Never touches the fixture's own recorded result
    or its optional human review override -- those are read later, only
    after the scan verdict already exists, so the verdict can never be
    influenced by what actually happened."""
    bars = Strat212Bars(
        two_bars_back_type=fixture.two_bars_back_type,
        two_bars_back_high=fixture.two_bars_back_high,
        two_bars_back_low=fixture.two_bars_back_low,
        previous_high=fixture.previous_high,
        previous_low=fixture.previous_low,
        current_high=fixture.current_high,
        current_low=fixture.current_low,
    )
    return WatchlistRow(
        ticker=fixture.ticker,
        timestamp=fixture.setup_datetime,
        direction=fixture.direction,
        bars=bars,
        entry_trigger=fixture.entry_trigger,
        underlying_invalidation=fixture.underlying_invalidation,
        target_1=fixture.target_1,
        target_2=fixture.target_2,
        level_inputs=fixture.level_inputs,
        market_context=fixture.market_context,
        market_context_inputs=fixture.market_context_inputs,
        contract_constraints=fixture.contract_constraints,
        contract_constraints_inputs=fixture.contract_constraints_inputs,
        notes=fixture.notes,
    )


def _bullish_bars_kwargs() -> dict:
    return dict(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=101.0,
        current_low=96.5,
    )


def _bearish_bars_kwargs() -> dict:
    return dict(
        two_bars_back_type="two_down",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=98.5,
        current_low=94.0,
    )


def _complete_real_like_triggered_winner_case() -> RealSetupFixture:
    return RealSetupFixture(
        id="real_like_triggered_winner_001",
        ticker="RL_WINNER",
        setup_datetime="2026-02-02T10:00:00Z",
        direction="CALL",
        provenance="placeholder",
        **_bullish_bars_kwargs(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        spot_at_setup=99.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        actual_outcome="hit_target_1",
        actual_outcome_notes=_PLACEHOLDER_OUTCOME_NOTE
        + " Stands in for a setup that hit its first target.",
        notes=_PLACEHOLDER_NOTE,
    )


def _complete_real_like_triggered_loser_case() -> RealSetupFixture:
    return RealSetupFixture(
        id="real_like_triggered_loser_001",
        ticker="RL_LOSER",
        setup_datetime="2026-02-02T10:05:00Z",
        direction="PUT",
        provenance="placeholder",
        **_bearish_bars_kwargs(),
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        spot_at_setup=95.5,
        target_1=92.0,
        target_2=89.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        actual_outcome="hit_stop",
        actual_outcome_notes=_PLACEHOLDER_OUTCOME_NOTE
        + " Stands in for a setup that hit its invalidation instead.",
        notes=_PLACEHOLDER_NOTE,
    )


def _rejected_missing_context_case() -> RealSetupFixture:
    return RealSetupFixture(
        id="rejected_missing_context_001",
        ticker="RL_NO_CONTEXT",
        setup_datetime="2026-02-02T10:10:00Z",
        direction="CALL",
        provenance="placeholder",
        **_bullish_bars_kwargs(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        spot_at_setup=99.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=None,
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        actual_outcome="unknown",
        actual_outcome_notes=_PLACEHOLDER_OUTCOME_NOTE
        + " No market-context data was ever supplied for this setup, so "
        "there is nothing to compare a real outcome against yet.",
        notes=_PLACEHOLDER_NOTE
        + " Demonstrates the fail-closed missing_market_context path.",
    )


def _rejected_missing_contract_case() -> RealSetupFixture:
    return RealSetupFixture(
        id="rejected_missing_contract_001",
        ticker="RL_NO_CONTRACT",
        setup_datetime="2026-02-02T10:15:00Z",
        direction="CALL",
        provenance="placeholder",
        **_bullish_bars_kwargs(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        spot_at_setup=99.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=None,
        actual_outcome="unknown",
        actual_outcome_notes=_PLACEHOLDER_OUTCOME_NOTE
        + " No contract data was ever supplied for this setup, so there "
        "is nothing to compare a real outcome against yet.",
        notes=_PLACEHOLDER_NOTE
        + " Demonstrates the fail-closed missing_contract_constraints path.",
    )


def _incomplete_data_fail_closed_case() -> RealSetupFixture:
    return RealSetupFixture(
        id="incomplete_data_fail_closed_001",
        ticker="RL_INCOMPLETE",
        setup_datetime="2026-02-02T10:20:00Z",
        direction="CALL",
        provenance="placeholder",
        **_bullish_bars_kwargs(),
        entry_trigger=None,
        underlying_invalidation=95.5,
        spot_at_setup=99.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        actual_outcome="unknown",
        actual_outcome_notes=_PLACEHOLDER_OUTCOME_NOTE
        + " No entry trigger was ever recorded for this setup, so there "
        "is nothing to compare a real outcome against yet.",
        notes=_PLACEHOLDER_NOTE
        + " Demonstrates the fail-closed missing_entry_trigger path.",
    )


_FIXTURE_CASE_BUILDERS = (
    ("complete_real_like_triggered_winner", _complete_real_like_triggered_winner_case),
    ("complete_real_like_triggered_loser", _complete_real_like_triggered_loser_case),
    ("rejected_missing_context", _rejected_missing_context_case),
    ("rejected_missing_contract", _rejected_missing_contract_case),
    ("incomplete_data_fail_closed", _incomplete_data_fail_closed_case),
)


def build_real_setup_validation_dataset() -> dict[str, RealSetupFixture]:
    """Returns a fresh dict of the 5 fixed real-setup validation fixture
    cases, keyed by a fixed case name. Each call rebuilds the fixtures
    from the individual builder functions above rather than returning a
    shared/cached dict, so nothing here can accumulate mutated state
    across calls (the fixtures themselves are also frozen dataclasses)."""
    return {case_name: builder() for case_name, builder in _FIXTURE_CASE_BUILDERS}


def run_real_setup_validation_dataset(
    fixtures: dict[str, RealSetupFixture] | None = None,
) -> dict[str, RealSetupValidationEntry]:
    """Scans `fixtures` (defaulting to build_real_setup_validation_dataset())
    through the existing advisory-only scanning path and pairs each
    fixture's scan verdict with its recorded outcome. The scan itself
    never sees a fixture's outcome fields -- rows are built from the
    setup packet alone, scanned, and only afterward matched back up with
    the fixture's own `actual_outcome`/`actual_outcome_notes`."""
    if fixtures is None:
        fixtures = build_real_setup_validation_dataset()

    names = list(fixtures.keys())
    rows = [_build_watchlist_row(fixtures[name]) for name in names]
    report: ScanReport = scan_watchlist_strat_212(rows)

    entries: dict[str, RealSetupValidationEntry] = {}
    for name, result in zip(names, report.results):
        fixture = fixtures[name]
        classification = classify_real_setup_outcome(
            scan_status=result.scan_status,
            actual_outcome=fixture.actual_outcome,
            human_classification_override=fixture.human_classification_override,
        )
        entries[name] = RealSetupValidationEntry(
            fixture_id=fixture.id,
            ticker=fixture.ticker,
            provenance=fixture.provenance,
            scan_status=result.scan_status,
            reason_code=result.reason_code,
            actual_outcome=fixture.actual_outcome,
            actual_outcome_notes=fixture.actual_outcome_notes,
            classification=classification,
        )
    return entries


def summarize_real_setup_validation_dataset(
    entries: dict[str, RealSetupValidationEntry] | None = None,
) -> RealSetupValidationSummary:
    """Deterministic rollup of a real-setup validation run (or `entries`,
    if supplied)."""
    if entries is None:
        entries = run_real_setup_validation_dataset()

    values = list(entries.values())
    counts_by_classification: dict[str, int] = {}
    counts_by_scan_status: dict[str, int] = {}
    placeholder_cases = 0
    user_supplied_cases = 0

    for entry in values:
        counts_by_classification[entry.classification] = (
            counts_by_classification.get(entry.classification, 0) + 1
        )
        counts_by_scan_status[entry.scan_status] = (
            counts_by_scan_status.get(entry.scan_status, 0) + 1
        )
        if entry.provenance == "placeholder":
            placeholder_cases += 1
        elif entry.provenance == "user_supplied":
            user_supplied_cases += 1

    return RealSetupValidationSummary(
        total_cases=len(values),
        placeholder_cases=placeholder_cases,
        user_supplied_cases=user_supplied_cases,
        counts_by_classification=counts_by_classification,
        counts_by_scan_status=counts_by_scan_status,
    )
