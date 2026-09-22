"""cross_instrument_observation_v1 — observation transport, hard boundaries.

Proves: collection-only roots (M2K/MGC/MCL/MBT) produce evidence while
DecisionEngine / RiskEngine / PaperBroker / Tradovate are unreachable, including
when the real book is blocked by an open position or daily limits; MNQ/MES
observation runs before those gates; Pine advisory brackets are ignored; M2K 5m
normalizes to M2K; sessions are product-aware; feed freshness is per
instrument; identity is campaign × strategy × instrument × variant × epoch;
zero-count populations stay visible; nothing is activated by default.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from context.bar_history import BarHistory
from context.futures_session import futures_session_active, product_of, product_session_active
from execution import cross_instrument_observation as cio
from journal.journal_logger import JournalLogger
from webhook.observation_transport import observe_collection_only_alert, strip_pine_advisory
from webhook.payload import AlertPayload
from webhook.runner import process_alert

ET = ZoneInfo("America/New_York")
DAY = date(2026, 9, 15)
EPOCH = "b4cb614+2026-09-16T00:00:00Z"
ENGINE_MODULES = ("strategy.signal_engine", "risk.risk_engine", "execution.paper_broker",
                  "execution.tradovate_broker", "webhook.runner", "execution.forward_evidence_campaign")


def _ts(hour: int, minute: int, day: date = DAY) -> str:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).isoformat()


def _payload(ticker="M2K1!", ts=None, *, o=2300.0, h=2305.0, l=2295.0, c=2302.0, tf="15", **extra) -> AlertPayload:
    data = {
        "ticker": ticker, "timestamp": ts or _ts(14, 30), "timeframe": tf,
        "open": o, "high": h, "low": l, "close": c, "volume": 1000, "avg_volume": 900,
        "vwap": 2298.0, "market_condition": "TRENDING", "trend_direction": "UP", "trend_strength": "MODERATE",
        "previous_day_high": 2320.0, "previous_day_low": 2280.0, "previous_day_close": 2299.0,
    }
    data.update(extra)
    return AlertPayload(**data)


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    assert cio.campaign_enabled()


@pytest.fixture
def engines_forbidden(monkeypatch):
    """Any touch of the execution path raises — the observation route must never trip it."""
    from strategy.signal_engine import DecisionEngine
    from risk.risk_engine import RiskEngine
    from execution.paper_broker import PaperBroker

    def boom(*a, **k):
        raise AssertionError("execution path reached from an observation route")

    monkeypatch.setattr(DecisionEngine, "evaluate", boom)
    monkeypatch.setattr(RiskEngine, "validate", boom)
    monkeypatch.setattr(PaperBroker, "execute_bracket", boom)
    monkeypatch.setattr(PaperBroker, "resolve_position", boom)
    try:
        import execution.tradovate_broker as tb
        for name in ("TradovateBroker",):
            if hasattr(tb, name):
                monkeypatch.setattr(getattr(tb, name), "__init__", boom)
    except Exception:
        pass


# ── 1. structural: no engine import from the observation modules ─────────────

def test_observation_modules_never_import_execution_code():
    code = (
        "import sys, webhook.observation_transport, execution.cross_instrument_observation, "
        "scripts.feed_watchdog, ops.cross_instrument_observation_report; "
        f"bad=[m for m in {ENGINE_MODULES!r} if m in sys.modules]; print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


def test_shadow_setups_has_no_risk_engine_link():
    import inspect
    from risk.risk_engine import RiskEngine
    from strategy import shadow_setups
    src = inspect.getsource(shadow_setups)
    assert "from risk.risk_engine" not in src and "import risk" not in src and "RiskEngine.calculate_rr(" not in src
    for args in (("LONG", 100, 96, 108), ("SHORT", 100, 104, 90), ("LONG", 100, 97.3, 103.1), ("X", 1, 2, 3)):
        assert shadow_setups._reward_to_risk(*args) == RiskEngine.calculate_rr(*args)


# ── 2. activation + identity + config ────────────────────────────────────────

def test_campaign_is_off_by_default_and_needs_both_env_vars(monkeypatch):
    monkeypatch.delenv(cio.ENV_NAME, raising=False)
    monkeypatch.delenv(cio.EPOCH_ENV_NAME, raising=False)
    assert not cio.campaign_enabled()
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    assert not cio.campaign_enabled()          # switch without epoch: still off
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    assert cio.campaign_enabled()
    monkeypatch.setenv(cio.ENV_NAME, "forward_ab_2026_08_v1")
    assert not cio.campaign_enabled()          # another campaign's id never enables this one


def test_config_identity_universe_and_populations():
    pops = cio.configured_populations(EPOCH)
    assert {p["instrument"] for p in pops} == set(cio.OBSERVATION_UNIVERSE)
    assert all(p["campaign_id"] == cio.CAMPAIGN_ID and p["evidence_epoch"] == EPOCH and p["variant"] == "observer" for p in pops)
    assert {p["collection_mode"] for p in pops} == {cio.STRUCTURAL_OUTCOME, cio.SIGNAL_METRICS}
    by = {(p["strategy"], p["instrument"]): p for p in pops}
    for inst in cio.OBSERVATION_UNIVERSE:                       # canonical 2-1-2 / 1-2-2 everywhere, structural
        assert by[("strat_212", inst)]["collection_mode"] == cio.STRUCTURAL_OUTCOME
        assert by[("strat_122", inst)]["collection_mode"] == cio.STRUCTURAL_OUTCOME
    for inst in cio.COLLECTION_ONLY_ROOTS:                      # DecisionEngine-backed observers: never
        assert ("vwap_hold_observed", inst) not in by and ("vwap_rejection_observed", inst) not in by
        assert ("strat_4hr_retrigger_observed", inst) not in by  # 4HR = current scope only
        assert ("strat_122_pullback", inst) not in by             # needs a policy table that does not exist
        assert by[("orb_false_break_fade", inst)]["collection_mode"] == cio.SIGNAL_METRICS
    assert ("gap_fill", "MBT") not in by and ("ovn_high_sweep_reclaim", "MBT") not in by
    assert cio.COLLECTION_ONLY_ROOTS == ("M2K", "MGC", "MCL", "MBT")
    assert cio.is_collection_only("CME_MINI:M2K1!") and cio.is_collection_only("MBTZ6") and not cio.is_collection_only("MNQ1!")


def test_population_key_requires_full_identity_and_universe():
    base = {"campaign_id": cio.CAMPAIGN_ID, "evidence_schema_version": cio.SCHEMA_VERSION,
            "strategy": "strat_212", "instrument": "M2K", "variant": "observer", "evidence_epoch": EPOCH}
    assert cio.population_key(base) == ("strat_212", "M2K", "observer", EPOCH)
    for bad in ({"evidence_epoch": None}, {"variant": ""}, {"instrument": "ES"}, {"campaign_id": "forward_ab_2026_08_v1"}):
        with pytest.raises(cio.ObservationError):
            cio.population_key({**base, **bad})


def test_existing_mnq_campaign_and_execution_universe_untouched():
    from config.settings import load_config
    from execution.evidence_identity import configured_populations
    from webhook.app import _INGEST_FUTURES_ROOTS
    fab = json.loads((Path(__file__).resolve().parents[1] / "config" / "forward_evidence_campaign.json").read_text())
    assert fab["campaign_id"] == "forward_ab_2026_08_v1" and fab["instrument"] == "MNQ" and len(fab["populations"]) == 5
    assert len(configured_populations()) == 5 and {p[1] for p in configured_populations()} == {"MNQ"}
    assert _INGEST_FUTURES_ROOTS == ("MNQ", "MES", "ES", "NQ", "MGC", "MCL")
    cfg = load_config("risk_rules.yaml")
    assert cfg.allowed_instruments == ["MNQ"] and not cfg.live_trading_enabled
    assert cio.EVIDENCE_FILENAME != "forward_ab_2026_08_v1.jsonl"


# ── 3. app routing ───────────────────────────────────────────────────────────

def test_route_collection_only_roots_to_observation_never_trading(monkeypatch):
    import webhook.app as app_module
    monkeypatch.delenv(cio.ENV_NAME, raising=False)
    monkeypatch.delenv(cio.EPOCH_ENV_NAME, raising=False)
    r = app_module._route_for_ticker
    assert r("MNQ1!") == "trading" and r("CME_MINI:MES1!") == "trading" and r("ESTC") is None and r("AAPL") is None
    assert r("MGC1!") == "observation" and r("MCLZ6") == "observation"      # diverted unconditionally
    assert r("M2K1!") is None and r("MBT1!") is None                         # gated until the campaign is armed
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID); monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    assert r("M2K1!") == "observation" and r("CME:MBTH27") == "observation"
    assert r("MNQ1!") == "trading"                                            # arming never changes the trading route


def test_webhook_endpoint_routes_m2k_to_observation_and_never_calls_process_alert(monkeypatch, tmp_path, armed):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import webhook.app as app_module
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret"); monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    app_module._RATE_BUCKETS.clear()
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    calls = {"trading": [], "observation": []}
    monkeypatch.setattr(app_module, "process_alert", lambda *a, **k: calls["trading"].append(a) or {"decision": "X"})

    def _obs(payload):
        calls["observation"].append(payload.ticker)
        app_module._handle_observation_blocking(payload)
    async def _obs_async(payload):
        _obs(payload)
    monkeypatch.setattr(app_module, "_process_observation_async", _obs_async)
    client = TestClient(app_module.app)
    body = _payload().model_dump(); body["secret"] = "s3cret"
    resp = client.post("/webhook/alert", json=body)
    assert resp.status_code == 200 and resp.json()["route"] == "observation_only"
    assert calls["trading"] == [] and calls["observation"] == ["M2K1!"]
    latest = json.loads((tmp_path / "latest_webhook_M2K.json").read_text())
    assert latest["result"]["decision"] == "OBSERVATION_ONLY"


def test_webhook_endpoint_ignores_m2k_and_mbt_while_campaign_is_off(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import webhook.app as app_module
    monkeypatch.delenv(cio.ENV_NAME, raising=False); monkeypatch.delenv(cio.EPOCH_ENV_NAME, raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret"); monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    app_module._RATE_BUCKETS.clear()
    client = TestClient(app_module.app)
    for ticker in ("M2K1!", "MBT1!"):
        body = _payload(ticker=ticker, c=65000.0 if ticker.startswith("MBT") else 2302.0).model_dump(); body["secret"] = "s3cret"
        assert client.post("/webhook/alert", json=body).json()["decision"] == "IGNORED"


# ── 4. runner backstop ───────────────────────────────────────────────────────

def test_process_alert_backstop_returns_observation_only_without_touching_anything(tmp_path, config, engines_forbidden, armed):
    for ticker, px in (("M2K1!", 2302.0), ("MGC1!", 2400.0), ("MCL1!", 65.0), ("MBT1!", 65000.0)):
        out = process_alert(_payload(ticker=ticker, o=px, h=px * 1.001, l=px * 0.999, c=px),
                            config=config, log_dir=str(tmp_path), for_date=DAY)
        assert out["decision"] == "OBSERVATION_ONLY" and out["execution_reachable"] is False
    assert not list(tmp_path.glob("journal_*.jsonl"))            # no journal, no daily state, no bars
    assert not list(tmp_path.glob("bars_*.jsonl"))


# ── 5. end-to-end: collection-only evidence while execution is unreachable ───

def _strat_212_sequence(ticker, day=DAY, base=2300.0, **extra):
    """Bar 1: 2U; bar 2: inside (arms 2-1-2 LONG at inside-high + 1 tick); bar 3: breaks the inside-bar high (fills)."""
    b = base
    return [
        _payload(ticker, _ts(14, 30, day), o=b, h=b + 10, l=b - 2, c=b + 8, previous_bar_type="2D", current_bar_type="2U", **extra),
        _payload(ticker, _ts(14, 45, day), o=b + 8, h=b + 9, l=b + 1, c=b + 5, previous_bar_type="2U", current_bar_type="1", **extra),
        _payload(ticker, _ts(15, 0, day), o=b + 6, h=b + 12, l=b + 4, c=b + 11, previous_bar_type="1", current_bar_type="2U", **extra),
    ]


def _rows(tmp_path):
    return cio.read_evidence(tmp_path)


def test_collection_only_alert_produces_evidence_while_engines_are_unreachable(tmp_path, config, armed, engines_forbidden):
    log_dir = str(tmp_path)
    # Real book BLOCKED: open MNQ position + daily capacity exhausted. None of this may matter.
    journal = JournalLogger(log_dir=log_dir)
    journal._append({"ts": datetime.now(timezone.utc).isoformat(), "instrument": "MNQ", "session": "new_york",
                     "decision": "TRADE", "reason": "seed", "market_condition": "TRENDING",
                     "context": {"timestamp": _ts(14, 0)},
                     "setup": {"direction": "LONG", "entry": 19500.0, "stop": 19460.0, "target": 19580.0, "rr_ratio": 2.0,
                               "strategy": "orb_reclaim", "notes": None, "contracts": 1},
                     "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None}, "outcome": None}, DAY)
    assert journal.get_daily_state(DAY).has_open_position

    results = [observe_collection_only_alert(p, config=replace(config, max_trades_per_day=0), log_dir=log_dir, for_date=DAY)
               for p in _strat_212_sequence("M2K1!", entry=9999.0, stop=9990.0, target=10020.0, signal_strategy="orb_breakout")]
    assert all(r["decision"] == "OBSERVATION_ONLY" and r["execution_reachable"] is False for r in results)
    assert all(r["observation"]["pine_advisory_ignored"] == {"entry": 9999.0, "stop": 9990.0, "target": 10020.0,
                                                              "signal_strategy": "orb_breakout"} for r in results)
    rows = _rows(tmp_path)
    cands = [r for r in rows if r["record_type"] == "CANDIDATE" and r["strategy"] == "strat_212"]
    assert len(cands) == 1
    c = cands[0]
    assert (c["campaign_id"], c["instrument"], c["variant"], c["evidence_epoch"]) == (cio.CAMPAIGN_ID, "M2K", "observer", EPOCH)
    assert c["collection_only"] is True and c["execution_reachable"] is False and c["bracket_authoritative"] is True
    assert c["direction"] == "LONG" and c["entry"] == pytest.approx(2309.1) and c["stop"] == 2301.0   # inside-bar high + one 0.10 tick
    assert c["entry"] != 9999.0 and c["pine_advisory_ignored"]["entry"] == 9999.0                     # Pine geometry never adopted
    assert c["tick_size"] == 0.10 and c["tick_value_dollars"] == 0.50 and c["source"] == "observation_transport"
    # The real book is still blocked and untouched.
    assert journal.get_daily_state(DAY).has_open_position
    assert not any("M2K" in (row.get("instrument") or "") for row in journal.read_day(DAY))
    assert (tmp_path / "bars_M2K_2026-09-15.jsonl").exists()


@pytest.mark.parametrize("ticker,close", [("MGC1!", 2400.0), ("MCL1!", 65.0), ("MBT1!", 65000.0)])
def test_every_collection_only_root_records_bars_and_feed_without_engines(tmp_path, config, armed, engines_forbidden, ticker, close):
    out = observe_collection_only_alert(_payload(ticker, o=close, h=close * 1.001, l=close * 0.999, c=close),
                                        config=config, log_dir=str(tmp_path), for_date=DAY)
    root = ticker.replace("1!", "")
    assert out["decision"] == "OBSERVATION_ONLY" and out["observation"]["bar_recorded"] is True and out["instrument"] == root
    assert (tmp_path / f"bars_{root}_2026-09-15.jsonl").exists()
    assert not list(tmp_path.glob("journal_*.jsonl"))


def test_mnq_observation_runs_before_capacity_and_open_position_gates(tmp_path, config, armed):
    log_dir = str(tmp_path)
    cfg = replace(config, max_trades_per_day=0, bonus_trades_after_max=0, enabled_concepts=[])  # capacity exhausted
    seq = _strat_212_sequence("MNQ1!", base=19500.0)
    results = [process_alert(p, config=cfg, log_dir=log_dir, for_date=DAY) for p in seq]
    assert all(r["decision"] == "BLOCKED_MAX_TRADES" for r in results)
    assert all("cross_instrument_observation" in r for r in results)
    cands = [r for r in _rows(tmp_path) if r["record_type"] == "CANDIDATE" and r["instrument"] == "MNQ" and r["strategy"] == "strat_212"]
    assert len(cands) == 1 and cands[0]["source"] == "process_alert" and cands[0]["entry"] == pytest.approx(19509.25)

    # Open position: still observed (fresh log dir, same sequence).
    log2 = tmp_path / "open"; log2.mkdir()
    journal = JournalLogger(log_dir=str(log2))
    journal._append({"ts": datetime.now(timezone.utc).isoformat(), "instrument": "MNQ", "session": "new_york",
                     "decision": "TRADE", "reason": "seed", "market_condition": "TRENDING", "context": {"timestamp": _ts(14, 0)},
                     "setup": {"direction": "LONG", "entry": 19500.0, "stop": 19460.0, "target": 19580.0, "rr_ratio": 2.0,
                               "strategy": "orb_reclaim", "notes": None, "contracts": 1},
                     "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None}, "outcome": None}, DAY)
    results = [process_alert(p, config=replace(config, enabled_concepts=[]), log_dir=str(log2), for_date=DAY) for p in seq]
    assert all(r["decision"] == "BLOCKED_OPEN_POSITION" for r in results)
    assert len([r for r in _rows(log2) if r["record_type"] == "CANDIDATE" and r["strategy"] == "strat_212"]) == 1


def test_campaign_off_writes_nothing_anywhere(tmp_path, config, monkeypatch):
    monkeypatch.delenv(cio.ENV_NAME, raising=False); monkeypatch.delenv(cio.EPOCH_ENV_NAME, raising=False)
    for p in _strat_212_sequence("MGC1!", base=2400.0):
        out = observe_collection_only_alert(p, config=config, log_dir=str(tmp_path), for_date=DAY)
        assert out["observation"]["enabled"] is False
    assert not (tmp_path / cio.EVIDENCE_FILENAME).exists() and not (tmp_path / cio.STATE_FILENAME).exists()
    for p in _strat_212_sequence("MNQ1!", base=19500.0):
        process_alert(p, config=replace(config, enabled_concepts=[]), log_dir=str(tmp_path), for_date=DAY)
    assert not (tmp_path / cio.EVIDENCE_FILENAME).exists()


# ── 6. 5-minute lane: canonical root ─────────────────────────────────────────

def test_m2k_five_minute_bars_land_under_the_m2k_root(tmp_path, config, armed, engines_forbidden):
    from context.five_min_feed import _root
    assert _root("M2K1!") == "M2K" and _root("CME_MINI:M2KZ6") == "M2K" and _root("MBT1!") == "MBT"
    assert _root("MYM1!") == "MYM"                                             # unknown roots keep the legacy parse
    out = observe_collection_only_alert(_payload("M2K1!", tf="5"), config=config, log_dir=str(tmp_path), for_date=DAY)
    assert out["observation"]["lane"] == "5m_feed" and out["decision"] == "OBSERVATION_ONLY"
    assert (tmp_path / "tf5m" / "bars_M2K_2026-09-15.jsonl").exists()
    assert not (tmp_path / "tf5m" / "bars_M_2026-09-15.jsonl").exists()


# ── 7. 1-minute lane: collection-only, isolated, execution-inert ───────────

@pytest.mark.parametrize(
    "ticker,root,price",
    [
        ("M2K1!", "M2K", 2300.0),
        ("MGC1!", "MGC", 2400.0),
        ("MCL1!", "MCL", 65.0),
        ("MBT1!", "MBT", 65000.0),
    ],
)
def test_collection_only_one_minute_bars_land_in_tf1m_only(
    monkeypatch, tmp_path, config, armed, engines_forbidden, ticker, root, price
):
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    out = observe_collection_only_alert(
        _payload(ticker, tf="1", o=price, h=price * 1.001, l=price * 0.999, c=price),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert out["decision"] == "OBSERVATION_ONLY"
    assert out["execution_reachable"] is False
    assert out["observation"]["lane"] == "1m_feed"
    assert out["observation"]["timeframe_minutes"] == 1
    assert out["observation"]["bar_recorded"] is True
    assert (tmp_path / "tf1m" / f"bars_{root}_2026-09-15.jsonl").exists()
    assert not list(tmp_path.glob("journal_*.jsonl"))
    assert not (tmp_path / cio.EVIDENCE_FILENAME).exists()


def test_collection_only_one_minute_lane_stays_off_with_flag_disabled(
    monkeypatch, tmp_path, config, armed, engines_forbidden
):
    monkeypatch.delenv("ONE_MIN_TRIGGER_ENABLED", raising=False)
    out = observe_collection_only_alert(
        _payload("M2K1!", tf="1"),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert out["decision"] == "OBSERVATION_ONLY"
    assert out["execution_reachable"] is False
    assert out["observation"]["lane"] == "unsupported_timeframe"
    assert out["observation"]["bar_recorded"] is False
    assert not (tmp_path / "tf1m").exists()


# ── 8. observe_bar / resolution semantics ────────────────────────────────────

def _state(instrument="M2K", ts=None, o=100.0, h=101.0, l=99.0, c=100.5):
    return build_state(_payload(f"{instrument}1!", ts or _ts(14, 30), o=o, h=h, l=l, c=c))


def build_state(payload):
    from webhook.state_builder import build_market_state
    return build_market_state(payload)


def test_signal_metrics_rows_are_never_resolved_and_unconfigured_are_dropped(tmp_path, armed):
    st = _state("M2K")
    summary = cio.observe_bar(tmp_path, st, [
        {"strategy": "gap_fill", "direction": "SHORT", "entry": 100.0, "stop": 101.0, "target": 98.0},
        {"strategy": "vwap_hold_observed", "direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 102.0},
        {"strategy": "strat_4hr_retrigger_observed", "direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 102.0},
        {"strategy": "strat_22_continuation_observed", "direction": "LONG", "entry": 101.1, "stop": 98.9, "target": 105.5},
    ], timeframe="15", for_date=DAY, source="test", include_strat_212_122=False)
    assert summary["written"] == 2 and summary["signal"] == 1 and summary["structural"] == 1
    assert sorted(summary["unconfigured_dropped"]) == ["strat_4hr_retrigger_observed", "vwap_hold_observed"]
    rows = _rows(tmp_path)
    sig = next(r for r in rows if r["strategy"] == "gap_fill")
    assert sig["record_type"] == "SIGNAL" and sig["bracket_authoritative"] is False
    state = json.loads((tmp_path / cio.STATE_FILENAME).read_text())
    assert [p["record"]["strategy"] for p in state["pending"].values()] == ["strat_22_continuation_observed"]
    # Duplicate bar → nothing new.
    again = cio.observe_bar(tmp_path, st, [{"strategy": "gap_fill", "direction": "SHORT", "entry": 100.0, "stop": 101.0, "target": 98.0}],
                            timeframe="15", for_date=DAY, source="test", include_strat_212_122=False)
    assert again["duplicate"] is True and len(_rows(tmp_path)) == 2


def test_structural_resolution_win_loss_no_fill_and_day_rollover(tmp_path, armed):
    def bars(*rows):
        return [{"ts": t, "open": o, "high": h, "low": l, "close": c} for (t, o, h, l, c) in rows]
    st = _state("M2K", ts=_ts(14, 30))
    cio.observe_bar(tmp_path, st, [
        {"strategy": "strat_22_continuation_observed", "direction": "LONG", "entry": 101.0, "stop": 99.0, "target": 105.0},   # WIN
        {"strategy": "strat_312_observed", "direction": "SHORT", "entry": 99.0, "stop": 101.0, "target": 95.0},             # LOSS
        {"strategy": "trend_consolidation_break_observed", "direction": "LONG", "entry": 150.0, "stop": 149.0, "target": 152.0},  # NO_FILL
    ], timeframe="15", for_date=DAY, source="test", include_strat_212_122=False)
    hist = bars((_ts(14, 30), 100, 101, 99, 100.5),         # signal bar: never used for resolution
                (_ts(14, 45), 100.5, 101.5, 99.5, 101.2),   # LONG fills @101 (stop 99 untouched, MAE 1.5 = 0.75R); SHORT no fill
                (_ts(15, 0), 101.2, 106.0, 100.0, 105.5),   # LONG target 105 hit → WIN (MFE 5 = 2.5R)
                (_ts(15, 15), 100.0, 100.5, 98.5, 99.0),    # SHORT fills @99 (stop 101 untouched on fill bar)
                (_ts(15, 30), 99.0, 101.2, 98.0, 100.0))    # SHORT stop 101 hit → LOSS
    out = cio.resolve_pending(tmp_path, instrument="M2K", bars=hist, current_bar_ts=_ts(15, 30), for_date=DAY)
    by = {r["strategy"]: r for r in out}
    assert by["strat_22_continuation_observed"]["result"] == "WIN" and by["strat_22_continuation_observed"]["pnl_r"] == pytest.approx(2.0)
    assert by["strat_22_continuation_observed"]["mfe_r"] == pytest.approx(2.5) and by["strat_22_continuation_observed"]["mae_r"] == pytest.approx(0.75)
    assert by["strat_22_continuation_observed"]["pnl_ticks"] == pytest.approx(40.0)         # 4 pts / 0.10
    assert by["strat_22_continuation_observed"]["gross_pnl_dollars_1_contract"] == pytest.approx(20.0)  # $0.50/tick
    assert by["strat_22_continuation_observed"]["commission_assumption_dollars"] is None    # no cost proof claimed
    assert by["strat_312_observed"]["result"] == "LOSS" and by["strat_312_observed"]["pnl_r"] == pytest.approx(-1.0)
    assert "trend_consolidation_break_observed" not in by                                    # still pending, never filled
    # Next trading date: the unfilled one expires as NO_FILL.
    out2 = cio.resolve_pending(tmp_path, instrument="M2K", bars=hist, current_bar_ts=_ts(14, 30, DAY + timedelta(days=1)),
                               for_date=DAY + timedelta(days=1))
    assert [(r["strategy"], r["result"]) for r in out2] == [("trend_consolidation_break_observed", "NO_FILL")]
    assert json.loads((tmp_path / cio.STATE_FILENAME).read_text())["pending"] == {}


def test_strat_212_detector_is_generic_and_pre_resolved_same_bar_is_pessimistic(tmp_path, armed):
    # MBT: tick 5.0; inside bar high 65010 → entry 65015; watch bar gaps through both sides → pessimistic LOSS.
    seq = [_payload("MBT1!", _ts(14, 30), o=65000, h=65020, l=64990, c=65015, previous_bar_type="2D", current_bar_type="2U"),
           _payload("MBT1!", _ts(14, 45), o=65015, h=65010, l=65000, c=65005, previous_bar_type="2U", current_bar_type="1"),
           _payload("MBT1!", _ts(15, 0), o=65012, h=65100, l=64900, c=65050, previous_bar_type="1", current_bar_type="2U")]
    for p in seq:
        cio.observe_bar(tmp_path, build_state(p), [], timeframe="15", for_date=DAY, source="test")
    rows = _rows(tmp_path)
    cand = next(r for r in rows if r["record_type"] == "CANDIDATE")
    outcome = next(r for r in rows if r["record_type"] == "OUTCOME")
    assert cand["strategy"] == "strat_212" and cand["entry"] == pytest.approx(65015.0) and cand["tick_size"] == 5.0
    assert outcome["candidate_id"] == cand["candidate_id"] and outcome["result"] == "LOSS"
    assert json.loads((tmp_path / cio.STATE_FILENAME).read_text())["pending"] == {}


# ── 8. identity across epochs + report ───────────────────────────────────────

def test_two_epochs_never_share_ids_or_populations(tmp_path, monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    st = _state("MCL", o=60.0, h=61.0, l=59.0, c=60.5)
    cand = [{"strategy": "strat_22_reversal_observed", "direction": "LONG", "entry": 61.01, "stop": 58.99, "target": 65.05}]
    ids = []
    for epoch in ("epoch-A", "epoch-B"):
        monkeypatch.setenv(cio.EPOCH_ENV_NAME, epoch)
        d = tmp_path / epoch; d.mkdir()
        cio.observe_bar(d, st, cand, timeframe="15", for_date=DAY, source="test", include_strat_212_122=False)
        ids.append(_rows(d)[0]["candidate_id"])
        assert _rows(d)[0]["evidence_epoch"] == epoch
    assert ids[0] != ids[1]


def test_report_lists_every_population_at_zero_and_never_pools(tmp_path, armed):
    report = cio.build_report(tmp_path)
    assert report["pooling_across_populations"] is False and report["grants_execution_eligibility"] is False
    assert len(report["populations"]) == len(cio.configured_populations()) and all(
        p["candidates"] == 0 and p["terminal_outcomes"] == 0 and p["status"] == "NOT COLLECTING" for p in report["populations"])
    assert {p["instrument"] for p in report["populations"]} == set(cio.OBSERVATION_UNIVERSE)
    # 8 M2K + 8 MGC + 8 MCL + 8 MBT WIN outcomes over 12 days = 32 pooled → each population INSUFFICIENT.
    for i in range(12):
        for inst in cio.COLLECTION_ONLY_ROOTS:
            if i >= 8:
                continue
            day = (DAY - timedelta(days=i)).isoformat()
            cio._append_evidence(tmp_path, {
                "evidence_schema_version": cio.SCHEMA_VERSION, "campaign_id": cio.CAMPAIGN_ID, "record_type": "OUTCOME",
                "candidate_id": f"x-{inst}-{i}", "strategy": "strat_212", "instrument": inst, "variant": "observer",
                "evidence_epoch": EPOCH, "signal_timestamp": f"{day}T14:30:00+00:00", "result": "WIN", "pnl_r": 2.0})
    report = cio.build_report(tmp_path)
    pops = {(p["instrument"], p["strategy"]): p for p in report["populations"]}
    assert sum(p["terminal_outcomes"] for p in report["populations"]) == 32
    for inst in cio.COLLECTION_ONLY_ROOTS:
        assert pops[(inst, "strat_212")]["terminal_outcomes"] == 8 and pops[(inst, "strat_212")]["status"] == "INSUFFICIENT SAMPLE"
    assert not any(p["status"] == "READY FOR REVIEW" for p in report["populations"])
    # Rows from another epoch are visible but never review-eligible.
    cio._append_evidence(tmp_path, {"evidence_schema_version": cio.SCHEMA_VERSION, "campaign_id": cio.CAMPAIGN_ID,
                                    "record_type": "OUTCOME", "candidate_id": "old", "strategy": "strat_212", "instrument": "M2K",
                                    "variant": "observer", "evidence_epoch": "old-epoch", "signal_timestamp": "2026-09-01T14:30:00+00:00",
                                    "result": "WIN", "pnl_r": 2.0})
    report = cio.build_report(tmp_path)
    assert report["unconfigured_rows"] == [{"strategy": "strat_212", "instrument": "M2K", "variant": "observer", "evidence_epoch": "old-epoch", "rows": 1}]


# ── 9. sessions ──────────────────────────────────────────────────────────────

def test_sessions_are_product_aware():
    sat_noon = datetime(2026, 9, 19, 12, 0, tzinfo=ET)
    assert product_of("MBT") == "crypto" and product_of("MGC") == "metals_energy" and product_of("M2K") == "equity_index"
    assert product_session_active("MBT", sat_noon) is True                       # 24/7
    assert product_session_active("MNQ", sat_noon) is False and product_session_active("MGC", sat_noon) is False
    assert product_session_active("MBT", datetime(2026, 9, 19, 4, 0, tzinfo=ET)) is False   # special Sat maintenance
    assert product_session_active("MBT", datetime(2026, 9, 19, 8, 59, tzinfo=ET)) is False  # extended through 08:00 CT
    assert product_session_active("MBT", datetime(2026, 9, 19, 9, 0, tzinfo=ET)) is True     # reopen at 08:00 CT
    assert product_session_active("MBT", datetime(2026, 9, 26, 4, 0, tzinfo=ET)) is False   # normal Sat maintenance
    assert product_session_active("MBT", datetime(2026, 9, 26, 5, 0, tzinfo=ET)) is True    # normal Sat reopen
    assert product_session_active("MBT", datetime(2026, 9, 14, 17, 1, tzinfo=ET)) is False  # Mon–Fri 16:00–16:02 CT
    assert product_session_active("MBT", datetime(2026, 9, 14, 17, 30, tzinfo=ET)) is True  # no 17:00–18:00 break for crypto
    # CME removed the 16:15–16:30 ET equity-index halt effective 2021-06-28.
    assert product_session_active("MNQ", datetime(2026, 9, 14, 16, 20, tzinfo=ET)) is True
    assert product_session_active("M2K", datetime(2026, 9, 14, 16, 15, tzinfo=ET)) is True
    assert product_session_active("MES", datetime(2026, 9, 14, 16, 29, tzinfo=ET)) is True
    assert product_session_active("MGC", datetime(2026, 9, 14, 16, 20, tzinfo=ET)) is True
    assert product_session_active("MNQ", datetime(2026, 9, 14, 17, 0, tzinfo=ET)) is False   # daily break
    assert product_session_active("MCL", datetime(2026, 9, 14, 17, 30, tzinfo=ET)) is False  # daily break
    assert product_session_active("XYZ", sat_noon) is None                                 # unknown = unknown
    # Legacy helper untouched for its existing callers.
    assert futures_session_active(datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)) is True
    assert futures_session_active(datetime(2026, 9, 14, 16, 20, tzinfo=ET)) is True


# ── 10. per-instrument feed freshness ────────────────────────────────────────

def _write_latest(tmp_path: Path, root: str | None, received_at: datetime, tf="15"):
    name = "latest_webhook.json" if root is None else f"latest_webhook_{root}.json"
    (tmp_path / name).write_text(json.dumps({"received_at": received_at.isoformat(), "payload": {"timeframe": tf, "ticker": f"{root}1!"},
                                             "result": {"decision": "OBSERVATION_ONLY"}}), encoding="utf-8")


def test_watchdog_flags_a_dead_instrument_behind_a_healthy_one(tmp_path):
    from scripts import feed_watchdog as fw
    now = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)   # Monday 10:00 ET
    cfg = SimpleNamespace(log_dir=str(tmp_path), expected_timeframe_minutes=15, discord_webhook_url="x")
    _write_latest(tmp_path, None, now - timedelta(minutes=2))
    _write_latest(tmp_path, "MNQ", now - timedelta(minutes=2))
    _write_latest(tmp_path, "M2K", now - timedelta(minutes=90))
    msgs = []
    out = fw.run(now=now, send=lambda c, m: msgs.append(m) or SimpleNamespace(sent=True), config=cfg)
    assert out["action"] == "ok"                                        # global feed healthy…
    assert out["instruments"]["stale"] and "M2K" in out["instruments"]["stale"][0]   # …but M2K is dead
    assert any("INSTRUMENT FEED STALE" in m and "M2K" in m for m in msgs) and not any("MNQ (" in m for m in msgs)
    # Recovery is per instrument too.
    _write_latest(tmp_path, "M2K", now + timedelta(minutes=5) - timedelta(minutes=1))
    msgs.clear()
    out = fw.run(now=now + timedelta(minutes=5), send=lambda c, m: msgs.append(m) or SimpleNamespace(sent=True), config=cfg)
    assert out["instruments"]["recovered"] == ["M2K"] and any("recovered: M2K" in m for m in msgs)


def test_watchdog_uses_crypto_calendar_for_mbt_and_ignores_never_reported(tmp_path):
    from scripts import feed_watchdog as fw
    sat_noon = datetime(2026, 9, 19, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    cfg = SimpleNamespace(log_dir=str(tmp_path), expected_timeframe_minutes=15, discord_webhook_url="x")
    _write_latest(tmp_path, "MBT", sat_noon - timedelta(hours=3))
    _write_latest(tmp_path, "MNQ", sat_noon - timedelta(hours=20))   # equity: expected idle on Saturday
    msgs = []
    out = fw.run(now=sat_noon, send=lambda c, m: msgs.append(m) or SimpleNamespace(sent=True), config=cfg)
    assert out["action"] == "idle_session"                            # legacy equity gate
    assert [s.split(" ")[0] for s in out["instruments"]["stale"]] == ["MBT"]   # crypto trades Saturday: MBT is dead
    assert "M2K" not in json.dumps(out["instruments"])                # never reported = not an outage


def test_status_observation_feeds_lists_every_instrument_on_its_own(tmp_path, monkeypatch, armed):
    import webhook.app as app_module
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    now = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    _write_latest(tmp_path, "MNQ", now - timedelta(minutes=1))
    _write_latest(tmp_path, "MCL", now - timedelta(minutes=45))
    status = app_module.observation_feed_status(now=now)
    assert set(status["instruments"]) == set(cio.OBSERVATION_UNIVERSE)
    assert status["instruments"]["MNQ"]["stale"] is False and status["instruments"]["MCL"]["stale"] is True
    assert status["instruments"]["M2K"]["ever_received"] is False and status["instruments"]["M2K"]["stale"] is True
    assert status["instruments"]["MBT"]["product"] == "crypto" and status["instruments"]["MBT"]["route"] == "observation_only"
    assert status["instruments"]["MNQ"]["route"] == "trading" and status["stale_instruments"] == ["M2K", "MBT", "MCL", "MES", "MGC"]
    assert status["campaign"]["enabled"] is True and status["campaign"]["evidence_epoch"] == EPOCH


# ── 11. Pine advisory stripping ──────────────────────────────────────────────

def test_strip_pine_advisory_clears_every_bracket_field_and_reports_them():
    p = _payload(entry=1.0, stop=2.0, target=3.0, signal_strategy="vwap_hold", signal_direction="LONG")
    clean, ignored = strip_pine_advisory(p)
    assert ignored == {"entry": 1.0, "stop": 2.0, "target": 3.0, "signal_strategy": "vwap_hold", "signal_direction": "LONG"}
    assert all(getattr(clean, f) is None for f in ("entry", "stop", "target", "signal_strategy", "signal_direction"))
    assert clean.close == p.close and clean.ticker == p.ticker
    same, none = strip_pine_advisory(_payload())
    assert none is None and same is not None
