from scripts.mes_122_fallback_full_engine_proof import (
    _classify,
    _frozen_373_config,
    _metrics,
)


def test_frozen_373_config_materializes_without_missing_snapshot_keys():
    cfg = _frozen_373_config()
    assert cfg.allowed_instruments == ["MES", "MNQ"]
    assert cfg.required_instruments == ["MES", "MNQ"]
    assert cfg.max_trades_per_day == 9999
    assert cfg.enabled_concepts == [
        "orb_breakout",
        "orb_reclaim",
        "orb_rejection",
        "vwap_reclaim",
        "vwap_rejection",
        "vwap_hold",
        "pdh_reclaim",
        "pdl_reclaim",
        "strat_4hr_retrigger",
        "strat_322_first_live",
        "strat_122",
    ]
    assert cfg.disabled_concepts_per_instrument == {
        "MES": [
            "vwap_reclaim",
            "pdl_reclaim",
            "orb_breakout",
            "orb_rejection",
            "vwap_rejection",
            "pdh_reclaim",
            "strat_4hr_retrigger",
            "strat_322_first_live",
        ],
        "MNQ": ["strat_122"],
    }
    assert cfg.strategy_status["strat_122"] == "PAPER_ELIGIBLE"
    assert cfg.strategy_status["vwap_hold"] == "SHADOW_ONLY"
    assert cfg.strategy_fallback_enabled is False


def test_metrics_use_broker_outcome_not_historical_known_pnl():
    rows = [
        {
            "date": "2026-01-01",
            "known_pnl": -9999.0,
            "treatment": {
                "classification": "TRADE_RESOLVED",
                "pnl_dollars": 50.0,
            },
        },
        {
            "date": "2026-01-02",
            "known_pnl": 9999.0,
            "treatment": {
                "classification": "TRADE_RESOLVED",
                "pnl_dollars": -25.0,
            },
        },
    ]
    out = _metrics(rows, "treatment")
    assert out["trades"] == 2
    assert out["wins"] == 1
    assert out["losses"] == 1
    assert out["net"] == 25.0
    assert out["profit_factor"] == 2.0


def test_classify_joins_trade_to_outcome_by_paper_order_id():
    run = {
        "decisions": {
            "2026-01-01T12:00:00+00:00": {
                "decision": "TRADE",
                "paper_order_id": "PAPER-1",
                "setup": {
                    "strategy": "strat_122",
                    "direction": "LONG",
                    "entry": 100.0,
                    "stop": 95.0,
                    "target": 110.0,
                    "rr_ratio": 2.0,
                },
            }
        },
        "outcomes": {
            "PAPER-1": {
                "result": "WIN",
                "pnl_dollars": 42.5,
                "exit_reason": "TARGET_HIT",
            }
        },
    }
    out = _classify(run, "2026-01-01T12:00:00+00:00")
    assert out["classification"] == "TRADE_RESOLVED"
    assert out["result"] == "WIN"
    assert out["pnl_dollars"] == 42.5
    assert out["exit_reason"] == "TARGET_HIT"
