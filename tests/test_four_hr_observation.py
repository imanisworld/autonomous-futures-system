"""Observation-state contract for the natural-1m 4HR lane."""
from __future__ import annotations

import ast
import copy
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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
