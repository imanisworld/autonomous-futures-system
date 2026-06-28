from __future__ import annotations

from dataclasses import replace

import webhook.app as app_module
from sources.gex_shadow_analysis import disabled_summary


def test_dashboard_gex_shadow_default_off(monkeypatch, tmp_path, config):
    cfg = replace(config, log_dir=str(tmp_path), gex_shadow_analysis_enabled=False)
    monkeypatch.setattr(app_module, "_config", cfg)

    payload = app_module._dashboard_payload(cfg_date := app_module.date(2026, 6, 23))

    assert payload["date"] == cfg_date.isoformat()
    assert payload["gex_shadow_analysis"] == disabled_summary()


def test_dashboard_gex_shadow_enabled_uses_journal_entries(monkeypatch, tmp_path, config):
    cfg = replace(config, log_dir=str(tmp_path), gex_shadow_analysis_enabled=True)
    monkeypatch.setattr(app_module, "_config", cfg)
    journal = app_module.JournalLogger(log_dir=str(tmp_path))
    for_date = app_module.date(2026, 6, 23)
    journal.log_decision(
        {
            "ts": "2026-06-23T14:30:00+00:00",
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "TRADE",
            "risk_check": {"result": "APPROVED"},
            "setup": {"strategy": "orb_reclaim", "direction": "LONG", "entry": 19580},
            "context": {"close": 19580},
            "gex_observed": {
                "ok": True,
                "ticker": "NDX",
                "regime": "positive",
                "flip_point": 19500,
                "call_wall": 19600,
                "put_wall": 19400,
                "delta_bias": "bullish",
                "spot_vs_flip": "above",
                "dist_to_flip": -80,
                "call_walls": [19600, 19650],
                "put_walls": [19400, 19350],
            },
        },
        None,
        for_date=for_date,
    )
    journal.log_outcome(
        instrument="MNQ",
        session="new_york",
        result="WIN",
        entry_price=19580,
        exit_price=19620,
        exit_reason="TARGET_HIT",
        pnl_ticks=160,
        pnl_dollars=80,
        for_date=for_date,
    )

    payload = app_module._dashboard_payload(for_date)

    assert payload["gex_shadow_analysis"]["enabled"] is True
    assert payload["gex_shadow_analysis"]["measured_trades"] == 1
    assert payload["gex_shadow_analysis"]["overall"]["expectancy"] == 80.0


def test_status_gex_shadow_endpoint_aggregates_recent_journals(monkeypatch, tmp_path, config):
    cfg = replace(config, log_dir=str(tmp_path), gex_shadow_analysis_enabled=True)
    monkeypatch.setattr(app_module, "_config", cfg)
    journal = app_module.JournalLogger(log_dir=str(tmp_path))
    today = app_module.date.today()
    yesterday = today - app_module.timedelta(days=1)

    for day, pnl in ((today, 80), (yesterday, -20)):
        journal.log_decision(
            {
                "ts": f"{day.isoformat()}T14:30:00+00:00",
                "instrument": "MNQ",
                "session": "new_york",
                "decision": "TRADE",
                "risk_check": {"result": "APPROVED"},
                "setup": {"strategy": "orb_reclaim", "direction": "LONG", "entry": 19580},
                "context": {"close": 19580},
                "gex_observed": {
                    "ok": True,
                    "ticker": "NDX",
                    "regime": "positive",
                "flip_point": 19500,
                "call_wall": 19600,
                "put_wall": 19400,
                "delta_bias": "bullish" if pnl > 0 else "bearish",
                "spot_vs_flip": "above",
                "dist_to_flip": -80,
                "call_walls": [19600, 19650],
                "put_walls": [19400, 19350],
            },
            },
            None,
            for_date=day,
        )
        journal.log_outcome(
            instrument="MNQ",
            session="new_york",
            result="WIN" if pnl > 0 else "LOSS",
            entry_price=19580,
            exit_price=19620 if pnl > 0 else 19560,
            exit_reason="TARGET_HIT" if pnl > 0 else "STOP_HIT",
            pnl_ticks=pnl,
            pnl_dollars=pnl,
            for_date=day,
        )

    import asyncio

    payload = asyncio.run(app_module.status_gex_shadow(days=2))

    assert payload["days"] == 2
    assert payload["journal_files_scanned"] == 2
    assert payload["journal_entries_scanned"] == 4
    assert payload["analysis_entries_retained"] == 4
    assert payload["measured_trades"] == 2
    assert payload["overall"]["expectancy"] == 30.0
    delta_rows = {
        row["key"]: row for row in payload["cohorts"]["by_delta_bias"]
    }
    assert delta_rows["bullish"]["expectancy"] == 80.0
    assert delta_rows["bearish"]["expectancy"] == -20.0


def test_status_gex_shadow_discards_irrelevant_journal_rows(monkeypatch, tmp_path, config):
    cfg = replace(config, log_dir=str(tmp_path), gex_shadow_analysis_enabled=True)
    monkeypatch.setattr(app_module, "_config", cfg)
    journal = app_module.JournalLogger(log_dir=str(tmp_path))
    today = app_module.date.today()
    journal.log_decision(
        {
            "ts": f"{today.isoformat()}T14:15:00+00:00",
            "instrument": "MNQ",
            "decision": "NO_TRADE",
            "risk_check": {"result": "NOT_RUN"},
        },
        None,
        for_date=today,
    )

    import asyncio

    payload = asyncio.run(app_module.status_gex_shadow(days=1))

    assert payload["journal_entries_scanned"] == 1
    assert payload["analysis_entries_retained"] == 0
    assert payload["measured_trades"] == 0
