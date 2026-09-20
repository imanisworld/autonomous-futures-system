"""Causal 4H treatment attribution for the 4HR pre-armed evidence lane."""
from datetime import datetime, timedelta, timezone

from context.four_hr_continuation_attribution import (
    CONTINUATION,
    DEFINITION,
    TREATMENT,
    completed_four_hour_sequence_context,
)

ET = timezone(timedelta(hours=-4))


def _bar(ts, high, low):
    return {
        "ts": ts.isoformat(),
        "open": (high + low) / 2,
        "high": high,
        "low": low,
        "close": (high + low) / 2,
    }


def test_completed_et_wall_clock_sequence_tags_22_continuation():
    rows = [
        _bar(datetime(2026, 6, 1, 16, 0, tzinfo=ET), 100, 90),
        _bar(datetime(2026, 6, 1, 20, 0, tzinfo=ET), 105, 91),
        _bar(datetime(2026, 6, 2, 0, 0, tzinfo=ET), 110, 92),
        _bar(datetime(2026, 6, 2, 4, 0, tzinfo=ET), 115, 93),
    ]
    ctx = completed_four_hour_sequence_context(
        rows, datetime(2026, 6, 2, 9, 31, tzinfo=ET)
    )
    assert ctx["definition"] == DEFINITION
    assert ctx["treatment"] == TREATMENT
    assert ctx["status"] == "OK"
    assert ctx["sequence"] == CONTINUATION
    assert ctx["treatment_eligible"] is True
    assert ctx["bar_types"] == ["two_up", "two_up", "two_up"]


def test_incomplete_current_4h_bucket_is_never_used():
    rows = [
        _bar(datetime(2026, 6, 1, 16, 0, tzinfo=ET), 100, 90),
        _bar(datetime(2026, 6, 1, 20, 0, tzinfo=ET), 105, 91),
        _bar(datetime(2026, 6, 2, 0, 0, tzinfo=ET), 110, 92),
        _bar(datetime(2026, 6, 2, 4, 0, tzinfo=ET), 115, 93),
        _bar(datetime(2026, 6, 2, 8, 0, tzinfo=ET), 112, 80),
    ]
    ctx = completed_four_hour_sequence_context(
        rows, datetime(2026, 6, 2, 9, 31, tzinfo=ET)
    )
    assert ctx["sequence"] == CONTINUATION
    assert ctx["treatment_eligible"] is True
    assert ctx["bar_starts"][-1].startswith("2026-06-02T04:00:00")


def test_missing_four_hour_history_is_unknown_not_false():
    ctx = completed_four_hour_sequence_context(
        [_bar(datetime(2026, 6, 2, 8, 0, tzinfo=ET), 100, 90)],
        datetime(2026, 6, 2, 9, 31, tzinfo=ET),
    )
    assert ctx["status"] == "INSUFFICIENT_CONTEXT"
    assert ctx["sequence"] is None
    assert ctx["treatment_eligible"] is None


def test_attribution_module_has_no_execution_or_risk_imports():
    import ast
    from pathlib import Path

    path = Path(__file__).parents[1] / "context" / "four_hr_continuation_attribution.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(name.startswith(("execution", "risk", "broker")) for name in imported)
