from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts import observer_grade_evaluation as ev


def _row(n, *, alignment="aligned", session="new_york", mc="TRENDING", instrument="MNQ", pnl_r=1.0,
         day=date(2026, 10, 1), strategy="strat_22_reversal_observed", result="WIN", label=True, roll_adjusted=False):
    row = {
        "record_type": "OUTCOME", "collection_mode": "structural_outcome", "candidate_id": f"c{n}",
        "result": result, "pnl_r": pnl_r, "session": session, "market_condition": mc, "instrument": instrument,
        "strategy": strategy, "trading_date": day.isoformat(), "risk_ticks": 20.0, "tick_value_dollars": 0.5,
    }
    if label:
        row["strat_ftfc"] = {"definition": ev.FTFC_DEFINITION, "alignment": alignment, "roll_adjusted": roll_adjusted}
    return row


def _write(tmp_path, rows):
    path = tmp_path / "evidence.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_grade_rule_is_the_preregistered_one():
    assert ev.grade(_row(1)) == "A"
    assert ev.grade(_row(1, session="asian")) == "B"
    assert ev.grade(_row(1, mc="DEAD")) == "B"
    assert ev.grade(_row(1, alignment="conflict")) == "C"
    assert ev.grade(_row(1, alignment="against")) == "D"
    assert ev.grade(_row(1, alignment="unknown")) is None
    assert ev.grade(_row(1, label=False)) is None


def test_cost_in_r_matches_prereg_formula():
    # MNQ: tick $0.50, 20-tick risk -> (2*0.5 + 1.48) / (20*0.5) = 0.248 R
    assert ev.cost_r(_row(1)) == pytest.approx(0.248)


def test_roll_window_is_third_friday_minus_14_days_to_month_end():
    assert ev.third_friday(2026, 12) == date(2026, 12, 18)
    assert not ev.in_roll_window(date(2026, 12, 3))
    assert ev.in_roll_window(date(2026, 12, 4)) and ev.in_roll_window(date(2026, 12, 31))
    assert not ev.in_roll_window(date(2026, 11, 30))


def test_load_filters_and_excludes(tmp_path):
    rows = [
        _row(1), _row(1),                                   # duplicate
        _row(2, label=False),                               # pre-#940 row
        _row(3, alignment="unknown"),
        _row(4, result="NO_FILL"),
        _row(5, day=date(2026, 12, 10)),                    # roll window, not adjusted
        _row(6, day=date(2026, 12, 10), roll_adjusted=True),  # adjusted -> kept
        {**_row(7), "collection_mode": "signal_metrics"},   # not structural: ignored
    ]
    out, excluded = ev.load_outcomes(_write(tmp_path, rows))
    assert [o["trading_date"] for o in out] == ["2026-10-01", "2026-12-10"]
    assert excluded == {"duplicate": 1, "unlabeled": 1, "ftfc_unknown": 1, "not_terminal": 1, "roll_window": 1}
    assert out[0]["r_net"] == pytest.approx(1.0 - 0.248)


def test_counts_mode_never_contains_performance_and_final_look_refuses_early(tmp_path, capsys):
    path = _write(tmp_path, [_row(1, pnl_r=3.0), _row(2, alignment="conflict", pnl_r=-1.0)])
    assert ev.main(["--evidence", str(path), "--as-of", "2026-10-02"]) == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert "final_look" not in report and "r_net" not in out and "mean" not in out
    assert report["look"] == {"minimums_met": False, "deadline_passed": False, "due": False}
    assert ev.main(["--evidence", str(path), "--as-of", "2026-10-02", "--final-look"]) == 2
    assert "refused" in capsys.readouterr().out


def _synthetic(a_edge: float):
    rows, n = [], 0
    for k in range(40):
        day = date(2026, 10, 1) + timedelta(days=k)
        for g, (al, sess) in {"A": ("aligned", "new_york"), "B": ("aligned", "asian"),
                              "C": ("conflict", "new_york"), "D": ("against", "new_york")}.items():
            for rep in range(3):
                n += 1
                win = ((n + k) % 3 != 0) if g == "A" else ((n + k) % 3 == 0)  # rotate across families
                pnl = (2.0 if win else -1.0) + (a_edge if g == "A" else 0.0)
                strategy = ("fam_a", "fam_b", "fam_c")[rep]
                rows.append(_row(n, alignment=al, session=sess, pnl_r=pnl, day=day, strategy=strategy,
                                 result="WIN" if pnl > 0 else "LOSS"))
    return rows


def test_final_look_validates_a_clearly_better_grade(tmp_path, capsys):
    path = _write(tmp_path, _synthetic(0.0))
    assert ev.main(["--evidence", str(path), "--as-of", "2026-11-15", "--final-look"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["look"]["minimums_met"] is True
    look = report["final_look"]
    assert look["verdict"] == "GRADE VALIDATED (MNQ)"
    assert all(look["results"]["MNQ"]["gates"].values())


def test_deadline_forces_look_and_reads_insufficient(tmp_path, capsys):
    path = _write(tmp_path, [_row(1), _row(2, alignment="conflict", pnl_r=-1.0)])
    assert ev.main(["--evidence", str(path), "--as-of", "2027-03-31", "--final-look"]) == 0
    assert json.loads(capsys.readouterr().out)["final_look"]["verdict"] == "INSUFFICIENT"
