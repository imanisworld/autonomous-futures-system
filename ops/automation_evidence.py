"""Read-only status for the cron-driven ops automations."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DAILY_FRESH_SECONDS = 36 * 60 * 60
WEEKLY_FRESH_SECONDS = 8 * 24 * 60 * 60


def _read_generated_at(path: Path) -> datetime | None:
    try:
        value = json.loads(path.read_text()).get("generated_at")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
        return None


def _job_status(name: str, path: Path | None, max_age_seconds: int, now: datetime) -> dict:
    generated_at = _read_generated_at(path) if path is not None else None
    if generated_at is None:
        return {
            "job": name,
            "status": "missing",
            "fresh": False,
            "generated_at": None,
            "age_seconds": None,
            "fresh_for_seconds": max_age_seconds,
            "evidence_path": str(path) if path is not None else None,
        }
    age = max(0, int((now - generated_at).total_seconds()))
    fresh = age <= max_age_seconds
    return {
        "job": name,
        "status": "fresh" if fresh else "stale",
        "fresh": fresh,
        "generated_at": generated_at.isoformat(),
        "age_seconds": age,
        "fresh_for_seconds": max_age_seconds,
        "evidence_path": str(path),
    }


def automation_evidence_status(
    log_dir: str | Path,
    *,
    backup_dir: str | Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Inspect evidence only; never runs a job or writes a file."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    logs = Path(log_dir)
    backup = Path(
        backup_dir
        or os.getenv("PROOF_BACKUP_DIR", os.path.expanduser("~/afs-backups"))
    )
    try:
        weekly_candidates = list(logs.glob("weekly_review_*.json"))
        weekly_path = max(weekly_candidates, key=lambda p: p.stat().st_mtime) if weekly_candidates else None
    except OSError:
        weekly_path = None

    jobs = [
        _job_status("health_digest", logs / "health_digest_latest.json", DAILY_FRESH_SECONDS, now),
        _job_status("backup_proof_data", backup / "backup_proof_data_latest.json", DAILY_FRESH_SECONDS, now),
        _job_status("weekly_review", weekly_path, WEEKLY_FRESH_SECONDS, now),
    ]
    return {
        "checked_at": now.isoformat(),
        "freshness": {
            "daily_seconds": DAILY_FRESH_SECONDS,
            "weekly_seconds": WEEKLY_FRESH_SECONDS,
            "boundary": "fresh when age_seconds is less than or equal to fresh_for_seconds",
        },
        "jobs": jobs,
    }
