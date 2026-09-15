from __future__ import annotations

from scripts.futures_signa_shadow_report import build_report, exact_join


def _shadow(**overrides):
    row = {
        "instrument": "MNQ",
        "timestamp": "2026-09-14T15:00:00+00:00",
        "strategy": "strat_322",
        "trade_direction": "LONG",
        "signa_v2_direction": "LONG",
        "signa_v2_grade": "A",
        "signa_v2_confidence": 84,
        "signa_v2_timeframe": "1h",
        "signa_v2_cached": False,
    }
    row.update(overrides)
    return row


def _outcome(**overrides):
    outcome = {
        "result": "WIN",
        "pnl_dollars": 40.0,
        "signal_timestamp": "2026-09-14T15:00:00+00:00",
        "strategy": "strat_322",
    }
    outcome.update(overrides.pop("outcome", {}))
    row = {"type": "OUTCOME", "instrument": "MNQ", "outcome": outcome}
    row.update(overrides)
    return row


def test_exact_identity_joins() -> None:
    joined, quality = exact_join([_shadow()], [_outcome()])
    assert quality["joined"] == 1
    assert joined[0]["relation"] == "ALIGNED"
    assert joined[0]["pnl_dollars"] == 40.0


def test_nearest_timestamp_is_never_used() -> None:
    joined, quality = exact_join(
        [_shadow()],
        [_outcome(outcome={"signal_timestamp": "2026-09-14T15:00:01+00:00"})],
    )
    assert joined == []
    assert quality["shadow_unmatched"] == 1


def test_strategy_mismatch_is_never_fallback_joined() -> None:
    joined, quality = exact_join(
        [_shadow()],
        [_outcome(outcome={"strategy": "orb_breakout"})],
    )
    assert joined == []
    assert quality["shadow_unmatched"] == 1


def test_outcome_missing_identity_is_not_joinable() -> None:
    joined, quality = exact_join(
        [_shadow()],
        [_outcome(outcome={"signal_timestamp": None})],
    )
    assert joined == []
    assert quality["outcomes_missing_identity"] == 1


def test_duplicate_identity_is_ambiguous_and_refused() -> None:
    joined, quality = exact_join([_shadow()], [_outcome(), _outcome()])
    assert joined == []
    assert quality["ambiguous_outcome_keys"] == 1
    assert quality["shadow_unmatched"] == 1


def test_report_is_observational_and_groups_without_promotion() -> None:
    joined, quality = exact_join(
        [
            _shadow(),
            _shadow(
                timestamp="2026-09-15T15:00:00+00:00",
                signa_v2_direction="SHORT",
                signa_v2_grade="B",
                signa_v2_confidence=None,
            ),
        ],
        [
            _outcome(),
            _outcome(
                outcome={
                    "signal_timestamp": "2026-09-15T15:00:00+00:00",
                    "pnl_dollars": -20.0,
                    "result": "LOSS",
                }
            ),
        ],
    )
    report = build_report(joined, quality)
    assert report["authority"] == "observational_only"
    assert report["promotion_permitted"] is False
    assert report["overall"]["n_joined"] == 2
    assert report["overall"]["net_pnl_dollars"] == 20.0
    assert report["by_relation"]["ALIGNED"]["n_joined"] == 1
    assert report["by_relation"]["OPPOSED"]["n_joined"] == 1
    assert report["by_confidence_band"]["MISSING"]["n_joined"] == 1
