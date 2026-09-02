from ops.pr_promotion_readiness.policy import SCOPE_POLICIES, classify_scope


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
