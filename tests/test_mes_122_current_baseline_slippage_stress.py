import dataclasses
import subprocess
import sys
from pathlib import Path

from scripts.mes_122_controlled_one_variable_tests import _fixed_one_contract_config
from scripts.mes_122_current_baseline_slippage_stress import SLIPPAGE_TICKS


def test_slippage_stress_keeps_strategy_and_sizing_frozen():
    base = _fixed_one_contract_config(isolated=True)
    assert SLIPPAGE_TICKS == (1.0, 2.0, 3.0)
    assert base.position_sizing.enabled is False
    assert base.max_contracts_hard_cap == 1
    assert base.enabled_concepts == ["strat_212", "strat_122"]
    for slip in SLIPPAGE_TICKS:
        cfg = dataclasses.replace(base, fill_slippage_ticks=slip)
        assert cfg.fill_slippage_ticks == slip
        assert cfg.position_sizing == base.position_sizing
        assert cfg.max_contracts_hard_cap == 1
        assert cfg.enabled_concepts == base.enabled_concepts


def test_slippage_runner_can_be_executed_directly_from_outside_repo(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "mes_122_current_baseline_slippage_stress.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
