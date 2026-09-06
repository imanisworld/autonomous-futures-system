"""Policy-regression scan: every charter category is caught; clean patches pass."""

from __future__ import annotations

import pytest

from ops.pr_promotion_readiness import HOLD, READY, REJECT, evaluate_promotion_readiness
from ops.pr_promotion_readiness.policy import SCOPE_POLICIES
from ops.pr_promotion_readiness.record import build_promotion_record
from ops.pr_promotion_readiness.regression import SELF_PATH, describe, scan_patch, scan_patches
from tests.test_pr_promotion_readiness import _ready_evidence

POLICY = SCOPE_POLICIES["options-advisory"]


def _patch(*added: str, removed: tuple[str, ...] = ()) -> str:
    lines = ["@@ -1,1 +1,1 @@"] + [f"-{r}" for r in removed] + [f"+{a}" for a in added]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "line, category",
    [
        ("def classify_strat(bar, prev):", "strat_authority"),
        ("INSIDE_BAR = 'inside'", "strat_authority"),
        ("enforce_signa_gate = True", "signa_authority"),
        ("SIGNA_GATE_ENFORCED = true", "signa_authority"),
        ("if not signa_aligned: return False", "signa_authority"),
        ("proxy_gex = compute(pivots)", "proxy_gex"),
        ("gex_flip = signa_pivot", "proxy_gex"),
        ("regime = estimate_gex(chain)", "proxy_gex"),
        ("max_open_positions = 3", "position_count_cap"),
        ("MAX_POSITIONS = 2", "position_count_cap"),
        ("max_aggregate_open_risk_dollars: float = 1000.0", "inferred_aggregate_risk"),
        ("DEFAULT_AGGREGATE_RISK_BUDGET = 1000", "inferred_aggregate_risk"),
        ("if budget is None: return True", "missing_risk_fail_open"),
        ("risk_budget = supplied or DEFAULT_RISK", "missing_risk_fail_open"),
        ("if contract is None: return True", "missing_contract_fail_open"),
        ("contract_valid: bool = True", "missing_contract_fail_open"),
        ("constraints_met = True", "missing_contract_fail_open"),
        ("broker.place_order(ticket)", "execution"),
        ("result = place_option_order(order)", "execution"),
        ("auto_exit = True", "execution"),
        ("client.create_order_instruction(x)", "execution"),
        ("if losing: average_down(position)", "automatic_averaging"),
    ],
)
def test_each_regression_category_is_caught_in_source(line, category):
    findings = scan_patch("options_manager/plans/manager.py", _patch(line))
    assert findings and findings[0].category == category and findings[0].severity == "reject", findings


def test_threshold_weakening_is_caught_and_tightening_is_not():
    weaker = scan_patch("options_manager/validation/contract_quality_gate.py", _patch("DEFAULT_MIN_VOLUME = 10", removed=("DEFAULT_MIN_VOLUME = 100",)))
    assert weaker[0].category == "threshold_weakening" and "100 -> 10" in weaker[0].note
    wider = scan_patch("options_manager/x.py", _patch("MAX_SPREAD_PERCENT = 25.0", removed=("MAX_SPREAD_PERCENT = 10.0",)))
    assert wider[0].category == "threshold_weakening"
    tighter = scan_patch("options_manager/x.py", _patch("MIN_OPEN_INTEREST = 800", removed=("MIN_OPEN_INTEREST = 500",)))
    assert tighter == ()
    new_constant = scan_patch("options_manager/x.py", _patch("MIN_DTE = 14"))
    assert new_constant == ()


def test_test_files_only_flag_removed_fail_closed_assertions_as_hold():
    src = scan_patch("tests/test_x.py", _patch("broker.place_order(ticket)  # asserted absent"))
    assert src == ()  # execution verbs in tests are not regressions
    removed = scan_patch("tests/test_x.py", _patch("assert result.status == 'VALID'", removed=("assert result.status == 'INVALID'",)))
    assert removed and removed[0].category == "test_weakening" and removed[0].severity == "hold"


def test_prose_about_signa_authority_in_a_validator_is_not_a_regression():
    # Real false positive caught on #441: a fail-closed validator's own reason string.
    line = 'hard.append(f"stage {key} used Signa as authority")'
    assert scan_patch("options_manager/validation/forward_session.py", _patch(line)) == ()


def test_exclusions_and_comments():
    assert scan_patch(SELF_PATH, _patch("broker.place_order(x)")) == ()
    assert scan_patch("options_manager/strategies/strat_212.py", _patch("def classify_strat(bar):")) == ()
    assert scan_patch("options_manager/strategies/other.py", _patch("def classify_strat(bar):")) != ()
    assert scan_patch("options_manager/x.py", _patch("# do not place_order here")) == ()
    assert scan_patch("docs/notes.md", _patch("place_order is forbidden")) == ()


def test_clean_patch_passes():
    assert scan_patches([("options_manager/contracts/selector.py", _patch("spread = None", "return (ask - bid) / mid * 100.0"))]) == ()


def test_regression_makes_the_verdict_reject_policy_regression_and_is_recorded():
    evidence = _ready_evidence(patches=(("options_manager/plans/manager.py", _patch("broker.place_order(ticket)")),))
    verdict = evaluate_promotion_readiness(evidence, POLICY)
    assert verdict.verdict == REJECT and verdict.label == "REJECT — POLICY REGRESSION"
    assert any("POLICY REGRESSION [execution]" in b for b in verdict.blockers)
    record = build_promotion_record(verdict)
    assert record["verdict_label"] == "REJECT — POLICY REGRESSION" and record["regression_findings"][0]["category"] == "execution"
    hold = evaluate_promotion_readiness(_ready_evidence(patches=(("tests/test_y.py", _patch("assert ok", removed=("assert blocked",))),)), POLICY)
    assert hold.verdict == HOLD and hold.label == HOLD


def test_missing_patch_content_holds_instead_of_assuming_clean():
    verdict = evaluate_promotion_readiness(_ready_evidence(patches=()), POLICY)
    assert verdict.verdict == HOLD and any("policy-regression scan not performed" in h for h in verdict.holds)
    assert evaluate_promotion_readiness(_ready_evidence(), POLICY).verdict == READY


def test_describe_is_human_readable():
    text = describe(scan_patch("options_manager/x.py", _patch("max_open_positions = 3")))
    assert text == ("POLICY REGRESSION [position_count_cap] options_manager/x.py: max_open_positions = 3",)
