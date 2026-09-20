"""Read-only proof-lane status for the three MNQ evidence lanes.

This module reports configuration, wiring, existing evidence freshness, and
whether exchange-closed silence is expected. It never advances a collector,
creates state, calls risk, contacts a broker, or changes execution behavior.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from context.futures_session import product_session_active
from execution.mnq_strat_evidence import evidence_path as mnq_evidence_path
from execution.mnq_strat_evidence import state_path as mnq_state_path


_LANES: tuple[dict[str, Any], ...] = (
    {
        "lane": "mnq_inverse_orb",
        "label": "MNQ inverse ORB",
        "env": "MNQ_ORB_BREAKOUT_INVERSE_MODE",
        "allowed_modes": {"observe_only", "paper_sim"},
        "default": "observe_only",
        "runner_tokens": (
            "evaluate_mnq_orb_breakout_inverse",
            "mnq_orb_breakout_inverse_audit",
        ),
        "journal_key": "mnq_orb_breakout_inverse_audit",
    },
    {
        "lane": "mnq_orb_reclaim",
        "label": "MNQ ORB reclaim",
        "env": "MNQ_ORB_RECLAIM_PROOF_MODE",
        "allowed_modes": {"observe_only", "paper_sim", "tradovate_demo"},
        "default": "observe_only",
        "runner_tokens": (
            "evaluate_mnq_orb_reclaim_proof",
            "mnq_orb_reclaim_proof_audit",
        ),
        "journal_key": "mnq_orb_reclaim_proof_audit",
        "state_glob": "mnq_orb_reclaim_proof_campaigns_*.json",
    },
    {
        "lane": "mnq_strat_22_reversal",
        "label": "MNQ 2-2 reversal",
        "env": "MNQ_STRAT_22_REVERSAL_MODE",
        "allowed_modes": {"observe_only", "paper_sim"},
        "default": "observe_only",
        "runner_tokens": ("process_mnq_strat_evidence",),
        "evidence_lane": "strat_22_reversal",
    },
)


def _deployment_value(repo_root: Path, name: str) -> tuple[str | None, str]:
    raw = os.getenv(name)
    if raw is not None and raw.strip():
        return raw.strip(), "process_env"
    env_path = repo_root / ".env"
    if env_path.exists():
        try:
            value = dotenv_values(env_path).get(name)
        except (OSError, ValueError):
            value = None
        if value is not None and str(value).strip():
            return str(value).strip(), ".env"
    return None, "code_default"


def _effective_mode(repo_root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    raw, source = _deployment_value(repo_root, spec["env"])
    candidate = str(raw or spec["default"]).strip().lower()
    valid = candidate in spec["allowed_modes"]
    return {
        "effective_mode": candidate if valid else spec["default"],
        "mode_source": source,
        "configured_value": raw,
        "config_valid": valid,
        "config_reason": (
            None
            if valid
            else f"{spec['env']}={candidate!r} is invalid; runtime validation must fail closed"
        ),
    }


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, (str, datetime)):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_ts(row: dict[str, Any]) -> datetime | None:
    for key in ("observed_at", "exit_ts", "resolved_at", "entry_ts", "timestamp", "ts", "bar_ts"):
        parsed = _parse_ts(row.get(key))
        if parsed is not None:
            return parsed
    context = row.get("context")
    if isinstance(context, dict):
        for key in ("timestamp", "bar_timestamp", "ts"):
            parsed = _parse_ts(context.get(key))
            if parsed is not None:
                return parsed
    return None


def _latest_matching_journal(
    log_dir: Path, audit_key: str
) -> tuple[datetime | None, str | None]:
    for path in sorted(log_dir.glob("journal_*.jsonl"), reverse=True):
        latest: datetime | None = None
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict) or row.get(audit_key) is None:
                        continue
                    ts = _row_ts(row)
                    if ts is not None and (latest is None or ts > latest):
                        latest = ts
        except OSError:
            continue
        if latest is not None:
            return latest, str(path)
    return None, None


def _latest_jsonl(path: Path) -> datetime | None:
    latest: datetime | None = None
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = _row_ts(row)
                if ts is not None and (latest is None or ts > latest):
                    latest = ts
    except OSError:
        return None
    return latest


def _latest_mtime(paths: list[Path]) -> tuple[datetime | None, str | None]:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None, None
    try:
        latest_path = max(existing, key=lambda path: path.stat().st_mtime_ns)
        ts = datetime.fromtimestamp(latest_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None, None
    return ts, str(latest_path)


def _wiring_status(repo_root: Path, spec: dict[str, Any]) -> tuple[str, str | None]:
    runner = repo_root / "webhook" / "runner.py"
    try:
        source = runner.read_text(encoding="utf-8")
    except OSError as exc:
        return "UNKNOWN", f"cannot verify webhook/runner.py: {exc}"
    missing = [token for token in spec["runner_tokens"] if token not in source]
    if missing:
        return "NOT_WIRED", "missing runner integration token(s): " + ", ".join(missing)
    return "WIRED", None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _newest(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _lane_activity(
    log_dir: Path, spec: dict[str, Any]
) -> tuple[datetime | None, str | None, datetime | None, str | None]:
    journal_ts = journal_source = None
    if spec.get("journal_key"):
        journal_ts, journal_source = _latest_matching_journal(
            log_dir, spec["journal_key"]
        )

    state_ts = state_source = None
    if spec.get("state_glob"):
        state_ts, state_source = _latest_mtime(
            list(log_dir.glob(spec["state_glob"]))
        )
    if spec.get("evidence_lane"):
        lane = spec["evidence_lane"]
        evidence = mnq_evidence_path(log_dir, lane)
        evidence_ts = _latest_jsonl(evidence)
        state_file_ts, state_file_source = _latest_mtime(
            [mnq_state_path(log_dir, lane)]
        )
        state_ts = _newest(evidence_ts, state_file_ts)
        if state_ts == evidence_ts and evidence_ts is not None:
            state_source = str(evidence)
        else:
            state_source = state_file_source
    return journal_ts, journal_source, state_ts, state_source


def build_proof_lane_status(
    *,
    repo_root: str | Path,
    log_dir: str | Path = "logs",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return deterministic read-only status for the requested MNQ proof lanes."""
    root = Path(repo_root)
    logs = Path(log_dir)
    if not logs.is_absolute():
        logs = root / logs
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    session_active = product_session_active("MNQ", now)
    lanes: list[dict[str, Any]] = []
    for spec in _LANES:
        mode = _effective_mode(root, spec)
        wiring, wiring_reason = _wiring_status(root, spec)
        journal_ts, journal_source, state_ts, state_source = _lane_activity(
            logs, spec
        )
        latest = _newest(journal_ts, state_ts)

        silence_expected = session_active is False
        if wiring == "NOT_WIRED":
            collection = "NOT_WIRED"
            reason = wiring_reason
        elif wiring == "UNKNOWN":
            collection = "UNKNOWN"
            reason = wiring_reason
        elif not mode["config_valid"]:
            collection = "CONFIG_INVALID"
            reason = mode["config_reason"]
        else:
            collection = (
                "PAPER_SIM"
                if mode["effective_mode"] == "paper_sim"
                else "DEMO"
                if mode["effective_mode"] == "tradovate_demo"
                else "OBSERVE_ONLY"
            )
            if silence_expected:
                reason = "MNQ product session is closed; evidence silence is expected"
            elif latest is None:
                reason = (
                    "wired and enabled for observation; no qualifying evidence "
                    "or lane state has been recorded yet"
                )
            else:
                reason = "wired; latest existing proof-lane activity is reported"

        lanes.append(
            {
                "lane": spec["lane"],
                "label": spec["label"],
                "instrument": "MNQ",
                **mode,
                "wiring_status": wiring,
                "collection_status": collection,
                "latest_journal_timestamp": _iso(journal_ts),
                "latest_journal_source": journal_source,
                "latest_state_or_evidence_timestamp": _iso(state_ts),
                "latest_state_or_evidence_source": state_source,
                "latest_activity_timestamp": _iso(latest),
                "off_session_silence_expected": silence_expected,
                "reason": reason,
            }
        )

    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "read_only": True,
        "instrument": "MNQ",
        "product_session_active": session_active,
        "lanes": lanes,
    }


def format_proof_lane_status(report: dict[str, Any]) -> str:
    lines = ["PROJECT_CHECK PROOF LANES — READ ONLY"]
    session = report.get("product_session_active")
    lines.append(
        "  MNQ product session: "
        + ("OPEN" if session is True else "CLOSED" if session is False else "UNKNOWN")
    )
    for lane in report.get("lanes", []):
        silence = "YES" if lane["off_session_silence_expected"] else "NO"
        latest = lane["latest_activity_timestamp"] or "none"
        lines.extend(
            [
                f"  {lane['label']}:",
                f"    deployed effective mode: {lane['effective_mode']} ({lane['mode_source']})",
                f"    collection: {lane['collection_status']} | wiring: {lane['wiring_status']}",
                f"    latest journal/state: {latest}",
                f"    silence expected off-session: {silence}",
                f"    reason: {lane['reason']}",
            ]
        )
    return "\n".join(lines)
