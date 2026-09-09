import subprocess
import sys
from pathlib import Path

from scripts.mes_122_h1h2_context_decomposition import (
    _alignment,
    _et_window,
    _stable_cells,
)


def test_trend_alignment_normalizes_trade_and_trend_directions():
    assert _alignment("LONG", "UP") == "aligned"
    assert _alignment("SHORT", "DOWN") == "aligned"
    assert _alignment("LONG", "DOWN") == "opposed"
    assert _alignment("SHORT", "UP") == "opposed"
    assert _alignment("LONG", "NEUTRAL") == "neutral"


def test_et_windows_use_eastern_clock():
    # January UTC->ET is -5h.
    assert _et_window("2026-01-02T14:30:00+00:00") == "09:30-11:00"
    assert _et_window("2026-01-02T16:00:00+00:00") == "11:00-13:30"
    assert _et_window("2026-01-02T19:00:00+00:00") == "13:30-16:00"
    assert _et_window("2026-01-02T07:00:00+00:00") == "18:00-03:00"


def test_stable_cells_require_n5_and_same_sign_through_all_slippage():
    def metrics(n, net):
        return {"trades": n, "commission_adjusted_net": net}

    groups = {
        "slip_1t": {"groups": {d: {} for d in (
            "half", "direction", "session", "et_window", "market_condition",
            "trend_direction", "trend_strength", "trend_alignment", "weekday", "calendar_year"
        )}},
        "slip_2t": {"groups": {d: {} for d in (
            "half", "direction", "session", "et_window", "market_condition",
            "trend_direction", "trend_strength", "trend_alignment", "weekday", "calendar_year"
        )}},
        "slip_3t": {"groups": {d: {} for d in (
            "half", "direction", "session", "et_window", "market_condition",
            "trend_direction", "trend_strength", "trend_alignment", "weekday", "calendar_year"
        )}},
    }
    for key, net in (("slip_1t", 10), ("slip_2t", 5), ("slip_3t", 1)):
        groups[key]["groups"]["direction"]["LONG"] = metrics(5, net)
        groups[key]["groups"]["session"]["tiny"] = metrics(4, 20)
        groups[key]["groups"]["weekday"]["flip"] = metrics(6, net if key != "slip_3t" else -1)

    out = _stable_cells(groups)
    assert any(x["dimension"] == "direction" and x["category"] == "LONG" for x in out["stable_positive"])
    assert not any(x["category"] == "tiny" for x in out["stable_positive"])
    assert not any(x["category"] == "flip" for x in out["stable_positive"])


def test_context_decomposition_script_can_execute_directly():
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "mes_122_h1h2_context_decomposition.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Describe why pure MES strat_122" in proc.stdout
