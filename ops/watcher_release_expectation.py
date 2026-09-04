"""Derive the watcher's expected release and epoch from the deploy's own pins.

The read-only watcher answers one question: *did anything change that the
sanctioned deploy path did not make?* It used to answer it from module-level
constants in its own source — expected release sha, release directory and
evidence epoch, written as literals. Nothing in the deploy path updated them, so
every legitimate release left the watcher reporting BLOCKED until an operator
hand-edited that source and restarted the unit. An alarm that fires on every
correct deploy is one people learn to clear without reading.

Nothing new needs to be written to fix this. The release wrapper already pins
its identity into the shared `.env` as part of the atomic promote:

    EXPECTED_LIVE_COMMIT           the released commit
    EXPECTED_RELEASE_FINGERPRINT   the release manifest fingerprint

and the epoch step pins the evidence window twice:

    MNQ_ORB_BREAKOUT_INVERSE_EPOCH_START
    EXPECTED_PROOF_MNQ_ORB_BREAKOUT_INVERSE_EPOCH_START

Those are the authoritative deploy-written record. This module reads them and
compares them against what is actually running, so a sanctioned deploy re-arms
the watcher by doing what it already does.

Fail-closed is preserved, and is the reason for the specific comparisons below:

* A deploy that bypasses the wrapper does not re-pin `.env`, so the live
  release's manifest no longer matches the pinned commit and the watcher BLOCKS.
  That is the case the watcher exists to catch, and it still trips.
* The symlink and the running process are compared to each other as well as to
  the pin, because either alone can lie: the pin is written before the switch,
  and the symlink can point at a directory staged by a run that never finished
  pinning. The release wrapper's own redundant-deploy guard reasons the same way.
* A pin that is missing, blank or unreadable BLOCKS. So does an observation that
  could not be made. "Cannot verify" is never reported as "verified".

This module holds no box-specific paths; callers pass locations in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

COMMIT_PIN = "EXPECTED_LIVE_COMMIT"
FINGERPRINT_PIN = "EXPECTED_RELEASE_FINGERPRINT"
EPOCH_PIN = "MNQ_ORB_BREAKOUT_INVERSE_EPOCH_START"
EPOCH_PROOF_PIN = "EXPECTED_PROOF_MNQ_ORB_BREAKOUT_INVERSE_EPOCH_START"

BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Finding:
    level: str
    key: str
    summary: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "key": self.key, "summary": self.summary}


@dataclass(frozen=True)
class Pins:
    """What the sanctioned deploy path recorded about the live release."""

    commit: str
    fingerprint: str
    epoch_utc: str


@dataclass(frozen=True)
class Observed:
    """What the watcher actually saw. `None` means "could not read"."""

    release_link_target: str | None = None
    service_cwd: str | None = None
    manifest_commit: str | None = None
    manifest_fingerprint: str | None = None


def read_env_pins(env_path: str | Path) -> tuple[dict[str, str], Finding | None]:
    """Parse `KEY=VALUE` lines. Later assignments win, matching shell `.env`."""
    try:
        raw = Path(env_path).read_text(encoding="utf-8")
    except OSError as exc:
        return {}, Finding(
            BLOCKED,
            "deploy_pins_unreadable",
            f"cannot read the deploy's pinned identity at {env_path}: {exc}",
        )

    values: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values, None


def load_pins(env_path: str | Path) -> tuple[Pins | None, list[Finding]]:
    """Read the deploy-written pins. Never raises; failures are BLOCKED findings.

    The two epoch pins must agree: the service's own startup guard refuses to
    boot when they diverge, so a divergence found here means one was edited
    while the service was already running.
    """
    values, err = read_env_pins(env_path)
    if err is not None:
        return None, [err]

    missing = [k for k in (COMMIT_PIN, FINGERPRINT_PIN) if not values.get(k)]
    epoch = values.get(EPOCH_PIN, "")
    proof = values.get(EPOCH_PROOF_PIN, "")
    if not epoch:
        missing.append(EPOCH_PIN)
    if not proof:
        missing.append(EPOCH_PROOF_PIN)
    if missing:
        return None, [
            Finding(
                BLOCKED,
                "deploy_pins_missing",
                "the deploy's pinned identity is incomplete — missing or blank: "
                f"{', '.join(sorted(set(missing)))}. The watcher has nothing "
                "authoritative to compare against.",
            )
        ]

    if epoch != proof:
        return None, [
            Finding(
                BLOCKED,
                "epoch_pin_divergence",
                f"epoch pins disagree: {EPOCH_PIN}={epoch} but "
                f"{EPOCH_PROOF_PIN}={proof} — the startup guard would refuse to "
                "boot on this configuration",
            )
        ]

    return Pins(commit=values[COMMIT_PIN], fingerprint=values[FINGERPRINT_PIN], epoch_utc=epoch), []


def evaluate(pins: Pins, observed: Observed) -> list[Finding]:
    """Compare the running box against the deploy's pins."""
    out: list[Finding] = []

    link = observed.release_link_target.rstrip("/") if observed.release_link_target else None
    cwd = observed.service_cwd.rstrip("/") if observed.service_cwd else None

    if link is None:
        out.append(
            Finding(BLOCKED, "release_link_unverifiable",
                    "release link could not be resolved — cannot confirm which "
                    "release is deployed")
        )
    if cwd is None:
        out.append(
            Finding(BLOCKED, "service_release_unverifiable",
                    "live process working directory could not be read — cannot "
                    "confirm which release is actually running")
        )
    if link is not None and cwd is not None and link != cwd:
        out.append(
            Finding(BLOCKED, "release_link_process_mismatch",
                    f"release link → {link} but the live process is running from "
                    f"{cwd} — a switch that did not complete, or a restart that "
                    "has not happened yet")
        )

    # Release identity. The manifest inside the directory the process is really
    # running from is the thing to trust; a directory name is a label.
    if observed.manifest_commit is None:
        out.append(
            Finding(BLOCKED, "release_manifest_unverifiable",
                    "live release manifest could not be read — cannot confirm the "
                    f"running release is the pinned {pins.commit[:12]}")
        )
    elif not _sha_match(observed.manifest_commit, pins.commit):
        out.append(
            Finding(BLOCKED, "unexpected_deploy",
                    f"live release is commit {observed.manifest_commit[:12]}, but "
                    f"the deploy pinned {pins.commit[:12]} — this release did not "
                    "come through the sanctioned deploy path")
        )

    if observed.manifest_fingerprint is None:
        out.append(
            Finding(BLOCKED, "release_fingerprint_unverifiable",
                    "live release fingerprint could not be read — cannot confirm "
                    "the running source matches the pinned release")
        )
    elif observed.manifest_fingerprint.strip().lower() != pins.fingerprint.strip().lower():
        out.append(
            Finding(BLOCKED, "release_fingerprint_mismatch",
                    f"live release fingerprint {observed.manifest_fingerprint[:16]}… "
                    f"does not match the pinned {pins.fingerprint[:16]}…")
        )

    return out


def check(env_path: str | Path, observed: Observed) -> list[Finding]:
    """Load pins and evaluate, fail-closed.

    When the pins cannot be trusted their findings are returned alone: a box
    compared against nothing is unverified, not healthy.
    """
    pins, findings = load_pins(env_path)
    if pins is None:
        return findings
    return evaluate(pins, observed)


def read_manifest(release_dir: str | Path, name: str = "release_manifest.json") -> Observed:
    """Read commit + fingerprint from a release manifest, tolerating failure.

    Returns `None` fields on any failure, which BLOCKS the checks that need them
    rather than passing them.
    """
    commit = fingerprint = None
    try:
        data = json.loads(Path(release_dir).joinpath(name).read_text(encoding="utf-8"))
        repo = data.get("repo")
        if isinstance(repo, dict) and isinstance(repo.get("commit"), str):
            commit = repo["commit"]
        if isinstance(data.get("fingerprint_sha256"), str):
            fingerprint = data["fingerprint_sha256"]
    except (OSError, ValueError, AttributeError):
        pass
    return Observed(manifest_commit=commit, manifest_fingerprint=fingerprint)


def _sha_match(a: str, b: str) -> bool:
    """Compare commit ids that may be recorded at different lengths."""
    a, b = a.strip().lower(), b.strip().lower()
    n = min(len(a), len(b))
    # Short enough to collide is not a match; a 12-hex prefix is the shortest
    # form the deploy path ever writes.
    return bool(a) and bool(b) and n >= 12 and a[:n] == b[:n]
