from __future__ import annotations

import json
from pathlib import Path

from ops.box_cancelled_option_c_proof import build_box_cancelled_option_c_proof, print_human


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _repo(tmp_path: Path, *, paper_mode: bool = False, live_enabled: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    repo.joinpath("risk_rules.yaml").write_text(
        "trading_mode:\n"
        f"  paper_mode: {str(paper_mode).lower()}\n"
        f"  live_trading_enabled: {str(live_enabled).lower()}\n",
        encoding="utf-8",
    )
    return repo


def _trade(ts: str, instrument: str, *, direction: str, entry: float, close: float) -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {
            "direction": direction,
            "strategy": "pdh_reclaim",
            "entry": entry,
            "stop": 0.0,
            "target": 0.0,
            "contracts": 1,
        },
        "context": {"close": close},
    }


def _cancelled(
    ts: str,
    instrument: str,
    *,
    no_fill_reason: str | None = None,
    broker_status_raw: str | None = None,
) -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {
            "result": "CANCELLED",
            "exit_reason": "execution_failed:CANCELLED",
            "pnl_dollars": 0.0,
            "contracts": 1,
            "no_fill_reason": no_fill_reason,
            "broker_status_raw": broker_status_raw,
        },
    }


def test_box_proof_passes_clean_post_taxonomy_cancelled(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    repo = _repo(tmp_path)
    log_dir = repo / "logs"
    log_dir.mkdir()
    _write_jsonl(
        log_dir / "journal_2026-07-08.jsonl",
        [
            # MES LONG tolerance = 4.0pt, close beyond cap -> honest no-fill.
            _trade("2026-07-08T18:40:00Z", "MES", direction="LONG", entry=7500.0, close=7510.0),
            _cancelled(
                "2026-07-08T18:41:00Z",
                "MES",
                no_fill_reason="NO_FILL_LIMIT_TOO_PASSIVE",
                broker_status_raw="Canceled",
            ),
        ],
    )

    report = build_box_cancelled_option_c_proof(
        repo_root=repo,
        log_dir=log_dir,
        api_base=None,
        health_payload={"ok": True, "live_trading_enabled": False},
        broker_payload={"position": None},
    )

    assert report["verdict"] == "PASS"
    assert report["execution_mode_label"] == "Tradovate demo"
    assert report["current_position_state"] == "FLAT"
    assert report["post_2026-07-07T18:35:33Z_CANCELLED_count"] == 1
    assert report["option_c_recurrence"] == 0
    assert report["MISLABELED_FILL_SUSPECT"] == 0
    assert report["journal_coverage"]["latest_journal_timestamp"] == "2026-07-08T18:41:00Z"


def test_box_proof_fails_post_taxonomy_option_c_recurrence(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    repo = _repo(tmp_path)
    log_dir = repo / "logs"
    log_dir.mkdir()
    _write_jsonl(
        log_dir / "journal_2026-07-08.jsonl",
        [
            # MNQ SHORT tolerance = 8.0pt, close above cap -> marketable but cancelled.
            _trade("2026-07-08T18:40:00Z", "MNQ", direction="SHORT", entry=30000.0, close=29995.0),
            _cancelled("2026-07-08T18:41:00Z", "MNQ", no_fill_reason="NO_FILL_UNKNOWN"),
        ],
    )

    report = build_box_cancelled_option_c_proof(
        repo_root=repo,
        log_dir=log_dir,
        api_base=None,
        health_payload={"ok": True, "live_trading_enabled": False},
        broker_payload={"position": {"instrument": "MNQ", "netPos": 1}},
    )

    assert report["verdict"] == "FAIL"
    assert report["post_2026-07-07T18:35:33Z_CANCELLED_count"] == 1
    assert report["option_c_recurrence"] == 1
    assert report["MISLABELED_FILL_SUSPECT"] == 1
    assert "option_c_recurrence_present" in report["verdict_reasons"]
    assert "mislabeled_fill_suspect_present" in report["verdict_reasons"]
    assert '"instrument": "MNQ"' in report["current_position_state"]


def test_box_proof_ignores_pre_taxonomy_suspect_for_headline_verdict(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    repo = _repo(tmp_path)
    log_dir = repo / "logs"
    log_dir.mkdir()
    _write_jsonl(
        log_dir / "journal_2026-06-25.jsonl",
        [
            _trade("2026-06-25T18:40:00Z", "MNQ", direction="SHORT", entry=30000.0, close=29995.0),
            _cancelled("2026-06-25T18:41:00Z", "MNQ"),
        ],
    )

    report = build_box_cancelled_option_c_proof(
        repo_root=repo,
        log_dir=log_dir,
        api_base=None,
        health_payload={"ok": True, "live_trading_enabled": False},
        broker_payload={"position": None},
    )

    assert report["verdict"] == "PASS"
    assert report["post_2026-07-07T18:35:33Z_CANCELLED_count"] == 0
    assert report["option_c_recurrence"] == 0
    assert report["MISLABELED_FILL_SUSPECT"] == 0
    assert report["audit_by_instrument"]["MNQ"]["classification_counts"] == {"MISLABELED_FILL_SUSPECT": 1}


def test_print_human_includes_required_box_side_lines(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BROKER", "paper")
    repo = _repo(tmp_path, paper_mode=True)
    log_dir = repo / "logs"
    log_dir.mkdir()

    report = build_box_cancelled_option_c_proof(
        repo_root=repo,
        log_dir=log_dir,
        api_base=None,
        health_payload={"ok": True, "live_trading_enabled": False},
        broker_payload={"position": None},
    )
    print_human(report)

    out = capsys.readouterr().out
    assert "hostname:" in out
    assert "deployed_sha:" in out
    assert "service_health:" in out
    assert "LIVE_TRADING_ENABLED: False" in out
    assert "execution_mode_label: Paper simulator" in out
    assert "current_position_state: FLAT" in out
    assert "LOG_DIR:" in out
    assert "journal_files_checked:" in out
    assert "post-2026-07-07T18:35:33Z CANCELLED count:" in out
    assert "option_c_recurrence:" in out
    assert "MISLABELED_FILL_SUSPECT:" in out
    assert "final_verdict: PASS" in out
