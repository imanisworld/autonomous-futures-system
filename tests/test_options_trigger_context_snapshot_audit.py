from __future__ import annotations

from scripts.options_trigger_context_snapshot_audit import summarize_context_rows


def _item(
    *,
    old_family="STRAT_212_REVERSAL",
    trigger_family="STRAT_212_REVERSAL",
    old_alignment=False,
    completed=False,
    proxy=False,
):
    return {
        "trigger_row": {
            "old_family": old_family,
            "trigger_family": trigger_family,
            "old_alignment_ok": old_alignment,
            "session_date": "2026-09-09",
            "symbol": "SPY",
            "trigger_direction": "LONG",
            "trigger_bar_start": "2026-09-09T14:35:00+00:00",
        },
        "context": {
            "completed_alignment_ok": completed,
            "developing_alignment_ok": proxy,
            "completed_failures": [] if completed else ["hourly"],
            "developing_failures": [] if proxy else ["hourly", "daily"],
        },
    }


def test_summary_denominators_and_212_subsets_are_explicit():
    rows = [
        _item(old_alignment=True, completed=True, proxy=False),
        _item(old_alignment=False, completed=False, proxy=False),
        _item(
            old_family=None,
            old_alignment=False,
            completed=True,
            proxy=True,
        ),
    ]
    summary = summarize_context_rows(rows)

    assert summary["all_triggered"] == {
        "n": 3,
        "completed_alignment_passes": 2,
        "completed_30m_htf_proxy_passes": 1,
    }
    comparable = summary["comparable_with_frozen_close_event"]
    assert comparable["n"] == 2
    assert comparable["old_close_alignment_passes"] == 1
    assert comparable["completed_alignment_passes"] == 1
    assert comparable["completed_30m_htf_proxy_passes"] == 0
    assert comparable["old_to_completed_matrix"] == {
        "False->False": 1,
        "True->True": 1,
    }

    frozen = summary["frozen_212_reversal"]
    assert frozen["n"] == 2
    assert frozen["old_close_alignment_passes"] == 1
    assert frozen["completed_alignment_passes"] == 1
    assert frozen["completed_30m_htf_proxy_passes"] == 0

    additional = summary["additional_212_reversal"]
    assert additional["n"] == 1
    assert additional["completed_alignment_passes"] == 1
    assert additional["completed_30m_htf_proxy_passes"] == 1


def test_context_basis_refuses_exact_live_state_claim():
    summary = summarize_context_rows([])
    basis = summary["context_basis"]
    assert basis["cutoff"] == "start_of_first_crossing_5m_bucket"
    assert basis["completed_alignment"] == "completed_30m_plus_prior_completed_daily"
    assert basis["completed_30m_htf_proxy"] == (
        "completed_30m_derived_htf_proxy_excludes_trigger_bucket"
    )
    assert basis["exact_partial_trigger_bar_included"] is False
