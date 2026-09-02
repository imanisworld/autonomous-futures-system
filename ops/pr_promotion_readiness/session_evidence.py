"""Verify a morning forward-proof session file is complete, timestamped,
and frozen -- read-only. Never modifies, reconstructs, or backfills it."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("09:26", r"^##\s*09:26\s*ET"),
    ("09:46", r"^##\s*09:46\s*ET"),
    ("10:03", r"^##\s*10:03\s*ET"),
)
FINAL_STAGE_ET = time(10, 3)
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

SESSION_INCOMPLETE = "HOLD — SESSION EVIDENCE INCOMPLETE"


@dataclass(frozen=True)
class SessionEvidenceStatus:
    path: str
    exists: bool
    session_date: Optional[str]
    sections_present: tuple[str, ...]
    sections_missing: tuple[str, ...]
    sections_without_timestamp: tuple[str, ...]
    sha256: Optional[str]
    size_bytes: Optional[int]
    modified_at: Optional[str]
    final_stage_reached: Optional[bool]
    frozen: Optional[bool]  # None on first sight; False if fingerprint changed
    reasons: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.reasons


def _split_sections(text: str) -> dict[str, str]:
    """Map section key -> body text (from its header to the next ## header)."""
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        for key, pattern in REQUIRED_SECTIONS:
            if re.match(pattern, line):
                starts.append((idx, key))
    bodies: dict[str, str] = {}
    for n, (idx, key) in enumerate(starts):
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        bodies.setdefault(key, "\n".join(lines[idx:end]))
    return bodies


def verify_session_evidence(
    path: Path,
    *,
    now: Optional[datetime] = None,
    previous_sha256: Optional[str] = None,
) -> SessionEvidenceStatus:
    path = Path(path)
    reasons: list[str] = []
    if not path.is_file():
        return SessionEvidenceStatus(
            path=str(path), exists=False, session_date=None, sections_present=(), sections_missing=tuple(k for k, _ in REQUIRED_SECTIONS),
            sections_without_timestamp=(), sha256=None, size_bytes=None, modified_at=None, final_stage_reached=None, frozen=None,
            reasons=(f"session file missing: {path}",),
        )
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    stat = path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")

    date_match = _DATE_RE.search(path.name) or _DATE_RE.search(text)
    session_date = date_match.group(1) if date_match else None
    if session_date is None:
        reasons.append("session date not found in file name or content")

    bodies = _split_sections(text)
    present = tuple(k for k, _ in REQUIRED_SECTIONS if k in bodies)
    missing = tuple(k for k, _ in REQUIRED_SECTIONS if k not in bodies)
    for key in missing:
        reasons.append(f"{key} ET section missing")
    without_ts = tuple(k for k in present if not _TIMESTAMP_RE.search(bodies[k]))
    for key in without_ts:
        reasons.append(f"{key} ET section has no retrieval timestamp")

    final_reached: Optional[bool] = None
    if session_date is not None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        gate = datetime.combine(datetime.strptime(session_date, "%Y-%m-%d").date(), FINAL_STAGE_ET, tzinfo=ET)
        final_reached = current >= gate
        if not final_reached:
            reasons.append(f"10:03 ET stage not reached yet (now {current.astimezone(ET).strftime('%H:%M')} ET)")

    frozen: Optional[bool] = None
    if previous_sha256 is not None:
        frozen = previous_sha256 == digest
        if not frozen:
            reasons.append(f"session evidence changed since last recorded fingerprint ({previous_sha256[:12]} -> {digest[:12]})")

    return SessionEvidenceStatus(
        path=str(path), exists=True, session_date=session_date, sections_present=present, sections_missing=missing,
        sections_without_timestamp=without_ts, sha256=digest, size_bytes=len(raw), modified_at=modified_at,
        final_stage_reached=final_reached, frozen=frozen, reasons=tuple(reasons),
    )
