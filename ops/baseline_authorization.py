"""Operator baseline authorization record.

The promote gate calls this module and nothing else in the reset procedure.
It is intentionally limited to the standard library so a posture check does
not import the broker.

A missing record means the box was already on the Path A six-tuple and the
historical gate still applies. A present record is an operator reset: only
``target_sha`` may promote, and an unreadable record refuses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

AUTH_FILENAME = "baseline_authorization.json"
AUDIT_FILENAME = "baseline_audit.jsonl"

SHA_RE = re.compile(r"[0-9a-f]{40}")
TICKET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,80}")

PATH_A_PIN_ORDER = (
    ("SCHEDULE_MODE", "always_on_shadow"),
    ("EXPECTED_PROOF_SCHEDULE_MODE", "always_on_shadow"),
    ("HTF_DIRECTION_MODE", "off"),
    ("EXPECTED_PROOF_HTF_DIRECTION_MODE", "off"),
    ("EXIT_MODE", "static"),
    ("EXPECTED_PROOF_EXIT_MODE", "static"),
)
PATH_A_PINS = dict(PATH_A_PIN_ORDER)

RELEASE_PIN_KEYS = (
    "EXPECTED_RELEASE_FINGERPRINT",
    "EXPECTED_LIVE_BRANCH",
    "EXPECTED_LIVE_COMMIT",
    "EXPECTED_RISK_RULES_SHA256",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assignment_key(line: str) -> str | None:
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _value = line.split("=", 1)
    if not key or key.strip() != key or " " in key:
        return None
    return key


def env_assignments(text: str) -> list[tuple[str, str]]:
    found = []
    for line in text.splitlines():
        key = assignment_key(line)
        if key is None:
            continue
        found.append((key, line.split("=", 1)[1]))
    return found


def env_values(text: str) -> dict[str, str]:
    return dict(env_assignments(text))


def path_a_configured(text: str) -> bool:
    """True only when each Path A pin appears once and matches exactly."""
    counts: dict[str, int] = {}
    values: dict[str, str] = {}
    for key, value in env_assignments(text):
        if key not in PATH_A_PINS:
            continue
        counts[key] = counts.get(key, 0) + 1
        values[key] = value
    for key, expected in PATH_A_PINS.items():
        if counts.get(key) != 1 or values.get(key) != expected:
            return False
    return True


def apply_pin_updates(text: str, updates: dict[str, str]) -> str:
    """Replace the named pins. An empty value deletes the key.

    Duplicate lines for an updated key collapse to one assignment so the
    gate's exact-line check and the preflight's one-line check agree.
    Every other line is copied unchanged.
    """
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        key = assignment_key(line)
        if key in updates:
            if key in seen:
                continue
            seen.add(key)
            value = updates[key]
            if value != "":
                out.append(f"{key}={value}")
            continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen and value != "":
            out.append(f"{key}={value}")
    if not out:
        return ""
    return "\n".join(out) + "\n"


def non_pin_assignments(text: str, allowed: set[str]) -> list[tuple[str, str]]:
    return [(key, value) for key, value in env_assignments(text) if key not in allowed]


def authorized_target(record_text: str) -> str:
    """Return the authorized SHA or raise ValueError if the record is unusable."""
    try:
        payload = json.loads(record_text)
    except json.JSONDecodeError as exc:
        raise ValueError("baseline authorization unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("baseline authorization unreadable")
    sha = payload.get("target_sha")
    if not isinstance(sha, str) or SHA_RE.fullmatch(sha) is None:
        raise ValueError("baseline authorization unreadable")
    return sha


def check_sha(candidate: str, record_text: str) -> int:
    try:
        authorized = authorized_target(record_text)
    except ValueError:
        print("baseline authorization unreadable", file=sys.stderr)
        return 1
    if candidate != authorized:
        print(
            f"baseline authorization accepts only {authorized}",
            file=sys.stderr,
        )
        return 1
    print(authorized)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baseline_authorization")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-sha")
    check.add_argument("--candidate-sha", required=True)
    args = parser.parse_args(argv)
    if args.command == "check-sha":
        return check_sha(args.candidate_sha, sys.stdin.read())
    print("unknown command", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
