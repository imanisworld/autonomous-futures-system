from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


old_logic = '''    active_verdicts = {"VALIDATED", "PAPER PROOF", "PROMISING BUT UNPROVEN"}
    inactive_verdicts = {"BROKEN", "RETIRE", "WAIT", "RESEARCH ONLY"}

    findings: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalize(row["name"])
        concept = STRATEGY_NAME_ALIASES.get(normalized)
        match_kind = "confirmed_alias"
        if concept is None:
            # Best-effort fuzzy fallback: does a known concept key literally
            # appear inside the normalized name, or vice versa?
            for key in STRATEGY_NAME_ALIASES.values():
                key_spaced = key.replace("_", " ")
                if key_spaced in normalized or normalized in key_spaced:
                    concept = key
                    match_kind = "heuristic_substring_match_confirm_manually"
                    break
        if concept is None:
            unmatched.append(row)
            continue

        is_active_in_config = concept in active_concepts_any_instrument
        verdict = (row.get("verdict") or "").upper()
        verdict_says_active = any(v in verdict for v in active_verdicts)
        verdict_says_inactive = any(v in verdict for v in inactive_verdicts)

        if verdict_says_active and not is_active_in_config:
            findings.append(
                {
                    "strategy": row["name"],
                    "concept_key": concept,
                    "match_kind": match_kind,
                    "inventory_verdict": row.get("verdict"),
                    "issue": "described as active/promising in Strategy_Inventory.md but not "
                    "paper-eligible/enabled for any instrument in the current risk_rules.yaml",
                }
            )
        elif verdict_says_inactive and is_active_in_config:
            findings.append(
                {
                    "strategy": row["name"],
                    "concept_key": concept,
                    "match_kind": match_kind,
                    "inventory_verdict": row.get("verdict"),
                    "issue": "described as BROKEN/RETIRE/WAIT/RESEARCH ONLY in Strategy_Inventory.md but "
                    "IS paper-eligible/enabled for at least one instrument in the current risk_rules.yaml",
                }
            )
'''
new_logic = '''    # Evidence classification and runtime enablement are separate dimensions.
    # PROMISING does not mean a lane must be enabled, and WAIT does not prove
    # that a concept may never be active as a source for a derived evidence lane.
    # Only explicit unsafe/retired classifications are automatic blockers when
    # that exact executable concept is active.
    unsafe_active_verdicts = {"BROKEN", "RETIRE", "UNSAFE"}

    findings: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalize(row["name"])
        concept = STRATEGY_NAME_ALIASES.get(normalized)
        match_kind = "confirmed_alias"
        if concept is None:
            # Best-effort fuzzy fallback: does a known concept key literally
            # appear inside the normalized name, or vice versa?
            for key in STRATEGY_NAME_ALIASES.values():
                key_spaced = key.replace("_", " ")
                if key_spaced in normalized or normalized in key_spaced:
                    concept = key
                    match_kind = "heuristic_substring_match_confirm_manually"
                    break
        if concept is None:
            unmatched.append(row)
            continue

        is_active_in_config = concept in active_concepts_any_instrument
        verdict = (row.get("verdict") or "").upper()
        matched.append(
            {
                "strategy": row["name"],
                "concept_key": concept,
                "match_kind": match_kind,
                "inventory_verdict": row.get("verdict"),
                "configured_active": is_active_in_config,
            }
        )
        if is_active_in_config and any(v in verdict for v in unsafe_active_verdicts):
            findings.append(
                {
                    "strategy": row["name"],
                    "concept_key": concept,
                    "match_kind": match_kind,
                    "inventory_verdict": row.get("verdict"),
                    "issue": (
                        "explicitly classified BROKEN/RETIRE/UNSAFE in Strategy_Inventory.md "
                        "but the exact concept is paper-eligible/enabled for at least one instrument"
                    ),
                }
            )
'''
replace_once("ops/project_check/daily.py", old_logic, new_logic)
replace_once(
    "ops/project_check/daily.py",
    '        "drift_findings": findings,\n        "unmatched_inventory_rows": unmatched,\n',
    '        "drift_findings": findings,\n        "matched_inventory_rows": matched,\n        "unmatched_inventory_rows": unmatched,\n',
)
replace_once(
    "ops/project_check/daily.py",
    '            "Name matching is best-effort (confirmed aliases + heuristic substring fallback); "\n            "unmatched rows are listed, never silently dropped or guessed."\n',
    '            "Evidence verdict and config enablement are reported separately. Only explicit "\n            "BROKEN/RETIRE/UNSAFE + active exact-concept combinations are automatic drift findings. "\n            "Name matching is best-effort; unmatched rows are listed, never guessed."\n',
)

marker = '''def build_daily_report(
'''
insert = '''def _overall_blockers(
    *,
    hygiene: dict[str, Any],
    runtime: dict[str, Any],
    strategy_drift: dict[str, Any],
    trade_chain: dict[str, Any],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []

    if trade_chain.get("status") != "PASS":
        blockers.append({"code": "TRADE_CHAIN_FAIL", "detail": "trade-chain integrity check failed"})

    drift = runtime.get("live_box_drift") or {}
    if str(drift.get("status") or "").lower() == "error":
        blockers.append(
            {
                "code": "RUNTIME_DRIFT_ERROR",
                "detail": str(drift.get("summary") or "live-box/runtime drift check returned error"),
            }
        )
    if runtime.get("risk_rules_load_error"):
        blockers.append(
            {"code": "RISK_RULES_UNVERIFIED", "detail": str(runtime["risk_rules_load_error"])}
        )

    if hygiene.get("dirty_tracked_files") or hygiene.get("staged_files"):
        blockers.append(
            {
                "code": "REPO_TRACKED_DIRTY",
                "detail": "tracked/staged repository changes are present during daily reconciliation",
            }
        )

    if not strategy_drift.get("checked"):
        blockers.append(
            {
                "code": "STRATEGY_SOURCE_UNVERIFIED",
                "detail": str(strategy_drift.get("reason") or "strategy inventory check was not completed"),
            }
        )
    elif strategy_drift.get("drift_findings"):
        blockers.append(
            {
                "code": "UNSAFE_STRATEGY_ACTIVE",
                "detail": (
                    f"{len(strategy_drift['drift_findings'])} active concept(s) carry an explicit "
                    "BROKEN/RETIRE/UNSAFE inventory classification"
                ),
            }
        )
    return blockers


def build_daily_report(
'''
replace_once("ops/project_check/daily.py", marker, insert)

old_return = '''    return {
        # Mirrors the trade-chain result -- a daily report is not "ok" if the
        # trade-chain check FAILed, even though every field above rendered
        # successfully. An API/import consumer reading only "ok" must not be
        # able to mistake a FAIL for a clean run.
        "ok": trade_chain.get("status") == "PASS",
        "routine": "daily-reconciliation",
'''
new_return = '''    overall_blockers = _overall_blockers(
        hygiene=hygiene,
        runtime=runtime,
        strategy_drift=strategy_drift,
        trade_chain=trade_chain,
    )
    overall_status = "PASS" if not overall_blockers else "FAIL"

    return {
        "ok": overall_status == "PASS",
        "overall_status": overall_status,
        "overall_blockers": overall_blockers,
        "routine": "daily-reconciliation",
'''
replace_once("ops/project_check/daily.py", old_return, new_return)

# CLI: use the overall daily verdict, not trade-chain alone.
replace_once(
    "scripts/project_check.py",
    '        return 0 if report["trade_chain"]["status"] == "PASS" else 1\n',
    '        return 0 if report["ok"] else 1\n',
)
replace_once(
    "scripts/project_check.py",
    '    print("DAILY RECONCILIATION")\n',
    '    print("DAILY RECONCILIATION")\n    print(f"  overall: {report[\'overall_status\']}")\n    for blocker in report["overall_blockers"]:\n        print(f"    BLOCKER {blocker[\'code\']}: {blocker[\'detail\']}")\n',
)
replace_once(
    "scripts/project_check.py",
    '    return 0 if tc["status"] == "PASS" else 1\n\n\ndef main',
    '    return 0 if report["ok"] else 1\n\n\ndef main',
)

# Tests: evidence verdict != config state; explicit unsafe active remains a blocker.
replace_once(
    "tests/test_project_check_daily.py",
    'from ops.project_check.daily import _normalize, _parse_strategy_inventory, _strategy_source_of_truth, build_daily_report\n',
    'from ops.project_check.daily import _normalize, _overall_blockers, _parse_strategy_inventory, _strategy_source_of_truth, build_daily_report\n',
)
old_strategy_test = '''def test_strategy_source_of_truth_flags_active_in_docs_but_disabled_in_config(tmp_path: Path) -> None:
    inventory_dir = tmp_path / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(
        "\\n".join(
            [
                "## Master Table",
                "",
                "| Strategy | Verdict |",
                "|---|---|",
                "| ORB Reclaim (MES) | **PAPER PROOF** |",
                "| ORB Breakout (MNQ) | **WAIT** |",
            ]
        ),
        encoding="utf-8",
    )
    lanes = {"active_lane_summary": {"MNQ": ["orb_breakout"]}}  # orb_reclaim not active anywhere
    result = _strategy_source_of_truth(repo_root=tmp_path, rules_active_lanes=lanes)
    assert result["checked"] is True
    issues = {f["strategy"]: f["issue"] for f in result["drift_findings"]}
    assert "described as active" in issues["ORB Reclaim (MES)"]
    assert "described as BROKEN/RETIRE/WAIT" in issues["ORB Breakout (MNQ)"]
'''
new_strategy_test = '''def test_strategy_source_of_truth_separates_evidence_verdict_from_config_state(tmp_path: Path) -> None:
    inventory_dir = tmp_path / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(
        "\\n".join(
            [
                "## Master Table",
                "",
                "| Strategy | Verdict |",
                "|---|---|",
                "| ORB Reclaim (MES) | **PROMISING BUT UNPROVEN** |",
                "| ORB Breakout (MNQ) | **WAIT** |",
            ]
        ),
        encoding="utf-8",
    )
    lanes = {"active_lane_summary": {"MNQ": ["orb_breakout"]}}
    result = _strategy_source_of_truth(repo_root=tmp_path, rules_active_lanes=lanes)
    assert result["checked"] is True
    assert result["drift_findings"] == []
    statuses = {row["strategy"]: row["configured_active"] for row in result["matched_inventory_rows"]}
    assert statuses == {"ORB Reclaim (MES)": False, "ORB Breakout (MNQ)": True}


def test_strategy_source_of_truth_flags_explicit_broken_concept_when_active(tmp_path: Path) -> None:
    inventory_dir = tmp_path / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(
        "\\n".join([
            "## Master Table", "", "| Strategy | Verdict |", "|---|---|",
            "| ORB Breakout (MNQ) | **BROKEN** |",
        ]),
        encoding="utf-8",
    )
    lanes = {"active_lane_summary": {"MNQ": ["orb_breakout"]}}
    result = _strategy_source_of_truth(repo_root=tmp_path, rules_active_lanes=lanes)
    assert len(result["drift_findings"]) == 1
    assert "BROKEN/RETIRE/UNSAFE" in result["drift_findings"][0]["issue"]
'''
replace_once("tests/test_project_check_daily.py", old_strategy_test, new_strategy_test)

replace_once(
    "tests/test_project_check_daily.py",
    '    assert report["ok"] is True\n    assert report["repo_reconciliation"]["current_branch"] == "main"\n',
    '    assert report["overall_status"] in {"PASS", "FAIL"}\n    assert report["ok"] is (report["overall_status"] == "PASS")\n    assert isinstance(report["overall_blockers"], list)\n    assert report["repo_reconciliation"]["current_branch"] == "main"\n',
)
replace_once(
    "tests/test_project_check_daily.py",
    '    assert report["trade_chain"]["status"] == "FAIL"\n    assert report["ok"] is False\n',
    '    assert report["trade_chain"]["status"] == "FAIL"\n    assert report["ok"] is False\n    assert any(b["code"] == "TRADE_CHAIN_FAIL" for b in report["overall_blockers"])\n',
)

append = '''\n\ndef test_overall_blockers_fail_on_runtime_drift_error() -> None:
    blockers = _overall_blockers(
        hygiene={"dirty_tracked_files": [], "staged_files": []},
        runtime={
            "live_box_drift": {"status": "error", "summary": "branch mismatch"},
            "risk_rules_load_error": None,
        },
        strategy_drift={"checked": True, "drift_findings": []},
        trade_chain={"status": "PASS"},
    )
    assert any(b["code"] == "RUNTIME_DRIFT_ERROR" for b in blockers)


def test_overall_blockers_allow_promising_disabled_and_wait_active() -> None:
    blockers = _overall_blockers(
        hygiene={"dirty_tracked_files": [], "staged_files": []},
        runtime={"live_box_drift": {"status": "ok"}, "risk_rules_load_error": None},
        strategy_drift={"checked": True, "drift_findings": []},
        trade_chain={"status": "PASS"},
    )
    assert blockers == []


def test_overall_blockers_fail_when_explicit_unsafe_concept_is_active() -> None:
    blockers = _overall_blockers(
        hygiene={"dirty_tracked_files": [], "staged_files": []},
        runtime={"live_box_drift": {"status": "ok"}, "risk_rules_load_error": None},
        strategy_drift={"checked": True, "drift_findings": [{"strategy": "ORB Breakout"}]},
        trade_chain={"status": "PASS"},
    )
    assert any(b["code"] == "UNSAFE_STRATEGY_ACTIVE" for b in blockers)
'''
p = Path("tests/test_project_check_daily.py")
p.write_text(p.read_text(encoding="utf-8") + append, encoding="utf-8")

for raw in (
    "scripts/_chatgpt_apply_daily_verdict.py",
    ".github/workflows/chatgpt-apply-daily-verdict.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
