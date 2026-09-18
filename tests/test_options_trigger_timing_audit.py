"""Pure summary checks for the trigger-time audit."""

from scripts.options_trigger_timing_audit import summarize


def test_summarize_counts_missing_old_and_family_changes():
    base_context = {"completed_alignment_ok": False, "developing_alignment_ok": True}
    rows = [
        {
            "trigger_status": "TRIGGERED", "old_family": "STRAT_212_CONTINUATION",
            "trigger_family": "STRAT_212_REVERSAL", "old_direction": "LONG",
            "trigger_direction": "SHORT", "final_scenario": "two_down",
            "old_first_sight_latency_minutes": 42.0, "trigger_context": base_context,
            "old_alignment_ok": False,
        },
        {
            "trigger_status": "TRIGGERED", "old_family": None,
            "trigger_family": "STRAT_212_CONTINUATION", "old_direction": None,
            "trigger_direction": "LONG", "final_scenario": "outside_bar",
            "old_first_sight_latency_minutes": None, "trigger_context": base_context,
            "old_alignment_ok": None,
        },
        {
            "trigger_status": "AMBIGUOUS", "old_family": None,
            "trigger_family": None, "old_direction": None, "trigger_direction": None,
            "final_scenario": "outside_bar", "old_first_sight_latency_minutes": None,
            "trigger_context": None, "old_alignment_ok": None,
        },
    ]
    summary = summarize(rows)
    assert summary["triggered"] == 2
    assert summary["ambiguous_first_break"] == 1
    assert summary["triggered_missing_from_old_directional_events"] == 1
    assert summary["family_changed_among_comparable"] == 1
    assert summary["direction_changed_among_comparable"] == 1
    assert summary["triggered_bar_later_became_outside"] == 1
    assert summary["old_first_sight_latency_minutes"]["median"] == 42.0
