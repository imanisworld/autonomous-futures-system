from research.mnq_pdl_sweep_reclaim_friction import ScoredEpisode, score_path, summarize


def test_score_path_applies_frozen_friction():
    row = score_path(entry_open=20000.0, exit_close=20008.0)
    assert row["gross_points"] == 8.0
    assert row["gross_dollars"] == 16.0
    assert row["net_2pt_points"] == 6.0
    assert row["net_3pt_points"] == 5.0


def _row(day: str, net3: float) -> ScoredEpisode:
    gross = net3 + 3.0
    return ScoredEpisode(
        session_date=day,
        event_bar_start=f"{day}T15:00:00+00:00",
        entry_bar_start=f"{day}T15:05:00+00:00",
        entry_open=20000.0,
        exit_close=20000.0 + gross,
        gross_points=gross,
        gross_dollars=gross * 2.0,
        net_2pt_points=gross - 2.0,
        net_3pt_points=net3,
    )


def test_advancement_requires_both_halves_and_50_each():
    rows = [_row("2025-08-01", 1.0) for _ in range(50)]
    rows += [_row("2025-10-01", 1.0) for _ in range(50)]
    assert summarize(rows)["advance_to_bracket_study"] is True


def test_negative_second_half_blocks_advancement():
    rows = [_row("2025-08-01", 1.0) for _ in range(50)]
    rows += [_row("2025-10-01", -1.0) for _ in range(50)]
    assert summarize(rows)["advance_to_bracket_study"] is False
