from __future__ import annotations

from execution.trail_shadow import shadow_trail, format_shadow_log


def _bars(*pairs):
    return [{"ts": f"2026-06-01T14:{i:02d}:00+00:00", "high": h, "low": l, "close": h}
            for i, (h, l) in enumerate(pairs)]


def test_long_armed_trail():
    # entry 100 stop 90 (R=10). Highs reach 115 (+1.5R) -> trail 0.5R behind -> 110.
    pos = {"direction": "LONG", "entry": 100.0, "stop": 90.0, "instrument": "MNQ"}
    r = shadow_trail(pos, _bars((105, 99), (115, 108)), activation_r=1.0, trail_r=0.5)
    assert r["trailing"] is True
    assert r["would_stop"] == 110.0
    assert r["moved"] is True
    assert "WOULD trail" in format_shadow_log(r, "MNQ")


def test_long_not_yet_armed():
    pos = {"direction": "LONG", "entry": 100.0, "stop": 90.0}
    r = shadow_trail(pos, _bars((105, 99), (108, 101)), activation_r=1.0, trail_r=0.5)
    assert r["trailing"] is False
    assert r["would_stop"] == 90.0
    assert "not yet armed" in format_shadow_log(r, "MNQ")


def test_short_armed_trail():
    # SHORT entry 100 stop 110 (R=10). Lows reach 85 (+1.5R) -> trail to 90.
    pos = {"direction": "SHORT", "entry": 100.0, "stop": 110.0}
    r = shadow_trail(pos, _bars((101, 92), (95, 85)), activation_r=1.0, trail_r=0.5)
    assert r["trailing"] is True
    assert r["would_stop"] == 90.0


def test_no_bars_or_bad_dir_returns_none():
    assert shadow_trail({"direction": "LONG", "entry": 100, "stop": 90}, []) is None
    assert shadow_trail({"direction": "", "entry": 100, "stop": 90}, _bars((105, 99))) is None


def test_is_pure_log_only_no_exceptions_on_garbage():
    # Missing fields must yield None, never raise (shadow must never break trading).
    assert shadow_trail({"direction": "LONG"}, _bars((105, 99))) is None
