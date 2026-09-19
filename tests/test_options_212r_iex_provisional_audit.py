from datetime import datetime, timezone

from scripts.options_212r_iex_provisional_audit import Arm, _summarize, reconcile_pair


UTC = timezone.utc


def _arm():
    return Arm(
        session_date="2026-09-18",
        symbol="SPY",
        watch_start=datetime(2026, 9, 18, 15, 0, tzinfo=UTC),
        watch_end=datetime(2026, 9, 18, 15, 30, tzinfo=UTC),
        boundary_high=101.0,
        boundary_low=99.0,
        reference_direction="two_up",
    )


def _source(*, family_side, direction, ns, status="PROVEN"):
    if status != "PROVEN":
        return {"status": status}
    return {
        "status": "PROVEN",
        "family_side": family_side,
        "direction": direction,
        "timestamp_ns": ns,
        "timestamp": "2026-09-18T15:01:00Z",
    }


def test_same_reversal_confirms_and_keeps_iex_delay():
    rec = reconcile_pair(
        arm=_arm(),
        sip=_source(family_side="REVERSAL", direction="SHORT", ns=1_000_000_000),
        iex=_source(family_side="REVERSAL", direction="SHORT", ns=4_500_000_000),
    )
    assert rec["provisional_emitted"] is True
    assert rec["reconciliation"] == "CONFIRMED_SAME_REVERSAL"
    assert rec["latency_seconds"] == 3.5


def test_iex_no_break_is_explicit_miss_not_backfill():
    rec = reconcile_pair(
        arm=_arm(),
        sip=_source(family_side="REVERSAL", direction="SHORT", ns=1_000_000_000),
        iex={"status": "NO_BREAK"},
    )
    assert rec == {
        "provisional_emitted": False,
        "reconciliation": "MISS_NO_PROVISIONAL",
        "reason_code": "iex_no_break",
        "latency_seconds": None,
    }


def test_iex_continuation_never_becomes_provisional_reversal():
    rec = reconcile_pair(
        arm=_arm(),
        sip=_source(family_side="REVERSAL", direction="SHORT", ns=1_000_000_000),
        iex=_source(family_side="CONTINUATION", direction="LONG", ns=2_000_000_000),
    )
    assert rec["provisional_emitted"] is False
    assert rec["reason_code"] == "iex_continuation_first"


def test_false_provisional_is_rejected_after_delayed_sip():
    rec = reconcile_pair(
        arm=_arm(),
        sip=_source(family_side="CONTINUATION", direction="LONG", ns=1_000_000_000),
        iex=_source(family_side="REVERSAL", direction="SHORT", ns=2_000_000_000),
    )
    assert rec["provisional_emitted"] is True
    assert rec["reconciliation"] == "REJECTED_SIP_CONTINUATION_FIRST"


def test_negative_source_latency_is_not_silently_confirmed():
    rec = reconcile_pair(
        arm=_arm(),
        sip=_source(family_side="REVERSAL", direction="SHORT", ns=5_000_000_000),
        iex=_source(family_side="REVERSAL", direction="SHORT", ns=4_000_000_000),
    )
    assert rec["reconciliation"] == "SOURCE_INCONSISTENT_NEGATIVE_LATENCY"


def test_blocked_sip_prevents_confirmed_cohort():
    rec = reconcile_pair(
        arm=_arm(),
        sip={"status": "DATA_BLOCKED"},
        iex=_source(family_side="REVERSAL", direction="SHORT", ns=2_000_000_000),
    )
    assert rec["provisional_emitted"] is True
    assert rec["reconciliation"] == "DATA_BLOCKED"


def test_summary_counts_confirmed_recall_and_false_provisional():
    rows = [
        {
            "session_date": "2026-09-18",
            "sip": _source(family_side="REVERSAL", direction="SHORT", ns=1_000_000_000),
            "iex": _source(family_side="REVERSAL", direction="SHORT", ns=2_000_000_000),
            "policy": {
                "provisional_emitted": True,
                "reconciliation": "CONFIRMED_SAME_REVERSAL",
                "latency_seconds": 1.0,
            },
        },
        {
            "session_date": "2026-09-18",
            "sip": _source(family_side="REVERSAL", direction="LONG", ns=1_000_000_000),
            "iex": {"status": "NO_BREAK"},
            "policy": {
                "provisional_emitted": False,
                "reconciliation": "MISS_NO_PROVISIONAL",
                "latency_seconds": None,
            },
        },
        {
            "session_date": "2026-09-18",
            "sip": _source(family_side="CONTINUATION", direction="LONG", ns=1_000_000_000),
            "iex": _source(family_side="REVERSAL", direction="SHORT", ns=2_000_000_000),
            "policy": {
                "provisional_emitted": True,
                "reconciliation": "REJECTED_SIP_CONTINUATION_FIRST",
                "latency_seconds": None,
            },
        },
    ]
    summary = _summarize(rows)
    assert summary["sip_reversal_n"] == 2
    assert summary["iex_provisional_reversal_n"] == 2
    assert summary["confirmed_reversal_n"] == 1
    assert summary["false_provisional_n"] == 1
    assert summary["provisional_confirmation_rate"] == 0.5
    assert summary["sip_reversal_recall"] == 0.5
    assert summary["exact_sip_substitute"] is False


def test_committed_all_arm_result_matches_preregistered_population_and_verdict():
    import json
    from pathlib import Path

    payload = json.loads(
        Path("data/options_212r_iex_provisional_audit_2026_09_18/result.json").read_text()
    )
    summary = payload["summary"]
    assert summary["arms"] == 183
    assert summary["sip_class_counts"] == {
        "CONTINUATION": 66,
        "NO_BREAK": 26,
        "REVERSAL": 91,
    }
    assert summary["iex_class_counts"] == {
        "CONTINUATION": 61,
        "NO_BREAK": 32,
        "REVERSAL": 90,
    }
    assert summary["reconciliation_counts"] == {
        "CONFIRMED_SAME_REVERSAL": 89,
        "MISS_NO_PROVISIONAL": 93,
        "REJECTED_SIP_CONTINUATION_FIRST": 1,
    }
    assert summary["study_verdict"] == "MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE"
    assert summary["exact_sip_substitute"] is False


def test_committed_result_hash_matches_repeat_proof():
    import hashlib
    import json
    from pathlib import Path

    root = Path("data/options_212r_iex_provisional_audit_2026_09_18")
    payload = (root / "result.json").read_bytes()
    proof = json.loads((root / "repeat_proof.json").read_text())
    digest = hashlib.sha256(payload).hexdigest()
    assert proof["byte_identical"] is True
    assert digest == proof["primary_sha256"] == proof["repeat_sha256"]
