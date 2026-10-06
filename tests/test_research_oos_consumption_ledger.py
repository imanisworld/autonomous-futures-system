"""Repo-governed OOS consumption ledger durability (U2).

Mirrors the trial-ledger register-before-count pattern: the append-only
receipt file must exist in the repo, parse as JSONL, and every receipt's
trial_id must already be registered in the research trial ledger. Tracked
evidence-tree OOS receipt artifacts must match a ledger line so a fresh
checkout cannot silently ignore prior OOS consumption.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OOS_LEDGER_REL = "docs/research-oos-consumption-ledger.jsonl"
TRIAL_LEDGER_REL = "docs/research-trial-ledger.jsonl"
EVIDENCE_PREFIX = "docs/research-evidence/"
RECEIPT_NAME = "oos_consumption_receipt.json"
TRIAL_ID_RE = re.compile(r"^T-\d{4}-\d{2}-\d{2}-.+-\d{2}$")


def _trial_first_ids() -> set[str]:
    path = ROOT / TRIAL_LEDGER_REL
    assert path.is_file(), f"missing {TRIAL_LEDGER_REL}"
    ids: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        assert isinstance(row, dict), f"{TRIAL_LEDGER_REL}:{lineno}: expected object"
        trial_id = row.get("trial_id")
        assert isinstance(trial_id, str) and trial_id, f"{TRIAL_LEDGER_REL}:{lineno}"
        ids.add(trial_id)
    return ids


def _oos_rows() -> list[dict]:
    path = ROOT / OOS_LEDGER_REL
    assert path.is_file(), (
        f"missing {OOS_LEDGER_REL}: repo-governed OOS ledger is required for "
        "durable once-only consumption across fresh checkouts"
    )
    rows: list[dict] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        row = json.loads(raw)
        assert isinstance(row, dict), f"{OOS_LEDGER_REL}:{lineno}: expected object"
        rows.append(row)
    return rows


def test_oos_consumption_ledger_exists_and_is_valid_jsonl() -> None:
    rows = _oos_rows()
    # Empty ledger is valid (no OOS looks yet). Non-empty rows must be well-formed.
    seen_trials: set[str] = set()
    for index, row in enumerate(rows, 1):
        trial_id = row.get("trial_id")
        assert isinstance(trial_id, str) and TRIAL_ID_RE.fullmatch(trial_id), (
            f"{OOS_LEDGER_REL} row {index}: invalid trial_id {trial_id!r}"
        )
        assert trial_id not in seen_trials, (
            f"{OOS_LEDGER_REL}: duplicate trial_id {trial_id!r} (once-only violated)"
        )
        seen_trials.add(trial_id)
        identity = row.get("receipt_identity")
        assert identity == f"trial:{trial_id}", (
            f"{OOS_LEDGER_REL} row {index}: receipt_identity must be trial-keyed"
        )
        assert row.get("family_wide_lock") is False
        assert row.get("partition") == "untouched_oos"
        window = row.get("untouched_oos_window")
        assert isinstance(window, dict) and "start" in window and "end" in window
        assert isinstance(row.get("oos_fingerprint"), str) and row["oos_fingerprint"]


def test_oos_receipts_register_before_count_against_trial_ledger() -> None:
    """Every durable OOS receipt must reference a trial already in the trial ledger."""
    trial_ids = _trial_first_ids()
    for index, row in enumerate(_oos_rows(), 1):
        trial_id = row["trial_id"]
        assert trial_id in trial_ids, (
            f"{OOS_LEDGER_REL} row {index}: trial_id {trial_id!r} is not registered "
            f"in {TRIAL_LEDGER_REL} (register-before-count)"
        )


def test_tracked_evidence_oos_receipts_match_ledger() -> None:
    """Evidence-tree receipt artifacts cannot claim OOS without a ledger line."""
    import subprocess

    tracked = subprocess.check_output(
        ["git", "ls-files", "docs/research-evidence"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    ledger_by_trial = {row["trial_id"]: row for row in _oos_rows()}
    for rel in tracked:
        path = PurePosixPath(rel)
        if path.name != RECEIPT_NAME:
            continue
        assert len(path.parts) >= 4 and "/".join(path.parts[:2]) == "docs/research-evidence"
        trial_id = path.parts[2]
        assert trial_id in ledger_by_trial, (
            f"{rel}: tracked OOS receipt has no matching line in {OOS_LEDGER_REL}"
        )
        payload = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        assert payload.get("trial_id") == trial_id
        assert payload.get("receipt_identity") == f"trial:{trial_id}"
