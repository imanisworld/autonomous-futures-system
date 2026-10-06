#!/usr/bin/env python3
"""Read-only, redacted status snapshot for the options prospective observer.

Lets an audit identity (``claude-audit`` / ``grok-audit``) answer "is the
observer running, from which tree, and is it writing evidence?" without a
shell, root, or general filesystem access.

Guarantees:

* Stdlib only; runs with the system ``python3`` (no release venv needed).
* Never reads ``.env`` or any environment of a unit. ``systemctl show`` is
  called with an explicit property allowlist; ``Environment``,
  ``EnvironmentFiles`` and similar are never requested.
* Files are reachable only through ``JOURNAL_ALLOWLIST`` names under one root
  (default ``/root/afs-shared/logs``); symlinks/paths escaping the root are
  refused. No caller-supplied path is ever opened.
* Journal tail is capped (``MAX_TAIL``) and projected onto ``TAIL_KEYS``;
  every string is passed through ``redact``.
* Writes nothing, restarts nothing, contacts no broker or provider.

Exit code: 0 when every inspected item is healthy, 1 when any item is
degraded/unknown, 2 on usage error. The JSON is printed either way.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA = "options-observer-status-v1"
DEFAULT_LOG_ROOT = Path("/root/afs-shared/logs")
LIVE_TREE = "/root/autonomous-futures-system"
RELEASES_ROOT = "/root/afs-releases/"
MAX_TAIL = 20
DEFAULT_TAIL = 5
MAX_STRING = 160

# Units an auditor may inspect. Nothing outside this set is queried.
UNIT_ALLOWLIST = (
    "options-122-prospective.service",
    "options-122-prospective.timer",
    "options-scanner.service",
    "afs-coverage-collector.service",
    "afs-coverage-collector.timer",
)

# Oneshot units are inactive between runs; they are judged by last result.
ONESHOT_UNITS = frozenset({"options-122-prospective.service", "afs-coverage-collector.service"})

# systemctl properties requested. Never add Environment* properties.
UNIT_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "Result",
    "UnitFileState",
    "ExecMainStatus",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
    "ActiveEnterTimestamp",
    "NRestarts",
    "WorkingDirectory",
    "ExecStart",
    "FragmentPath",
    "DropInPaths",
    "LastTriggerUSec",
    "NextElapseUSecRealtime",
)
_FORBIDDEN_PROPERTY = re.compile(r"environment|credential|loadcredential|setcredential", re.I)

# Journals an auditor may inspect: logical name -> file name under the root.
JOURNAL_ALLOWLIST = {
    "options_122_prospective": "options_122_prospective.jsonl",
}

# Optional heartbeat files (written by the trigger monitor once it exists).
HEARTBEAT_ALLOWLIST = {
    "options_prospective_trigger_monitor": "options_prospective_trigger_monitor_heartbeat.json",
}

TAIL_KEYS = (
    "record_type",
    "observed_at",
    "setup_id",
    "ticker",
    "collector_version",
    "policy_epoch",
    "source_outcome",
    "capture_gate_eligible",
    "option_evidence_usable",
    "reconciliation_status",
    "reason_code",
    "prearmed_at",
    "status",
    "lifecycle_state",
)
TIMESTAMP_KEYS = ("observed_at", "recorded_at", "detected_at", "ts", "timestamp")

_SECRET_PATTERNS = (
    re.compile(r"https?://\S+", re.I),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|apikey|webhook)[=:]\s*\S+"),
    # Long opaque tokens; lowercase hex (git SHAs, sha256 digests) is kept.
    re.compile(r"\b(?![0-9a-f]{32,}\b)[A-Za-z0-9_-]{32,}\b"),
)


def redact(value: Any) -> Any:
    """Redact URL/secret-looking substrings and cap string length."""
    if isinstance(value, str):
        out = value
        for pattern in _SECRET_PATTERNS:
            out = pattern.sub("<redacted>", out)
        return out if len(out) <= MAX_STRING else out[:MAX_STRING] + "...<truncated>"
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact(str(value))


# ── systemd ──────────────────────────────────────────────────────────────────

Runner = Callable[[list[str]], tuple[int, str]]


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}"
    return proc.returncode, proc.stdout


def _exec_start_path(exec_start: str) -> str | None:
    match = re.search(r"path=([^ ;]+)", exec_start or "")
    if match:
        return match.group(1)
    first = (exec_start or "").split()
    return first[0] if first else None


def classify_runtime_tree(working_directory: str, exec_path: str | None) -> dict[str, Any]:
    """Which code tree a unit runs from.

    PINNED_RELEASE: both cwd and interpreter are under one immutable release dir.
    LIVE_TREE: cwd or interpreter is the mutable ``/root/autonomous-futures-system``
      path (a symlink that follows futures-bot releases, not the observer's pin).
    MIXED / UNKNOWN otherwise.
    """
    paths = [p for p in (working_directory, exec_path) if p]

    def release_of(path: str) -> str | None:
        if path.startswith(RELEASES_ROOT):
            return path[len(RELEASES_ROOT):].split("/", 1)[0] or None
        return None

    releases = {release_of(p) for p in paths}
    if not paths:
        return {"classification": "UNKNOWN", "release": None}
    if any(p == LIVE_TREE or p.startswith(LIVE_TREE + "/") for p in paths):
        return {"classification": "LIVE_TREE", "release": None}
    if None not in releases and len(releases) == 1:
        return {"classification": "PINNED_RELEASE", "release": releases.pop()}
    if any(r is not None for r in releases):
        return {"classification": "MIXED", "release": None}
    return {"classification": "UNKNOWN", "release": None}


def unit_status(unit: str, run: Runner = _run) -> dict[str, Any]:
    if unit not in UNIT_ALLOWLIST:
        raise ValueError(f"unit not allowlisted: {unit}")
    assert not any(_FORBIDDEN_PROPERTY.search(p) for p in UNIT_PROPERTIES)
    code, out = run(["systemctl", "show", unit, "--no-pager", "-p", ",".join(UNIT_PROPERTIES)])
    if code != 0:
        return {"unit": unit, "health": "UNKNOWN", "error": "systemctl_show_failed"}
    props: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in UNIT_PROPERTIES:
            props[key] = value
    result: dict[str, Any] = {"unit": unit}
    for key in UNIT_PROPERTIES:
        if key in props:
            value = props[key]
            if key == "ExecStart":
                # Keep only the interpreter path and module; drop argv values.
                path = _exec_start_path(value)
                module = re.search(r"-m\s+([\w.]+)", value)
                result["ExecStartPath"] = path
                result["ExecStartModule"] = module.group(1) if module else None
                continue
            result[key] = redact(value)
    if unit.endswith(".service"):
        result["runtime_tree"] = classify_runtime_tree(
            props.get("WorkingDirectory", ""), result.get("ExecStartPath")
        )
    load_ok = props.get("LoadState") == "loaded"
    if unit.endswith(".timer"):
        healthy = load_ok and props.get("ActiveState") == "active"
    elif unit in ONESHOT_UNITS:
        healthy = load_ok and props.get("Result") in {"success", ""} and props.get("ActiveState") != "failed"
    else:
        healthy = load_ok and props.get("ActiveState") == "active"
    result["health"] = "OK" if healthy else "DEGRADED"
    return result


# ── files ────────────────────────────────────────────────────────────────────

def _safe_path(root: Path, name: str) -> Path | None:
    """Resolve an allowlisted file under ``root``; refuse escapes/symlink tricks."""
    root_resolved = root.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root_resolved:
        return None
    return candidate


def _last_timestamp(row: Mapping[str, Any]) -> str | None:
    for key in TIMESTAMP_KEYS:
        if row.get(key):
            return str(row[key])
    return None


def journal_status(
    root: Path, name: str, *, tail: int = DEFAULT_TAIL, now: datetime | None = None
) -> dict[str, Any]:
    if name not in JOURNAL_ALLOWLIST:
        raise ValueError(f"journal not allowlisted: {name}")
    tail = max(0, min(int(tail), MAX_TAIL))
    path = _safe_path(root, JOURNAL_ALLOWLIST[name])
    result: dict[str, Any] = {"journal": name, "file": JOURNAL_ALLOWLIST[name]}
    if path is None:
        return {**result, "health": "DEGRADED", "error": "path_outside_root"}
    if not path.is_file():
        return {**result, "exists": False, "health": "DEGRADED"}
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    lines = 0
    malformed = 0
    recent: list[dict[str, Any]] = []
    last_ts: str | None = None
    with path.open("rb") as handle:
        for raw in handle:
            digest.update(raw)
            lines += 1
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                malformed += 1
                continue
            if not isinstance(row, dict):
                malformed += 1
                continue
            counts[str(row.get("record_type") or row.get("status") or "UNKNOWN")] += 1
            last_ts = _last_timestamp(row) or last_ts
            if tail:
                recent.append({k: redact(row[k]) for k in TAIL_KEYS if k in row})
                if len(recent) > tail:
                    recent.pop(0)
    stat = path.stat()
    now = now or datetime.now(timezone.utc)
    age = None
    if last_ts:
        try:
            parsed = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                age = round((now - parsed).total_seconds(), 1)
        except ValueError:
            pass
    return {
        **result,
        "exists": True,
        "size_bytes": stat.st_size,
        "sha256": digest.hexdigest(),
        "lines": lines,
        "malformed_lines": malformed,
        "record_type_counts": dict(sorted(counts.items())),
        "last_event_at": redact(last_ts),
        "last_event_age_seconds": age,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "tail": recent,
        "health": "OK" if malformed == 0 else "DEGRADED",
    }


def heartbeat_status(root: Path, name: str, *, now: datetime | None = None) -> dict[str, Any]:
    if name not in HEARTBEAT_ALLOWLIST:
        raise ValueError(f"heartbeat not allowlisted: {name}")
    path = _safe_path(root, HEARTBEAT_ALLOWLIST[name])
    result: dict[str, Any] = {"heartbeat": name, "file": HEARTBEAT_ALLOWLIST[name]}
    if path is None:
        return {**result, "health": "DEGRADED", "error": "path_outside_root"}
    if not path.is_file():
        # The trigger monitor is being built in a separate workstream; absence
        # is reported, not treated as proof of liveness or death.
        return {**result, "exists": False, "health": "NOT_PRESENT"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**result, "exists": True, "health": "DEGRADED", "error": "unreadable"}
    if not isinstance(payload, dict):
        return {**result, "exists": True, "health": "DEGRADED", "error": "not_object"}
    beat = _last_timestamp(payload)
    now = now or datetime.now(timezone.utc)
    age = None
    if beat:
        try:
            parsed = datetime.fromisoformat(str(beat).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                age = round((now - parsed).total_seconds(), 1)
        except ValueError:
            pass
    return {
        **result,
        "exists": True,
        "last_beat_at": redact(beat),
        "age_seconds": age,
        "state": redact(payload.get("state") or payload.get("status")),
        "health": "OK" if age is not None else "DEGRADED",
    }


def snapshot(
    *,
    root: Path = DEFAULT_LOG_ROOT,
    units: Iterable[str] = UNIT_ALLOWLIST,
    tail: int = DEFAULT_TAIL,
    run: Runner = _run,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    unit_rows = [unit_status(u, run) for u in units]
    journals = [journal_status(root, n, tail=tail, now=now) for n in JOURNAL_ALLOWLIST]
    beats = [heartbeat_status(root, n, now=now) for n in HEARTBEAT_ALLOWLIST]
    live_tree_units = [
        u["unit"] for u in unit_rows
        if u.get("runtime_tree", {}).get("classification") == "LIVE_TREE"
    ]
    degraded = [
        row.get("unit") or row.get("journal") or row.get("heartbeat")
        for row in (*unit_rows, *journals, *beats)
        if row.get("health") not in {"OK", "NOT_PRESENT"}
    ]
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "read_only": True,
        "execution_authority": False,
        "units": unit_rows,
        "journals": journals,
        "heartbeats": beats,
        "runtime_integrity": {
            "units_running_from_live_tree": live_tree_units,
            "ok": not live_tree_units,
        },
        "degraded": degraded,
        "overall": "OK" if not degraded and not live_tree_units else "DEGRADED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tail", type=int, default=DEFAULT_TAIL, help=f"0..{MAX_TAIL} redacted rows")
    parser.add_argument(
        "--unit",
        action="append",
        choices=UNIT_ALLOWLIST,
        help="limit to allowlisted units (repeatable); default all",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.tail <= MAX_TAIL:
        parser.error(f"--tail must be between 0 and {MAX_TAIL}")
    # The log root is fixed; an env override exists only for tests and must
    # still be an absolute path.
    root = Path(os.environ.get("AFS_OBSERVER_STATUS_LOG_ROOT", str(DEFAULT_LOG_ROOT)))
    if not root.is_absolute():
        parser.error("log root must be absolute")
    report = snapshot(root=root, units=args.unit or UNIT_ALLOWLIST, tail=args.tail)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["overall"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
