#!/usr/bin/env python3
"""Read-only weekday proof pass for forward collection lanes (futures HOLD, six-micro, options 122).

Runs on the VPS after the New York close. Writes a single JSON line to
``/root/afs-shared/logs/forward_lanes_daily_proof.jsonl`` and prints the same
document to stdout. Stdlib only; never prints secrets or full ``.env`` contents.

Exit 0 when all required checks pass, 1 when any required check fails, 2 on
usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SCHEMA = "forward-lanes-daily-proof-v1"
FORWARD_SESSION_START = date(2026, 10, 12)
SIX_MICRO_RETIRED_ON = date(2026, 10, 18)
TRACK_B_HEARTBEAT_NAME = "track_b_daily_heartbeat.jsonl"
TRACK_B_HEARTBEAT_SCHEMA = "track-b-daily-heartbeat-v1"
TRACK_B_LAST_BAR_MINIMUM_ET = time(15, 50)
ET = ZoneInfo("America/New_York")

DEFAULT_SHARED = Path("/root/afs-shared")
DEFAULT_CURRENT = Path("/root/autonomous-futures-system")
DEFAULT_LOG = DEFAULT_SHARED / "logs" / "forward_lanes_daily_proof.jsonl"

ENV_ALLOWLIST = frozenset({"EXPECTED_LIVE_COMMIT"})
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _run(cmd: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def read_env_pins(env_path: Path, keys: frozenset[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not env_path.is_file():
        return out
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in keys:
            out[key] = value.strip().strip('"').strip("'")
    return out


def live_release_sha(current_link: Path) -> str:
    target = current_link.resolve()
    manifest = target / "release_manifest.json"
    if manifest.is_file():
        repo = json.loads(manifest.read_text(encoding="utf-8")).get("repo") or {}
        commit = str(repo.get("commit", "")).strip().lower()
        if _FULL_SHA.fullmatch(commit):
            return commit
    return ""



def last_completed_track_b_session(now: datetime) -> date:
    """Latest completed weekday RTH; holiday proof remains fail-closed."""
    local = now.astimezone(ET)
    day = local.date()
    if local.time().replace(tzinfo=None) < time(16, 5):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def track_b_heartbeat_proof(shared: Path, now: datetime) -> dict[str, Any]:
    """Verify sealed Track B work rather than treating silence as 'no setup'."""
    day = last_completed_track_b_session(now)
    base: dict[str, Any] = {
        "session_date_et": day.isoformat(),
        "status": "FAIL",
        "reason": "MISSING_HEARTBEAT",
        "passed": False,
    }
    if day < FORWARD_SESSION_START:
        return {**base, "status": "NOT_STARTED", "reason": None, "passed": True}
    path = shared / "logs" / TRACK_B_HEARTBEAT_NAME
    if not path.is_file():
        return base
    try:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("invalid journal entry")
            if row.get("session_date_et") == day.isoformat():
                rows.append(row)
        if len(rows) != 1:
            return {**base, "reason": "DUPLICATE_HEARTBEAT" if rows else "MISSING_HEARTBEAT"}
        row = rows[0]
        if row.get("schema") != TRACK_B_HEARTBEAT_SCHEMA:
            return {**base, "reason": "INVALID_HEARTBEAT_SCHEMA"}
        bars = row.get("bars_evaluated")
        checked = row.get("setups_checked")
        if type(bars) is not int or type(checked) is not int or bars <= 0 or checked <= 0:
            return {**base, "reason": "NO_BARS_OR_SETUP_EVALUATIONS"}
        bar_string = row.get("last_bar_time")
        if not isinstance(bar_string, str):
            return {**base, "reason": "MISSING_LAST_BAR_TIME"}
        last_bar = datetime.fromisoformat(bar_string)
        if last_bar.tzinfo is None:
            return {**base, "reason": "NAIVE_LAST_BAR_TIME"}
        last_local = last_bar.astimezone(ET)
        if last_local.date() != day or not (
            TRACK_B_LAST_BAR_MINIMUM_ET <= last_local.time().replace(tzinfo=None)
            < time(16, 0)
        ):
            return {**base, "reason": "STALE_LAST_BAR"}
        if (
            type(row.get("signal")) is not bool
            or type(row.get("entry_recorded")) is not bool
            or row.get("source") != "paper_5m_collector"
            or row.get("sealed_by") != "demo_eod_fallback"
            or row.get("complete") is not True
            or row.get("bars_fresh") is not True
            or row.get("fallback_action") == "FAIL_CLOSED"
        ):
            return {**base, "reason": "INCOMPLETE_OR_INVALID_HEARTBEAT"}
        signal = row["signal"]
        entry = row["entry_recorded"]
        reason = row.get("no_signal_reason")
        if (not signal and (not isinstance(reason, str) or not reason.strip())) or (entry and not signal):
            return {**base, "reason": "INCONSISTENT_SIGNAL_CLASSIFICATION"}
        status = "ENTRY_RECORDED" if entry else ("SIGNAL_NO_ENTRY" if signal else "OK — no signal")
        return {
            "session_date_et": day.isoformat(),
            "status": status,
            "reason": reason if not signal else None,
            "bars_evaluated": bars,
            "last_bar_time": bar_string,
            "setups_checked": checked,
            "signal": signal,
            "entry_recorded": entry,
            "passed": True,
        }
    except (OSError, ValueError, TypeError):
        return {**base, "reason": "UNREADABLE_HEARTBEAT"}


def check_futures_track_b(
    *,
    current: Path,
    shared: Path,
    health_url: str,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]],
    now: datetime | None = None,
) -> dict[str, Any]:
    pins = read_env_pins(shared / ".env", ENV_ALLOWLIST)
    expected = pins.get("EXPECTED_LIVE_COMMIT", "").lower()
    live_sha = live_release_sha(current)
    symlink_ok = current.is_symlink() and current.resolve().is_dir()
    active = run(["systemctl", "is-active", "futures-bot"]).stdout.strip() == "active"
    health: dict[str, Any] = {"ok": False}
    try:
        with urllib.request.urlopen(health_url, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        health = {
            "ok": bool(body.get("ok")),
            "live_trading_enabled": body.get("live_trading_enabled"),
            "broker": body.get("broker"),
        }
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        health["error"] = type(exc).__name__
    lock_path = shared / "deploy.lock"
    deploy_lock = lock_path.is_dir() or lock_path.is_file()
    sha_match = bool(expected and live_sha and expected == live_sha)
    heartbeat = track_b_heartbeat_proof(shared, now or datetime.now(timezone.utc))
    passed = bool(symlink_ok and active and health.get("ok") and not deploy_lock and sha_match and heartbeat["passed"])
    return {
        "lane": "track_b_wide_stop",
        "live_sha_prefix": live_sha[:12] if live_sha else None,
        "expected_sha_prefix": expected[:12] if expected else None,
        "symlink_ok": symlink_ok,
        "futures_bot_active": active,
        "health": health,
        "deploy_lock": deploy_lock,
        "heartbeat": heartbeat,
        "passed": passed,
    }


def _systemd_on_calendar(unit: str, run: Callable[[list[str]], subprocess.CompletedProcess[str]]) -> str:
    return run(["systemctl", "is-enabled", unit]).stdout.strip()


def _oneshot_result(unit: str, run: Callable[[list[str]], subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    proc = run(
        [
            "systemctl",
            "show",
            unit,
            "-p",
            "Result,ExecMainStatus,StateChangeTimestamp,ActiveState",
            "--value",
        ]
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    result = lines[0] if len(lines) > 0 else "unknown"
    status = lines[1] if len(lines) > 1 else ""
    changed = lines[2] if len(lines) > 2 else ""
    state = lines[3] if len(lines) > 3 else ""
    return {
        "result": result,
        "exec_main_status": status,
        "state_change": changed,
        "active_state": state,
    }


def check_six_micro(
    *,
    shared: Path,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]],
    today_et: date,
) -> dict[str, Any]:
    timer = _systemd_on_calendar("six-micro-daily-tsmom.timer", run)
    service = _oneshot_result("six-micro-daily-tsmom.service", run)
    journal_dir = shared / "logs" / "six-micro-daily-tsmom"
    state_file = journal_dir / "state.json"
    journal_ok = state_file.is_file()
    last_write = (
        datetime.fromtimestamp(state_file.stat().st_mtime, tz=timezone.utc).isoformat()
        if journal_ok
        else None
    )
    timer_ok = timer == "enabled"
    # After the first eligible close, expect a successful oneshot since that session.
    need_run = today_et >= FORWARD_SESSION_START
    service_ok = (not need_run) or (
        service.get("result") == "success" and service.get("exec_main_status") in {"0", ""}
    )
    passed = timer_ok and journal_ok and service_ok
    return {
        "lane": "six_micro_daily_tsmom",
        "timer_enabled": timer,
        "last_service": service,
        "journal_state_present": journal_ok,
        "journal_state_mtime_utc": last_write,
        "passed": passed,
    }


def _journal_armed_binding_stats(path: Path, since: date) -> dict[str, int]:
    armed = 0
    bound = 0
    since_lines = 0
    since_prefix = since.isoformat()
    if not path.is_file():
        return {"armed": 0, "armed_with_canonical_binding": 0, "lines_on_or_after_start": 0}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ts = str(row.get("recorded_at") or row.get("observed_at") or "")
        if ts[:10] >= since_prefix:
            since_lines += 1
        if row.get("record_type") != "ARMED":
            continue
        if ts[:10] >= since_prefix:
            armed += 1
            if row.get("canonical_binding"):
                bound += 1
    return {
        "armed": armed,
        "armed_with_canonical_binding": bound,
        "lines_on_or_after_start": since_lines,
    }


def check_options_122(
    *,
    shared: Path,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]],
    today_et: date,
) -> dict[str, Any]:
    timer = run(["systemctl", "is-active", "options-122-prospective.timer"]).stdout.strip()
    service = _oneshot_result("options-122-prospective.service", run)
    journal = shared / "logs" / "options_122_prospective.jsonl"
    stats = _journal_armed_binding_stats(journal, FORWARD_SESSION_START)
    timer_ok = timer == "active"
    on_rth = today_et.weekday() < 5
    need_binding = today_et > FORWARD_SESSION_START or (
        today_et == FORWARD_SESSION_START and on_rth
    )
    # First eligible Monday: allow zero stamped rows until EOD proof window; warn only.
    binding_ok = (not need_binding) or stats["armed"] == 0 or stats["armed_with_canonical_binding"] > 0
    service_ok = service.get("result") in {"success", "done", ""} or not on_rth
    passed = timer_ok and service_ok and binding_ok
    return {
        "lane": "options_122_iex_e1",
        "timer_active": timer,
        "last_service": service,
        "journal_since_forward_start": stats,
        "require_canonical_binding": need_binding,
        "passed": passed,
    }


def run_options_observer_snapshot(
    current: Path,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    tool = current / "ops" / "options_observer_status.py"
    if not tool.is_file():
        return {"present": False, "overall": "SKIPPED", "passed": True}
    proc = run(
        [
            sys.executable,
            str(tool),
            "--tail",
            "0",
            "--unit",
            "options-122-prospective.service",
            "--unit",
            "options-122-prospective.timer",
            "--unit",
            "options-scanner.service",
        ]
    )
    if proc.returncode not in {0, 1} or not proc.stdout.strip():
        return {
            "present": True,
            "overall": "ERROR",
            "passed": False,
            "stderr_prefix": proc.stderr[:200],
        }
    report = json.loads(proc.stdout)
    overall = report.get("overall", "UNKNOWN")
    degraded = list(report.get("degraded") or [])
    forward_relevant = [
        item
        for item in degraded
        if "122" in item or "scanner" in item.lower()
    ]
    passed = overall == "OK" or not forward_relevant
    return {
        "present": True,
        "overall": overall,
        "degraded": degraded,
        "forward_relevant_degraded": forward_relevant,
        "passed": passed,
    }


def build_report(
    *,
    shared: Path = DEFAULT_SHARED,
    current: Path = DEFAULT_CURRENT,
    health_url: str = "http://127.0.0.1:8000/health",
    now: datetime | None = None,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today_et = now.astimezone(ET).date()
    lanes = [
        check_futures_track_b(current=current, shared=shared, health_url=health_url, run=run, now=now),
        check_six_micro(shared=shared, run=run, today_et=today_et),
        check_options_122(shared=shared, run=run, today_et=today_et),
    ]
    observer = run_options_observer_snapshot(current, run)
    required = [ln["passed"] for ln in lanes] + [observer["passed"]]
    overall = "PASS" if all(required) else "FAIL"
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "session_date_et": today_et.isoformat(),
        "forward_session_start_et": FORWARD_SESSION_START.isoformat(),
        "read_only": True,
        "lanes": lanes,
        "options_observer": observer,
        "overall": overall,
    }


def append_jsonl(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(doc, sort_keys=True))
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help="append-only proof log (default: shared logs path on VPS)",
    )
    parser.add_argument("--no-log", action="store_true", help="stdout only")
    parser.add_argument("--shared", type=Path, default=DEFAULT_SHARED)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    args = parser.parse_args(argv)
    if not args.shared.is_absolute() or not args.current.is_absolute():
        parser.error("--shared and --current must be absolute paths")
    report = build_report(shared=args.shared, current=args.current)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if not args.no_log:
        append_jsonl(args.log, report)
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
