import asyncio
from datetime import datetime, timezone
from pathlib import Path

from alert_ranker.v1_capacity import (
    classify_symbol_result,
    run_serial_capacity_preflight,
)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


class FakeScanner:
    def __init__(self, clock, rows, durations=None, exceptions=None):
        self.clock = clock
        self.rows = rows
        self.durations = durations or {}
        self.exceptions = exceptions or {}
        self.calls = []

    async def _build_normalized_data(self, ticker, context, now):
        assert context == {}
        assert now.tzinfo is not None
        self.calls.append(ticker)
        self.clock.advance(self.durations.get(ticker, 1.0))
        if ticker in self.exceptions:
            raise self.exceptions[ticker]
        return dict(self.rows[ticker])


def _ok(price=100.0, **extra):
    row = {
        "price": price,
        "market_data_error": None,
        "bar_context_available": True,
        "bar_context_reason": None,
        "missing_bar_count": 0,
        "setup_status": "WATCH",
    }
    row.update(extra)
    return row


def _run(scanner, tickers, clock, budget=300.0):
    return asyncio.run(
        run_serial_capacity_preflight(
            scanner,
            tickers,
            now=datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc),
            interval_seconds=budget,
            monotonic=clock,
        )
    )


def test_serial_preflight_passes_complete_fast_clean_cycle():
    clock = FakeClock()
    rows = {ticker: _ok() for ticker in ("AAPL", "MSFT", "SPY")}
    scanner = FakeScanner(clock, rows, {"AAPL": 2.0, "MSFT": 3.0, "SPY": 4.0})
    report = _run(scanner, list(rows), clock, budget=20.0)

    assert report.verdict == "PASS"
    assert report.reasons == ()
    assert scanner.calls == ["AAPL", "MSFT", "SPY"]
    assert report.serial_order_preserved is True
    assert report.complete_universe is True
    assert report.total_elapsed_seconds == 9.0
    assert report.headroom_seconds == 11.0
    assert report.contract_fetches == 0
    assert report.storage_writes == 0
    assert report.alerts_sent == 0
    assert report.live_watchlist_changed is False


def test_cycle_over_configured_interval_fails_even_with_clean_data():
    clock = FakeClock()
    rows = {"AAPL": _ok(), "MSFT": _ok()}
    scanner = FakeScanner(clock, rows, {"AAPL": 6.0, "MSFT": 5.0})
    report = _run(scanner, list(rows), clock, budget=10.0)

    assert report.verdict == "FAIL"
    assert "cycle_exceeds_configured_interval" in report.reasons
    assert report.critical_failures == 0
    assert report.headroom_seconds == -1.0


def test_rate_limit_and_timeout_are_visible_and_block_when_critical():
    rate = classify_symbol_result(
        "AAPL",
        1.0,
        {
            "price": None,
            "market_data_error": "rate_limited",
            "bar_context_available": False,
            "bar_context_reason": "provider_error:HTTP 429",
        },
    )
    timeout = classify_symbol_result(
        "MSFT",
        1.0,
        {
            "price": None,
            "market_data_error": "timeout",
            "bar_context_available": False,
            "bar_context_reason": "provider_unavailable:ReadTimeout",
        },
    )
    assert rate.rate_limited is True
    assert rate.critical_ok is False
    assert timeout.timed_out is True
    assert timeout.critical_ok is False


def test_signa_timeout_is_observational_for_setup_but_still_blocks_capacity_proof():
    clock = FakeClock()
    rows = {
        "AAPL": _ok(signa_error="ReadTimeout"),
        "MSFT": _ok(signa_v2_ok=False, signa_v2_error="timeout"),
    }
    scanner = FakeScanner(clock, rows)
    report = _run(scanner, list(rows), clock, budget=20.0)

    # Signa has zero V1 scoring authority, so these are not critical data
    # failures.  But a capacity proof requires a clean provider cycle: repeated
    # timeouts still block expansion because they consume serial scan time.
    assert report.verdict == "FAIL"
    assert report.critical_failures == 0
    assert report.observational_failures == 2
    assert report.signa_error_symbols == ("AAPL", "MSFT")
    assert report.timed_out_symbols == ("AAPL", "MSFT")
    assert "timeouts:2" in report.reasons


def test_missing_or_incomplete_bar_context_blocks_capacity_proof():
    clock = FakeClock()
    rows = {
        "AAPL": _ok(),
        "MSFT": {
            "price": 200.0,
            "market_data_error": None,
            "bar_context_available": False,
            "bar_context_reason": "incomplete_session",
            "missing_bar_count": 2,
        },
    }
    scanner = FakeScanner(clock, rows)
    report = _run(scanner, list(rows), clock, budget=20.0)

    assert report.verdict == "FAIL"
    assert "critical_data_failures:1" in report.reasons
    assert report.missing_bar_symbols == ("MSFT",)
    assert report.bar_context_error_symbols == ("MSFT",)


def test_symbol_exception_is_recorded_and_census_continues():
    clock = FakeClock()
    rows = {"AAPL": _ok(), "MSFT": _ok(), "SPY": _ok()}
    scanner = FakeScanner(
        clock,
        rows,
        exceptions={"MSFT": RuntimeError("boom")},
    )
    report = _run(scanner, ["AAPL", "MSFT", "SPY"], clock, budget=20.0)

    assert scanner.calls == ["AAPL", "MSFT", "SPY"]
    assert report.verdict == "FAIL"
    assert report.critical_failures == 1
    msft = next(item for item in report.results if item.ticker == "MSFT")
    assert msft.exception == "RuntimeError:boom"


def test_preflight_cli_does_not_call_stateful_scanner_entrypoints_or_open_contracts():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "options_v1_capacity_preflight.py").read_text()
    assert ".scan_ticker(" not in source
    assert ".scan_watchlist(" not in source
    assert "ScanStorage(" not in source
    assert "DiscordAlerter(" not in source
    assert "fetch_option_chain" not in source
    assert "fetch_option_expirations" not in source
    assert "_build_normalized_data(" not in source  # core owns the one intentional private call


def test_core_only_calls_normalized_data_builder_not_trade_entrypoints():
    source = (Path(__file__).resolve().parents[1] / "alert_ranker" / "v1_capacity.py").read_text()
    assert "scanner._build_normalized_data(" in source
    assert "scanner.scan_ticker(" not in source
    assert "scanner.scan_watchlist(" not in source
    assert "fetch_option_chain" not in source
