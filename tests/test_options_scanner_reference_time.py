"""The V1 chain path must use one reference time for expiry and evidence.

Expiry/DTE selection and the selector-evidence ``decision_ts`` must both come
from the ``now`` passed to the scanner; the wall clock is only the default when
no reference time is passed.  Otherwise a historical/replayed decision records
a decision time at which its own chosen expiration is outside the V1 DTE range.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from alert_ranker import multisetup_scanner, scanner_legacy, v1_evidence_hardening
from alert_ranker.discord import DiscordAlerter
from alert_ranker.market_data import OptionChain
from alert_ranker.options_production_selector_replay import replay_production_selector
from alert_ranker.paper_v1 import MIN_DTE, choose_expiration
from alert_ranker.scanner import OptionsScanner
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot
from tests.test_options_paper_v1 import cfg, quote, triggered_context

NY = ZoneInfo("America/New_York")
# Historical reference time passed into the scanner.
REFERENCE_NOW = datetime(2026, 9, 8, 10, 0, tzinfo=NY)
# Frozen "real clock", one week after the reference time.
WALL_CLOCK = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)
# 17 DTE from REFERENCE_NOW (valid 14-44 exception), 10 DTE from WALL_CLOCK.
EXPIRY = "2026-09-25"


class _FrozenWallClock(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D102 - test clock
        return WALL_CLOCK.astimezone(tz) if tz is not None else WALL_CLOCK.replace(tzinfo=None)


class _ReferenceMarket:
    provider_name = "public"
    last_error = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(ticker.upper(), price=500.0, volume=10_000_000)

    async def fetch_option_expirations(self, ticker: str) -> list[str]:
        return [EXPIRY]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
        assert expiration == EXPIRY
        return OptionChain(ticker.upper(), expiration, calls=(quote(),), puts=())


@pytest.fixture
def frozen_wall_clock(monkeypatch):
    # The public scanner's wrappers fill in the default ``now`` before legacy.
    for module in (v1_evidence_hardening, multisetup_scanner, scanner_legacy):
        monkeypatch.setattr(module, "datetime", _FrozenWallClock)


def _scan(tmp_path, *, now):
    config = cfg(tmp_path)
    storage = ScanStorage(config.sqlite_path)
    scanner = OptionsScanner(config, _ReferenceMarket(), storage, DiscordAlerter(config, storage))
    asyncio.run(scanner.scan_ticker("SPY", source="webhook", context=triggered_context(), now=now))
    rows = storage.latest_selector_evidence()
    assert len(rows) == 1
    return rows[0]


def test_min_dte_result_flips_between_reference_time_and_wall_clock():
    # Precondition for the regression below: the clock choice decides validity.
    at_reference = choose_expiration([EXPIRY], REFERENCE_NOW)
    at_wall_clock = choose_expiration([EXPIRY], WALL_CLOCK)
    assert at_reference.valid and at_reference.expiry.dte == 17 >= MIN_DTE
    assert not at_wall_clock.valid
    assert at_wall_clock.reason == "no_expiration_in_v1_dte_range"


def test_evidence_decision_ts_equals_injected_reference_time(tmp_path, frozen_wall_clock):
    evidence = _scan(tmp_path, now=REFERENCE_NOW)

    decision_ts = datetime.fromisoformat(evidence["decision_ts"])
    assert decision_ts == REFERENCE_NOW
    assert decision_ts != WALL_CLOCK
    stored_at = datetime.fromisoformat(evidence["_stored_at"])
    assert stored_at == REFERENCE_NOW
    assert stored_at.utcoffset() == timezone.utc.utcoffset(None)


def test_evidence_replays_same_expiry_under_reference_time(tmp_path, frozen_wall_clock):
    evidence = _scan(tmp_path, now=REFERENCE_NOW)

    assert evidence["status"] == "CAPTURED"
    assert evidence["chosen_expiration"] == EXPIRY
    # The recorded decision time must itself satisfy the 14-day minimum DTE
    # for the expiry production chose at that time.
    redo = choose_expiration(evidence["expiration_candidates"], datetime.fromisoformat(evidence["decision_ts"]))
    assert redo.valid and redo.expiry.expiration == EXPIRY and redo.expiry.dte >= MIN_DTE
    replay = replay_production_selector(evidence)
    assert replay["status"] == "VALID", replay
    assert replay["expiry"]["expiration"] == EXPIRY
    assert evidence["production_replay_parity"] is True


def test_blocked_evidence_also_uses_injected_reference_time(tmp_path, frozen_wall_clock, monkeypatch):
    def _raise(**_kwargs):
        raise ValueError("forced capture failure")

    monkeypatch.setattr(scanner_legacy, "build_selector_evidence_capture", _raise)
    evidence = _scan(tmp_path, now=REFERENCE_NOW)

    assert evidence["status"] == "DATA_BLOCKED"
    assert evidence["reason_code"] == "selector_capture_invalid:ValueError"
    assert datetime.fromisoformat(evidence["decision_ts"]) == REFERENCE_NOW
    assert datetime.fromisoformat(evidence["_stored_at"]) == REFERENCE_NOW


def test_wall_clock_is_default_only_when_no_reference_time_passed(tmp_path, frozen_wall_clock, monkeypatch):
    # At the wall clock the 2026-09-25 expiry is 10 DTE, so use a 45+ expiry
    # that is valid from either clock; this checks the default, not the flip.
    monkeypatch.setattr(_ReferenceMarket, "fetch_option_expirations", _later_expirations)
    monkeypatch.setattr(_ReferenceMarket, "fetch_option_chain", _later_chain)
    evidence = _scan(tmp_path, now=None)

    assert datetime.fromisoformat(evidence["decision_ts"]) == WALL_CLOCK
    assert datetime.fromisoformat(evidence["_stored_at"]) == WALL_CLOCK
    assert evidence["chosen_expiration"] == "2026-10-30"
    assert evidence["production_replay_parity"] is True


async def _later_expirations(self, ticker: str) -> list[str]:
    return ["2026-10-30"]


async def _later_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
    assert expiration == "2026-10-30"
    return OptionChain(ticker.upper(), expiration, calls=(quote(),), puts=())
