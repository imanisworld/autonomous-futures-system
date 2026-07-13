from ops.behavior_neutral_gate import (
    APP_PY_PATH,
    app_py_change_is_safe,
    evaluate_changed_files,
    top_level_defs,
)


def test_safe_prefixes_pass_with_no_content_review():
    result = evaluate_changed_files([
        "scripts/atomic_release.sh",
        "ops/release_manifest.py",
        "tests/test_atomic_release_script.py",
        "docs/some-notes.md",
        "research/mnq_structural_level_5m.py",
    ])
    assert result.is_behavior_neutral
    assert result.blocking_reasons == []


def test_strategy_and_risk_paths_are_denied_by_default():
    result = evaluate_changed_files(["strategy/signal_engine.py"])
    assert not result.is_behavior_neutral
    assert "strategy/signal_engine.py" in result.blocking_reasons[0]

    result = evaluate_changed_files(["risk_rules.yaml"])
    assert not result.is_behavior_neutral

    result = evaluate_changed_files(["webhook/runner.py"])
    assert not result.is_behavior_neutral

    result = evaluate_changed_files(["execution/tradovate_broker.py"])
    assert not result.is_behavior_neutral

    result = evaluate_changed_files(["config/settings.py"])
    assert not result.is_behavior_neutral


def test_app_py_change_outside_protected_functions_is_safe():
    baseline = (
        "def _shadow_feed_status():\n"
        "    return 'old'\n"
        "\n"
        "def receive_alert():\n"
        "    return 'unchanged'\n"
    )
    candidate = (
        "def _shadow_feed_status():\n"
        "    return 'new label text'\n"
        "\n"
        "def receive_alert():\n"
        "    return 'unchanged'\n"
    )
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert ok
    assert reasons == []

    result = evaluate_changed_files([APP_PY_PATH], app_py_sources=(baseline, candidate))
    assert result.is_behavior_neutral


def test_app_py_change_inside_protected_function_is_blocked():
    baseline = (
        "def receive_alert():\n"
        "    return process(payload)\n"
    )
    candidate = (
        "def receive_alert():\n"
        "    return process(payload, skip_risk_check=True)\n"
    )
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok
    assert any("receive_alert" in r for r in reasons)

    result = evaluate_changed_files([APP_PY_PATH], app_py_sources=(baseline, candidate))
    assert not result.is_behavior_neutral


def test_app_py_new_top_level_function_is_not_auto_trusted():
    baseline = "def _dashboard_payload():\n    return {}\n"
    candidate = (
        "def _dashboard_payload():\n    return {}\n"
        "\n"
        "def _new_helper():\n    return 1\n"
    )
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok
    assert any("_new_helper" in r for r in reasons)


def test_app_py_removed_function_is_blocked():
    baseline = "def _old_helper():\n    return 1\n"
    candidate = ""
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok
    assert any("_old_helper" in r for r in reasons)


def test_app_py_changed_without_sources_provided_fails_closed():
    result = evaluate_changed_files([APP_PY_PATH], app_py_sources=None)
    assert not result.is_behavior_neutral


def test_top_level_defs_extracts_exact_source_segments():
    source = "def foo():\n    return 1\n\n\nclass Bar:\n    pass\n"
    defs = top_level_defs(source)
    assert set(defs) == {"foo", "Bar"}
    assert defs["foo"] == "def foo():\n    return 1"
