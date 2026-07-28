"""Tests for ops/change_scope_test_gate.py (Session Safety addendum)."""

from __future__ import annotations

from ops.change_scope_test_gate import classify_change_scope, evaluate_test_coverage


def test_docs_only_diff_is_not_applicable():
    result = evaluate_test_coverage(["docs/2026-07-27-notes.md"])
    assert result["status"] == "NOT_APPLICABLE"


def test_empty_diff_is_not_applicable():
    assert evaluate_test_coverage([])["status"] == "NOT_APPLICABLE"


def test_execution_change_without_matching_test_fails():
    result = evaluate_test_coverage(["execution/tradovate_broker.py"])
    assert result["status"] == "FAIL"
    assert "execution" in result["missing_required_tests"]


def test_execution_change_with_matching_test_passes():
    result = evaluate_test_coverage(
        ["execution/tradovate_broker.py", "tests/test_fill_realism.py"]
    )
    assert result["status"] == "PASS"


def test_strategy_change_requires_detector_or_replay_parity_test():
    failing = evaluate_test_coverage(["strategy/signal_engine.py"])
    assert failing["status"] == "FAIL"

    passing = evaluate_test_coverage(
        ["strategy/signal_engine.py", "tests/test_signal_engine_gates.py"]
    )
    assert passing["status"] == "PASS"


def test_risk_change_requires_risk_engine_test():
    result = evaluate_test_coverage(["risk/risk_engine.py"])
    assert result["status"] == "FAIL"
    assert "risk" in result["missing_required_tests"]


def test_webhook_change_requires_payload_or_routing_test():
    result = evaluate_test_coverage(["webhook/runner.py"])
    assert result["status"] == "FAIL"
    assert "webhook" in result["missing_required_tests"]


def test_multiple_categories_each_tracked_independently():
    result = evaluate_test_coverage(
        [
            "execution/tradovate_broker.py",
            "risk/risk_engine.py",
            "tests/test_risk_engine_rejection.py",
        ]
    )
    assert result["status"] == "FAIL"
    assert result["missing_required_tests"] == ["execution"]
    assert set(result["changed_categories"]) == {"execution", "risk"}


def test_test_file_changes_alone_do_not_count_as_a_category():
    scope = classify_change_scope(["tests/test_something.py"])
    assert scope["changed_categories"] == []


def test_mixed_docs_and_code_is_not_docs_only():
    scope = classify_change_scope(["docs/notes.md", "execution/paper_broker.py"])
    assert scope["docs_only"] is False
