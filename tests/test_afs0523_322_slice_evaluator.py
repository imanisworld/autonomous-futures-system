"""Synthetic checks for the registered slice-success evaluator. No slice is scored."""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from config.futures_contracts import contract_economics
from research.afs0523_322_repro_wrapper import (
    OCT4_SCORING_PROFILE,
    REPRO_END,
    REPRO_START,
    _run_window,
    _target_and_stop_nets,
    compare_replay_structural,
    partition_invalid_gap_opens,
)
from research.afs0523_322_slice_evaluator import (
    ACCOUNT_DD_FLOOR_USD,
    ACCOUNT_SINGLE_LOSS_USD,
    COMMISSION_ROUND_TRIP_USD,
    DECISIVE_SLIPPAGE_TICKS,
    TICK_SIZE_PT,
    TICK_VALUE_USD,
    USD_PER_POINT,
    clopper_pearson_lower,
    combine_cells,
    evaluate_cell,
    geometric_nets,
    trade_records_from_ledger,
)
from scripts.edge_decomposition_audit import COMMISSION_ROUND_TRIP

ROOT = Path(__file__).resolve().parents[1]
REPLAY = (
    ROOT
    / "docs/research-evidence/T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01/reproduction-replay-ledger.json"
)
REPLAY_SHA256 = "6c6b0141e4936795db1dd19c87efb3a257684943d0e637d7c7143f52530ae573"
GATE = (
    ROOT
    / "docs/research-evidence/T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01/reproduction-gate.json"
)
FILL = 1000.0
STOP = 985.0
TARGET = 1007.0
WIN = 11.02
LOSS = -32.98
SHA = "a" * 64
OTHER_SHA = "b" * 64


def _weekdays(count: int, start: date = date(2015, 1, 5)) -> list[str]:
    found: list[str] = []
    day = start
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day.isoformat())
        day += timedelta(days=1)
    return found


def _cell_id(market: str) -> str:
    slice_id = "nq-2010-06-to-2019-05" if market == "NQ" else "mnq-2019-05-to-2024-06"
    return f"{slice_id}/honest_ioc/3tick"


def _trade(
    day: str,
    *,
    net: float | None,
    status: str = "RESOLVED",
    market: str = "NQ",
    harness: str | None = None,
    reason: str | None = None,
    slippage: float = 3.0,
) -> dict:
    if status == "RESOLVED" and harness is None and net is not None:
        if net > 0:
            harness = "WIN"
        elif net < 0:
            harness = "LOSS"
        else:
            harness = "BREAKEVEN"
    return {
        "date": day,
        "cell_id": _cell_id(market),
        "market": market,
        "model": "honest_ioc",
        "slippage_ticks": slippage,
        "direction": "LONG",
        "status": status,
        "fill_entry": None if status == "NO_FILL" else FILL,
        "stop": STOP,
        "target": TARGET,
        "net": net if status == "RESOLVED" else None,
        "harness_result": harness if status == "RESOLVED" else None,
        "reason": reason,
    }


def _resolved(nets: list[float], *, market: str = "NQ", extra: list[dict] | None = None) -> list[dict]:
    rows = [_trade(day, net=net, market=market) for day, net in zip(_weekdays(len(nets)), nets)]
    if extra:
        rows.extend(extra)
    return rows


def _spaced(wins: int, losses: int, *, market: str = "NQ") -> list[float]:
    prefix = max(wins - 3 * losses, 0)
    nets = [WIN] * prefix
    for _loss in range(losses):
        nets.extend([WIN, WIN, WIN, LOSS])
    assert nets.count(WIN) == wins
    assert nets.count(LOSS) == losses
    return nets


def _score(nets: list[float], *, market: str = "NQ", extra: list[dict] | None = None) -> dict:
    rows = _resolved(nets, market=market, extra=extra)
    return evaluate_cell(rows, cell_id=_cell_id(market), market=market)


def _stamp(cell: dict, digest: str = SHA) -> dict:
    stamped = dict(cell)
    stamped["evaluator_sha256"] = digest
    return stamped


def _shell(status: str, market: str, digest: str = SHA) -> dict:
    return {
        "market": market,
        "model": "honest_ioc",
        "slippage_ticks": 3.0,
        "status": status,
        "evaluator_sha256": digest,
        "unsafe_account": False if market == "MNQ" else None,
    }


def test_t1_fewer_than_30_resolved_is_insufficient():
    cell = _score([WIN] * 29)
    assert cell["status"] == "INSUFFICIENT"
    assert cell["decided_by_check"] == 1
    assert "VALIDATED" not in json.dumps(cell)


def test_t2_unresolved_fraction_above_ten_percent_waits():
    days = _weekdays(100)
    rows = [_trade(day, net=WIN) for day in days[:89]]
    rows.extend(_trade(day, net=None, status="UNRESOLVED") for day in days[89:])
    cell = evaluate_cell(rows, cell_id=_cell_id("NQ"), market="NQ")
    assert cell["counts"]["fills"] == 100
    assert cell["unresolved_fraction"] == pytest.approx(0.11)
    assert cell["status"] == "WAIT_UNRESOLVED"
    assert cell["decided_by_check"] == 3


def test_t2b_unresolved_fraction_at_ten_percent_is_not_a_wait():
    days = _weekdays(100)
    rows = [_trade(day, net=WIN) for day in days[:90]]
    rows.extend(_trade(day, net=None, status="UNRESOLVED") for day in days[90:])
    cell = evaluate_cell(rows, cell_id=_cell_id("NQ"), market="NQ")
    assert cell["unresolved_fraction"] == pytest.approx(0.10)
    assert cell["status"] != "WAIT_UNRESOLVED"
    assert cell["status"] == "PASS"


def test_t2c_negative_net_fails_before_the_unresolved_check():
    days = _weekdays(34)
    nets = [WIN] * 20 + [LOSS] * 10
    rows = [_trade(day, net=net) for day, net in zip(days[:30], nets)]
    rows.extend(_trade(day, net=None, status="UNRESOLVED") for day in days[30:])
    cell = evaluate_cell(rows, cell_id=_cell_id("NQ"), market="NQ")
    assert cell["net_total_usd"] == -109.40
    assert cell["unresolved_fraction"] == pytest.approx(4 / 34)
    assert cell["status"] == "FAIL"
    assert cell["decided_by_check"] == 2


def test_t3_non_positive_net_is_fail():
    cell = _score([WIN] * 20 + [LOSS] * 10)
    assert cell["net_total_usd"] == -109.40
    assert cell["status"] == "FAIL"
    assert cell["decided_by_check"] == 2


def test_t4_nine_r_drawdown_fails_the_cap():
    cell = _score([WIN] * 31 + [LOSS] * 9)
    assert cell["net_total_usd"] == 44.80
    assert cell["max_dd_usd"] == 296.82
    assert cell["max_dd_r"] == pytest.approx(9.0)
    assert cell["status"] == "FAIL_DRAWDOWN"
    assert cell["decided_by_check"] == 4


def test_t5a_spaced_27_of_30_passes():
    cell = _score(_spaced(27, 3))
    assert cell["net_total_usd"] == 198.60
    assert cell["max_dd_r"] <= 4.0
    assert cell["lcb"] == pytest.approx(0.761402, abs=1e-6)
    assert cell["be"] == pytest.approx(0.7495454545, abs=1e-10)
    assert cell["status"] == "PASS"
    assert cell["decided_by_check"] == 6


def test_t5b_spaced_26_of_30_waits_on_the_interval():
    cell = _score(_spaced(26, 4))
    assert cell["net_total_usd"] == 154.60
    assert cell["lcb"] == pytest.approx(0.720385, abs=1e-6)
    assert cell["status"] == "WAIT_CI"
    assert cell["decided_by_check"] == 5


def test_t6_zero_net_is_a_loss_and_the_cell_still_passes():
    cell = _score([WIN] * 27 + [LOSS] * 2 + [0.0])
    assert cell["wins_net"] == 27
    assert cell["losses_net"] == 3
    assert cell["zero_net_count"] == 1
    assert cell["avg_net_loss_usd"] == pytest.approx(21.9866666667, abs=1e-10)
    assert cell["be_real"] == pytest.approx(0.6661278406, abs=1e-6)
    assert cell["be"] == pytest.approx(0.7495454545, abs=1e-10)
    assert cell["status"] == "PASS"


def test_t7_harness_win_with_non_positive_net_is_a_gate_loss():
    nets = _spaced(27, 3)
    win_index = nets.index(WIN)
    rows = _resolved(nets)
    rows[win_index] = _trade(
        rows[win_index]["date"],
        net=-0.98,
        harness="WIN",
        reason="DAY_ONLY_FLATTEN",
    )
    cell = evaluate_cell(rows, cell_id=_cell_id("NQ"), market="NQ")
    assert cell["net_total_usd"] == 186.60
    assert cell["wins_net"] == 26
    assert cell["losses_net"] == 4
    assert cell["harness_label_counts"]["WIN"] == 27
    assert cell["lcb"] == pytest.approx(0.720385, abs=1e-6)
    assert cell["status"] == "WAIT_CI"


def test_t8_a_negative_cell_is_overfit():
    left = combine_cells(_shell("PASS", "NQ"), _shell("FAIL", "MNQ"))
    right = combine_cells(_shell("FAIL", "NQ"), _shell("PASS", "MNQ"))
    assert left["overall"] == right["overall"] == "FAIL"
    assert left["verdict"] == right["verdict"] == "OVERFIT"
    assert "VALIDATED" not in {left["verdict"], right["verdict"]}


def test_t8b_drawdown_only_is_a_risk_profile_fail():
    left = combine_cells(_shell("PASS", "NQ"), _shell("FAIL_DRAWDOWN", "MNQ"))
    right = combine_cells(_shell("FAIL_DRAWDOWN", "NQ"), _shell("PASS", "MNQ"))
    assert left["overall"] == right["overall"] == "FAIL"
    assert left["verdict"] == right["verdict"] == "FAIL — risk profile"


def test_t8c_a_negative_cell_outranks_drawdown():
    combined = combine_cells(_shell("FAIL", "NQ"), _shell("FAIL_DRAWDOWN", "MNQ"))
    assert combined["overall"] == "FAIL"
    assert combined["verdict"] == "OVERFIT"


def test_t9_a_mixed_non_fail_is_wait():
    pairs = (("PASS", "INSUFFICIENT"), ("PASS", "WAIT_CI"), ("PASS", "WAIT_UNRESOLVED"))
    for nq_status, mnq_status in pairs:
        combined = combine_cells(_shell(nq_status, "NQ"), _shell(mnq_status, "MNQ"))
        assert combined["overall"] == "WAIT"
        assert combined["verdict"] == "WAIT"


def test_t10_both_passes_are_promising_but_unproven():
    combined = combine_cells(_shell("PASS", "NQ"), _shell("PASS", "MNQ"))
    assert combined["overall"] == "PASS"
    assert combined["verdict"] == "PROMISING BUT UNPROVEN"
    assert combined["cells"] == ["PASS", "PASS"]


def test_t11_clopper_pearson_lower_matches_the_registered_values():
    expected = {
        (27, 30): 0.761402,
        (26, 30): 0.720385,
        (31, 35): 0.757278,
        (35, 40): 0.754969,
        (30, 30): 0.904966,
        (0, 30): 0.0,
    }
    for (k, n), value in expected.items():
        assert clopper_pearson_lower(k, n) == pytest.approx(value, abs=1e-6)
    assert clopper_pearson_lower(30, 30) == pytest.approx(0.05 ** (1 / 30), abs=1e-6)


def test_t12_constants_and_geometry_match_the_repo_sources():
    tick, tick_value = contract_economics("MNQ")
    assert COMMISSION_ROUND_TRIP_USD == COMMISSION_ROUND_TRIP == 1.48
    assert (TICK_SIZE_PT, TICK_VALUE_USD) == (tick, tick_value) == (0.25, 0.50)
    assert USD_PER_POINT == tick_value / tick == 2.0
    assert DECISIVE_SLIPPAGE_TICKS in OCT4_SCORING_PROFILE["slippage_ticks"]
    rules = yaml.safe_load((ROOT / "risk_rules.yaml").read_text(encoding="utf-8"))
    daily = rules["daily_limits"]["max_daily_loss"]
    floor = rules["daily_limits"]["max_drawdown_percent"] * rules["position_sizing"]["starting_balance"]
    assert ACCOUNT_SINGLE_LOSS_USD == float(daily) == 150.0
    assert ACCOUNT_DD_FLOOR_USD == floor == 450.0
    for net in _spaced(27, 3):
        win_geom, loss_geom = geometric_nets("LONG", FILL, STOP, TARGET)
        source_win, source_loss = _target_and_stop_nets("LONG", FILL, STOP, TARGET, 3.0)
        assert (win_geom, loss_geom) == (source_win, source_loss)
        assert net in {WIN, LOSS}


def test_t13_invalid_rows_raise():
    duplicate = _resolved([WIN, WIN])
    duplicate[1]["date"] = duplicate[0]["date"]
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_cell(duplicate, cell_id=_cell_id("NQ"), market="NQ")
    missing_net = _resolved([WIN])
    missing_net[0]["net"] = None
    with pytest.raises(ValueError, match="net"):
        evaluate_cell(missing_net, cell_id=_cell_id("NQ"), market="NQ")
    wrong_slip = _resolved([WIN])
    wrong_slip[0]["slippage_ticks"] = 2.0
    with pytest.raises(ValueError, match="slippage"):
        evaluate_cell(wrong_slip, cell_id=_cell_id("NQ"), market="NQ")


def test_t14_the_evaluator_is_deterministic_and_has_no_io_imports():
    rows = _resolved(_spaced(27, 3))
    kwargs = {"cell_id": _cell_id("NQ"), "market": "NQ"}
    first = json.dumps(evaluate_cell(rows, **kwargs), sort_keys=True)
    second = json.dumps(evaluate_cell(rows, **kwargs), sort_keys=True)
    assert first == second
    source = (ROOT / "research/afs0523_322_slice_evaluator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {"math", "statistics", "typing", "__future__"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= allowed
    assert "datetime" not in imported
    assert "pathlib" not in imported


def test_t15_a_single_loss_past_150_marks_mnq_unsafe_without_changing_status():
    mnq = _score([-150.01], market="MNQ")
    nq = _score([-150.01], market="NQ")
    assert mnq["status"] == nq["status"] == "INSUFFICIENT"
    assert mnq["unsafe_account"] is True
    assert nq["unsafe_account"] is None
    assert mnq["worst_net_loss_usd"] == -150.01


def test_t16_recorded_gate_replay_is_insufficient():
    raw = REPLAY.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REPLAY_SHA256
    ledger = json.loads(raw)["ledger"]
    records = trade_records_from_ledger(ledger, slice_id="reproduction", market="MNQ")
    cell = evaluate_cell(records, cell_id="reproduction/honest_ioc/3tick", market="MNQ")
    assert cell["counts"] == {
        "candidates": 34,
        "fills": 20,
        "resolved": 19,
        "unresolved": 1,
        "open": 0,
        "no_fill": 14,
    }
    assert cell["wins_net"] == 18
    assert cell["net_total_usd"] == 1603.38
    assert cell["status"] == "INSUFFICIENT"


def test_t17_combine_refuses_a_sha_mismatch_or_a_short_sha():
    nq = _shell("PASS", "NQ", SHA)
    mnq = _shell("PASS", "MNQ", OTHER_SHA)
    with pytest.raises(ValueError, match="mismatch"):
        combine_cells(nq, mnq)
    missing = _shell("PASS", "MNQ", SHA)
    missing.pop("evaluator_sha256")
    with pytest.raises(ValueError, match="evaluator_sha256"):
        combine_cells(_shell("PASS", "NQ", SHA), missing)
    with pytest.raises(ValueError, match="evaluator_sha256"):
        combine_cells(_shell("PASS", "NQ", "ab"), _shell("PASS", "MNQ", "ab"))


def test_t18_builder_refuses_an_mnq_row_on_the_seal_date():
    ledger = {
        "3tick": [
            {
                "date": "2026-06-29",
                "direction": "LONG",
                "stop": STOP,
                "target": TARGET,
                "models": {"ioc_close": {"status": "NO_FILL", "reason": "ENTRY_NOT_FILLED"}},
            }
        ]
    }
    with pytest.raises(ValueError, match="2026-06-29"):
        trade_records_from_ledger(ledger, slice_id="mnq-2019-05-to-2024-06", market="MNQ")


def test_t19_strict_bracket_drops_zero_width_and_boundary_equality():
    def row(day: str, direction: str, entry: float, stop: float, target: float) -> dict:
        return {
            "date": date.fromisoformat(day),
            "direction": direction,
            "gap_open": False,
            "entry_trigger": entry,
            "entry_price": entry,
            "stop": stop,
            "target": target,
        }

    zero_width = row("2013-07-16", "LONG", 100.0, 90.0, 100.0)
    long_stop = row("2014-05-26", "LONG", 90.0, 90.0, 110.0)
    long_target = row("2016-08-26", "LONG", 110.0, 90.0, 110.0)
    short_target = row("2017-09-05", "SHORT", 90.0, 110.0, 90.0)
    short_stop = row("2017-09-07", "SHORT", 110.0, 110.0, 90.0)
    long_inside = row("2015-01-05", "LONG", 100.0, 90.0, 110.0)
    short_inside = row("2015-01-06", "SHORT", 100.0, 110.0, 90.0)
    kept, invalid = partition_invalid_gap_opens(
        [zero_width, long_stop, long_target, short_target, short_stop, long_inside, short_inside]
    )
    assert [item["date"] for item in kept] == [long_inside["date"], short_inside["date"]]
    assert all(item["gap_open"] is False for item in invalid)
    assert len(invalid) == 5
    assert all(item["invalidation"] == "INVALID_ENTRY_BRACKET" for item in invalid)


def test_t20_committed_replay_stays_34_candidates_and_zero_bracket_drops():
    artifact = json.loads(GATE.read_text(encoding="utf-8"))
    replay = json.loads(REPLAY.read_text(encoding="utf-8"))
    structural = compare_replay_structural(replay)
    assert structural["comparisons"] == 306
    assert structural["mismatches"] == []
    assert len(replay["ledger"]["1tick"]) == 34
    assert artifact["invalid_entry_bracket_count"] == 0
    assert artifact["gap_open_count"] == 0


def test_n1_reproduction_window_does_not_drop_a_planted_closure(monkeypatch, tmp_path):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.data_gap_dates_from_trees",
        lambda *_args, **_kwargs: {
            "data_gaps": [
                {"date": "2025-01-09", "status": "DATA_GAP", "scored": False},
                {"date": "2025-11-28", "status": "DATA_GAP", "scored": False},
            ],
            "data_gap_count": 2,
            "expected_holiday_closures": [
                {"date": "2024-07-04", "kind": "closed"},
                {"date": "2024-11-29", "kind": "early_close"},
            ],
            "early_close_sessions": [],
        },
    )
    seen = {}

    def _parity(_research, _audit, start, end, _expected, gap_dates):
        seen["window"] = (start, end)
        seen["gap_dates"] = set(gap_dates)
        return [], {}, [], []

    monkeypatch.setattr("research.afs0523_322_repro_wrapper._parity", _parity)
    result = _run_window(
        tmp_path / "research",
        tmp_path / "audit",
        start=REPRO_START,
        end=REPRO_END,
        expected_count=None,
    )
    assert seen["window"] == (REPRO_START, REPRO_END)
    assert result["holiday_closed_unscored"] == []
    assert "2024-07-04" not in seen["gap_dates"]
    assert seen["gap_dates"] == {"2025-01-09", "2025-11-28"}
