import pytest

from ops.pr_promotion_readiness.policy import FORBIDDEN_AREA_PATTERNS, SCOPE_POLICIES, classify_scope


def test_options_data_health_profile_allows_only_current_read_only_surface():
    policy = SCOPE_POLICIES["options-data-health"]
    files = [
        "ops/options_data_health.py",
        "options_companion/chain_provider.py",
        "tests/test_options_data_health.py",
        "tests/test_gex_observer.py",
    ]

    findings = classify_scope(files, policy)

    assert findings
    assert all(f.category == "allowed" for f in findings)


def test_other_options_companion_files_remain_forbidden():
    policy = SCOPE_POLICIES["options-data-health"]

    finding = classify_scope(["options_companion/execution.py"], policy)[0]

    assert finding.category == "forbidden"
    assert finding.rule == "credentialed companion lane"


def test_chain_provider_is_not_silently_allowed_in_general_options_scope():
    policy = SCOPE_POLICIES["options-advisory"]

    finding = classify_scope(["options_companion/chain_provider.py"], policy)[0]

    assert finding.category == "out_of_scope"


@pytest.mark.parametrize(
    "path",
    [
        "options_companion/execution.py",
        "options_companion/broker.py",
        "options_companion/models.py",
        "options_companion/__init__.py",
        "options_companion/order_router.py",
    ],
)
def test_no_other_options_companion_file_is_allowed_by_the_data_health_profile(path):
    """The carveout must stay exact-file scoped: only chain_provider.py, never
    a general options_companion/* exemption, regardless of filename shape."""
    policy = SCOPE_POLICIES["options-data-health"]

    finding = classify_scope([path], policy)[0]

    assert finding.category == "forbidden"
    assert finding.rule == "credentialed companion lane"


def test_data_health_profile_allowed_patterns_contain_no_options_companion_wildcard():
    """Guard against a future edit widening the allowlist itself: every
    allowed pattern must be an exact filename, never a options_companion/
    prefix or wildcard."""
    policy = SCOPE_POLICIES["options-data-health"]

    for pattern in policy.allowed_patterns:
        if "options_companion" in pattern:
            assert pattern == r"^options_companion/chain_provider\.py$", pattern


def test_forbidden_area_pattern_excludes_only_chain_provider_by_exact_name():
    """The global FORBIDDEN_AREA_PATTERNS carveout (independent of any scope
    profile) is a negative-lookahead on the exact filename chain_provider.py
    -- confirm it matches every other options_companion file and only spares
    that one, including lookalike names it must NOT be fooled by."""
    import re

    pattern = next(p for p, area in FORBIDDEN_AREA_PATTERNS if p.startswith("^options_companion/"))

    assert re.search(pattern, "options_companion/execution.py")
    assert re.search(pattern, "options_companion/chain_provider.pyc")
    assert re.search(pattern, "options_companion/chain_provider.py.bak")
    assert re.search(pattern, "options_companion/subdir/chain_provider.py")
    assert not re.search(pattern, "options_companion/chain_provider.py")
