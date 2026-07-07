"""Append-only proof that the live ``process_alert`` runner shadow path ran.

This is deliberately separate from the trade journal: shadow observations must
never affect position reconstruction, trade counts, or risk state.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


EVIDENCE_FILENAME = "runner_shadow_evidence.jsonl"
DEFAULT_FRESH_SECONDS = 30 * 60
_TRUTHY = {"1", "true", "yes", "on"}


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def append_runner_shadow_evidence(
    log_dir: str | Path,
    *,
    instrument: str,
    setup: str | None,
    bar_ts: str | None,
    result: dict[str, Any],
    fill_confirmed: bool | None = None,
) -> None:
    """Append one live-path observation. Callers own fail-soft exception handling.

    ``fill_confirmed``: whether the position's ENTRY order verifiably filled at
    the broker (True), or the fill status was unreadable at write time (None —
    kept but tagged so evidence review can filter). Callers must NOT write a row
    for a definitive no-fill: shadow math on a never-filled entry is fill
    fiction (2026-07-02 MES orb_reclaim row; same artifact class as PR #150).
    """
    path = Path(log_dir) / EVIDENCE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "process_alert",
        "instrument": instrument,
        "setup": setup,
        "bar_ts": bar_ts,
        "direction": result.get("direction"),
        "favorable_r": result.get("favorable_r"),
        "armed": bool(result.get("trailing")),
        "moved": bool(result.get("moved")),
        "would_stop": result.get("would_stop"),
        "original_stop": result.get("original_stop"),
        "fill_confirmed": fill_confirmed,
    }
    with open(path, "a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def runner_shadow_status(
    log_dir: str | Path,
    *,
    now: datetime | None = None,
    fresh_seconds: int = DEFAULT_FRESH_SECONDS,
) -> dict[str, Any]:
    """Return the latest readable live-path proof, or an actionable empty state."""
    explicit_mode = os.getenv("EXIT_MODE")
    if explicit_mode is not None:
        mode = explicit_mode.strip().lower()
        enabled = mode == "runner_shadow"
        live_enabled = mode == "runner_live"
    else:
        enabled = _enabled("RUNNER_SHADOW_ENABLED")
        live_enabled = _enabled("RUNNER_LIVE_ENABLED")
    path = Path(log_dir) / EVIDENCE_FILENAME
    latest: dict[str, Any] | None = None
    read_error: str | None = None
    try:
        if path.exists():
            with open(path) as handle:
                for line in handle:
                    try:
                        candidate = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if candidate.get("source") == "process_alert" and candidate.get("observed_at"):
                        latest = candidate
    except OSError as exc:
        read_error = type(exc).__name__

    age_seconds: int | None = None
    if latest is not None:
        try:
            observed = datetime.fromisoformat(str(latest["observed_at"]).replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            age_seconds = max(0, int((current - observed.astimezone(timezone.utc)).total_seconds()))
        except (TypeError, ValueError):
            latest = None

    recent = latest is not None and age_seconds is not None and age_seconds <= fresh_seconds
    armed_and_moved = bool(
        recent and latest is not None and latest.get("armed") and latest.get("moved")
    )
    # Proof requires the entry fill to be broker-CONFIRMED. Rows tagged
    # fill_confirmed:null (status unreadable at write time) and legacy rows
    # missing the key entirely (e.g. the contaminated 2026-07-02 MES row, whose
    # entry never filled) are path evidence only — never promotion proof.
    proof_sufficient = armed_and_moved and latest.get("fill_confirmed") is True
    if recent:
        state = "proof_sufficient" if proof_sufficient else "recent_path_evidence"
        if proof_sufficient:
            detail = "trail armed and stop moved on a broker-confirmed entry fill"
        elif armed_and_moved:
            detail = (
                "trail armed and moved, but the entry fill is unconfirmed at the "
                "broker — excluded from proof"
            )
        else:
            detail = "path observed, but no armed stop movement yet"
        summary = (
            f"Live process_alert runner shadow observed {age_seconds}s ago for "
            f"{latest.get('instrument') or 'unknown instrument'}"
            f"{' / ' + str(latest.get('setup')) if latest.get('setup') else ''}; "
            f"{detail}."
        )
        if proof_sufficient and live_enabled:
            next_step = (
            "Live trailing is enabled; continue monitoring shadow evidence and broker stop replacements."
            )
        elif proof_sufficient:
            next_step = "Shadow proof is sufficient; review multiple observations before enabling live trailing."
        elif armed_and_moved:
            next_step = (
                "Keep live trailing blocked: the armed observation lacks a "
                "broker-confirmed entry fill (fill-fiction guard)."
            )
        else:
            next_step = "Keep live trailing blocked until recent evidence shows an armed, moved stop."
    elif latest is not None:
        state = "stale_evidence"
        summary = (
            f"Last live process_alert runner shadow evidence is stale "
            f"({age_seconds}s old; limit {fresh_seconds}s)."
        )
        next_step = "Keep live trailing blocked until an open position receives a fresh same-instrument bar."
    elif enabled:
        state = "awaiting_evidence"
        summary = "Runner shadow is enabled, but the live process_alert path has produced no readable evidence."
        next_step = "Keep live trailing blocked until an open position receives a same-instrument bar."
    else:
        state = "disabled"
        summary = "Runner shadow proof collection is disabled."
        next_step = "Set EXIT_MODE=runner_shadow and restart before considering live trailing."

    return {
        "enabled": enabled,
        "live_enabled": live_enabled,
        "state": state,
        "evidence_observed": latest is not None,
        "recent": recent,
        "path_observed_recently": recent,
        "proof_sufficient": proof_sufficient,
        "fresh_for_seconds": fresh_seconds,
        "age_seconds": age_seconds,
        "live_trailing_blocked": not proof_sufficient,
        "latest": latest,
        "evidence_path": str(path),
        "read_error": read_error,
        "summary": summary,
        "next_step": next_step,
    }
