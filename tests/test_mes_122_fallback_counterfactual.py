from scripts.mes_122_fallback_counterfactual import analyze


def test_control_reproduces_pr373_executable_population():
    report = analyze()
    assert report["control_current_executable"] == {
        "n": 16,
        "wins": 5,
        "losses": 11,
        "net": 120.0,
        "profit_factor": 1.421053,
        "h1_net": 11.25,
        "h2_net": 108.75,
        "max_drawdown": 121.25,
        "dates": [
            "2025-09-05", "2025-10-01", "2025-10-08", "2025-10-16",
            "2025-11-13", "2025-11-27", "2025-12-01", "2025-12-05",
            "2025-12-17", "2026-03-17", "2026-03-30", "2026-04-06",
            "2026-04-09", "2026-05-29", "2026-06-03", "2026-06-19",
        ],
    }


def test_entry_detached_candidate_fallback_is_the_material_repair_hypothesis():
    report = analyze()["entry_detached_fallback_only"]
    assert [row["date"] for row in report["recovered_rows"]] == [
        "2025-10-24", "2026-02-20", "2026-03-13", "2026-03-26"
    ]
    assert sum(row["pnl"] for row in report["recovered_rows"]) == 402.5
    metrics = report["metrics"]
    assert metrics["n"] == 20
    assert metrics["wins"] == 9
    assert metrics["losses"] == 11
    assert metrics["net"] == 522.5
    assert metrics["profit_factor"] == 2.833333
    assert metrics["h1_net"] == 82.5
    assert metrics["h2_net"] == 440.0
    assert metrics["max_drawdown"] == 121.25


def test_permission_fallback_is_not_the_source_of_the_improvement():
    metrics = analyze()["permission_blocked_only_diagnostic"]["metrics"]
    assert metrics["n"] == 19
    assert metrics["net"] == 150.0
    assert metrics["profit_factor"] == 1.434783


def test_population_accounting_stays_pinned_to_pr373():
    provenance = analyze()["provenance"]
    assert provenance["known_candidate_count"] == 33
    assert provenance["executable_count"] == 16
    assert provenance["preempted_count"] == 7
    assert provenance["blocked_no_engine_decision_count"] == 10
