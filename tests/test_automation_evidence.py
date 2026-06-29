from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ops.automation_evidence import automation_evidence_status


def _write(path, generated_at):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_at": generated_at.isoformat()}))


def test_automation_evidence_fresh_stale_and_missing(tmp_path):
    now = datetime(2026, 6, 28, 12, tzinfo=timezone.utc)
    logs = tmp_path / "logs"
    backup = tmp_path / "backups"
    _write(logs / "health_digest_latest.json", now - timedelta(hours=36))
    _write(logs / "weekly_review_2026-W25.json", now - timedelta(days=9))

    result = automation_evidence_status(logs, backup_dir=backup, now=now)
    jobs = {job["job"]: job for job in result["jobs"]}

    assert jobs["health_digest"]["status"] == "fresh"  # inclusive boundary
    assert jobs["weekly_review"]["status"] == "stale"
    assert jobs["backup_proof_data"]["status"] == "missing"
    assert jobs["backup_proof_data"]["evidence_path"] == str(
        backup / "backup_proof_data_latest.json"
    )


def test_automation_evidence_corrupt_receipt_is_missing(tmp_path):
    receipt = tmp_path / "logs" / "health_digest_latest.json"
    receipt.parent.mkdir()
    receipt.write_text("not json")

    result = automation_evidence_status(
        receipt.parent,
        backup_dir=tmp_path / "backups",
        now=datetime(2026, 6, 28, tzinfo=timezone.utc),
    )
    health = next(job for job in result["jobs"] if job["job"] == "health_digest")
    assert health["status"] == "missing"
