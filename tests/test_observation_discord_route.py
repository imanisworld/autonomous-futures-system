"""Dedicated Discord route for cross_instrument_observation_v1 — OBSERVATION ONLY.

Proves: the route is env-backed (no credential in code/YAML); collection-only
roots' observation events reach the ``observation`` route with root + label;
an unset route never breaks collection; watchdog/error/signal routes are
unchanged; and the notification adds no path from M2K/MGC/MCL/MBT into the
execution pipeline.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from execution import cross_instrument_observation as cio
from notifications import observation_notifier as obs
from notifications.discord_router import DiscordRouter, load_routes
from webhook.observation_transport import observe_collection_only_alert
from webhook.payload import AlertPayload

DAY = date(2026, 9, 15)
EPOCH = "obs-route-epoch"
ROOT = Path(__file__).resolve().parents[1]


def _root(msg: str) -> str:
    """Card title is '<emoji> <ROOT> practice …'; the root is its second token."""
    return msg.splitlines()[0].split()[1]


def _is_observation_card(msg: str, root: str) -> bool:
    return _root(msg) == root and msg.splitlines()[-1].startswith("OBSERVATION ONLY")


def _ts(hour: int, minute: int) -> str:
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=timezone.utc).isoformat()


def _payload(ticker="MGC1!", ts=None, *, o=2400.0, h=2410.0, l=2398.0, c=2408.0, **extra) -> AlertPayload:
    data = {
        "ticker": ticker, "timestamp": ts or _ts(14, 30), "timeframe": "15",
        "open": o, "high": h, "low": l, "close": c, "volume": 1000, "avg_volume": 900,
        "vwap": 2399.0, "market_condition": "TRENDING", "trend_direction": "UP", "trend_strength": "MODERATE",
        "previous_day_high": 2420.0, "previous_day_low": 2380.0, "previous_day_close": 2399.0,
    }
    data.update(extra)
    return AlertPayload(**data)


def _strat_212_sequence(ticker, base):
    b = base
    return [
        _payload(ticker, _ts(14, 30), o=b, h=b + 10, l=b - 2, c=b + 8, previous_bar_type="2D", current_bar_type="2U"),
        _payload(ticker, _ts(14, 45), o=b + 8, h=b + 9, l=b + 1, c=b + 5, previous_bar_type="2U", current_bar_type="1"),
        _payload(ticker, _ts(15, 0), o=b + 6, h=b + 12, l=b + 4, c=b + 11, previous_bar_type="1", current_bar_type="2U"),
    ]


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)


@pytest.fixture
def capture_router(monkeypatch):
    """Route the real router at a fake transport; record (route, message)."""
    sent: list[tuple[str, str]] = []

    def transport(url, message):
        sent.append((url, message))

    real_init = DiscordRouter.__init__

    def patched_init(self, routes=None, routes_path=None, transport_=None, env=None):
        real_init(self, routes=routes, routes_path=routes_path, transport=transport, env=env)

    monkeypatch.setattr(DiscordRouter, "__init__", patched_init)
    return sent


# ── 1. configuration: env-backed, optional, no credential anywhere ────────────

def test_observation_route_is_env_backed_optional_and_uncommitted():
    routes = load_routes()
    assert "observation" in routes
    assert routes["observation"].env_var == "DISCORD_ROUTE_OBSERVATION"
    assert routes["observation"].required is False
    # No webhook URL in code, config, or this test tree.
    for path in (ROOT / "config" / "notification_routes.yaml",
                 ROOT / "notifications" / "observation_notifier.py",
                 ROOT / "notifications" / "discord_router.py",
                 ROOT / "webhook" / "observation_transport.py",
                 ROOT / "execution" / "cross_instrument_observation.py"):
        assert "discord.com/api/webhooks" not in path.read_text(encoding="utf-8"), path
    router = DiscordRouter(env={})
    assert router.is_enabled("observation") is False
    assert "observation" not in router.missing_required_routes()
    # Existing routes and their env names are unchanged.
    assert {n: (r.env_var, r.required) for n, r in routes.items() if n != "observation"} == {
        "heartbeat": ("DISCORD_ROUTE_HEARTBEAT", True),
        "signal": ("DISCORD_ROUTE_SIGNAL", True),
        "signa": ("DISCORD_ROUTE_SIGNA", False),
        "error": ("DISCORD_ROUTE_ERROR", True),
        "daily_report": ("DISCORD_ROUTE_DAILY_REPORT", False),
        "deployment": ("DISCORD_ROUTE_DEPLOYMENT", False),
    }


# ── 2. message format: root first, OBSERVATION ONLY always ───────────────────

def test_format_event_always_leads_with_root_and_label():
    cand = obs.format_event({"record_type": "CANDIDATE", "instrument": "MGC", "strategy": "strat_212",
                             "direction": "LONG", "entry": 2409.1, "stop": 2401.0, "target": 2425.3,
                             "signal_timestamp": _ts(15, 0), "tick_size": 0.1, "tick_value_dollars": 1.0})
    assert cand.startswith("👀 MGC practice buy setup spotted\nMarket: MGC (Micro Gold)")
    assert "Setup: 2-1-2 pattern" in cand and "Would buy at: 2,409.1" in cand
    assert "Stop-loss: 2,401 (would lose about $81.00)" in cand
    assert "Profit target: 2,425.3 (would make about $162.00)" in cand
    assert cand.splitlines()[-1].startswith("OBSERVATION ONLY")
    sig = obs.format_event({"record_type": "SIGNAL", "instrument": "M2K", "strategy": "ema_pullback_trend",
                            "direction": "SHORT", "entry": 1, "stop": 2, "target": 0, "signal_timestamp": _ts(15, 0)})
    assert sig.startswith("👀 M2K practice sell setup spotted") and "rough, from the chart alert" in sig
    assert "Stop-loss: 2\n" in sig                                                  # no tick data → no $ guess
    out = obs.format_event({"record_type": "OUTCOME", "instrument": "MBT", "strategy": "strat_212_observed",
                            "direction": "LONG", "result": "WIN", "pnl_r": 2.0, "exit_reason": "TARGET_HIT",
                            "gross_pnl_dollars_1_contract": 36.0, "resolved_at_bar_ts": _ts(15, 15)})
    assert out.startswith("🟢 MBT practice buy won\nMarket: MBT (Micro Bitcoin)\nSetup: 2-1-2 pattern")
    assert "Result: +$36.00 (1 contract, before fees)" in out and "How it ended: hit the profit target" in out
    for text in (cand, sig, out):                                                     # no jargon left
        assert "R\n" not in text and "Z\n" not in text and "UTC" not in text and " ET, " in text
    assert obs.format_event({"record_type": "CANDIDATE", "strategy": "x"}) is None          # no root → nothing
    assert obs.format_event({"record_type": "BAR", "instrument": "MGC", "strategy": "x"}) is None


def test_format_event_times_are_eastern_and_losses_are_negative_dollars():
    out = obs.format_event({"record_type": "OUTCOME", "instrument": "MNQ", "strategy": "strat_22_reversal_observed",
                            "direction": "SHORT", "result": "LOSS", "exit_reason": "STOP_HIT_ON_FILL_BAR",
                            "entry": 20000.0, "exit_price": 20010.0, "pnl_points": -10.0,
                            "tick_size": 0.25, "tick_value_dollars": 0.5,
                            "exit_timestamp": "2026-09-23T01:00:00+00:00"})
    assert out.startswith("🔴 MNQ practice sell lost")
    assert "Result: -$20.00 (1 contract, before fees)" in out
    assert "How it ended: hit the stop-loss right after entry" in out
    assert "Prices: in at 20,000, out at 20,010" in out
    assert "Closed: 9:00 PM ET, Tue Sep 22" in out


# ── 3. end-to-end: collection-only root → observation route only ─────────────

def test_collection_only_detection_routes_to_observation_with_root_and_label(tmp_path, config, armed, monkeypatch, capture_router):
    monkeypatch.setenv("DISCORD_ROUTE_OBSERVATION", "https://obs.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_SIGNAL", "https://signal.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_ERROR", "https://error.invalid/route")
    for p in _strat_212_sequence("MGC1!", 2400.0):
        out = observe_collection_only_alert(p, config=config, log_dir=str(tmp_path), for_date=DAY)
        assert out["decision"] == "OBSERVATION_ONLY" and out["execution_reachable"] is False
    rows = cio.read_evidence(tmp_path)
    assert any(r["record_type"] == "CANDIDATE" and r["strategy"] == "strat_212" for r in rows)
    assert capture_router, "a strat_212 candidate must produce one observation message"
    urls = {u for u, _ in capture_router}
    assert urls == {"https://obs.invalid/route"}                 # never signal/error
    for _, msg in capture_router:
        assert _is_observation_card(msg, "MGC")
        assert re.search(r"\bMGC\b", msg)
    assert any("practice buy setup spotted" in m and "Setup: 2-1-2 pattern" in m for _, m in capture_router)


def test_unset_observation_route_never_breaks_collection(tmp_path, config, armed, monkeypatch, capture_router):
    for name in ("DISCORD_ROUTE_OBSERVATION", "DISCORD_ROUTE_SIGNAL", "DISCORD_ROUTE_ERROR"):
        monkeypatch.delenv(name, raising=False)
    for p in _strat_212_sequence("MCL1!", 65.0):
        out = observe_collection_only_alert(p, config=config, log_dir=str(tmp_path), for_date=DAY)
        assert out["observation"]["transport_ok"] is True and out["observation"]["bar_recorded"] is True
    rows = cio.read_evidence(tmp_path)
    assert any(r["record_type"] == "CANDIDATE" and r["instrument"] == "MCL" for r in rows)
    assert capture_router == []                                  # nothing sent anywhere
    state = json.loads((tmp_path / cio.STATE_FILENAME).read_text())
    assert state["pending"]                                      # collection state intact


def test_notifier_failure_is_swallowed(tmp_path, config, armed, monkeypatch):
    monkeypatch.setenv("DISCORD_ROUTE_OBSERVATION", "https://obs.invalid/route")

    def boom(*a, **k):
        raise RuntimeError("discord down")

    monkeypatch.setattr("notifications.discord_router.DiscordRouter.send", boom)
    for p in _strat_212_sequence("M2K1!", 2300.0):
        out = observe_collection_only_alert(p, config=config, log_dir=str(tmp_path), for_date=DAY)
        assert out["observation"]["transport_ok"] is True
    assert any(r["record_type"] == "CANDIDATE" for r in cio.read_evidence(tmp_path))


# ── 4. boundary: notification adds no execution path ─────────────────────────

def test_collection_only_roots_still_cannot_reach_execution_with_route_enabled(tmp_path, config, armed, monkeypatch, capture_router):
    from strategy.signal_engine import DecisionEngine
    from risk.risk_engine import RiskEngine
    from execution.paper_broker import PaperBroker
    from webhook.runner import process_alert

    monkeypatch.setenv("DISCORD_ROUTE_OBSERVATION", "https://obs.invalid/route")

    def boom(*a, **k):
        raise AssertionError("execution path reached from an observation route")

    monkeypatch.setattr(DecisionEngine, "evaluate", boom)
    monkeypatch.setattr(RiskEngine, "validate", boom)
    monkeypatch.setattr(PaperBroker, "execute_bracket", boom)
    monkeypatch.setattr(PaperBroker, "resolve_position", boom)
    for ticker, base in (("M2K1!", 2300.0), ("MGC1!", 2400.0), ("MCL1!", 65.0), ("MBT1!", 65000.0)):
        for p in _strat_212_sequence(ticker, base):
            observe_collection_only_alert(p, config=config, log_dir=str(tmp_path), for_date=DAY)
        out = process_alert(_payload(ticker, o=base, h=base * 1.001, l=base * 0.999, c=base),
                            config=config, log_dir=str(tmp_path), for_date=DAY)
        assert out["decision"] == "OBSERVATION_ONLY" and out["execution_reachable"] is False
    assert not list(tmp_path.glob("journal_*.jsonl"))            # no trade/decision rows
    assert capture_router and all(u == "https://obs.invalid/route" for u, _ in capture_router)
    assert {_root(m) for _, m in capture_router} <= {"M2K", "MGC", "MCL", "MBT"}
    # The notifier module itself imports nothing from the execution/risk/broker stack.
    src = (ROOT / "notifications" / "observation_notifier.py").read_text(encoding="utf-8")
    for forbidden in ("signal_engine", "risk_engine", "paper_broker", "tradovate", "webhook.runner"):
        assert forbidden not in src


# ── 5. existing routes unchanged ─────────────────────────────────────────────

def test_mnq_signal_route_and_watchdog_error_route_are_unchanged(monkeypatch, tmp_path, capture_router):
    monkeypatch.setenv("DISCORD_ROUTE_SIGNAL", "https://signal.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_ERROR", "https://error.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_OBSERVATION", "https://obs.invalid/route")
    router = DiscordRouter()
    assert router.send("signal", "MNQ TRADE (existing signal path)") is True
    assert router.send("error", "🚨 No price updates for some markets") is True
    assert [u for u, _ in capture_router] == ["https://signal.invalid/route", "https://error.invalid/route"]
    # The watchdog module never references the observation route.
    wd = (ROOT / "scripts" / "feed_watchdog.py").read_text(encoding="utf-8")
    assert "observation_notifier" not in wd and '"observation"' not in wd
    # The MNQ/MES signal dispatch in app.py still targets "signal" and only "signal".
    app_src = (ROOT / "webhook" / "app.py").read_text(encoding="utf-8")
    assert '_router.send("signal", _card(payload, result))' in app_src
    assert 'send("observation"' not in app_src


def test_mnq_campaign_leg_uses_observation_route_not_signal(tmp_path, config, armed, monkeypatch, capture_router):
    """MNQ participates in the campaign: its campaign notice goes to observation,
    while process_alert's own decision/signal path is untouched (still a normal
    decision with no signal send from the runner)."""
    from webhook.runner import process_alert

    monkeypatch.setenv("DISCORD_ROUTE_OBSERVATION", "https://obs.invalid/route")
    monkeypatch.setenv("DISCORD_ROUTE_SIGNAL", "https://signal.invalid/route")
    cfg = replace(config, enabled_concepts=[])
    for p in _strat_212_sequence("MNQ1!", 19500.0):
        out = process_alert(p, config=cfg, log_dir=str(tmp_path), for_date=DAY)
        assert "cross_instrument_observation" in out
    assert capture_router and all(u == "https://obs.invalid/route" for u, _ in capture_router)
    assert all(_is_observation_card(m, "MNQ") for _, m in capture_router)


# ── 6. idempotence: an already-persisted OUTCOME with stale pending state ────

def test_stale_pending_with_persisted_outcome_is_cleared_without_duplicate_event(tmp_path, armed, monkeypatch, capture_router):
    """Simulates a crash between the OUTCOME append and the state save: the
    evidence file already holds the OUTCOME row but the candidate is still in
    ``pending``. resolve_pending must clear the pending entry, must NOT append
    a second OUTCOME row, must NOT return it as newly resolved, and therefore
    must NOT notify Discord again."""
    monkeypatch.setenv("DISCORD_ROUTE_OBSERVATION", "https://obs.invalid/route")
    signal_ts = _ts(15, 0)
    record = {
        "evidence_schema_version": cio.SCHEMA_VERSION, "campaign_id": cio.CAMPAIGN_ID,
        "record_type": "CANDIDATE", "candidate_id": "stale-pending-1", "strategy": "strat_212",
        "instrument": "M2K", "variant": "observer", "evidence_epoch": EPOCH,
        "collection_mode": cio.STRUCTURAL_OUTCOME, "direction": "LONG", "signal_timestamp": signal_ts,
        "trading_date": cio.observation_day("M2K", signal_ts).isoformat(),
        "observation_date": cio.observation_day("M2K", signal_ts).isoformat(),
        "source_timeframe": "15", "entry": 100.0, "stop": 99.0, "target": 105.0,
    }
    pending = {"record": record, "filled": True, "fill_ts": signal_ts, "mae_points": 0.0, "mfe_points": 0.0, "bars_seen": 0}
    # Persist CANDIDATE and an already-resolved OUTCOME (as a prior run would have).
    assert cio._append_evidence(tmp_path, record) is True
    outcome_row = {**record, "record_type": "OUTCOME", "resolved_at_bar_ts": _ts(15, 15),
                   "result": "WIN", "exit_reason": "TARGET_HIT", "exit_price": 105.0, "exit_timestamp": _ts(15, 15),
                   "pnl_r": 5.0}
    assert cio._append_evidence(tmp_path, outcome_row) is True
    # Stale state: pending never cleared (crash before _save_state).
    state = {"campaign_id": cio.CAMPAIGN_ID, "pending": {record["candidate_id"]: pending},
             "seen_candidate_ids": [record["candidate_id"]], "seen_bars": [], "strat_212_122": {}}
    (tmp_path / cio.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    # A forward bar that resolves the bracket again.
    from context.bar_history import BarHistory
    BarHistory(log_dir=str(tmp_path)).record("M2K", ts=_ts(15, 15), open=100, high=106, low=100, close=105, timeframe="15")

    resolved = cio.resolve_pending(tmp_path, instrument="M2K", bars=[], current_bar_ts=_ts(15, 15))

    assert resolved == []                                                        # not reported twice
    rows = cio.read_evidence(tmp_path)
    assert sum(1 for r in rows if r["record_type"] == "OUTCOME" and r["candidate_id"] == "stale-pending-1") == 1
    assert json.loads((tmp_path / cio.STATE_FILENAME).read_text())["pending"] == {}  # stale entry cleared
    assert obs.notify_observation(resolved) == 0 and capture_router == []             # no duplicate notification
    # Control: a genuinely new outcome still resolves, is returned once, and notifies once.
    fresh = {**record, "candidate_id": "fresh-1", "signal_timestamp": _ts(15, 15), "entry": 100.0, "stop": 99.0, "target": 105.5}
    assert cio._append_evidence(tmp_path, fresh) is True
    state["pending"] = {"fresh-1": {"record": fresh, "filled": True, "fill_ts": _ts(15, 15), "mae_points": 0.0, "mfe_points": 0.0, "bars_seen": 0}}
    (tmp_path / cio.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    BarHistory(log_dir=str(tmp_path)).record("M2K", ts=_ts(15, 30), open=105, high=107, low=104, close=106, timeframe="15")
    resolved = cio.resolve_pending(tmp_path, instrument="M2K", bars=[], current_bar_ts=_ts(15, 30))
    assert [r["candidate_id"] for r in resolved] == ["fresh-1"]
    assert obs.notify_observation(resolved) == 1 and len(capture_router) == 1
    assert capture_router[0][1].startswith("🟢 M2K practice buy won")
    assert cio.resolve_pending(tmp_path, instrument="M2K", bars=[], current_bar_ts=_ts(15, 45)) == []  # nothing left
