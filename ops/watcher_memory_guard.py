"""Memory guard primitives shared by the AFS watcher and runtime gates.

The watcher owns sampling and state publication.  Runtime code only consumes
the published CRITICAL state; it never samples memory or attempts recovery on
the webhook hot path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable


DEFAULT_WATCHER_STATE = Path("/tmp/afs_watcher/state.json")
WARNING_RESERVE_FRACTION = 0.20
CRITICAL_RESERVE_FRACTION = 0.10
OOM_HEADROOM_MULTIPLIER = 2.0
WARNING_GROWTH_HORIZON_MINUTES = 30.0
CRITICAL_GROWTH_HORIZON_MINUTES = 10.0


@dataclass(frozen=True)
class MemoryReading:
    observed_utc: str
    pid: int
    service_rss_bytes: int
    mem_total_bytes: int
    mem_available_bytes: int
    cgroup_limit_bytes: int | None = None
    cgroup_current_bytes: int | None = None

    @property
    def effective_capacity_bytes(self) -> int:
        limits = [self.mem_total_bytes]
        if self.cgroup_limit_bytes and self.cgroup_limit_bytes > 0:
            limits.append(self.cgroup_limit_bytes)
        return min(limits)

    @property
    def effective_headroom_bytes(self) -> int:
        headroom = self.mem_available_bytes
        if self.cgroup_limit_bytes and self.cgroup_current_bytes is not None:
            headroom = min(
                headroom,
                max(0, self.cgroup_limit_bytes - self.cgroup_current_bytes),
            )
        return max(0, headroom)


@dataclass(frozen=True)
class MemoryGuardStatus:
    level: str
    reason: str
    reading: MemoryReading
    warning_headroom_bytes: int
    critical_headroom_bytes: int
    warning_service_rss_bytes: int
    critical_service_rss_bytes: int
    rss_growth_bytes_per_minute: float | None = None
    projected_minutes_to_critical: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["effective_capacity_bytes"] = self.reading.effective_capacity_bytes
        data["effective_headroom_bytes"] = self.reading.effective_headroom_bytes
        return data


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _growth_rate(readings: Iterable[MemoryReading]) -> float | None:
    ordered = sorted(readings, key=lambda row: _parse_utc(row.observed_utc))
    if len(ordered) < 2:
        return None
    first, last = ordered[0], ordered[-1]
    minutes = (_parse_utc(last.observed_utc) - _parse_utc(first.observed_utc)).total_seconds() / 60.0
    if minutes <= 0:
        return None
    return (last.service_rss_bytes - first.service_rss_bytes) / minutes


def evaluate_memory(
    reading: MemoryReading,
    *,
    recent_readings: Iterable[MemoryReading] = (),
    observed_oom_headroom_bytes: int | None = None,
) -> MemoryGuardStatus:
    """Derive capacity-aware WARNING/CRITICAL limits.

    The service RSS thresholds are budgets, not fixed process limits.  They
    shrink when other processes/caches consume more of the effective host or
    cgroup capacity.  A recorded OOM headroom can raise the reserve, but the
    process RSS at which a previous OOM happened is never used as a limit.
    """
    capacity = reading.effective_capacity_bytes
    if capacity <= 0:
        raise ValueError("effective memory capacity must be positive")

    critical_reserve = int(capacity * CRITICAL_RESERVE_FRACTION)
    if observed_oom_headroom_bytes is not None and observed_oom_headroom_bytes >= 0:
        critical_reserve = max(
            critical_reserve,
            int(observed_oom_headroom_bytes * OOM_HEADROOM_MULTIPLIER),
        )
    critical_reserve = min(critical_reserve, capacity)
    warning_reserve = min(
        capacity,
        max(int(capacity * WARNING_RESERVE_FRACTION), critical_reserve * 2),
    )

    comparable = [
        row
        for row in [*recent_readings, reading]
        if row.effective_capacity_bytes == capacity
    ]
    non_service_used = max(
        max(
            0,
            capacity - row.effective_headroom_bytes - row.service_rss_bytes,
        )
        for row in comparable
    )
    warning_rss = max(0, capacity - non_service_used - warning_reserve)
    critical_rss = max(0, capacity - non_service_used - critical_reserve)

    history = comparable
    growth = _growth_rate(history)
    projected = None
    if growth is not None and growth > 0:
        rss_budget = max(0, critical_rss - reading.service_rss_bytes)
        headroom_budget = max(0, reading.effective_headroom_bytes - critical_reserve)
        projected = min(rss_budget, headroom_budget) / growth

    critical_reasons: list[str] = []
    warning_reasons: list[str] = []
    if reading.effective_headroom_bytes <= critical_reserve:
        critical_reasons.append("available memory is inside the critical reserve")
    elif reading.effective_headroom_bytes <= warning_reserve:
        warning_reasons.append("available memory is inside the warning reserve")
    if reading.service_rss_bytes >= critical_rss:
        critical_reasons.append("service RSS exhausted its dynamic critical budget")
    elif reading.service_rss_bytes >= warning_rss:
        warning_reasons.append("service RSS exhausted its dynamic warning budget")
    if projected is not None and projected <= CRITICAL_GROWTH_HORIZON_MINUTES:
        critical_reasons.append("current RSS growth projects critical headroom within 10 minutes")
    elif projected is not None and projected <= WARNING_GROWTH_HORIZON_MINUTES:
        warning_reasons.append("current RSS growth projects critical headroom within 30 minutes")

    if critical_reasons:
        level, reason = "CRITICAL", "; ".join(critical_reasons)
    elif warning_reasons:
        level, reason = "WARNING", "; ".join(warning_reasons)
    else:
        level, reason = "HEALTHY", "memory headroom and service RSS are within derived budgets"

    return MemoryGuardStatus(
        level=level,
        reason=reason,
        reading=reading,
        warning_headroom_bytes=warning_reserve,
        critical_headroom_bytes=critical_reserve,
        warning_service_rss_bytes=warning_rss,
        critical_service_rss_bytes=critical_rss,
        rss_growth_bytes_per_minute=growth,
        projected_minutes_to_critical=projected,
    )


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def sample_process_memory(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    observed_utc: str | None = None,
) -> MemoryReading:
    meminfo: dict[str, int] = {}
    for line in (proc_root / "meminfo").read_text(encoding="utf-8").splitlines():
        key, _, raw = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            meminfo[key] = int(raw.strip().split()[0]) * 1024
    if set(meminfo) != {"MemTotal", "MemAvailable"}:
        raise RuntimeError("MemTotal/MemAvailable missing from proc meminfo")

    rss = None
    for line in (proc_root / str(pid) / "status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1]) * 1024
            break
    if rss is None:
        raise RuntimeError(f"VmRSS missing for pid {pid}")

    return MemoryReading(
        observed_utc=observed_utc or datetime.now(timezone.utc).isoformat(),
        pid=pid,
        service_rss_bytes=rss,
        mem_total_bytes=meminfo["MemTotal"],
        mem_available_bytes=meminfo["MemAvailable"],
        cgroup_limit_bytes=_read_int(cgroup_root / "memory.max"),
        cgroup_current_bytes=_read_int(cgroup_root / "memory.current"),
    )


DEFAULT_STALE_AFTER_MINUTES = 30.0  # six 5-minute watcher ticks
STALE_MINUTES_ENV = "AFS_WATCHER_STALE_MINUTES"
CODE_MEMORY_CRITICAL = "MEMORY_CRITICAL"
CODE_STATE_MALFORMED = "WATCHER_STATE_MALFORMED"
CODE_STATE_STALE = "WATCHER_STATE_STALE"
CODE_STATE_MISSING = "WATCHER_STATE_MISSING"


def _latest_state_timestamp(state: dict[str, Any]) -> datetime | None:
    candidates: list[datetime] = []
    for raw in (
        state.get("last_tick_utc"),
        ((state.get("memory_guard") or {}).get("reading") or {}).get("observed_utc"),
    ):
        if isinstance(raw, str) and raw:
            try:
                candidates.append(_parse_utc(raw))
            except ValueError:
                continue
    return max(candidates) if candidates else None


def _stale_after_minutes(override: float | None) -> float:
    if override is not None:
        return float(override)
    raw = os.getenv(STALE_MINUTES_ENV)
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_STALE_AFTER_MINUTES


def read_critical_memory_block(
    state_path: str | os.PathLike[str] | None = None,
    *,
    now: datetime | None = None,
    stale_after_minutes: float | None = None,
) -> dict[str, Any] | None:
    """Return the block that must stop new paper entries / deploys, or None.

    Fail-closed rules (every returned block carries a ``code``):
      * ``MEMORY_CRITICAL``        — the watcher published CRITICAL (or a
        ``memory_critical`` BLOCKED entry).  Stays authoritative until the
        watcher writes a non-critical state, even if that state is stale.
      * ``WATCHER_STATE_MISSING``   — no state file at all.  The watcher keeps
        its state on tmpfs, so a reboot removes it; until the watcher is
        re-armed and publishes a fresh tick, memory is unprotected and new
        paper entries stay blocked.  Service startup is unaffected: this gate
        is only consulted on the paper-entry path.
      * ``WATCHER_STATE_MALFORMED`` — the state file exists but is unreadable,
        not JSON, not an object, or carries no watcher timestamp.
      * ``WATCHER_STATE_STALE``    — the newest watcher timestamp is older than
        ``stale_after_minutes`` (default 30, env ``AFS_WATCHER_STALE_MINUTES``)
        or more than five minutes in the future: the watcher is dead or the
        clock is wrong, so memory is unprotected.
    Only a present, parseable, fresh, non-critical state returns None.
    """
    path = Path(state_path or os.getenv("AFS_WATCHER_STATE_FILE", str(DEFAULT_WATCHER_STATE)))
    if not path.exists():
        return {
            "level": "MISSING",
            "code": CODE_STATE_MISSING,
            "reason": (
                f"afs-watcher state {path} does not exist; no fresh watcher tick "
                "(watcher not armed since reboot?), failing closed"
            ),
        }
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        return {
            "level": "UNKNOWN",
            "code": CODE_STATE_MALFORMED,
            "reason": f"afs-watcher state {path} unreadable ({type(exc).__name__}); failing closed",
        }
    if not isinstance(state, dict):
        return {
            "level": "UNKNOWN",
            "code": CODE_STATE_MALFORMED,
            "reason": f"afs-watcher state {path} is not a JSON object; failing closed",
        }
    observed = _latest_state_timestamp(state)
    if observed is None:
        return {
            "level": "UNKNOWN",
            "code": CODE_STATE_MALFORMED,
            "reason": f"afs-watcher state {path} carries no watcher timestamp; failing closed",
        }
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_minutes = (current - observed).total_seconds() / 60.0
    limit = _stale_after_minutes(stale_after_minutes)
    stale = age_minutes > limit or age_minutes < -5.0

    guard = state.get("memory_guard")
    blocked = state.get("blocked")
    critical_block: dict[str, Any] | None = None
    if isinstance(guard, dict) and guard.get("level") == "CRITICAL":
        critical_block = dict(guard)
    elif isinstance(blocked, dict) and "memory_critical" in blocked:
        detail = blocked["memory_critical"]
        critical_block = dict(detail) if isinstance(detail, dict) else {"level": "CRITICAL"}
    if critical_block is not None:
        critical_block.setdefault("level", "CRITICAL")
        critical_block["code"] = CODE_MEMORY_CRITICAL
        critical_block["state_observed_utc"] = observed.isoformat()
        critical_block["state_stale"] = stale
        return critical_block
    if stale:
        return {
            "level": "STALE",
            "code": CODE_STATE_STALE,
            "reason": (
                f"afs-watcher state last updated {age_minutes:.0f} min ago "
                f"(limit {limit:.0f} min); watcher appears dead, failing closed"
                if age_minutes >= 0 else
                f"afs-watcher state timestamp is {-age_minutes:.0f} min in the future; failing closed"
            ),
            "state_observed_utc": observed.isoformat(),
        }
    return None
