from journal.journal_logger import JournalLogger


def test_runner_stop_and_order_id_survive_journal_restart(tmp_path):
    journal = JournalLogger(log_dir=str(tmp_path))
    journal.log_decision(
        {
            "ts": "2026-07-02T15:00:00+00:00",
            "instrument": "MES",
            "session": "new_york",
            "decision": "TRADE",
            "risk_check": {"result": "APPROVED"},
            "setup": {
                "direction": "LONG",
                "entry": 5900.0,
                "stop": 5890.0,
                "target": 5920.0,
                "contracts": 1,
                "strategy": "orb_breakout",
            },
            "context": {"timestamp": "2026-07-02T14:45:00+00:00"},
        },
        None,
    )
    journal.log_order_ids(
        "MES",
        "new_york",
        {"instrument": "MES", "entry": 10, "target": None, "stop": 21},
        stop=5905.0,
        exit_mode="runner_live",
    )
    restored = JournalLogger(log_dir=str(tmp_path)).get_open_position()
    assert restored["stop"] == 5905.0
    assert restored["target"] is None
    assert restored["exit_mode"] == "runner_live"
    assert restored["order_ids"]["stop"] == 21
