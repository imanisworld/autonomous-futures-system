"""Observation-state contract for the natural-1m 4HR lane."""
from __future__ import annotations

import ast
import copy
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from config.settings import load_config
from context.four_hr_observation import (
    observation_state_path,
    publish_4hr_observation,
    read_armed_observation,
)
from context.wide_stop_forward_collector import process_five_min_bar

DAY = date(2026, 9, 8)
ARMED = {
    "trading_date": DAY.isoformat(),
    "status": "ARMED",
    "direction": "LONG",
    "trigger": 20000.0,
    "target": 20100.0,
    "setup_bar_ts": "2026-09-08T09:10:00-04:00",
    "four_am_bar_ts": "2026-09-08T04:00:00-04:00",
}


def _as_of():
    return datetime(2026, 9, 8, 13, 40, tzinfo=timezone.utc)


def test_risk_rules_keep_4hr_out_of_enabled_concepts():
    assert "strat_4hr_retrigger" not in load_config().enabled_concepts


def test_malformed_and_non_armed_snapshots_fail_closed(tmp_path):
    path = observation_state_path(tmp_path, DAY)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert read_armed_observation(tmp_path, DAY, as_of=_as_of()) is None

    assert publish_4hr_observation(
        tmp_path,
        {"trading_date": DAY.isoformat(), "status": "TRIGGERED"},
        source_timestamp=_as_of(),
    )
    assert read_armed_observation(tmp_path, DAY, as_of=_as_of()) is None


def test_future_source_timestamp_is_not_readable(tmp_path):
    assert publish_4hr_observation(
        tmp_path,
        ARMED,
        source_timestamp=datetime(2026, 9, 8, 13, 45, tzinfo=timezone.utc),
    )
    assert read_armed_observation(tmp_path, DAY, as_of=_as_of()) is None


def test_collector_publishes_without_adding_executable_authority(monkeypatch, tmp_path):
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = "2026-09-08T00:00:00+00:00"
    cfg.enabled_concepts = [
        name for name in cfg.enabled_concepts if name != "strat_4hr_retrigger"
    ]
    before = list(cfg.enabled_concepts)
    before_stops = dict(cfg.max_stop_ticks)

    def fake(*, payload, cfg, bars_5m, strategy):
        if strategy != "strat_4hr_retrigger":
            return None, None, None, None
        return None, None, None, dict(ARMED)

    monkeypatch.setattr(
        "context.wide_stop_forward_collector._evaluate_canonical_candidate",
        fake,
    )
    process_five_min_bar(
        payload=SimpleNamespace(
            ticker="MNQ1!",
            timestamp="2026-09-08T13:35:00+00:00",
            timeframe="5m",
            open=20000.0,
            high=20010.0,
            low=19990.0,
            close=20005.0,
            volume=1,
        ),
        cfg=cfg,
        bars_5m=[],
        log_dir=tmp_path,
    )

    assert cfg.enabled_concepts == before
    assert "strat_4hr_retrigger" not in cfg.enabled_concepts
    assert cfg.max_stop_ticks == before_stops
    observed = read_armed_observation(tmp_path, DAY, as_of=_as_of())
    assert observed is not None
    assert observed["executable"] is False
    assert observed["trade_authorized"] is False
    assert observed["order_authority"] is False
    assert observed["source"] == "wide_stop_forward_v1"
    assert observed["source_timestamp"].startswith("2026-09-08T09:40:00")


def test_observation_module_and_executable_paths_stay_separate():
    tree = ast.parse(Path("context/four_hr_observation.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("execution", "risk", "strategy", "webhook")
    assert not any(
        name == item or name.startswith(f"{item}.")
        for name in imported
        for item in forbidden
    )
    signal = Path("strategy/signal_engine.py").read_text(encoding="utf-8")
    risk = Path("risk/risk_engine.py").read_text(encoding="utf-8")
    demo = Path("context/wide_stop_demo_runtime_core.py").read_text(encoding="utf-8")
    assert "four_hr_observation" not in signal
    assert "four_hr_observation" not in risk
    assert "publish_4hr_observation" not in demo


def _et(hour, minute):
    return datetime(2026, 9, 8, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def test_repeated_armed_publish_keeps_first_arm_time(tmp_path):
    # Arm known at 09:40 ET; the 09:40 5m bar republishes ARMED at 09:45 ET.
    assert publish_4hr_observation(tmp_path, ARMED, source_timestamp=_et(9, 40))
    assert publish_4hr_observation(tmp_path, ARMED, source_timestamp=_et(9, 45))
    # The 09:44 1m webhook processed after the 09:45 publish still sees the arm.
    observed = read_armed_observation(tmp_path, DAY, as_of=_et(9, 44))
    assert observed is not None
    assert observed["armed_available_at"].startswith("2026-09-08T09:40:00")
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 39)) is None


def test_trigger_publish_does_not_hide_touch_inside_trigger_bar(tmp_path):
    # Arm known at 09:40 ET; the 09:40 5m bar touches in its last minute and
    # TRIGGERED is published at 09:45 ET before the 09:44 1m webhook is read.
    assert publish_4hr_observation(tmp_path, ARMED, source_timestamp=_et(9, 40))
    triggered = {**ARMED, "status": "TRIGGERED", "entry_time": "x", "stop": 1.0}
    assert publish_4hr_observation(tmp_path, triggered, source_timestamp=_et(9, 45))
    inside = read_armed_observation(tmp_path, DAY, as_of=_et(9, 44))
    assert inside is not None
    assert inside["terminal_available_at"].startswith("2026-09-08T09:45:00")
    # After the arm resolved, no later 1m bar can use it.
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 45)) is None
    assert read_armed_observation(tmp_path, DAY, as_of=_et(10, 30)) is None
    # A repeated terminal publish keeps the first terminal time.
    assert publish_4hr_observation(tmp_path, triggered, source_timestamp=_et(9, 50))
    again = read_armed_observation(tmp_path, DAY, as_of=_et(9, 44))
    assert again["terminal_available_at"].startswith("2026-09-08T09:45:00")


def test_trigger_without_published_arm_is_never_readable(tmp_path):
    # Armed and triggered on the same 09:30 bar: ARMED was never published.
    triggered = {**ARMED, "status": "TRIGGERED"}
    assert publish_4hr_observation(tmp_path, triggered, source_timestamp=_et(9, 35))
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 31)) is None
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 34)) is None


def test_different_arm_does_not_inherit_window(tmp_path):
    assert publish_4hr_observation(tmp_path, ARMED, source_timestamp=_et(9, 40))
    other = {**ARMED, "status": "TRIGGERED", "trigger": 20050.0}
    assert publish_4hr_observation(tmp_path, other, source_timestamp=_et(9, 45))
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 44)) is None


def test_arm_keeps_the_contract_it_was_published_with(tmp_path):
    assert publish_4hr_observation(
        tmp_path, ARMED, source_timestamp=_et(9, 40), contract="MNQZ2026"
    )
    # A later publish of the same arm cannot change or erase its contract.
    assert publish_4hr_observation(tmp_path, ARMED, source_timestamp=_et(9, 45))
    assert publish_4hr_observation(
        tmp_path, ARMED, source_timestamp=_et(9, 50), contract="MNQH2027"
    )
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 51))["contract"] == "MNQZ2026"
    triggered = {**ARMED, "status": "TRIGGERED"}
    assert publish_4hr_observation(tmp_path, triggered, source_timestamp=_et(9, 55))
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 54))["contract"] == "MNQZ2026"


def test_arm_without_proven_contract_records_none_then_first_known(tmp_path):
    assert publish_4hr_observation(tmp_path, ARMED, source_timestamp=_et(9, 40))
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 41))["contract"] is None
    assert publish_4hr_observation(
        tmp_path, ARMED, source_timestamp=_et(9, 45), contract="MNQZ2026"
    )
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 46))["contract"] == "MNQZ2026"


def test_new_arm_does_not_inherit_old_contract(tmp_path):
    assert publish_4hr_observation(
        tmp_path, ARMED, source_timestamp=_et(9, 40), contract="MNQZ2026"
    )
    other = {**ARMED, "trigger": 20050.0}
    assert publish_4hr_observation(tmp_path, other, source_timestamp=_et(9, 45))
    assert read_armed_observation(tmp_path, DAY, as_of=_et(9, 46))["contract"] is None


def test_collector_publishes_the_5m_alert_contract(monkeypatch, tmp_path):
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = "2026-09-08T00:00:00+00:00"

    def fake(*, payload, cfg, bars_5m, strategy):
        if strategy != "strat_4hr_retrigger":
            return None, None, None, None
        return None, None, None, dict(ARMED)

    monkeypatch.setattr(
        "context.wide_stop_forward_collector._evaluate_canonical_candidate",
        fake,
    )
    process_five_min_bar(
        payload=SimpleNamespace(
            ticker="MNQ1!",
            contract_hint="CME_MINI:MNQZ2026",
            timestamp="2026-09-08T13:35:00+00:00",
            timeframe="5m",
            open=20000.0,
            high=20010.0,
            low=19990.0,
            close=20005.0,
            volume=1,
        ),
        cfg=cfg,
        bars_5m=[],
        log_dir=tmp_path,
    )
    assert read_armed_observation(tmp_path, DAY, as_of=_as_of())["contract"] == "MNQZ2026"
