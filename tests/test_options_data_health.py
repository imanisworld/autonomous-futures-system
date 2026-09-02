"""Read-only options data health: READY / DEGRADED / BLOCKED, fail closed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ops.options_data_health import (
    BLOCKED,
    DEGRADED,
    GEX_STATUS,
    READY,
    SourceObservation,
    collect_observations,
    evaluate_data_health,
    main,
    observations_from_json,
)

NOW = datetime(2026, 9, 2, 14, 10, tzinfo=timezone.utc)  # 10:10 ET


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _ready() -> dict[str, SourceObservation]:
    return {
        "calendar": SourceObservation(
            "calendar",
            True,
            NOW.isoformat(),
            {
                "is_trading_day": True,
                "session_open": "2026-09-02T13:30:00+00:00",
                "session_close": "2026-09-02T20:00:00+00:00",
                "is_early_close": False,
            },
            provider="calendar",
        ),
        "quote": SourceObservation("quote", True, _iso(1), {"last": 123.4}, provider="rh"),
        "prior_close": SourceObservation("prior_close", True, _iso(1), {"close": 121.0, "date": "2026-09-01"}, provider="rh"),
        "bars": SourceObservation("bars", True, _iso(0), {"count": 8, "interval_minutes": 5, "bounds": "regular", "first_bar_start": "2026-09-02T13:30:00+00:00", "last_bar_end": _iso(5)}, provider="rh"),
        "chain": SourceObservation("chain", True, _iso(1), {"expirations": ["2026-09-04", "2026-09-11", "2026-10-16"], "sample_contract": {"bid": 1.95, "ask": 2.05, "volume": 1401, "open_interest": 7683, "iv": 0.13, "delta": 0.57, "theta": -0.19, "updated_at": _iso(2)}}, provider="rh"),
        "signa": SourceObservation("signa", True, _iso(1), {"grade": "B", "score": 63.0, "technicals_as_of": "2026-09-01", "stale": False}, provider="signa"),
    }


def test_all_sources_fresh_is_ready():
    report = evaluate_data_health(_ready(), ticker="xyz", now=NOW)
    assert report.status == READY and report.reasons == () and report.ticker == "XYZ"
    assert report.in_session is True and report.gex == GEX_STATUS and report.source_status["gex"] == "UNAVAILABLE"
    assert report.source_status["greeks"] == READY


def _with(obs, name, **changes):
    base = obs[name]
    fields = dict(base.fields)
    fields.update(changes.pop("fields", {}))
    obs[name] = SourceObservation(name=name, ok=changes.get("ok", base.ok), observed_at=changes.get("observed_at", base.observed_at), fields=fields, error=changes.get("error", base.error), provider=base.provider)
    return obs


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda o: {k: v for k, v in o.items() if k != "quote"}, "quote: unavailable"),
        (lambda o: _with(o, "chain", ok=False, error="HTTP 502"), "chain: unavailable (HTTP 502)"),
        (lambda o: _with(o, "quote", fields={"last": float("nan")}), "no finite positive last price"),
        # Python can represent integers that overflow float(); health evaluation must
        # still fail closed instead of raising on malformed provider/fixture data.
        (lambda o: _with(o, "quote", fields={"last": 10**10000}), "no finite positive last price"),
        (lambda o: _with(o, "quote", observed_at=None), "no source timestamp"),
        (lambda o: _with(o, "quote", observed_at=_iso(45)), "45 min stale"),
        (lambda o: _with(o, "quote", observed_at=(NOW + timedelta(minutes=5)).isoformat()), "in the future"),
        (lambda o: _with(o, "prior_close", fields={"close": 0.0}), "no finite positive close"),
        (lambda o: _with(o, "prior_close", fields={"date": "2026-09-02"}), "not a prior session"),
        (lambda o: _with(o, "bars", fields={"count": 0}), "no bars returned"),
        (lambda o: _with(o, "bars", fields={"last_bar_end": (NOW + timedelta(minutes=10)).isoformat()}), "future leakage"),
        (lambda o: _with(o, "chain", fields={"expirations": []}), "no expirations"),
        (lambda o: _with(o, "chain", fields={"expirations": ["garbage", "2026-08-01"]}), "no parseable current/future expiration"),
        (lambda o: _with(o, "chain", fields={"sample_contract": {"bid": 1.95, "ask": 2.05, "volume": 1, "open_interest": 1, "updated_at": _iso(1)}}), "missing iv, delta, theta"),
        (lambda o: _with(o, "chain", fields={"sample_contract": {**o["chain"].fields["sample_contract"], "delta": float("inf")}}), "delta is not finite"),
        (lambda o: _with(o, "chain", fields={"sample_contract": {**o["chain"].fields["sample_contract"], "volume": float("nan")}}), "volume is not finite"),
        (lambda o: _with(o, "chain", fields={"sample_contract": {**o["chain"].fields["sample_contract"], "updated_at": "yesterday"}}), "updated_at unparseable"),
        (lambda o: _with(o, "chain", fields={"sample_contract": {**o["chain"].fields["sample_contract"], "updated_at": (NOW + timedelta(minutes=5)).isoformat()}}), "timestamp -5.0 min in the future"),
        (lambda o: _with(o, "calendar", fields={"is_trading_day": False}), "not a trading day"),
    ],
)
def test_hard_gaps_block(mutate, fragment):
    report = evaluate_data_health(mutate(_ready()), ticker="XYZ", now=NOW)
    assert report.status == BLOCKED, report.reasons
    assert any(fragment in r for r in report.reasons), report.reasons


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda o: _with(o, "quote", observed_at=_iso(12)), "12 min stale"),
        (lambda o: _with(o, "prior_close", fields={"date": ""}), "close date missing"),
        (lambda o: _with(o, "bars", fields={"first_bar_start": "2026-09-02T14:00:00+00:00"}), "misaligned"),
        (lambda o: _with(o, "bars", fields={"last_bar_end": _iso(20)}), "last bar 20 min old"),
        (lambda o: _with(o, "bars", fields={"bounds": "extended"}), "not regular session"),
        (lambda o: _with(o, "chain", fields={"sample_contract": {**o["chain"].fields["sample_contract"], "ask": 1.90}}), "crossed or locked"),
        (lambda o: _with(o, "chain", fields={"sample_contract": {**o["chain"].fields["sample_contract"], "updated_at": _iso(20)}}), "20 min stale"),
        (lambda o: {k: v for k, v in o.items() if k != "signa"}, "signa: unavailable (observational only)"),
        (lambda o: _with(o, "signa", ok=False, error="timeout"), "signa: unavailable"),
        (lambda o: _with(o, "signa", fields={"technicals_as_of": "2026-08-21"}), "is stale (12 days; observational only)"),
        (lambda o: _with(o, "signa", fields={"stale": True}), "provider marks the signal stale"),
        (lambda o: _with(o, "signa", fields={"grade": None}), "grade/score missing"),
        (lambda o: {k: v for k, v in o.items() if k != "calendar"}, "calendar alignment unknown"),
        (lambda o: _with(o, "calendar", fields={"session_open": None}), "verified session open/close unavailable"),
    ],
)
def test_soft_gaps_degrade(mutate, fragment):
    report = evaluate_data_health(mutate(_ready()), ticker="XYZ", now=NOW)
    assert report.status == DEGRADED, report.reasons
    assert any(fragment in r for r in report.reasons), report.reasons


def test_longer_dated_chain_is_not_degraded_just_for_lacking_weeklies():
    obs = _with(_ready(), "chain", fields={"expirations": ["2026-12-18"]})
    report = evaluate_data_health(obs, ticker="XYZ", now=NOW)
    assert report.status == READY


def test_verified_early_close_controls_session_state():
    now = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)  # 13:00 ET
    obs = _ready()
    obs["calendar"] = SourceObservation(
        "calendar",
        True,
        now.isoformat(),
        {
            "is_trading_day": True,
            "session_open": "2026-11-27T14:30:00+00:00",
            "session_close": "2026-11-27T18:00:00+00:00",
            "is_early_close": True,
        },
        provider="calendar",
    )
    obs["quote"] = SourceObservation("quote", True, (now - timedelta(hours=2)).isoformat(), {"last": 123.4}, provider="rh")
    obs["bars"] = SourceObservation("bars", True, now.isoformat(), {"count": 42, "interval_minutes": 5, "bounds": "regular", "first_bar_start": "2026-11-27T14:30:00+00:00", "last_bar_end": "2026-11-27T18:00:00+00:00"}, provider="rh")
    obs["prior_close"] = SourceObservation("prior_close", True, now.isoformat(), {"close": 121.0, "date": "2026-11-25"}, provider="rh")
    obs["chain"] = SourceObservation("chain", True, now.isoformat(), {"expirations": ["2026-12-18"], "sample_contract": {"bid": 1.95, "ask": 2.05, "volume": 1401, "open_interest": 7683, "iv": 0.13, "delta": 0.57, "theta": -0.19, "updated_at": (now - timedelta(hours=2)).isoformat()}}, provider="rh")
    obs["signa"] = SourceObservation("signa", True, now.isoformat(), {"grade": "B", "score": 63.0, "technicals_as_of": "2026-11-27", "stale": False}, provider="signa")
    report = evaluate_data_health(obs, ticker="XYZ", now=now)
    assert report.in_session is False
    assert report.status == READY


def test_signa_never_blocks_and_gex_never_changes_status():
    obs = _with(_ready(), "signa", ok=False, error="down")
    assert evaluate_data_health(obs, ticker="XYZ", now=NOW).status == DEGRADED
    obs["gex"] = SourceObservation("gex", True, NOW.isoformat(), {"regime": "POSITIVE"})
    report = evaluate_data_health(obs, ticker="XYZ", now=NOW)
    assert report.source_status["gex"] == "UNAVAILABLE" and report.gex == GEX_STATUS


def test_out_of_session_staleness_is_not_penalized():
    evening = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    obs = _with(_ready(), "quote", observed_at=(evening - timedelta(minutes=200)).isoformat())
    obs = _with(obs, "bars", fields={"last_bar_end": (evening - timedelta(minutes=120)).isoformat()})
    report = evaluate_data_health(obs, ticker="XYZ", now=evening)
    assert report.in_session is False and report.status == READY


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        evaluate_data_health(_ready(), ticker="XYZ", now=datetime(2026, 9, 2, 14, 10))


def test_collectors_are_injectable_and_failures_are_reported_not_raised():
    def boom(ticker, now):
        raise RuntimeError("provider down")

    obs = collect_observations("XYZ", now=NOW, collectors={"quote": boom, "calendar": lambda t, n: _ready()["calendar"]})
    assert obs["quote"].ok is False and "provider down" in obs["quote"].error
    assert evaluate_data_health(obs, ticker="XYZ", now=NOW).status == BLOCKED


def test_default_collectors_never_place_orders():
    import ops.options_data_health as mod
    from pathlib import Path

    source = Path(mod.__file__).read_text()
    for forbidden in ("place_order", "submit", "create_order", "preview_order", "execution", "tradovate", "webhook"):
        assert forbidden not in source, forbidden


def test_json_observations_round_trip_and_cli(tmp_path, capsys):
    payload = {k: {"ok": v.ok, "observed_at": v.observed_at, "fields": dict(v.fields), "error": v.error, "provider": v.provider} for k, v in _ready().items()}
    path = tmp_path / "obs.json"
    path.write_text(json.dumps(payload))
    assert evaluate_data_health(observations_from_json(payload), ticker="XYZ", now=NOW).status == READY
    assert main(["--ticker", "XYZ", "--observations", str(path), "--now", NOW.isoformat()]) == 0
    assert "DATA HEALTH XYZ  READY" in capsys.readouterr().out
    payload["chain"]["ok"] = False
    path.write_text(json.dumps(payload))
    assert main(["--ticker", "XYZ", "--observations", str(path), "--now", NOW.isoformat(), "--json"]) == 2
    assert '"status": "BLOCKED"' in capsys.readouterr().out
    assert main(["--ticker", "XYZ", "--observations", str(path), "--now", "2026-09-02T14:10:00"]) == 2
