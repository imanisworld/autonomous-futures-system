"""Asia-session D+EMA forward paper cohort — default OFF, isolation, parity, scope.

The parity fixture (tests/fixtures/asia_d_ema_parity.json) holds two
observation days of the exact corpus snapshot the archived
counterfactual_representation_v3/v4 producers ran on (1,029 MNQ 15m bars /
1,119 distinct candidates), plus every D+EMA candidate row those producers
emitted for those days (all sessions, all strategies). Parity is asserted
field by field against those archived rows.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import context.asia_d_ema_paper_cohort as cohort
from config.settings import ConfigError, _validate_config
from context.market_context import OHLCData, TrendData
from execution.cross_instrument_observation import observation_day

FIXTURE = Path(__file__).parent / "fixtures" / "asia_d_ema_parity.json"
EPOCH = "2026-08-01T00:00:00+00:00"


@pytest.fixture(scope="module")
def parity():
    return json.loads(FIXTURE.read_text())


def _paper_cfg(config, epoch: str | None = EPOCH):
    return replace(config, asia_d_ema_paper_mode="paper_sim", asia_d_ema_paper_epoch_start=epoch)


def _bar_state(fresh_market_state, bar: dict, ctx: dict, *, instrument: str = "MNQ", timeframe: str = "15"):
    state = copy.deepcopy(fresh_market_state)
    state.instrument = instrument
    state.timestamp = datetime.fromisoformat(bar["ts"])
    state.session = ctx.get("session")
    state.ohlc = OHLCData(open=bar["open"], high=bar["high"], low=bar["low"], close=bar["close"], timeframe=timeframe)
    state.market_condition = ctx.get("market_condition")
    state.trend = TrendData(direction=(ctx.get("trend") or {}).get("direction"), strength=(ctx.get("trend") or {}).get("strength"))
    state.structural_regime = {
        "structural_market_condition": ctx.get("structural_market_condition"),
        "structural_direction": ctx.get("structural_direction"),
    }
    return state


def _events(log_dir) -> list[dict]:
    path = cohort.evidence_path(log_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _replay_day(day: str, data: dict, fresh_market_state, cfg, log_dir, *, timeframe: str = "15"):
    """Drive process_bar with the runner-shaped inputs for one observation day."""
    journal = {row["ts"]: row for row in data["journal"]}
    summaries = []
    for bar in data["bars"]:
        row = journal[bar["ts"]]
        state = _bar_state(fresh_market_state, bar, row["context"], timeframe=timeframe)
        summaries.append(
            cohort.process_bar(state=state, cfg=cfg, log_dir=log_dir, shadow_candidates=row["shadow_candidates"])
        )
    return summaries


# ── 1. default OFF ──────────────────────────────────────────────────────────


def test_default_mode_is_off_and_unset_env_is_off(config, monkeypatch):
    monkeypatch.delenv(cohort.MODE_ENV, raising=False)
    monkeypatch.delenv(cohort.EPOCH_ENV, raising=False)
    assert config.asia_d_ema_paper_mode == "off"
    assert cohort.mode(config) == "off"
    assert cohort.mode(None) == "off"
    assert not cohort.is_active(config)


@pytest.mark.parametrize("value", ["observe_only", "on", "true", "1", "PAPER", "paper-sim", ""])
def test_only_exact_paper_sim_token_can_activate(config, value):
    assert cohort.mode(replace(config, asia_d_ema_paper_mode=value, asia_d_ema_paper_epoch_start=EPOCH)) == "off"
    assert cohort.mode(replace(config, asia_d_ema_paper_mode="paper_sim", asia_d_ema_paper_epoch_start=EPOCH)) == "paper_sim"


def test_paper_sim_without_valid_epoch_is_inactive(config):
    for epoch in (None, "", "not-a-date", "2026-09-16T00:00:00"):  # naive timestamp rejected
        assert not cohort.is_active(_paper_cfg(config, epoch))
    assert cohort.is_active(_paper_cfg(config, EPOCH))
    assert cohort.is_active(_paper_cfg(config, "2026-09-16T00:00:00Z"))


def test_default_off_creates_no_state_or_evidence(config, fresh_market_state, parity, tmp_path):
    day, data = next(iter(parity["days"].items()))
    summaries = _replay_day(day, data, fresh_market_state, config, tmp_path)
    assert summaries == [None] * len(data["bars"])
    assert not cohort.cohort_dir(tmp_path).exists()
    assert list(tmp_path.iterdir()) == []


def test_runner_hook_is_inert_by_default(config, fresh_market_state, tmp_path):
    """The runner import path with the shipped default config produces nothing."""
    state = copy.deepcopy(fresh_market_state)
    state.ohlc = OHLCData(open=1.0, high=2.0, low=0.5, close=1.5, timeframe="15")
    assert cohort.process_bar(state=state, cfg=config, log_dir=tmp_path, shadow_candidates=[
        {"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 1.5, "stop": 1.0, "target": 2.0}
    ]) is None
    assert list(tmp_path.iterdir()) == []


# ── settings validation (fail closed) ───────────────────────────────────────


def test_settings_reject_unknown_mode_and_missing_or_naive_epoch(config):
    base = replace(config, max_staleness_seconds=900)  # the shared fixture's 0 is itself invalid
    with pytest.raises(ConfigError, match="ASIA_D_EMA_PAPER_MODE"):
        _validate_config(replace(base, asia_d_ema_paper_mode="observe_only"))
    with pytest.raises(ConfigError, match="ASIA_D_EMA_PAPER_EPOCH_START is required"):
        _validate_config(replace(base, asia_d_ema_paper_mode="paper_sim", asia_d_ema_paper_epoch_start=None))
    with pytest.raises(ConfigError, match="UTC offset"):
        _validate_config(replace(base, asia_d_ema_paper_mode="paper_sim", asia_d_ema_paper_epoch_start="2026-09-16T00:00:00"))
    with pytest.raises(ConfigError, match="ISO-8601"):
        _validate_config(replace(base, asia_d_ema_paper_mode="paper_sim", asia_d_ema_paper_epoch_start="nope"))
    _validate_config(replace(base, asia_d_ema_paper_mode="off"))
    _validate_config(_paper_cfg(base))


def test_settings_env_default_is_off(monkeypatch):
    from config.settings import load_config

    monkeypatch.delenv(cohort.MODE_ENV, raising=False)
    monkeypatch.delenv(cohort.EPOCH_ENV, raising=False)
    try:
        cfg = load_config()
    except Exception:  # environment without a full box .env — the dataclass default still holds
        pytest.skip("load_config needs the full environment")
    assert cfg.asia_d_ema_paper_mode == "off"
    assert cfg.asia_d_ema_paper_epoch_start is None


# ── 2. existing lanes untouched ─────────────────────────────────────────────


def test_active_cohort_writes_only_its_own_files(config, fresh_market_state, parity, tmp_path):
    day, data = next(iter(parity["days"].items()))
    _replay_day(day, data, fresh_market_state, _paper_cfg(config), tmp_path)
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == ["asia_d_ema_cohort/evidence.jsonl", "asia_d_ema_cohort/state.json"]
    # Nothing the other lanes own exists.
    for other in ("hypothetical_ledger", "mnq_strat_22_reversal_state.json", "decision_journal.jsonl",
                  "forward_evidence_campaign.jsonl", "cross_instrument_observation"):
        assert not (tmp_path / other).exists()


def test_mes_lane_and_strat_evidence_do_not_see_the_cohort_keys(config):
    """The MES lane and the MNQ Strat lanes read their own mode keys only."""
    from context import mes_122_paper_lane
    from execution.mnq_strat_evidence import LANES, lane_mode

    active = _paper_cfg(config)
    assert mes_122_paper_lane.mode(active) == mes_122_paper_lane.mode(config)
    assert not mes_122_paper_lane.is_active(active)
    for lane in LANES:
        assert lane_mode(lane, active) == lane_mode(lane, config)


def test_cohort_module_has_no_broker_route():
    """Paper-only by construction: the module never imports a live broker path."""
    import ast

    tree = ast.parse(Path(cohort.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "execution.paper_broker" in imported
    for name in imported:
        assert "tradovate" not in name and "ibkr" not in name and "broker_factory" not in name, name
    assert "execution.broker" not in imported  # no broker selection, only the BracketOrder dataclass module
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert calls & {"execute_bracket", "resolve_position", "restore_position"}
    assert not calls & {"place_order", "submit_order", "execute_live", "get_broker"}


# ── 3. exact D+EMA parity against archived producer rows ────────────────────


def _key(ts: str, c: dict) -> tuple:
    return (ts, c["strategy"], c["direction"], float(c["entry"]), float(c["stop"]), float(c["target"]))


def test_d_ema_selection_matches_archived_rows_exactly(parity):
    for day, data in parity["days"].items():
        seen: set[str] = set()
        selected = set()
        for row in data["journal"]:
            for c in cohort.d_ema_candidates(row["context"], row["shadow_candidates"], day, seen):
                selected.add(_key(row["ts"], c))
        expected = {_key(r["ts"], r) for r in data["expected_d_ema_rows"]}
        assert selected == expected, day
        assert len(expected) > 0


def test_d_ema_outcomes_match_archived_rows_exactly(parity):
    fields = ("result", "exit_reason", "entry_price", "exit_price", "exit_ts", "bars_seen", "pnl_dollars", "pnl_r")
    checked = {"WIN": 0, "LOSS": 0, "NO_FILL": 0, "EXPIRED": 0}
    for day, data in parity["days"].items():
        bars = {b["ts"]: b for b in data["bars"]}
        order = [b["ts"] for b in data["bars"]]
        for r in data["expected_d_ema_rows"]:
            cand = {k: r[k] for k in ("strategy", "direction", "entry", "stop", "target")}
            i = order.index(r["ts"])
            out = cohort.resolve_offline(cand, bars[r["ts"]], [bars[t] for t in order[i + 1:]])
            for f in fields:
                got, exp = out.get(f), r.get(f)
                if isinstance(exp, (int, float)) and isinstance(got, (int, float)):
                    assert got == pytest.approx(exp, abs=1e-9), (day, r["ts"], f)
                else:
                    assert got == exp, (day, r["ts"], f)
            checked[r["result"]] += 1
    assert checked["WIN"] and checked["LOSS"] and checked["NO_FILL"] and checked["EXPIRED"]


def test_condition_primitives_match_producer_definition():
    assert cohort.is_cohort_d({"market_condition": "RANGE_BOUND", "structural_market_condition": "STRUCTURAL_TRANSITION"})
    assert cohort.is_cohort_d({"market_condition": None, "structural_market_condition": None})
    assert not cohort.is_cohort_d({"market_condition": "TRENDING", "structural_market_condition": "STRUCTURAL_RANGE_DEAD"})
    assert not cohort.is_cohort_d({"market_condition": "RANGE_BOUND", "structural_market_condition": "STRUCTURAL_TREND_UP"})
    assert not cohort.is_cohort_d({"market_condition": "DEAD", "structural_market_condition": "STRUCTURAL_TREND_DOWN"})
    assert cohort.ema_side({"trend": {"direction": "UP"}}) == "LONG"
    assert cohort.ema_side({"trend": {"direction": "DOWN"}}) == "SHORT"
    assert cohort.ema_side({"trend": {"direction": "FLAT"}}) is None
    assert cohort.ema_side({}) is None
    assert cohort.valid_geometry({"direction": "LONG", "entry": 10, "stop": 9, "target": 12})
    assert not cohort.valid_geometry({"direction": "LONG", "entry": 10, "stop": 11, "target": 12})
    assert not cohort.valid_geometry({"direction": "SHORT", "entry": 10, "stop": 9, "target": 8})
    assert not cohort.valid_geometry({"direction": "LONG", "entry": "x", "stop": 9, "target": 12})


def test_persistent_geometry_is_one_setup_per_day_even_when_first_seen_off_condition():
    cand = [{"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 10.0, "stop": 9.0, "target": 12.0}]
    seen: set[str] = set()
    trending = {"market_condition": "TRENDING", "structural_market_condition": None, "trend": {"direction": "UP"}}
    ranging = {"market_condition": "RANGE_BOUND", "structural_market_condition": None, "trend": {"direction": "UP"}}
    assert cohort.d_ema_candidates(trending, cand, "2026-09-16", seen) == []
    assert cohort.d_ema_candidates(ranging, cand, "2026-09-16", seen) == []  # consumed on the TRENDING bar
    assert len(cohort.d_ema_candidates(ranging, cand, "2026-09-17", seen)) == 1  # new observation day


# ── 4. allowlists ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("instrument", "session", "strategy", "timeframe", "ok"),
    [
        ("MNQ", "asian", "ema_pullback_trend", "15", True),
        ("MNQ1!", "asian", "strat_22_continuation_observed", "15m", True),
        ("MES", "asian", "ema_pullback_trend", "15", False),
        ("M2K", "asian", "ema_pullback_trend", "15", False),
        ("MNQ", "london", "ema_pullback_trend", "15", False),
        ("MNQ", "new_york", "ema_pullback_trend", "15", False),
        ("MNQ", None, "ema_pullback_trend", "15", False),
        ("MNQ", "asian", "strat_22_reversal_observed", "15", False),
        ("MNQ", "asian", "impulse_first_pullback_observed", "15", False),
        ("MNQ", "asian", "orb_breakout", "15", False),
        ("MNQ", "asian", "ema_pullback_trend", "5", False),
        ("MNQ", "asian", "ema_pullback_trend", "60", False),
    ],
)
def test_scope_allowlists(instrument, session, strategy, timeframe, ok):
    assert cohort.in_scope(instrument, session, strategy, timeframe) is ok


def test_replay_never_fills_outside_scope(config, fresh_market_state, parity, tmp_path):
    for day, data in parity["days"].items():
        _replay_day(day, data, fresh_market_state, _paper_cfg(config), tmp_path)
    events = _events(tmp_path)
    assert events
    for e in events:
        assert e["instrument"] == "MNQ"
        assert e["campaign_id"] == cohort.CAMPAIGN_ID
        assert e["broker_route"] == "PaperBroker"
        assert e["observation_only"] is True
        assert e["normal_execution_affected"] is False
        assert e["timeframe"] == 15
        assert isinstance(e["et_hour"], int)  # logged, never filtered on
        if e["event"] in ("CANDIDATE_FILLED", "NO_FILL", "CANDIDATE_SKIPPED_BUSY", "CANDIDATE_PRE_EPOCH"):
            assert e["session"] == "asian"
            assert e["strategy"] in cohort.STRATEGIES
            assert e["cohort_d"] is True
            assert {"UP": "LONG", "DOWN": "SHORT"}[e["ema_direction"]] == e["direction"]
    # Expected London / NY / out-of-strategy rows are numerous in the fixture and none were touched.
    out_of_scope = [r for d in parity["days"].values() for r in d["expected_d_ema_rows"]
                    if r["session"] != "asian" or r["strategy"] not in cohort.STRATEGIES]
    assert len(out_of_scope) > 20
    attempted = {(e["ts"], e["strategy"], e["direction"]) for e in events if e["event"] != "OUTCOME"}
    assert not attempted & {(r["ts"], r["strategy"], r["direction"]) for r in out_of_scope}


def test_wrong_instrument_and_timeframe_bars_are_ignored_entirely(config, fresh_market_state, parity, tmp_path):
    day, data = next(iter(parity["days"].items()))
    cfg = _paper_cfg(config)
    row = data["journal"][0]
    for instrument, timeframe in (("MES", "15"), ("MNQ", "5"), ("M2K", "15")):
        state = _bar_state(fresh_market_state, data["bars"][0], row["context"], instrument=instrument, timeframe=timeframe)
        assert cohort.process_bar(state=state, cfg=cfg, log_dir=tmp_path, shadow_candidates=row["shadow_candidates"]) is None
    assert not cohort.cohort_dir(tmp_path).exists()


# ── 5. one position at a time ───────────────────────────────────────────────


def _expected_one_position_stream(data: dict, day: str) -> list[tuple]:
    """Independent reference: the audit's collapse rule applied to the archived rows."""
    rows = sorted(
        (r for r in data["expected_d_ema_rows"] if r["session"] == "asian" and r["strategy"] in cohort.STRATEGIES),
        key=lambda r: (r["ts"], r["strategy"], r["direction"]),
    )
    open_until = None
    taken = []
    for r in rows:
        if open_until is not None and r["ts"] < open_until:
            continue
        if r["result"] == "NO_FILL":
            continue
        taken.append((r["ts"], r["strategy"], r["direction"], r["result"], r.get("exit_ts"), r.get("pnl_dollars")))
        open_until = r.get("exit_ts") or r["ts"]
    return taken


def test_one_position_rule_matches_audit_collapse_and_archived_outcomes(config, fresh_market_state, parity, tmp_path):
    for day, data in parity["days"].items():
        log_dir = tmp_path / day
        _replay_day(day, data, fresh_market_state, _paper_cfg(config), log_dir)
        events = _events(log_dir)
        fills = [e for e in events if e["event"] == "CANDIDATE_FILLED"]
        outcomes = {e["candidate_key"]: e for e in events if e["event"] == "OUTCOME"}
        busy = [e for e in events if e["event"] == "CANDIDATE_SKIPPED_BUSY"]
        expected = _expected_one_position_stream(data, day)
        got = []
        for f in fills:
            o = outcomes[f["candidate_key"]]
            got.append((f["ts"], f["strategy"], f["direction"], o["result"], o.get("exit_ts"), o.get("pnl_dollars")))
        assert [g[:3] for g in got] == [e[:3] for e in expected], day
        for g, e in zip(got, expected):
            assert g[3] == e[3], (day, g)
            assert g[4] == e[4], (day, g)
            if e[5] is None:
                assert g[5] is None
            else:
                assert g[5] == pytest.approx(e[5], abs=1e-9), (day, g)
        # Every fill while a position was open was refused, and never more than one open.
        assert busy, day
        for b in busy:
            assert b["open_candidate_key"] in outcomes or b["open_candidate_key"] == fills[-1]["candidate_key"]
        state = cohort.load_state(log_dir)
        assert state["position"] is None or state["position"]["day"] == day


def test_open_position_expires_at_day_roll_and_never_carries(config, fresh_market_state, tmp_path):
    cfg = _paper_cfg(config)
    ctx = {"session": "asian", "market_condition": "RANGE_BOUND", "structural_market_condition": None,
           "trend": {"direction": "UP", "strength": 1}}
    bar1 = {"ts": "2026-09-15T23:00:00+00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
    cand = [{"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 120.0}]
    s1 = _bar_state(fresh_market_state, bar1, ctx)
    cohort.process_bar(state=s1, cfg=cfg, log_dir=tmp_path, shadow_candidates=cand)
    assert cohort.load_state(tmp_path)["position"] is not None
    # Next bar is after the 18:00 ET roll (new observation day) and would have hit the target.
    bar2 = {"ts": "2026-09-16T22:15:00+00:00", "open": 100.0, "high": 130.0, "low": 99.5, "close": 125.0}
    s2 = _bar_state(fresh_market_state, bar2, ctx)
    assert observation_day("MNQ", bar2["ts"]) != observation_day("MNQ", bar1["ts"])
    cohort.process_bar(state=s2, cfg=cfg, log_dir=tmp_path, shadow_candidates=[])
    events = _events(tmp_path)
    assert events[-1]["event"] == "OUTCOME" and events[-1]["result"] == "EXPIRED"
    assert events[-1]["exit_reason"] == "OBSERVATION_DATE_ROLLED"
    assert cohort.load_state(tmp_path)["position"] is None


def test_candidates_before_epoch_are_recorded_not_traded(config, fresh_market_state, tmp_path):
    cfg = _paper_cfg(config, "2026-09-16T00:00:00+00:00")
    ctx = {"session": "asian", "market_condition": "RANGE_BOUND", "structural_market_condition": None,
           "trend": {"direction": "UP", "strength": 1}}
    bar = {"ts": "2026-09-15T23:00:00+00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
    cand = [{"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 120.0}]
    cohort.process_bar(state=_bar_state(fresh_market_state, bar, ctx), cfg=cfg, log_dir=tmp_path, shadow_candidates=cand)
    events = _events(tmp_path)
    assert [e["event"] for e in events] == ["CANDIDATE_PRE_EPOCH"]
    assert cohort.load_state(tmp_path)["position"] is None


# ── invalid state fails closed ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        json.dumps([1, 2, 3]),
        json.dumps({"version": 1, "campaign_id": "other", "seen": [], "position": None}),
        json.dumps({"version": 99, "campaign_id": cohort.CAMPAIGN_ID, "seen": [], "position": None}),
        json.dumps({"version": 1, "campaign_id": cohort.CAMPAIGN_ID, "seen": "x", "position": None}),
        json.dumps({"version": 1, "campaign_id": cohort.CAMPAIGN_ID, "seen": [],
                    "position": {"candidate_key": "k", "direction": "LONG"}}),
    ],
)
def test_invalid_state_fails_closed(config, fresh_market_state, tmp_path, payload):
    cohort.cohort_dir(tmp_path).mkdir(parents=True)
    cohort.state_path(tmp_path).write_text(payload)
    before = cohort.state_path(tmp_path).read_text()
    ctx = {"session": "asian", "market_condition": "RANGE_BOUND", "structural_market_condition": None,
           "trend": {"direction": "UP", "strength": 1}}
    bar = {"ts": "2026-09-15T23:00:00+00:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
    cand = [{"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 120.0}]
    summary = cohort.process_bar(state=_bar_state(fresh_market_state, bar, ctx), cfg=_paper_cfg(config),
                                 log_dir=tmp_path, shadow_candidates=cand)
    assert summary["cohort_result"] == "STATE_INVALID"
    assert cohort.state_path(tmp_path).read_text() == before  # nothing rewritten
    assert not cohort.evidence_path(tmp_path).exists()  # nothing traded or journaled


def test_missing_state_file_is_a_fresh_cohort(tmp_path):
    assert cohort.load_state(tmp_path) == {"version": 1, "campaign_id": cohort.CAMPAIGN_ID, "seen": [], "position": None}


# ── 6 / 7. exact ticker and paper-only execution path ───────────────────────


def test_exact_ticker_is_mnq():
    assert cohort.INSTRUMENT == "MNQ"
    assert cohort.instrument_root("MNQ1!") == "MNQ"
    assert cohort.instrument_root("mnq") == "MNQ"
    assert cohort.instrument_root("MES") != "MNQ"
    assert cohort.instrument_root("MNQU6") != "MNQ"  # a dated contract symbol is not the pinned root


def test_fill_model_is_the_canonical_ioc_paper_broker(monkeypatch):
    """Every fill and every resolution goes through PaperBroker with the archived constants."""
    seen_kwargs = []
    real = cohort.PaperBroker

    class Spy(real):
        def __init__(self, *a, **kw):
            seen_kwargs.append(kw)
            super().__init__(*a, **kw)

    monkeypatch.setattr(cohort, "PaperBroker", Spy)
    cand = {"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 120.0}
    out = cohort.resolve_offline(cand, {"ts": "t0", "close": 100.0},
                                 [{"ts": "t1", "open": 100.0, "high": 121.0, "low": 99.0}])
    assert out["result"] == "WIN"
    assert out["entry_price"] == 100.0 + cohort.SLIP_TICKS * cohort.TICK
    assert seen_kwargs and all(
        kw["entry_fill_model"] == "ioc_limit"
        and kw["slippage_ticks"] == 1.0
        and kw["pessimistic_both_hit"] is True
        and kw["breakeven_at_1r"] is False
        and kw["runner_mode"] is False
        and kw["entry_tolerance_ticks_by_root"] == {"MNQ": 32.0}
        for kw in seen_kwargs
    )


def test_unmarketable_ioc_self_cancels_as_no_fill():
    cand = {"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 120.0}
    far = 100.0 + (cohort.IOC_TOLERANCE_TICKS + 1) * cohort.TICK
    out = cohort.resolve_offline(cand, {"ts": "t0", "close": far}, [])
    assert out["result"] == "NO_FILL"
    assert out["pnl_dollars"] is None


def test_runner_result_carries_only_a_summary(config, fresh_market_state, parity, tmp_path):
    day, data = next(iter(parity["days"].items()))
    summaries = [s for s in _replay_day(day, data, fresh_market_state, _paper_cfg(config), tmp_path) if s]
    assert summaries
    for s in summaries:
        assert set(s) == {"campaign_id", "cohort_result", "position_open", "events"}
        assert s["cohort_result"] == "ADVANCED"


# ── runner end-to-end: real book identical with the cohort OFF vs armed ─────


def _alert(**ov):
    from webhook.payload import AlertPayload

    data = {
        "ticker": "MNQ1!", "timestamp": "2026-09-15T23:00:00+00:00", "timeframe": "15",  # 19:00 ET = asian
        "open": 24010.0, "high": 24060.0, "low": 24005.0, "close": 24050.0,
        "volume": 4200, "avg_volume": 3800, "vwap": 23995.0,
        "orb_high": 23998.0, "orb_low": 23962.0, "orb_status": "above",
        "market_condition": "RANGE_BOUND", "trend_direction": "UP", "trend_strength": "MODERATE",
        "previous_day_high": 24020.0, "previous_day_low": 23940.0, "previous_day_close": 23975.0,
    }
    data.update(ov)
    return AlertPayload(**data)


def _run(config, tmp_path, name, monkeypatch, *, armed: bool):
    from webhook.runner import process_alert

    monkeypatch.setenv("BROKER", "paper")
    log_dir = tmp_path / name
    cfg = replace(config, paper_mode=True, allowed_sessions=["asian", "london", "new_york"], disabled_sessions=[])
    if armed:
        cfg = _paper_cfg(cfg, "2026-09-01T00:00:00+00:00")
    results = []
    for ts in ("2026-09-15T23:00:00+00:00", "2026-09-15T23:15:00+00:00", "2026-09-15T23:30:00+00:00"):
        results.append(process_alert(_alert(timestamp=ts), config=cfg, log_dir=str(log_dir), for_date=None))
    return log_dir, results


def _strip(result: dict) -> dict:
    out = {k: v for k, v in result.items() if k != "asia_d_ema_cohort"}
    out.pop("timestamp_processed", None)
    return json.loads(json.dumps(out, sort_keys=True, default=str))


def _journal_files(log_dir: Path) -> dict[str, int]:
    return {p.relative_to(log_dir).as_posix(): p.stat().st_size for p in log_dir.rglob("*")
            if p.is_file() and not p.relative_to(log_dir).as_posix().startswith("asia_d_ema_cohort/")}


def test_runner_real_book_identical_with_cohort_off_and_armed(config, tmp_path, monkeypatch):
    off_dir, off = _run(config, tmp_path, "off", monkeypatch, armed=False)
    on_dir, on = _run(config, tmp_path, "on", monkeypatch, armed=True)
    assert [ _strip(r) for r in off ] == [ _strip(r) for r in on ]
    assert all("asia_d_ema_cohort" not in r for r in off)
    assert not (off_dir / "asia_d_ema_cohort").exists()
    # Armed: the cohort reports through its own key only, and its files are the only difference.
    assert any("asia_d_ema_cohort" in r for r in on)
    for r in on:
        if "asia_d_ema_cohort" in r:
            assert r["asia_d_ema_cohort"]["campaign_id"] == cohort.CAMPAIGN_ID
            assert r["asia_d_ema_cohort"]["cohort_result"] in ("ADVANCED", "STATE_INVALID")
    off_files, on_files = _journal_files(off_dir), _journal_files(on_dir)
    assert set(off_files) == set(on_files)
    for name in off_files:
        off_lines = (off_dir / name).read_text().splitlines() if name.endswith((".jsonl", ".json", ".txt")) else None
        on_lines = (on_dir / name).read_text().splitlines() if name.endswith((".jsonl", ".json", ".txt")) else None
        if off_lines is not None:
            assert len(off_lines) == len(on_lines), name
