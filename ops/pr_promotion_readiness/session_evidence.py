"""Verify a morning forward-proof session file is complete, timestamped,
and frozen -- read-only. Never modifies, reconstructs, or backfills it.

Freeze standard (what counts as frozen / durable evidence)
----------------------------------------------------------
Seeing a complete file is not proof: a file first seen at run time could
have been written seconds earlier. The session file counts as FROZEN only
when ALL of the following hold, checked against a *persisted* freeze
record (a previous ``post_session_workflow`` record in the append-only
promotion record carrying ``session_file``, ``session_sha256`` and its
``timestamp``):

1. ``freeze_record_exists``   a previous run persisted a sha256 for this
   exact session path. First sight (``frozen=None``) is NEVER sufficient;
   the first run only records the fingerprint and HOLDs.
2. ``fingerprint_unchanged``   the file's current sha256 equals the
   persisted one. Any change -> ``frozen=False``.
3. ``frozen_after_final_stage``   the freeze was recorded at or after the
   10:03 ET final stage of the session date (a fingerprint of an
   incomplete file proves nothing about the finished one).
4. ``frozen_contemporaneously``   the freeze was recorded within
   ``MAX_FREEZE_LATENCY`` of the 10:03 ET stage. A hash persisted more
   than a quarter hour later cannot anchor the file's content to the
   session it describes.
5. ``frozen_long_enough``   the freeze was not recorded in the future
   relative to the current check (age >= ``MIN_FROZEN_AGE``, which is
   zero: waiting does not make evidence more trustworthy, and a second
   run moments after the first is sufficient once rules 1-4 hold).
   ``MIN_FROZEN_AGE`` exists only as a clock-skew guard against a freeze
   record timestamped after "now".

The freeze record's age is measured from the EARLIEST persisted
observation of the current sha256 in the unbroken tail of records for
the path, so rerunning the workflow never refreshes (or shortens) the
age. Nothing here writes the freeze record; ``post_session`` appends it
as a side effect of every run, exactly as before. Every failed rule is a
``session_evidence_*`` reason and the gate HOLDs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
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
# Freeze policy knobs (see module docstring). Deliberately module constants,
# not CLI flags: a run cannot loosen them.
MIN_FROZEN_AGE = timedelta(minutes=0)
MAX_FREEZE_LATENCY = timedelta(minutes=15)

_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

SESSION_INCOMPLETE = "HOLD — SESSION EVIDENCE INCOMPLETE"
NOT_FROZEN = "session_evidence_not_frozen"
CHANGED = "session_evidence_changed"


@dataclass(frozen=True)
class SessionFreeze:
    """A persisted fingerprint of the session file: what it hashed to, and
    when that was recorded. Read from the append-only record, never made up."""

    sha256: str
    recorded_at: str  # ISO-8601 timestamp of the persisted record
    source: str = ""  # where it was read from (for the report)


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
    # None  = no persisted freeze record (first sight) -> NOT sufficient
    # False = fingerprint changed, or the freeze record fails a durability rule
    # True  = every freeze rule in the module docstring holds
    frozen: Optional[bool]
    reasons: tuple[str, ...]
    freeze_recorded_at: Optional[str] = None
    freeze_age_seconds: Optional[int] = None
    freeze_latency_seconds: Optional[int] = None  # freeze time minus 10:03 ET stage

    @property
    def complete(self) -> bool:
        # Belt and braces: even with no reasons, only a verified freeze counts.
        return not self.reasons and self.frozen is True


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


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fmt_minutes(delta: timedelta) -> str:
    return f"{int(delta.total_seconds() // 60)} min"


def verify_session_evidence(
    path: Path,
    *,
    now: Optional[datetime] = None,
    freeze: Optional[SessionFreeze] = None,
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

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    final_reached: Optional[bool] = None
    gate: Optional[datetime] = None
    if session_date is not None:
        gate = datetime.combine(datetime.strptime(session_date, "%Y-%m-%d").date(), FINAL_STAGE_ET, tzinfo=ET)
        final_reached = current >= gate
        if not final_reached:
            reasons.append(f"10:03 ET stage not reached yet (now {current.astimezone(ET).strftime('%H:%M')} ET)")

    # --- freeze standard (module docstring) ----------------------------------
    frozen: Optional[bool] = None
    freeze_recorded_at: Optional[str] = None
    age_s: Optional[int] = None
    latency_s: Optional[int] = None
    if freeze is None:
        reasons.append(
            f"{NOT_FROZEN}: first sight of sha256 {digest[:12]} — no persisted freeze record for this session file; "
            "this run records the fingerprint; a second run against the unchanged file, within "
            f"{_fmt_minutes(MAX_FREEZE_LATENCY)} of the 10:03 ET stage, is sufficient"
        )
    elif freeze.sha256 != digest:
        frozen = False
        reasons.append(f"{CHANGED}: sha256 differs from persisted freeze record ({freeze.sha256[:12]} -> {digest[:12]})")
    else:
        freeze_recorded_at = freeze.recorded_at
        recorded = _parse_iso(freeze.recorded_at)
        if recorded is None:
            frozen = False
            reasons.append(f"{NOT_FROZEN}: persisted freeze record has no parseable timestamp ({freeze.recorded_at!r})")
        else:
            age = current - recorded
            age_s = int(age.total_seconds())
            ok = True
            if gate is not None:
                latency = recorded - gate
                latency_s = int(latency.total_seconds())
                if latency < timedelta(0):
                    ok = False
                    reasons.append(
                        f"{NOT_FROZEN}: freeze recorded {recorded.isoformat(timespec='seconds')} is before the 10:03 ET stage of {session_date}"
                    )
                elif latency > MAX_FREEZE_LATENCY:
                    ok = False
                    reasons.append(
                        f"{NOT_FROZEN}: freeze recorded {recorded.isoformat(timespec='seconds')} is {_fmt_minutes(latency)} after the 10:03 ET stage "
                        f"(max {_fmt_minutes(MAX_FREEZE_LATENCY)}) — cannot anchor content to the session"
                    )
            else:
                ok = False  # no session date -> stage unknown -> latency unverifiable
                reasons.append(f"{NOT_FROZEN}: session date unknown, freeze latency unverifiable")
            if age < MIN_FROZEN_AGE:
                ok = False
                reasons.append(
                    f"{NOT_FROZEN}: freeze recorded_at is after the current check ({_fmt_minutes(age)} ago) — "
                    "clock skew or a future timestamp, not evidence"
                )
            frozen = ok

    return SessionEvidenceStatus(
        path=str(path), exists=True, session_date=session_date, sections_present=present, sections_missing=missing,
        sections_without_timestamp=without_ts, sha256=digest, size_bytes=len(raw), modified_at=modified_at,
        final_stage_reached=final_reached, frozen=frozen, reasons=tuple(reasons),
        freeze_recorded_at=freeze_recorded_at, freeze_age_seconds=age_s, freeze_latency_seconds=latency_s,
    )
