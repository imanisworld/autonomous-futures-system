from __future__ import annotations

from scripts import mes_asian_d_ema_baseline as mes


def _record(strategy: str, ts: str):
    return {
        "ts": ts,
        "session": "asian",
        "pine": "RANGE_BOUND",
        "struct": "STRUCTURAL_RANGE",
        "sdir": None,
        "bar_cohort": "D",
        "ema_dir": "UP",
        "ema_str": "STRONG",
        "regime_pine": "NOT_EVALUATED(label)",
        "regime_struct": "n/a",
        "regime_nolabel": "RESTRICTED",
        "candidates": [
            {
                "strategy": strategy,
                "direction": "LONG",
                "entry": 100.0,
                "stop": 95.0,
                "target": 110.0,
            }
        ],
    }


def test_precursor_contract_contains_explicit_labels_and_never_no_fill(monkeypatch):
    records = [
        _record("winner", "2026-09-01T23:00:00+00:00"),
        _record("no_fill", "2026-09-01T23:15:00+00:00"),
    ]

    def fake_resolve(candidate, *_args, **_kwargs):
        if candidate["strategy"] == "winner":
            return {
                "result": "WIN",
                "exit_reason": "TARGET_HIT",
                "bars_seen": 2,
                "pnl_r": 1.95,
                "pnl_dollars": 48.75,
                "mae_r": 0.1,
                "mfe_r": 2.0,
            }
        return {
            "result": "NO_FILL",
            "exit_reason": "NEVER_TOUCHED",
            "bars_seen": 3,
            "pnl_r": None,
            "pnl_dollars": None,
            "mae_r": None,
            "mfe_r": None,
        }

    monkeypatch.setattr(mes, "resolve_ioc", fake_resolve)
    full, precursor, summary = mes.produce_baseline(records, bars={}, bar_timestamps=[])

    assert [row["result"] for row in full] == ["WIN", "NO_FILL"]
    assert summary["selected_candidates"] == 2
    assert len(precursor) == 1
    row = precursor[0]
    required = {
        "candidate_id",
        "instrument",
        "strategy",
        "session",
        "direction",
        "signal_ts",
        "outcome_label",
        "baseline_pnl_dollars",
        "baseline_stop_ticks",
        "target_r",
        "source_variant",
    }
    assert required <= set(row)
    assert row["outcome_label"] in {"WIN", "LOSS"}
    assert row["instrument"] == "MES"
    assert row["session"] == "asian"
    assert row["source_variant"] == "D0_D_EMA"
    assert all(r["outcome_label"] in {"WIN", "LOSS"} for r in precursor)
