import dataclasses
import subprocess
import sys
from pathlib import Path

from scripts.mes_122_controlled_one_variable_tests import _fixed_one_contract_config
from scripts.mes_122_slippage_path_decomposition import SLIPS, STRATEGY


def test_pure_122_isolation_changes_only_enabled_concepts():
    combined = _fixed_one_contract_config(isolated=True)
    pure = dataclasses.replace(combined, enabled_concepts=[STRATEGY])

    assert combined.enabled_concepts == ["strat_212", "strat_122"]
    assert pure.enabled_concepts == ["strat_122"]
    assert pure.position_sizing == combined.position_sizing
    assert pure.max_contracts_hard_cap == combined.max_contracts_hard_cap == 1
    assert pure.fill_pessimistic_both_hit == combined.fill_pessimistic_both_hit is True
    assert pure.runner_mode == combined.runner_mode is False
    assert SLIPS == (1.0, 2.0, 3.0)


def test_decomposition_script_can_execute_directly():
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "mes_122_slippage_path_decomposition.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Decompose the MES strat_122" in proc.stdout
