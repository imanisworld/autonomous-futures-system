import json

from ops.watcher_memory_guard import (
    MemoryReading,
    evaluate_memory,
    read_critical_memory_block,
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


def test_runtime_reads_only_active_critical_state(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("AFS_WATCHER_STATE_FILE", str(state))
    state.write_text(json.dumps({"memory_guard": {"level": "WARNING"}}))
    assert read_critical_memory_block() is None

    state.write_text(
        json.dumps(
            {
                "memory_guard": {
                    "level": "CRITICAL",
                    "observed_utc": "2026-09-02T19:00:00Z",
                    "reason": "derived headroom exhausted",
                }
            }
        )
    )
    assert read_critical_memory_block()["reason"] == "derived headroom exhausted"


def test_malformed_or_missing_watcher_state_preserves_legacy_behavior(tmp_path):
    missing = tmp_path / "missing.json"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")

    assert read_critical_memory_block(missing) is None
    assert read_critical_memory_block(malformed) is None
