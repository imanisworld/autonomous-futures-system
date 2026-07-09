"""Read-only box-side proof for post-taxonomy CANCELLED / Option C checks."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import yaml

from ops.audit_plain_cancelled import TAXONOMY_DEPLOY_TS, build_audit
from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR, parse_proof_ts, read_journal_entries


DEFAULT_LOCAL_API_BASE = "http://127.0.0.1:8000"


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _load_json_url(url: str, timeout_s: float = 4.0) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - local/operator-provided URL
            return json.loads(response.read().decode("utf-8")), None
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_risk_rules(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "risk_rules.yaml"
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _paper_mode(repo_root: Path) -> bool:
    rules = _read_risk_rules(repo_root)
    trading = rules.get("trading_mode") or {}
    default = bool(trading.get("paper_mode", True))
    return _env_bool(os.getenv("PAPER_MODE"), default)


def _live_trading_enabled(repo_root: Path, health: dict[str, Any] | None) -> bool:
    if health and isinstance(health.get("live_trading_enabled"), bool):
        return bool(health["live_trading_enabled"])
    rules = _read_risk_rules(repo_root)
    trading = rules.get("trading_mode") or {}
    yaml_live = bool(trading.get("live_trading_enabled", False))
    return _env_bool(os.getenv("LIVE_TRADING_ENABLED"), yaml_live)


def _execution_mode_label(repo_root: Path, live_enabled: bool) -> str:
    broker = os.getenv("BROKER", "paper").strip().lower()
    tv_env = os.getenv("TRADOVATE_ENV", "").strip().lower()
    if _paper_mode(repo_root):
        return "Paper simulator"
    if live_enabled and tv_env == "live":
        return "Live"
    if broker == "tradovate" and tv_env == "demo":
        return "Tradovate demo"
    return f"{broker or 'unknown'} execution"


def _journal_files(log_dir: Path) -> list[Path]:
    return sorted(log_dir.glob("journal_*.jsonl"))


def _latest_journal_ts(entries: list[dict[str, Any]]) -> str | None:
    latest: datetime | None = None
    latest_raw: str | None = None
    for entry in entries:
        dt = parse_proof_ts(entry.get("ts"))
        if dt is None:
            continue
        if latest is None or dt > latest:
            latest = dt
            latest_raw = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return latest_raw


def _position_state(broker_account: dict[str, Any] | None, error: str | None) -> str:
    if error:
        return f"unavailable: {error}"
    if not broker_account:
        return "unavailable"
    position = broker_account.get("position")
    if not position:
        return "FLAT"
    return json.dumps(position, sort_keys=True, default=str)


def _audit_counts(audit: dict[str, Any]) -> dict[str, int]:
    post_taxonomy_cancelled = 0
    option_c = 0
    suspect = 0
    for report in audit.values():
        post_taxonomy_rows = [row for row in report.get("all_rows") or [] if row.get("post_taxonomy")]
        post_taxonomy_cancelled += len(post_taxonomy_rows)
        option_c += sum(1 for row in post_taxonomy_rows if row.get("option_c_recurrence"))
        suspect += sum(
            1 for row in post_taxonomy_rows
            if row.get("classification") == "MISLABELED_FILL_SUSPECT"
        )
    return {
        "post_taxonomy_plain_cancelled_count": post_taxonomy_cancelled,
        "option_c_recurrence": option_c,
        "MISLABELED_FILL_SUSPECT": suspect,
    }


def build_box_cancelled_option_c_proof(
    *,
    repo_root: str | Path,
    log_dir: str | Path,
    api_base: str | None = DEFAULT_LOCAL_API_BASE,
    health_payload: dict[str, Any] | None = None,
    broker_payload: dict[str, Any] | None = None,
    health_error: str | None = None,
    broker_error: str | None = None,
) -> dict[str, Any]:
    """Build the proof report without writing files or changing runtime state."""
    repo = Path(repo_root).resolve()
    journal_dir = Path(log_dir).expanduser()
    if not journal_dir.is_absolute():
        journal_dir = (repo / journal_dir).resolve()
    else:
        journal_dir = journal_dir.resolve()

    if health_payload is None and health_error is None and api_base:
        health_payload, health_error = _load_json_url(f"{api_base.rstrip('/')}/health")
    if broker_payload is None and broker_error is None and api_base:
        broker_payload, broker_error = _load_json_url(f"{api_base.rstrip('/')}/status/broker-account")

    entries = read_journal_entries(journal_dir)
    read_errors = [entry for entry in entries if entry.get("type") == "READ_ERROR"]
    audit = build_audit(journal_dir)
    counts = _audit_counts(audit)
    live_enabled = _live_trading_enabled(repo, health_payload)

    journal_paths = _journal_files(journal_dir)
    service_ok = bool(health_payload and health_payload.get("ok") is True and not health_error)
    verdict_reasons: list[str] = []
    if not service_ok:
        verdict_reasons.append("service_health_unavailable_or_not_ok")
    if live_enabled:
        verdict_reasons.append("live_trading_enabled")
    if read_errors:
        verdict_reasons.append("journal_read_errors")
    if counts["option_c_recurrence"]:
        verdict_reasons.append("option_c_recurrence_present")
    if counts["MISLABELED_FILL_SUSPECT"]:
        verdict_reasons.append("mislabeled_fill_suspect_present")

    verdict = "PASS" if not verdict_reasons else "INSPECT"
    if live_enabled or counts["option_c_recurrence"] or counts["MISLABELED_FILL_SUSPECT"]:
        verdict = "FAIL"

    return {
        "hostname": socket.gethostname(),
        "deployed_sha": _git(repo, "rev-parse", "HEAD") or "unknown",
        "service_health": {
            "ok": service_ok,
            "payload": health_payload,
            "error": health_error,
        },
        "LIVE_TRADING_ENABLED": live_enabled,
        "execution_mode_label": _execution_mode_label(repo, live_enabled),
        "current_position_state": _position_state(broker_payload, broker_error),
        "LOG_DIR": str(journal_dir),
        "journal_coverage": {
            "files_checked": len(journal_paths),
            "journal_files_checked": [str(path) for path in journal_paths],
            "latest_journal_timestamp": _latest_journal_ts(entries),
            "read_errors": read_errors,
        },
        "taxonomy_cutoff": TAXONOMY_DEPLOY_TS.replace("+00:00", "Z"),
        "post_2026-07-07T18:35:33Z_CANCELLED_count": counts["post_taxonomy_plain_cancelled_count"],
        "option_c_recurrence": counts["option_c_recurrence"],
        "MISLABELED_FILL_SUSPECT": counts["MISLABELED_FILL_SUSPECT"],
        "verdict": verdict,
        "verdict_reasons": verdict_reasons,
        "audit_by_instrument": audit,
        "broker_account": {
            "payload": broker_payload,
            "error": broker_error,
        },
    }


def print_human(report: dict[str, Any]) -> None:
    coverage = report["journal_coverage"]
    service = report["service_health"]
    print("RiskSentinel CANCELLED / Option C Box Proof")
    print(f"hostname: {report['hostname']}")
    print(f"deployed_sha: {report['deployed_sha']}")
    if service.get("error"):
        print(f"service_health: ERROR {service['error']}")
    else:
        print(f"service_health: ok={service['ok']} payload={service.get('payload')}")
    print(f"LIVE_TRADING_ENABLED: {report['LIVE_TRADING_ENABLED']}")
    print(f"execution_mode_label: {report['execution_mode_label']}")
    print(f"current_position_state: {report['current_position_state']}")
    print(f"LOG_DIR: {report['LOG_DIR']}")
    print(f"journal_files_checked: {coverage['files_checked']}")
    for path in coverage["journal_files_checked"]:
        print(f"  - {path}")
    print(f"latest_journal_timestamp: {coverage['latest_journal_timestamp'] or 'none'}")
    print(
        "post-2026-07-07T18:35:33Z CANCELLED count: "
        f"{report['post_2026-07-07T18:35:33Z_CANCELLED_count']}"
    )
    print(f"option_c_recurrence: {report['option_c_recurrence']}")
    print(f"MISLABELED_FILL_SUSPECT: {report['MISLABELED_FILL_SUSPECT']}")
    if report["verdict_reasons"]:
        print(f"verdict_reasons: {', '.join(report['verdict_reasons'])}")
    print(f"final_verdict: {report['verdict']}")
