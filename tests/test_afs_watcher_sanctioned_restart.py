"""The watcher adopts the sanctioned deploy's own restart — and nothing else.

Every deploy through the release wrapper restarts `futures-bot`, and until now
the watcher's persisted process baseline made every one of them a false
`unexpected_restart` that an operator had to clear by hand (2026-09-04 and
2026-09-05 both needed it). The fix lets the watcher re-baseline on its own
ONLY when the new process is provably the pinned release's: the pins are
coherent and name the release this watcher verified, the link and the live
pid's cwd are that release, the service is active, `NRestarts` did not move,
and — the discriminator — the baseline was recorded under a DIFFERENT release.
A hand `systemctl restart` on the same release, a crash, or any ambiguity
still BLOCKS exactly as before.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location("afs_watcher_sanctioned_restart", WATCHER_DIR / "watcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()

NOW = datetime(2026, 9, 5, 23, 59, tzinfo=timezone.utc)
EPOCH = NOW - timedelta(hours=6)
NEW_SHA = "73bffb1c776d8585086d1f9c915da633e7ec69fb"
NEW_FP = "f07a55c0a3bb3413b8b7da31e041474d7af7747ef5afd454ba3b34881828ee6f"
OLD_SHA = "7566a35a31ccac9b12efd972ebb7f452db7347dd"
OLD_FP = "8d1ac09efcf69ef52c31cb5721bbe17f2afa8433046041f022dda610d39211a0"

OLD_ENTER = "Fri 2026-09-04 00:08:05 UTC"
NEW_ENTER = "Sat 2026-09-05 23:58:46 UTC"
OLD_PID = "737895"
NEW_PID = "1473936"


def _props(*, state="active", pid=NEW_PID, enter=NEW_ENTER, nrestarts="0") -> str:
    return (f"ActiveState={state}\nSubState=running\nExecMainPID={pid}\n"
            f"NRestarts={nrestarts}\nActiveEnterTimestamp={enter}\n")


def _old_baseline(*, release: dict | None | str = "old") -> dict:
    base = {"ActiveEnterTimestamp": OLD_ENTER, "NRestarts": "0", "ExecMainPID": OLD_PID,
            "recorded_utc": "2026-09-04T00:13:13Z"}
    if release == "old":
        base["release"] = {"commit": OLD_SHA, "fingerprint": OLD_FP, "release_dir": "/root/afs-releases/old"}
    elif release is not None:
        base["release"] = release
    return base


def _tick(tmp_path: Path, monkeypatch, *, baseline, props: str, cwd: str | None = "release",
          pins="coherent", link_to: str | None = None, webhook_procs: int = 1):
    """Run `check_runtime` against a fully stubbed box and return (state, findings, tick).

    `cwd="release"` means the live pid runs from the verified release dir.
    `pins="coherent"` means `.env` names exactly the release this watcher verified.
    `link_to` redirects the release symlink to another directory (an unpinned promote).
    `webhook_procs` != 1 raises `webhook_process_count`, a blocker found AFTER the baseline block.
    """
    release_dir = (tmp_path / "rel-new").resolve()
    release_dir.mkdir()
    other_dir = (tmp_path / "rel-other").resolve()
    other_dir.mkdir()
    (other_dir / "release_manifest.json").write_text(
        json.dumps({"repo": {"commit": OLD_SHA}, "fingerprint_sha256": OLD_FP}), encoding="utf-8")
    link = tmp_path / "current"
    link.symlink_to(other_dir if link_to == "other" else release_dir)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    journal = log_dir / "journal_2026-09-05.jsonl"
    journal.write_text('{"record_type":"decision"}\n', encoding="utf-8")
    feed_state = log_dir / "feed_gap_alarm_state.json"
    feed_state.write_text(json.dumps({"instruments": {"MNQ": {"status": "healthy"}}}))

    monkeypatch.setattr(w, "STATE_DIR", state_dir)
    monkeypatch.setattr(w, "LOG_FILE", state_dir / "watcher.log")
    monkeypatch.setattr(w, "EVENTS_FILE", state_dir / "events.jsonl")
    monkeypatch.setattr(w, "LOG_DIR", log_dir)
    monkeypatch.setattr(w, "FEED_STATE", feed_state)
    monkeypatch.setattr(w, "RELEASE_LINK", link)
    monkeypatch.setattr(w, "RELEASE_DIR", release_dir)
    monkeypatch.setattr(w, "RELEASE_SHA", NEW_SHA)
    monkeypatch.setattr(w, "RELEASE_FINGERPRINT", NEW_FP)
    monkeypatch.setattr(w, "EPOCH", EPOCH)
    monkeypatch.setattr(w, "now_utc", lambda: NOW)
    monkeypatch.setattr(w, "read_prod_text", lambda path: path.read_text(encoding="utf-8"))
    monkeypatch.setattr(w, "read_prod_bytes_tail", lambda path, _n: path.read_bytes())

    if pins == "coherent":
        pin_values = {w.COMMIT_PIN: NEW_SHA, w.FINGERPRINT_PIN: NEW_FP,
                      w.EPOCH_PIN: w.iso(EPOCH), w.EPOCH_PROOF_PIN: w.iso(EPOCH)}
        monkeypatch.setattr(w, "load_deploy_pins", lambda _p: (pin_values, None))
    elif pins == "unreadable":
        monkeypatch.setattr(w, "load_deploy_pins", lambda _p: (None, "deploy pins missing or blank"))
    else:
        monkeypatch.setattr(w, "load_deploy_pins", lambda _p: (dict(pins), None))

    live_cwd = str(release_dir) if cwd == "release" else cwd
    real_readlink = os.readlink

    def fake_readlink(path, *args, **kwargs):
        if str(path).startswith("/proc/"):
            if live_cwd is None:
                raise PermissionError(path)
            return live_cwd
        return real_readlink(path, *args, **kwargs)

    monkeypatch.setattr(w.os, "readlink", fake_readlink)

    def fake_run(cmd, **_kwargs):
        if cmd[:2] == ["systemctl", "show"]:
            return 0, props
        if cmd[:2] == ["systemctl", "list-units"]:
            return 0, ""
        if cmd[:2] == ["pgrep", "-af"]:
            return 0, "".join(f"{int(NEW_PID) + i} uvicorn webhook.app\n" for i in range(webhook_procs))
        if cmd and cmd[0] == "journalctl":
            return 0, ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(w, "run", fake_run)

    def fake_get(path, **_kwargs):
        if path == "/status/tradovate-reliability":
            return {"state": "HEALTHY", "ready": True, "market_active": False}, None
        if path == "/status/broker-account":
            return {"ok": True, "env": "demo", "position": None}, None
        if path == "/status/today":
            return {"live_trading_enabled": False}, None
        raise AssertionError(f"unexpected endpoint: {path}")

    monkeypatch.setattr(w, "http_get_json", fake_get)

    state = {"baseline": baseline, "events_seen": {}, "notified": {}, "blocked": {}}
    findings = w.Findings()
    tick: dict = {}
    w.check_runtime(state, findings, tick)
    return state, findings, tick


def _keys(findings) -> set[str]:
    return {b["key"] for b in findings.blocked()}


# ── the fix: a sanctioned deploy's restart is adopted ────────────────────────
def test_sanctioned_deploy_restart_is_adopted_without_unexpected_restart(tmp_path, monkeypatch):
    state, findings, tick = _tick(tmp_path, monkeypatch, baseline=_old_baseline(), props=_props())

    assert _keys(findings) == set(), findings.items
    base = state["baseline"]
    assert base["ExecMainPID"] == NEW_PID and base["ActiveEnterTimestamp"] == NEW_ENTER
    assert base["release"]["commit"] == NEW_SHA and base["release"]["fingerprint"] == NEW_FP
    assert base["adopted_from"]["ExecMainPID"] == OLD_PID
    assert base["adopted_from"]["release"]["commit"] == OLD_SHA
    assert NEW_PID in tick["runtime"]["baseline_adopted"]

    events = [json.loads(l) for l in (w.STATE_DIR / "events.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in events] == ["REBASELINED"]
    assert OLD_SHA[:12] in events[0]["summary"] and NEW_SHA[:12] in events[0]["summary"]


def test_next_tick_after_adoption_is_quiet(tmp_path, monkeypatch):
    first = tmp_path / "t1"
    first.mkdir()
    state, _, _ = _tick(first, monkeypatch, baseline=_old_baseline(), props=_props())

    second = tmp_path / "t2"
    second.mkdir()
    again, findings, _ = _tick(second, monkeypatch, baseline=state["baseline"], props=_props())
    assert _keys(findings) == set()
    assert again["baseline"]["ExecMainPID"] == NEW_PID
    assert "adopted_from" in again["baseline"]  # untouched: same process, nothing to adopt


# ── everything that must still BLOCK ─────────────────────────────────────────
def test_hand_restart_on_the_same_release_still_blocks(tmp_path, monkeypatch):
    """The discriminator: same release, new pid — no deploy explains it."""
    same_release = {"commit": NEW_SHA, "fingerprint": NEW_FP, "release_dir": str((tmp_path / "rel-new").resolve())}
    baseline = _old_baseline(release=same_release)
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=baseline, props=_props())

    assert _keys(findings) == {"unexpected_restart"}
    [b] = findings.blocked()
    assert "same release" in b["detail"]["not_adopted"]
    assert state["baseline"] == baseline


def test_crash_restart_still_blocks_and_is_never_adopted(tmp_path, monkeypatch):
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=_old_baseline(), props=_props(nrestarts="1"))

    assert _keys(findings) == {"unexpected_restart", "service_crash_restart"}
    assert state["baseline"]["ExecMainPID"] == OLD_PID


@pytest.mark.parametrize(
    ("case", "kwargs", "also_blocked"),
    [
        ("cwd_wrong_release", {"cwd": "/root/afs-releases/somewhere-else"}, {"service_wrong_release"}),
        ("cwd_unreadable", {"cwd": None}, {"service_pid_cwd_unreadable"}),
        ("pins_unreadable", {"pins": "unreadable"}, {"deploy_pins_unreadable"}),
        ("pins_name_another_commit",
         {"pins": {w.COMMIT_PIN: OLD_SHA, w.FINGERPRINT_PIN: NEW_FP,
                   w.EPOCH_PIN: w.iso(EPOCH), w.EPOCH_PROOF_PIN: w.iso(EPOCH)}}, set()),
        ("pins_name_another_fingerprint",
         {"pins": {w.COMMIT_PIN: NEW_SHA, w.FINGERPRINT_PIN: OLD_FP,
                   w.EPOCH_PIN: w.iso(EPOCH), w.EPOCH_PROOF_PIN: w.iso(EPOCH)}}, set()),
        ("epoch_moved",
         {"pins": {w.COMMIT_PIN: NEW_SHA, w.FINGERPRINT_PIN: NEW_FP,
                   w.EPOCH_PIN: "2026-09-06T00:00:00Z", w.EPOCH_PROOF_PIN: "2026-09-06T00:00:00Z"}},
         {"epoch_drift"}),
        ("link_points_at_unpinned_release", {"link_to": "other"}, {"unexpected_deploy"}),
        ("service_not_active", {"props": _props(state="activating")}, {"service_not_active"}),
        ("nrestarts_unreadable", {"props": _props(nrestarts="?")}, set()),
        ("pid_unchanged_but_timestamp_moved", {"props": _props(pid=OLD_PID)}, set()),
        ("timestamp_unchanged_but_pid_moved", {"props": _props(enter=OLD_ENTER)}, set()),
        # found AFTER the baseline block — the decision must still see it
        ("blocker_raised_later_in_the_tick", {"webhook_procs": 2}, {"webhook_process_count"}),
    ],
)
def test_mismatched_or_ambiguous_state_never_auto_adopts(tmp_path, monkeypatch, case, kwargs, also_blocked):
    kwargs = {"props": _props(), **kwargs}
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=_old_baseline(), **kwargs)

    keys = _keys(findings)
    assert "unexpected_restart" in keys, (case, findings.items)
    assert also_blocked <= keys, (case, keys)
    assert state["baseline"]["ExecMainPID"] == OLD_PID, case
    assert "adopted_from" not in state["baseline"], case
    assert not (w.STATE_DIR / "events.jsonl").exists(), case


def test_watcher_still_armed_on_the_old_release_blocks_not_adopts(tmp_path, monkeypatch):
    """Promote happened (pins + link name the new release) but THIS watcher was not restarted yet."""
    still_armed = {"commit": NEW_SHA, "fingerprint": NEW_FP, "release_dir": str((tmp_path / "rel-new").resolve())}
    new_pins = {w.COMMIT_PIN: OLD_SHA, w.FINGERPRINT_PIN: OLD_FP, w.EPOCH_PIN: w.iso(EPOCH), w.EPOCH_PROOF_PIN: w.iso(EPOCH)}
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=_old_baseline(release=still_armed),
                               props=_props(), pins=new_pins, link_to="other",
                               cwd=str((tmp_path / "rel-other").resolve()))

    keys = _keys(findings)
    assert {"unexpected_restart", "watcher_release_stale", "service_wrong_release"} <= keys, keys
    assert state["baseline"]["ExecMainPID"] == OLD_PID


def test_later_blocker_annotates_the_restart_instead_of_adopting(tmp_path, monkeypatch):
    _, findings, _ = _tick(tmp_path, monkeypatch, baseline=_old_baseline(), props=_props(), webhook_procs=2)
    [restart] = [b for b in findings.blocked() if b["key"] == "unexpected_restart"]
    assert "webhook_process_count" in restart["detail"]["not_adopted"]


@pytest.mark.parametrize("release", [{}, {"commit": NEW_SHA}, "not-a-dict"])
def test_incomplete_release_identity_blocks_and_is_not_repaired(tmp_path, monkeypatch, release):
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=_old_baseline(release=release), props=_props())
    assert _keys(findings) == {"unexpected_restart"}
    assert state["baseline"]["ExecMainPID"] == OLD_PID
    assert state["baseline"].get("release") == release


def test_baseline_without_release_identity_cannot_prove_a_deploy(tmp_path, monkeypatch):
    """A pre-fix baseline has no release to compare with: fail closed, as before."""
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=_old_baseline(release=None), props=_props())

    assert _keys(findings) == {"unexpected_restart"}
    [b] = findings.blocked()
    assert "predates" in b["detail"]["not_adopted"]
    assert state["baseline"]["ExecMainPID"] == OLD_PID


# ── baseline bookkeeping that makes the next deploy provable ─────────────────
def test_first_start_records_baseline_with_release_identity(tmp_path, monkeypatch):
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=None, props=_props())

    assert _keys(findings) == set()
    assert state["baseline"]["ExecMainPID"] == NEW_PID
    assert state["baseline"]["release"] == {"commit": NEW_SHA, "fingerprint": NEW_FP,
                                            "release_dir": str((tmp_path / "rel-new").resolve())}


def test_legacy_baseline_for_the_same_process_is_backfilled_not_blocked(tmp_path, monkeypatch):
    """The box's current baseline predates release tracking; stamp it in place while the pid still matches."""
    legacy = {"ActiveEnterTimestamp": NEW_ENTER, "NRestarts": "0", "ExecMainPID": NEW_PID,
              "recorded_utc": "2026-09-06T00:01:33Z"}
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=legacy, props=_props())

    assert _keys(findings) == set()
    assert state["baseline"]["ExecMainPID"] == NEW_PID
    assert state["baseline"]["release"]["commit"] == NEW_SHA


@pytest.mark.parametrize("kwargs", [{"cwd": "/root/afs-releases/somewhere-else"}, {"webhook_procs": 2}])
def test_legacy_baseline_is_not_backfilled_on_a_dirty_tick(tmp_path, monkeypatch, kwargs):
    legacy = {"ActiveEnterTimestamp": NEW_ENTER, "NRestarts": "0", "ExecMainPID": NEW_PID}
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=legacy, props=_props(), **kwargs)
    assert findings.blocked()
    assert "release" not in state["baseline"]


def test_legacy_baseline_is_not_backfilled_across_a_restart(tmp_path, monkeypatch):
    """Backfilling must never launder an unexplained restart into a provable one."""
    state, findings, _ = _tick(tmp_path, monkeypatch, baseline=_old_baseline(release=None), props=_props())

    assert "unexpected_restart" in _keys(findings)
    assert "release" not in state["baseline"]


def test_shipped_source_passes_the_watcher_static_selfcheck():
    """The watcher refuses to start if its own source contains a forbidden token
    (anywhere after the FORBIDDEN_TOKENS list — docstrings and comments included).
    On the box that is a silent crash loop under supervisor.sh; here it must be a
    failing test. Regression for the 2026-09-06 install of #465, whose docstring
    mentioned a service restart command verbatim."""
    w.static_selfcheck()
