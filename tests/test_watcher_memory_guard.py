import json
from datetime import datetime, timedelta, timezone

from ops.watcher_memory_guard import (
    MemoryReading,
    evaluate_memory,
    read_critical_memory_block,
    CODE_MEMORY_CRITICAL,
    CODE_STATE_MALFORMED,
    CODE_STATE_STALE,
    CODE_STATE_MISSING,
    CODE_MEMORY_WARNING,
)


GIB = 1024**3
MIB = 1024**2


def _reading(*, rss_mib: int, available_mib: int, minute: int = 0) -> MemoryReading:
    return MemoryReading(
        observed_utc=f"2026-09-02T19:{minute:02d}:00+00:00",
        pid=123,
        service_rss_bytes=rss_mib * MIB,
        mem_total_bytes=2 * GIB,
        mem_available_bytes=available_mib * MIB,
    )


def test_thresholds_follow_capacity_and_co_tenant_usage():
    quiet = evaluate_memory(_reading(rss_mib=500, available_mib=900))
    crowded = evaluate_memory(_reading(rss_mib=500, available_mib=500))

    assert quiet.warning_headroom_bytes == int(2 * GIB * 0.20)
    assert quiet.critical_headroom_bytes == int(2 * GIB * 0.10)
    assert crowded.warning_service_rss_bytes < quiet.warning_service_rss_bytes
    assert crowded.critical_service_rss_bytes < quiet.critical_service_rss_bytes
    assert quiet.critical_service_rss_bytes != 805 * MIB


def test_observed_oom_headroom_raises_reserve_without_using_oom_rss():
    status = evaluate_memory(
        _reading(rss_mib=500, available_mib=700),
        observed_oom_headroom_bytes=160 * MIB,
    )

    assert status.critical_headroom_bytes == 320 * MIB
    assert status.warning_headroom_bytes == 640 * MIB


def test_growth_projection_warns_before_static_threshold():
    previous = _reading(rss_mib=400, available_mib=1000, minute=0)
    current = _reading(rss_mib=600, available_mib=800, minute=10)

    status = evaluate_memory(current, recent_readings=[previous])

    assert status.level == "WARNING"
    assert 10 < status.projected_minutes_to_critical <= 30


def test_critical_available_memory_blocks():
    status = evaluate_memory(_reading(rss_mib=700, available_mib=150))

    assert status.level == "CRITICAL"
    assert "critical reserve" in status.reason


NOW = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)


def _state(level="HEALTHY", *, age_minutes=1.0, extra=None):
    observed = (NOW - timedelta(minutes=age_minutes)).isoformat()
    state = {
        "last_tick_utc": observed,
        "memory_guard": {
            "level": level,
            "reason": "derived headroom exhausted" if level == "CRITICAL" else "ok",
            "reading": {"observed_utc": observed, "pid": 123},
        },
    }
    if extra:
        state.update(extra)
    return state


def test_fresh_healthy_state_does_not_block(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("AFS_WATCHER_STATE_FILE", str(state))
    state.write_text(json.dumps(_state("HEALTHY")))
    assert read_critical_memory_block(now=NOW) is None


def test_fresh_critical_state_blocks_with_code(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("CRITICAL")))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_MEMORY_CRITICAL
    assert block["reason"] == "derived headroom exhausted"
    assert block["state_stale"] is False


def test_blocked_memory_critical_entry_blocks_even_without_guard_level(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("HEALTHY", extra={"blocked": {"memory_critical": {"summary": "held"}}})))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_MEMORY_CRITICAL and block["level"] == "CRITICAL"


def test_missing_state_fails_closed(tmp_path, monkeypatch):
    block = read_critical_memory_block(tmp_path / "missing.json", now=NOW)
    assert block["code"] == CODE_STATE_MISSING and block["level"] == "MISSING"
    assert "does not exist" in block["reason"]
    # the default path (env override) is subject to the same rule
    monkeypatch.setenv("AFS_WATCHER_STATE_FILE", str(tmp_path / "also-missing.json"))
    assert read_critical_memory_block(now=NOW)["code"] == CODE_STATE_MISSING


def test_malformed_state_fails_closed(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    block = read_critical_memory_block(malformed, now=NOW)
    assert block["code"] == CODE_STATE_MALFORMED
    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2]")
    assert read_critical_memory_block(not_object, now=NOW)["code"] == CODE_STATE_MALFORMED
    no_stamp = tmp_path / "nostamp.json"
    no_stamp.write_text(json.dumps({"memory_guard": {"level": "HEALTHY"}}))
    assert read_critical_memory_block(no_stamp, now=NOW)["code"] == CODE_STATE_MALFORMED


def test_stale_state_fails_closed_when_watcher_is_silent(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("HEALTHY", age_minutes=31)))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_STATE_STALE
    assert "31 min ago" in block["reason"]
    # just inside the window is fine
    state.write_text(json.dumps(_state("HEALTHY", age_minutes=29)))
    assert read_critical_memory_block(state, now=NOW) is None
    # operator-tunable window
    monkeypatch.setenv("AFS_WATCHER_STALE_MINUTES", "45")
    state.write_text(json.dumps(_state("HEALTHY", age_minutes=40)))
    assert read_critical_memory_block(state, now=NOW) is None
    assert read_critical_memory_block(state, now=NOW, stale_after_minutes=10)["code"] == CODE_STATE_STALE


def test_future_timestamp_fails_closed(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("HEALTHY", age_minutes=-10)))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_STATE_STALE and "in the future" in block["reason"]


def test_stale_critical_still_reports_memory_critical(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("CRITICAL", age_minutes=120)))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_MEMORY_CRITICAL and block["state_stale"] is True


def test_fresh_warning_state_blocks_with_code(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("WARNING")))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_MEMORY_WARNING
    assert block["state_stale"] is False


def test_blocked_memory_warning_entry_blocks_even_without_guard_level(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("HEALTHY", extra={"blocked": {"memory_warning": {"summary": "elevated"}}})))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_MEMORY_WARNING and block["level"] == "WARNING"


def test_stale_warning_reports_stale_not_warning(tmp_path):
    # Unlike CRITICAL, a stale WARNING carries no evidence the watcher died,
    # so the stale rule (not the warning rule) governs.
    state = tmp_path / "state.json"
    state.write_text(json.dumps(_state("WARNING", age_minutes=31)))
    block = read_critical_memory_block(state, now=NOW)
    assert block["code"] == CODE_STATE_STALE


def test_newest_of_tick_and_reading_timestamp_is_used(tmp_path):
    state = tmp_path / "state.json"
    data = _state("HEALTHY", age_minutes=50)
    data["last_tick_utc"] = (NOW - timedelta(minutes=2)).isoformat()
    state.write_text(json.dumps(data))
    assert read_critical_memory_block(state, now=NOW) is None
