"""Two properties pull in opposite directions; both are required.

* A deploy through the sanctioned path re-arms the watcher by doing what it
  already does — pinning its identity into `.env`. No source edit, so no false
  BLOCKED after a correct release.
* A change that did NOT go through that path still BLOCKS. That is the only
  reason the watcher exists, and it has to survive the convenience above.

A fix that satisfies only the first is worse than the bug it replaces, so the
bypass cases are tested as carefully as the happy path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.watcher_release_expectation import (
    BLOCKED,
    COMMIT_PIN,
    EPOCH_PIN,
    EPOCH_PROOF_PIN,
    FINGERPRINT_PIN,
    Observed,
    check,
    load_pins,
    read_manifest,
)

SHA = "7566a35a31ccac9b12efd972ebb7f452db7347dd"
FP = "8d1ac09efcf69ef52c31cb5721bbe17f2afa8433046041f022dda610d39211a0"
EPOCH = "2026-09-04T00:09:00Z"
RELDIR = "/releases/7566a35a31cc-20260903-200749"

OLD_SHA = "bbdb85e6aaa30475e86350049f249821a629e310"
OLD_FP = "1111111111111111111111111111111111111111111111111111111111111111"
OLD_RELDIR = "/releases/bbdb85e6aaa3-20260903-181638"


def _env(tmp_path: Path, **overrides) -> Path:
    values = {
        COMMIT_PIN: SHA,
        FINGERPRINT_PIN: FP,
        EPOCH_PIN: EPOCH,
        EPOCH_PROOF_PIN: EPOCH,
    }
    values.update(overrides)
    p = tmp_path / ".env"
    body = ["# deploy-written pins", "PAPER_MODE=false", "LIVE_TRADING_ENABLED=false"]
    body += [f"{k}={v}" for k, v in values.items() if v is not None]
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return p


def _live(**overrides) -> Observed:
    base = dict(
        release_link_target=RELDIR,
        service_cwd=RELDIR,
        manifest_commit=SHA,
        manifest_fingerprint=FP,
    )
    base.update(overrides)
    return Observed(**base)


def _keys(findings) -> set[str]:
    return {f.key for f in findings}


# ── the defect this replaces ────────────────────────────────────────────────
def test_sanctioned_deploy_rearms_the_watcher_with_no_source_edit(tmp_path: Path) -> None:
    """The whole point.

    Before this, the expectation lived in watcher.py constants: after a correct
    release the watcher kept comparing against the previous one and reported
    BLOCKED until someone edited and restarted it. Re-pinning `.env` is
    something the release wrapper already does, so re-arming is now free.
    """
    env = _env(tmp_path, **{COMMIT_PIN: OLD_SHA, FINGERPRINT_PIN: OLD_FP})
    old_box = Observed(
        release_link_target=OLD_RELDIR, service_cwd=OLD_RELDIR,
        manifest_commit=OLD_SHA, manifest_fingerprint=OLD_FP,
    )
    assert check(env, old_box) == []

    _env(tmp_path)  # the release wrapper re-pins on promote
    assert check(env, _live()) == []
    # …and the release it replaced is now what would be wrong.
    assert "unexpected_deploy" in _keys(check(env, old_box))


def test_deploy_that_bypassed_the_wrapper_still_blocks(tmp_path: Path) -> None:
    """Fail-closed survives the convenience.

    A hand-swapped symlink and restart does not re-pin `.env`, so the running
    release no longer matches the pinned commit.
    """
    env = _env(tmp_path)
    smuggled = _live(
        release_link_target=OLD_RELDIR, service_cwd=OLD_RELDIR,
        manifest_commit=OLD_SHA, manifest_fingerprint=OLD_FP,
    )
    findings = check(env, smuggled)
    assert {"unexpected_deploy", "release_fingerprint_mismatch"} <= _keys(findings)
    assert all(f.level == BLOCKED for f in findings)


def test_clean_box_reports_nothing(tmp_path: Path) -> None:
    assert check(_env(tmp_path), _live()) == []


def test_hand_patched_source_under_a_pinned_release_blocks(tmp_path: Path) -> None:
    # Same commit, different content: the fingerprint is what catches this.
    findings = check(_env(tmp_path), _live(manifest_fingerprint=OLD_FP))
    assert _keys(findings) == {"release_fingerprint_mismatch"}


def test_incomplete_switch_blocks(tmp_path: Path) -> None:
    """Pin written, symlink moved, but the process never restarted."""
    findings = check(_env(tmp_path), _live(service_cwd=OLD_RELDIR))
    assert "release_link_process_mismatch" in _keys(findings)


# ── fail-closed on the pins themselves ──────────────────────────────────────
def test_missing_env_blocks_rather_than_passing(tmp_path: Path) -> None:
    findings = check(tmp_path / "does-not-exist", _live())
    assert _keys(findings) == {"deploy_pins_unreadable"}
    assert findings[0].level == BLOCKED


@pytest.mark.parametrize("pin", [COMMIT_PIN, FINGERPRINT_PIN, EPOCH_PIN, EPOCH_PROOF_PIN])
def test_missing_or_blank_pin_blocks(tmp_path: Path, pin: str) -> None:
    for value in (None, ""):
        env = _env(tmp_path, **{pin: value})
        pins, findings = load_pins(env)
        assert pins is None
        assert _keys(findings) == {"deploy_pins_missing"}
        assert pin in findings[0].summary
        # check() must surface the pin failure alone, never compare against
        # nothing and call the result healthy.
        assert _keys(check(env, _live())) == {"deploy_pins_missing"}


def test_diverging_epoch_pins_block(tmp_path: Path) -> None:
    env = _env(tmp_path, **{EPOCH_PROOF_PIN: "2026-09-03T22:17:33Z"})
    pins, findings = load_pins(env)
    assert pins is None
    assert _keys(findings) == {"epoch_pin_divergence"}


def test_epoch_comes_from_the_pins(tmp_path: Path) -> None:
    pins, findings = load_pins(_env(tmp_path))
    assert findings == []
    assert (pins.commit, pins.fingerprint, pins.epoch_utc) == (SHA, FP, EPOCH)


# ── an observation that failed is not an observation that passed ────────────
@pytest.mark.parametrize(
    ("field", "expected_key"),
    [
        ("release_link_target", "release_link_unverifiable"),
        ("service_cwd", "service_release_unverifiable"),
        ("manifest_commit", "release_manifest_unverifiable"),
        ("manifest_fingerprint", "release_fingerprint_unverifiable"),
    ],
)
def test_unreadable_observation_blocks_its_check(tmp_path: Path, field, expected_key) -> None:
    findings = check(_env(tmp_path), _live(**{field: None}))
    assert expected_key in _keys(findings)
    assert all(f.level == BLOCKED for f in findings)


def test_unreadable_link_does_not_mask_a_wrong_release(tmp_path: Path) -> None:
    findings = check(_env(tmp_path), _live(release_link_target=None, manifest_commit=OLD_SHA))
    assert {"release_link_unverifiable", "unexpected_deploy"} <= _keys(findings)


# ── env parsing ─────────────────────────────────────────────────────────────
def test_quoted_and_redefined_values_parse_like_the_shell(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "\n".join(
            [
                "# comment",
                f'{COMMIT_PIN}="{OLD_SHA}"',
                f"{COMMIT_PIN}='{SHA}'",  # later assignment wins
                f"{FINGERPRINT_PIN}={FP}",
                f"{EPOCH_PIN}={EPOCH}",
                f"{EPOCH_PROOF_PIN}={EPOCH}",
                "MALFORMED_LINE_WITHOUT_EQUALS",
            ]
        ),
        encoding="utf-8",
    )
    pins, findings = load_pins(p)
    assert findings == []
    assert pins.commit == SHA


# ── manifest reading ────────────────────────────────────────────────────────
def test_read_manifest_extracts_commit_and_fingerprint(tmp_path: Path) -> None:
    (tmp_path / "release_manifest.json").write_text(
        json.dumps({"repo": {"commit": SHA}, "fingerprint_sha256": FP}), encoding="utf-8"
    )
    observed = read_manifest(tmp_path)
    assert (observed.manifest_commit, observed.manifest_fingerprint) == (SHA, FP)


@pytest.mark.parametrize(
    "payload", ["{not json", json.dumps({"repo": "not-a-dict"}), json.dumps({})]
)
def test_unusable_manifest_yields_none_and_therefore_blocks(tmp_path: Path, payload: str) -> None:
    (tmp_path / "release_manifest.json").write_text(payload, encoding="utf-8")
    observed = read_manifest(tmp_path)
    assert observed.manifest_commit is None
    assert "release_manifest_unverifiable" in _keys(
        check(_env(tmp_path), _live(manifest_commit=observed.manifest_commit))
    )


def test_absent_manifest_yields_none(tmp_path: Path) -> None:
    assert read_manifest(tmp_path / "nowhere").manifest_commit is None


# ── commit comparison ───────────────────────────────────────────────────────
def test_short_and_long_commit_forms_match(tmp_path: Path) -> None:
    assert check(_env(tmp_path), _live(manifest_commit=SHA[:12])) == []


def test_prefix_too_short_to_be_unique_is_not_a_match(tmp_path: Path) -> None:
    assert "unexpected_deploy" in _keys(check(_env(tmp_path), _live(manifest_commit=SHA[:8])))
