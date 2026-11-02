"""The v11 rollback shell checks must be part of the PR workflow."""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_ci_workflow_runs_gate_test_v11() -> None:
    workflow = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "docs/ops/gate_test_v11/test_journal_err_clean.sh" in workflow
    assert "docs/ops/gate_test_v11/test_postgate_and_rollback.sh" in workflow
