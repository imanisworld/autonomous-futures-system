"""The watcher's expectation comes from the deploy's pins, and fails closed.

Two properties pull in opposite directions and both are required:

* A deploy through the sanctioned path re-pins `.env`, so the watcher's
  expectation moves with it. No source edit, so no false BLOCKED after a
  correct release.
* A release the deploy never pinned still BLOCKS, and is never executed from.
  That is the only reason this process exists, and it has to survive the
  convenience above.

A change that satisfies only the first is worse than the bug it replaces, so
the bypass cases are tested at least as carefully as the happy path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    """Import the deployed watcher the way it runs on the box.

    It imports its own local `watcher_memory_guard`, not the repo-level twin, so
    its directory goes on `sys.path` first. At import it tries to read the box's
    `.env`, which does not exist here — that must leave it unarmed, not crash.
    """
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location("afs_watcher_watcher", WATCHER_DIR / "watcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()

SHA = "7566a35a31ccac9b12efd972ebb7f452db7347dd"
FP = "8d1ac09efcf69ef52c31cb5721bbe17f2afa8433046041f022dda610d39211a0"
EPOCH = "2026-09-04T00:09:00Z"
OLD_SHA = "bbdb85e6aaa30475e86350049f249821a629e310"
OLD_FP = "1111111111111111111111111111111111111111111111111111111111111111"


def _env(tmp_path: Path, **overrides) -> Path:
    values = {
        w.COMMIT_PIN: SHA,
        w.FINGERPRINT_PIN: FP,
        w.EPOCH_PIN: EPOCH,
        w.EPOCH_PROOF_PIN: EPOCH,
    }
    values.update(overrides)
    p = tmp_path / ".env"
    lines = ["# deploy-written pins", "LIVE_TRADING_ENABLED=false"]
    lines += [f"{k}={v}" for k, v in values.items() if v is not None]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _release(tmp_path: Path, name: str = "rel", commit: str = SHA, fingerprint: str = FP) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "release_manifest.json").write_text(
        json.dumps({"repo": {"commit": commit}, "fingerprint_sha256": fingerprint}), encoding="utf-8"
    )
    return d


# ── the defect this replaces ────────────────────────────────────────────────
def test_importing_without_the_box_leaves_it_unarmed_not_crashed() -> None:
    """The box `.env` does not exist here, so it must come up unarmed."""
    assert w.DEPLOY_PINS is None
    assert w.RELEASE_DIR is None
    assert w.PINS_ERROR


def test_expectation_follows_the_pins_with_no_source_edit(tmp_path: Path) -> None:
    """The whole point: re-pinning is something the release wrapper already does."""
    pins, err = w.load_deploy_pins(_env(tmp_path))
    assert err is None
    assert pins[w.COMMIT_PIN] == SHA and pins[w.EPOCH_PIN] == EPOCH

    # A later release re-pins; the expectation moves with it, untouched by hand.
    moved, err = w.load_deploy_pins(_env(tmp_path, **{w.COMMIT_PIN: OLD_SHA, w.FINGERPRINT_PIN: OLD_FP}))
    assert err is None and moved[w.COMMIT_PIN] == OLD_SHA


# ── resolve-then-verify, and only what the deploy pinned ────────────────────
def test_verified_release_is_adopted(tmp_path: Path, monkeypatch) -> None:
    rel = _release(tmp_path)
    link = tmp_path / "current"
    link.symlink_to(rel)
    pins, _ = w.load_deploy_pins(_env(tmp_path))
    resolved, err = w.resolve_release_dir(link, pins)
    assert err is None and resolved == rel.resolve()


@pytest.mark.parametrize(
    ("commit", "fingerprint", "expected_in_reason"),
    [
        (OLD_SHA, FP, "refusing to run against an unpinned release"),
        (SHA, OLD_FP, "source does not match the pinned release"),
    ],
)
def test_release_the_deploy_did_not_pin_is_refused(
    tmp_path: Path, commit, fingerprint, expected_in_reason
) -> None:
    """A hijacked or hand-swapped link must never be adopted.

    This is the security case: the resolved directory is where the watcher
    executes a Python interpreter from, so an unpinned release is refused
    outright rather than adopted and merely reported on.
    """
    rel = _release(tmp_path, commit=commit, fingerprint=fingerprint)
    link = tmp_path / "current"
    link.symlink_to(rel)
    pins, _ = w.load_deploy_pins(_env(tmp_path))
    resolved, err = w.resolve_release_dir(link, pins)
    assert resolved is None
    assert expected_in_reason in err


def test_unreadable_manifest_is_refused_not_assumed(tmp_path: Path) -> None:
    rel = tmp_path / "rel"
    rel.mkdir()
    link = tmp_path / "current"
    link.symlink_to(rel)
    pins, _ = w.load_deploy_pins(_env(tmp_path))
    resolved, err = w.resolve_release_dir(link, pins)
    assert resolved is None and "unreadable" in err


def test_link_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")
    pins, _ = w.load_deploy_pins(_env(tmp_path))
    resolved, err = w.resolve_release_dir(target, pins)
    assert resolved is None and "does not resolve to a directory" in err


# ── fail-closed on the pins ─────────────────────────────────────────────────
def test_unreadable_env_blocks(tmp_path: Path) -> None:
    pins, err = w.load_deploy_pins(tmp_path / "absent")
    assert pins is None and "cannot read the deploy's pinned identity" in err


@pytest.mark.parametrize(
    "pin", ["COMMIT_PIN", "FINGERPRINT_PIN", "EPOCH_PIN", "EPOCH_PROOF_PIN"]
)
def test_missing_or_blank_pin_blocks(tmp_path: Path, pin: str) -> None:
    name = getattr(w, pin)
    for value in (None, ""):
        pins, err = w.load_deploy_pins(_env(tmp_path, **{name: value}))
        assert pins is None
        assert name in err


def test_diverging_epoch_pins_block(tmp_path: Path) -> None:
    pins, err = w.load_deploy_pins(_env(tmp_path, **{w.EPOCH_PROOF_PIN: "2026-09-03T22:17:33Z"}))
    assert pins is None and "epoch pins disagree" in err


def test_unparseable_epoch_blocks(tmp_path: Path) -> None:
    pins, err = w.load_deploy_pins(
        _env(tmp_path, **{w.EPOCH_PIN: "not-a-time", w.EPOCH_PROOF_PIN: "not-a-time"})
    )
    assert pins is None and "ISO-8601" in err


def test_quoted_and_redefined_values_parse_like_the_shell(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "\n".join(
            [
                "# comment",
                f'{w.COMMIT_PIN}="{OLD_SHA}"',
                f"{w.COMMIT_PIN}='{SHA}'",  # later assignment wins
                f"{w.FINGERPRINT_PIN}={FP}",
                f"{w.EPOCH_PIN}={EPOCH}",
                f"{w.EPOCH_PROOF_PIN}={EPOCH}",
                "MALFORMED_LINE_WITHOUT_EQUALS",
            ]
        ),
        encoding="utf-8",
    )
    pins, err = w.load_deploy_pins(p)
    assert err is None and pins[w.COMMIT_PIN] == SHA


# ── execution stays inside the verified release ─────────────────────────────
def test_unverified_release_cannot_execute_anything(monkeypatch) -> None:
    """With no verified release there is no interpreter path to trust.

    The allowlist must refuse rather than fall back — this is what stops a
    hijacked link from choosing what the watcher runs.
    """
    monkeypatch.setattr(w, "RELEASE_DIR", None)
    with pytest.raises(RuntimeError, match="not in read-only allowlist"):
        w.run(["/anywhere/python", "-c", "print(1)"])


def test_interpreter_outside_the_verified_release_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(w, "RELEASE_DIR", tmp_path / "verified")
    with pytest.raises(RuntimeError, match="not in read-only allowlist"):
        w.run([str(tmp_path / "other" / ".venv" / "bin" / "python"), "-c", "print(1)"])


def test_watcher_refuses_to_start_when_it_cannot_state_its_expectation(monkeypatch) -> None:
    """Unarmed must mean "not running", never "reporting OK"."""
    monkeypatch.setattr(w, "DEPLOY_PINS", None)
    monkeypatch.setattr(w, "RELEASE_DIR", None)
    monkeypatch.setattr(w, "RELEASE_ERROR", "no pins in the test environment")
    assert w.main([]) == 4


# ── manifest reading + sha comparison ───────────────────────────────────────
def test_manifest_identity_reads_commit_and_fingerprint(tmp_path: Path) -> None:
    assert w.manifest_identity(_release(tmp_path)) == (SHA, FP)


@pytest.mark.parametrize("payload", ["{not json", json.dumps({"repo": "not-a-dict"}), json.dumps({})])
def test_unusable_manifest_yields_no_identity(tmp_path: Path, payload: str) -> None:
    d = tmp_path / "rel"
    d.mkdir()
    (d / "release_manifest.json").write_text(payload, encoding="utf-8")
    assert w.manifest_identity(d)[0] is None


def test_absent_manifest_yields_no_identity(tmp_path: Path) -> None:
    assert w.manifest_identity(tmp_path / "nowhere") == (None, None)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (SHA, SHA[:12], True),
        (SHA[:12], SHA, True),
        (SHA[:8], SHA, False),   # too short to be unique is not a match
        (SHA, OLD_SHA, False),
        ("", SHA, False),
    ],
)
def test_sha_comparison(a: str, b: str, expected: bool) -> None:
    assert w._sha_match(a, b) is expected
