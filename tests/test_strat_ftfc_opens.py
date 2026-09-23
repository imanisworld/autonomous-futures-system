from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from execution import cross_instrument_observation as cio
from execution import strat_ftfc_opens as ftfc
from notifications.observation_notifier import format_event
from scripts.paper_collection_report import ftfc_tracker_field

EPOCH = "test-epoch+2026-09-01T00:00:00Z"


def _feed(tracker, ts, o, td, start=False):
    return ftfc.update(tracker, bar_ts=ts, bar_open=o, trading_date=td, is_session_start=start)


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_opens_recorded_only_when_provable():
    t = ftfc.empty_tracker()
    # First trading day ever seen: day open known, week/month unknown (no continuity proof).
    _feed(t, _utc(2026, 8, 27, 22, 0), 100.0, date(2026, 8, 28), start=True)
    assert t["day"] == 100.0 and t["week"] is None and t["month"] is None
    # Monday of a new week, previous td 3 days earlier -> week open known.
    _feed(t, _utc(2026, 8, 30, 22, 0), 110.0, date(2026, 8, 31), start=True)
    assert t["week"] == 110.0 and t["month"] is None
    # First td of September, continuous -> month open known; week unchanged.
    _feed(t, _utc(2026, 8, 31, 22, 0), 120.0, date(2026, 9, 1), start=True)
    assert (t["month"], t["week"], t["day"]) == (120.0, 110.0, 120.0)


def test_outage_blocks_week_open_and_mid_hour_blocks_hour_open():
    t = ftfc.empty_tracker()
    _feed(t, _utc(2026, 9, 3, 22, 0), 100.0, date(2026, 9, 4), start=True)
    # Bot down a whole week: next seen day is 11 days later -> week/month opens stay unknown.
    _feed(t, _utc(2026, 9, 14, 22, 15), 90.0, date(2026, 9, 15), start=False)
    assert t["week"] is None and t["day"] is None and t["hour"] is None
    r = ftfc.evaluate(t, bar_ts=_utc(2026, 9, 14, 22, 15), close=95.0, trading_date=date(2026, 9, 15))
    assert r["state"] == "UNKNOWN"


def test_states_and_alignment():
    t = {"trading_date": "2026-09-01", "hour_key": ftfc._hour_key(_utc(2026, 9, 1, 14, 30)),
         "month": 100.0, "week": 101.0, "day": 102.0, "hour": 103.0}
    ev = lambda c: ftfc.evaluate(t, bar_ts=_utc(2026, 9, 1, 14, 30), close=c, trading_date=date(2026, 9, 1))["state"]
    assert ev(104.0) == "UP" and ev(99.0) == "DOWN" and ev(102.5) == "CONFLICT" and ev(103.0) == "CONFLICT"
    assert ftfc.alignment("LONG", "UP") == "aligned"
    assert ftfc.alignment("SHORT", "UP") == "against"
    assert ftfc.alignment("SHORT", "CONFLICT") == "conflict"
    assert ftfc.alignment("LONG", "UNKNOWN") == "unknown"


def test_stale_or_duplicate_bar_ignored():
    t = ftfc.empty_tracker()
    _feed(t, _utc(2026, 9, 1, 14, 0), 100.0, date(2026, 9, 1))
    before = dict(t)
    _feed(t, _utc(2026, 9, 1, 13, 45), 50.0, date(2026, 9, 1))
    assert t == before


def _write_history(log_dir: Path, until: datetime) -> None:
    """Continuous 15m MNQ bars from td 2026-08-28, respecting breaks/weekend."""
    ts = _utc(2026, 8, 27, 22, 0)
    price = 20000.0
    rows: dict[str, list] = {}
    while ts < until:
        et_hour = (ts.hour - 4) % 24  # EDT
        weekend = ts.weekday() == 5 or (ts.weekday() == 4 and et_hour >= 17) or (ts.weekday() == 6 and et_hour < 18)
        if not weekend and et_hour != 17:
            rows.setdefault(ts.date().isoformat(), []).append(
                {"ts": ts.isoformat(), "open": price, "high": price + 1, "low": price - 0.5,
                 "close": price + 0.25, "volume": 100, "timeframe": "15"})
            price += 0.25
        ts += timedelta(minutes=15)
    log_dir.mkdir(parents=True, exist_ok=True)
    for day, bars in rows.items():
        (log_dir / f"bars_MNQ_{day}.jsonl").write_text("\n".join(json.dumps(b) for b in bars) + "\n")


def test_observe_bar_labels_rows_seeded_from_bar_history(tmp_path, monkeypatch):
    from webhook.payload import AlertPayload
    from webhook.state_builder import build_market_state

    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    log_dir = tmp_path / "logs"
    now = _utc(2026, 9, 1, 14, 0)
    _write_history(log_dir, now)
    state = build_market_state(AlertPayload(
        ticker="MNQ1!", timestamp=now.isoformat(), timeframe="15",
        open=21000.0, high=21002.0, low=20999.0, close=21001.0, volume=1000, avg_volume=900, vwap=21000.0,
        market_condition="TRENDING", trend_direction="UP", trend_strength="MODERATE",
        previous_day_high=21010.0, previous_day_low=20980.0, previous_day_close=20995.0,
    ))
    summary = cio.observe_bar(
        log_dir, state,
        [{"strategy": "strat_22_reversal_observed", "direction": "LONG", "entry": 21002.25, "stop": 20998.75, "target": 21009.25}],
        timeframe="15", source="test", include_strat_212_122=False,
    )
    label = cio.read_evidence(log_dir)[0]["strat_ftfc"]
    # month/week/day opens come from the seeded history (~20000-20050); the hour open
    # is the current top-of-hour bar (21000). Close 21001 is above all four.
    assert label["opens"]["hour"] == 21000.0
    assert all(v is not None and v < 21001.0 for v in label["opens"].values())
    assert summary["written"] == 1 and summary["strat_ftfc_state"] == "UP"
    assert (label["state"], label["alignment"]) == ("UP", "aligned")
    assert summary["events"][0]["strat_ftfc"]["alignment"] == label["alignment"]


def test_split_and_cards(tmp_path, monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    base = {"evidence_schema_version": cio.SCHEMA_VERSION, "campaign_id": cio.CAMPAIGN_ID, "record_type": "OUTCOME",
            "strategy": "strat_22_reversal_observed", "instrument": "MNQ", "variant": "observer", "evidence_epoch": EPOCH}
    rows = [
        {**base, "candidate_id": "a", "result": "WIN", "gross_pnl_dollars_1_contract": 30.0,
         "signal_timestamp": "2026-09-01T14:00:00+00:00", "strat_ftfc": {"alignment": "aligned"}},
        {**base, "candidate_id": "b", "result": "LOSS", "gross_pnl_dollars_1_contract": -12.0,
         "signal_timestamp": "2026-09-02T14:00:00+00:00", "strat_ftfc": {"alignment": "conflict"}},
        {**base, "candidate_id": "c", "result": "LOSS", "gross_pnl_dollars_1_contract": -9.0,
         "signal_timestamp": "2026-08-30T14:00:00+00:00"},  # written before labels existed
    ]
    (log_dir / cio.EVIDENCE_FILENAME).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    split = cio.strat_ftfc_split(log_dir)
    assert split["by_alignment"]["aligned"] == {"trades": 1, "wins": 1, "gross_dollars_1_contract": 30.0}
    assert split["by_alignment"]["conflict"]["gross_dollars_1_contract"] == -12.0
    assert split["unlabeled_outcomes"] == 1
    assert split["first_labeled_signal"].startswith("2026-09-01")

    field = ftfc_tracker_field(split)
    assert "Lined up only: **1** trades · **1** won · **+$30**" in field["value"]
    assert "Mixed: **1** trades · **0** won · **−$12**" in field["value"]

    card = format_event({**rows[0], "record_type": "CANDIDATE", "direction": "LONG", "entry": 1.0,
                         "stop": 0.5, "target": 2.0})
    assert "Big-picture check: ✅" in card and "Lined-up-only tracker: counts this one" in card
    other = format_event({**rows[1], "instrument": "MES", "record_type": "CANDIDATE", "direction": "LONG",
                          "entry": 1.0, "stop": 0.5, "target": 2.0})
    assert "Big-picture check: ⚠️ mixed" in other and "tracker" not in other
    assert "Big-picture" not in format_event({**rows[2], "direction": "LONG"})
