"""Read-only proof report for the next resolved MNQ runtime trades.

Approved runtime evidence sources are intentionally narrow:
  /root/autonomous-futures-system/logs/journal_YYYY-MM-DD.jsonl
  /root/autonomous-futures-system/logs/errors.log
  /status/today
  /status/broker-account
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_JOURNAL_DIR = Path("/root/autonomous-futures-system/logs")
DEFAULT_API_BASE = "http://5.78.84.223"
DEFAULT_INSTRUMENT = "MNQ"
DEFAULT_LIMIT = 30


@dataclass
class ResolvedTrade:
    trade: dict[str, Any]
    outcome: dict[str, Any]

    @property
    def trade_ts(self) -> str:
        return str(self.trade.get("ts") or "")

    @property
    def outcome_ts(self) -> str:
        return str(self.outcome.get("ts") or "")

    @property
    def setup(self) -> dict[str, Any]:
        return self.trade.get("setup") or {}

    @property
    def outcome_body(self) -> dict[str, Any]:
        return self.outcome.get("outcome") or {}

    def to_summary(self) -> dict[str, Any]:
        setup = self.setup
        outcome = self.outcome_body
        risk = self.trade.get("risk_check") or {}
        return {
            "trade_ts": self.trade_ts,
            "outcome_ts": self.outcome_ts,
            "instrument": self.trade.get("instrument"),
            "direction": setup.get("direction"),
            "strategy": setup.get("strategy"),
            "entry": setup.get("entry"),
            "stop": setup.get("stop"),
            "target": setup.get("target"),
            "risk": risk.get("result"),
            "result": outcome.get("result"),
            "exit_reason": outcome.get("exit_reason"),
            "pnl_dollars": outcome.get("pnl_dollars"),
            "contracts": outcome.get("contracts") or setup.get("contracts"),
        }


def parse_proof_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value)
    try:
        if raw.isdigit():
            seconds = int(raw) / 1000 if len(raw) >= 13 else int(raw)
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (OSError, ValueError):
        return None


def load_json_url(url: str, timeout_s: float = 8.0) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - operator-provided URL
            return json.loads(response.read().decode("utf-8")), None
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return None, str(exc)


def read_journal_entries(journal_dir: Path, through_date: str | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    paths = sorted(journal_dir.glob("journal_*.jsonl"))
    if through_date:
        cutoff = f"journal_{through_date}.jsonl"
        paths = [path for path in paths if path.name <= cutoff]
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                entries.append({
                    "type": "READ_ERROR",
                    "path": str(path),
                    "line": line_no,
                    "reason": "invalid_json",
                })
                continue
            entry.setdefault("_path", str(path))
            entry.setdefault("_line", line_no)
            entries.append(entry)
    return entries


def pair_resolved_trades(
    entries: list[dict[str, Any]],
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    freeze_ts: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[ResolvedTrade], list[dict[str, Any]]]:
    inst = instrument.upper()
    open_trades: list[dict[str, Any]] = []
    resolved: list[ResolvedTrade] = []
    unmatched_outcomes: list[dict[str, Any]] = []

    def after_freeze(entry: dict[str, Any]) -> bool:
        if freeze_ts is None:
            return True
        ts = parse_proof_ts(entry.get("ts"))
        return ts is not None and ts >= freeze_ts

    for entry in entries:
        if (entry.get("instrument") or "").upper() != inst:
            continue
        if entry.get("decision") == "TRADE" and (entry.get("risk_check") or {}).get("result") == "APPROVED":
            if after_freeze(entry):
                open_trades.append(entry)
            continue
        if entry.get("type") == "OUTCOME":
            if not after_freeze(entry):
                continue
            if open_trades:
                resolved.append(ResolvedTrade(open_trades.pop(0), entry))
                if len(resolved) >= limit:
                    break
            else:
                unmatched_outcomes.append(entry)
    return resolved, unmatched_outcomes


def summarize_errors(journal_dir: Path) -> dict[str, Any]:
    path = journal_dir / "errors.log"
    if not path.exists():
        return {"path": str(path), "exists": False, "size": 0, "lines": 0, "tail": []}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"path": str(path), "exists": True, "error": str(exc)}
    return {
        "path": str(path),
        "exists": True,
        "size": path.stat().st_size,
        "lines": len(lines),
        "tail": lines[-10:],
    }


def build_report(
    *,
    journal_dir: Path,
    freeze_ts: datetime | None,
    limit: int,
    api_base: str | None,
    instrument: str = DEFAULT_INSTRUMENT,
    status_json: Path | None = None,
    broker_json: Path | None = None,
    status_payload: dict[str, Any] | None = None,
    broker_payload: dict[str, Any] | None = None,
    status_error: str | None = None,
    broker_error: str | None = None,
) -> dict[str, Any]:
    instrument = instrument.upper()
    entries = read_journal_entries(journal_dir)
    resolved, unmatched_outcomes = pair_resolved_trades(
        entries,
        instrument=instrument,
        freeze_ts=freeze_ts,
        limit=limit,
    )
    if status_payload is None and status_error is None:
        status_payload, status_error = _payload_from_path_or_url(status_json, api_base, "/status/today")
    if broker_payload is None and broker_error is None:
        broker_payload, broker_error = _payload_from_path_or_url(broker_json, api_base, "/status/broker-account")
    pnl = sum(float((trade.outcome_body.get("pnl_dollars") or 0.0)) for trade in resolved)
    trade_summaries = [trade.to_summary() for trade in resolved]
    rejected_count = sum(
        1 for entry in entries
        if (entry.get("instrument") or "").upper() == instrument
        and (
            entry.get("decision") == "RISK_REJECTED"
            or (entry.get("risk_check") or {}).get("result") == "REJECTED"
        )
    )
    read_errors = [entry for entry in entries if entry.get("type") == "READ_ERROR"]
    errors = summarize_errors(journal_dir)

    broker_realized = None
    if broker_payload:
        broker_realized = broker_payload.get("realized_pnl")
    warnings: list[str] = []
    status_journal_path = (status_payload or {}).get("journal_path")
    if status_journal_path:
        expected = journal_dir / Path(str(status_journal_path)).name
        if not expected.exists():
            warnings.append(
                f"/status/today reports journal_path={status_journal_path}, "
                f"but {expected} does not exist from this run context."
            )
    status_trade_count = (status_payload or {}).get("trade_count")
    try:
        if status_trade_count is not None and int(status_trade_count) > 0 and not resolved:
            warnings.append(
                "/status/today reports trades, but this journal-dir produced zero resolved "
                f"{instrument} trades. Run on the active box or point --journal-dir at the frozen runtime logs."
            )
    except (TypeError, ValueError):
        pass

    return {
        "ok": not read_errors,
        "proof_name": f"next_{limit}_{instrument.lower()}_resolved_trades",
        "instrument": instrument,
        "runtime_sources": {
            "journal_dir": str(journal_dir),
            "journal_pattern": str(journal_dir / "journal_YYYY-MM-DD.jsonl"),
            "errors_log": str(journal_dir / "errors.log"),
            "status_today": f"{api_base.rstrip('/')}/status/today" if api_base else "/status/today",
            "broker_account": f"{api_base.rstrip('/')}/status/broker-account" if api_base else "/status/broker-account",
        },
        "source_of_truth_rule": (
            "Only the active runtime journal, errors.log, /status/today, and "
            "/status/broker-account count as proof. Replay output, local ignored logs, "
            "Discord messages, screenshots, and broker P&L alone are not end-to-end proof."
        ),
        "freeze_ts": freeze_ts.isoformat() if freeze_ts else None,
        "target_trades": limit,
        "resolved_mnq_trades": len(resolved),
        "resolved_trades": len(resolved),
        "remaining_to_target": max(0, limit - len(resolved)),
        "journal_pnl_dollars": round(pnl, 2),
        "mnq_risk_rejected_count": rejected_count,
        "risk_rejected_count": rejected_count,
        "unmatched_mnq_outcomes": len(unmatched_outcomes),
        "unmatched_outcomes": len(unmatched_outcomes),
        "journal_read_errors": read_errors,
        "errors_log": errors,
        "status_today": status_payload,
        "status_today_error": status_error,
        "broker_account": broker_payload,
        "broker_account_error": broker_error,
        "broker_realized_pnl": broker_realized,
        "warnings": warnings,
        "trades": trade_summaries,
    }


def _payload_from_path_or_url(
    path: Path | None,
    api_base: str | None,
    suffix: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if path:
        try:
            return json.loads(path.read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError) as exc:
            return None, str(exc)
    if api_base:
        return load_json_url(f"{api_base.rstrip('/')}{suffix}")
    return None, None
