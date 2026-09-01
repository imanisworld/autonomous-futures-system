from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "ops/project_check/promotion.py",
    '  "execution_context_claimed": {\n    "entry_fill_model": "ioc_limit",\n',
    '  "execution_context_claimed": {\n    "instrument": "MNQ",\n    "entry_fill_model": "ioc_limit",\n',
)

old_context = '''def _execution_context_check(*, repo_root: Path, claimed: dict[str, Any]) -> dict[str, Any]:
    live = runtime_snapshot(repo_root=repo_root)
    live_view = {
        "entry_fill_model": live.get("entry_fill_model"),
        "entry_tolerance_ticks": live.get("entry_tolerance_ticks"),
        "quantity_caps": live.get("quantity_caps"),
    }
    mismatches = []
    claimed_fill_model = claimed.get("entry_fill_model")
    if claimed_fill_model and live_view["entry_fill_model"] not in (UNKNOWN, None):
        if str(claimed_fill_model) != str(live_view["entry_fill_model"]):
            mismatches.append(
                f"claimed entry_fill_model={claimed_fill_model!r} != live runtime "
                f"entry_fill_model={live_view['entry_fill_model']!r}"
            )
    live_tol = live_view["entry_tolerance_ticks"] or {}
    claimed_tolerance = claimed.get("entry_tolerance_ticks")
    if claimed_tolerance is not None:
        replay_paper_values = [
            v.get("effective_replay_paper")
            for k, v in live_tol.items()
            if isinstance(v, dict) and "effective_replay_paper" in v
        ]
        def _as_float(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        claimed_float = _as_float(claimed_tolerance)
        replay_paper_floats = [_as_float(v) for v in replay_paper_values]
        if replay_paper_values and (claimed_float is None or claimed_float not in replay_paper_floats):
            mismatches.append(
                f"claimed entry_tolerance_ticks={claimed_tolerance!r} does not match any live "
                f"runtime replay/paper-path tolerance {live_tol}"
            )
    for root, info in live_tol.items():
        if isinstance(info, dict) and info.get("diverges"):
            mismatches.append(
                f"entry tolerance for {root} is unpinned (env unset): replay/paper path would "
                f"use {info.get('effective_replay_paper')} ticks but the live Tradovate broker "
                f"path would use {info.get('effective_live_broker')} ticks -- pin "
                f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{root} before treating this as promotion evidence"
            )
    return {
        "claimed": claimed,
        "live_verified": live_view,
        "mismatches": mismatches,
        "parity_ok": not mismatches,
    }
'''

new_context = '''def _execution_context_check(*, repo_root: Path, claimed: dict[str, Any]) -> dict[str, Any]:
    live = runtime_snapshot(repo_root=repo_root)
    live_view = {
        "entry_fill_model": live.get("entry_fill_model"),
        "entry_tolerance_ticks": live.get("entry_tolerance_ticks"),
        "quantity_caps": live.get("quantity_caps"),
    }
    mismatches: list[str] = []

    def _as_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _as_positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    instrument_raw = claimed.get("instrument")
    instrument = str(instrument_raw).strip().upper() if instrument_raw not in (None, "") else None
    claimed_tolerance = claimed.get("entry_tolerance_ticks")
    claimed_qty = claimed.get("contract_qty")
    if (claimed_tolerance is not None or claimed_qty is not None) and instrument is None:
        mismatches.append(
            "execution_context_claimed.instrument is required when entry_tolerance_ticks "
            "or contract_qty is claimed"
        )

    claimed_fill_model = claimed.get("entry_fill_model")
    if claimed_fill_model and live_view["entry_fill_model"] not in (UNKNOWN, None):
        if str(claimed_fill_model) != str(live_view["entry_fill_model"]):
            mismatches.append(
                f"claimed entry_fill_model={claimed_fill_model!r} != live runtime "
                f"entry_fill_model={live_view['entry_fill_model']!r}"
            )

    live_tol = live_view["entry_tolerance_ticks"] or {}
    if claimed_tolerance is not None and instrument is not None:
        info = live_tol.get(instrument)
        if not isinstance(info, dict) or "effective_replay_paper" not in info:
            mismatches.append(
                f"no live runtime replay/paper-path tolerance is available for instrument {instrument}"
            )
        else:
            claimed_float = _as_float(claimed_tolerance)
            live_float = _as_float(info.get("effective_replay_paper"))
            if claimed_float is None or live_float is None or claimed_float != live_float:
                mismatches.append(
                    f"claimed entry_tolerance_ticks={claimed_tolerance!r} for {instrument} != "
                    f"live runtime replay/paper-path tolerance {info.get('effective_replay_paper')!r}"
                )

    roots_to_check = [instrument] if instrument is not None else [
        root for root, info in live_tol.items() if isinstance(info, dict)
    ]
    for root in roots_to_check:
        info = live_tol.get(root)
        if isinstance(info, dict) and info.get("diverges"):
            mismatches.append(
                f"entry tolerance for {root} is unpinned (env unset): replay/paper path would "
                f"use {info.get('effective_replay_paper')} ticks but the live Tradovate broker "
                f"path would use {info.get('effective_live_broker')} ticks -- pin "
                f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{root} before treating this as promotion evidence"
            )

    if claimed_qty is not None and instrument is not None:
        claimed_qty_int = _as_positive_int(claimed_qty)
        if claimed_qty_int is None:
            mismatches.append(f"claimed contract_qty={claimed_qty!r} is not a positive integer")
        else:
            caps = live_view["quantity_caps"] or {}
            per_instrument = caps.get("max_contracts_per_instrument_config")
            per_cap = per_instrument.get(instrument) if isinstance(per_instrument, dict) else None
            hard_cap = caps.get("hard_cap_env")
            known_caps = [
                cap
                for cap in (_as_positive_int(per_cap), _as_positive_int(hard_cap))
                if cap is not None
            ]
            if not known_caps:
                mismatches.append(
                    f"could not resolve a live contract cap for instrument {instrument}"
                )
            else:
                effective_cap = min(known_caps)
                if claimed_qty_int > effective_cap:
                    mismatches.append(
                        f"claimed contract_qty={claimed_qty_int} for {instrument} exceeds "
                        f"live effective contract cap {effective_cap}"
                    )

    return {
        "claimed": claimed,
        "live_verified": live_view,
        "mismatches": mismatches,
        "parity_ok": not mismatches,
    }
'''
replace_once("ops/project_check/promotion.py", old_context, new_context)

old_report = '''    return {
        "ok": evidence_error is None,
        "routine": "promotion-proof-gate",
'''
new_report = '''    evidence_supplied = bool(evidence)
    gate_pass = (
        evidence_error is None
        and evidence_supplied
        and not caps["blockers"]
        and execution_context["parity_ok"]
    )

    return {
        "ok": evidence_error is None,
        "gate_pass": gate_pass,
        "promotion_eligible": gate_pass,
        "routine": "promotion-proof-gate",
'''
replace_once("ops/project_check/promotion.py", old_report, new_report)
replace_once(
    "ops/project_check/promotion.py",
    '        "evidence_supplied": bool(evidence),\n',
    '        "evidence_supplied": evidence_supplied,\n',
)

old_cli = '''def _cmd_promotion(args: argparse.Namespace) -> int:
    report = build_promotion_report(
        strategy=args.strategy,
        repo_root=ROOT,
        evidence_path=args.evidence_file,
    )
    if args.json:
        _print_json(report)
        return 0 if report.get("ok") else 1
    print(f"PROMOTION PROOF GATE: {args.strategy}")
    if report.get("evidence_load_error"):
        print(f"  evidence load error: {report['evidence_load_error']}")
    print(f"  evidence supplied: {report['evidence_supplied']}")
    cls = report["classification"]
    print(f"  stated classification:   {cls['stated_classification']}")
    print(f"  effective classification:{cls['effective_classification']}")
    if cls["override_reason"]:
        print(f"    override reason: {cls['override_reason']}")
    if cls["blockers"]:
        print("  BLOCKERS:")
        for b in cls["blockers"]:
            print(f"    - {b}")
    if cls["warnings"]:
        print("  warnings:")
        for w in cls["warnings"]:
            print(f"    - {w}")
    ctx = report["execution_context"]
    print(f"  execution context parity ok: {ctx['parity_ok']}")
    if ctx["mismatches"]:
        for m in ctx["mismatches"]:
            print(f"    - {m}")
    return 0
'''
new_cli = '''def _cmd_promotion(args: argparse.Namespace) -> int:
    report = build_promotion_report(
        strategy=args.strategy,
        repo_root=ROOT,
        evidence_path=args.evidence_file,
    )
    if args.json:
        _print_json(report)
        return 0 if report.get("gate_pass") else 1
    print(f"PROMOTION PROOF GATE: {args.strategy}")
    if report.get("evidence_load_error"):
        print(f"  evidence load error: {report['evidence_load_error']}")
    print(f"  evidence supplied: {report['evidence_supplied']}")
    print(f"  gate pass: {report['gate_pass']}")
    cls = report["classification"]
    print(f"  stated classification:   {cls['stated_classification']}")
    print(f"  effective classification:{cls['effective_classification']}")
    if cls["override_reason"]:
        print(f"    override reason: {cls['override_reason']}")
    if cls["blockers"]:
        print("  BLOCKERS:")
        for b in cls["blockers"]:
            print(f"    - {b}")
    if cls["warnings"]:
        print("  warnings:")
        for w in cls["warnings"]:
            print(f"    - {w}")
    ctx = report["execution_context"]
    print(f"  execution context parity ok: {ctx['parity_ok']}")
    if ctx["mismatches"]:
        for m in ctx["mismatches"]:
            print(f"    - {m}")
    return 0 if report.get("gate_pass") else 1
'''
replace_once("scripts/project_check.py", old_cli, new_cli)

replace_once(
    "tests/test_project_check_promotion.py",
    'import json\nfrom pathlib import Path\n',
    'import json\nimport subprocess\nimport sys\nfrom pathlib import Path\n',
)
replace_once(
    "tests/test_project_check_promotion.py",
    '        "ENTRY_FILL_MODEL",\n',
    '        "ENTRY_FILL_MODEL",\n        "MAX_CONTRACTS_HARD_CAP",\n',
)
replace_once(
    "tests/test_project_check_promotion.py",
    '    assert report["evidence_supplied"] is False\n    assert report["classification"]["effective_classification"] == "REQUIRES_OPERATOR_CLASSIFICATION"\n',
    '    assert report["evidence_supplied"] is False\n    assert report["gate_pass"] is False\n    assert report["promotion_eligible"] is False\n    assert report["classification"]["effective_classification"] == "REQUIRES_OPERATOR_CLASSIFICATION"\n',
)
replace_once(
    "tests/test_project_check_promotion.py",
    '    assert report["ok"] is False\n    assert "not found" in report["evidence_load_error"]\n',
    '    assert report["ok"] is False\n    assert report["gate_pass"] is False\n    assert "not found" in report["evidence_load_error"]\n',
)
replace_once(
    "tests/test_project_check_promotion.py",
    '            "execution_context_claimed": {\n                "entry_fill_model": "ioc_limit",\n                "entry_tolerance_ticks": 32,\n',
    '            "execution_context_claimed": {\n                "instrument": "MNQ",\n                "entry_fill_model": "ioc_limit",\n                "entry_tolerance_ticks": 32,\n',
)

append = '''\n\ndef test_gate_fails_when_blockers_exist_even_if_classification_is_promising(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 1,
                "fills": 0,
                "cancellations": 1,
                "rejects_or_known_no_fills": 0,
                "resolved_outcomes": 0,
                "legitimately_open": 0,
            },
            "stated_classification": "PROMISING BUT UNPROVEN",
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["ok"] is True
    assert report["gate_pass"] is False
    assert report["classification"]["effective_classification"] == "PROMISING BUT UNPROVEN"


def test_claimed_tolerance_requires_instrument(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    evidence = _write_evidence(
        tmp_path,
        {"execution_context_claimed": {"entry_tolerance_ticks": 32}},
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["gate_pass"] is False
    assert any("instrument is required" in m for m in report["execution_context"]["mismatches"])


def test_tolerance_is_checked_only_against_claimed_instrument(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    evidence = _write_evidence(
        tmp_path,
        {
            "execution_context_claimed": {
                "instrument": "MES",
                "entry_tolerance_ticks": 32,
            }
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["gate_pass"] is False
    assert any("for MES" in m and "32" in m and "16" in m for m in report["execution_context"]["mismatches"])


def test_contract_qty_is_checked_against_relevant_effective_cap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    (tmp_path / "risk_rules.yaml").write_text(
        "position_rules:\n  max_contracts_per_instrument:\n    MNQ: 6\n",
        encoding="utf-8",
    )
    evidence = _write_evidence(
        tmp_path,
        {
            "execution_context_claimed": {
                "instrument": "MNQ",
                "contract_qty": 2,
            }
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["gate_pass"] is False
    assert any("contract_qty=2" in m and "cap 1" in m for m in report["execution_context"]["mismatches"])


def test_cli_returns_nonzero_when_promotion_gate_is_blocked(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 1,
                "fills": 0,
                "cancellations": 1,
                "rejects_or_known_no_fills": 0,
                "resolved_outcomes": 0,
                "legitimately_open": 0,
            },
            "stated_classification": "PROMISING BUT UNPROVEN",
        },
    )
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "project_check.py"),
            "promotion",
            "--strategy",
            "x",
            "--evidence-file",
            str(evidence),
            "--json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
'''

p = Path("tests/test_project_check_promotion.py")
p.write_text(p.read_text(encoding="utf-8") + append, encoding="utf-8")

for raw in (
    "scripts/_chatgpt_apply_promotion_gate.py",
    ".github/workflows/chatgpt-apply-promotion-gate.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
