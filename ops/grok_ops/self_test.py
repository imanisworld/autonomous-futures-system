"""Installer self-test for the grok-ops lane.

Prints 34/34 when every static guard holds. A dropped key option or a
sudoers wildcard fails the run.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ops.grok_ops.actions import BASELINE_RESET_REL, promote_argv
from ops.grok_ops.allowlist import CommandRejected, parse_command
from ops.grok_ops.auditlog import AuditLog
from ops.grok_ops.gates import FlatSnapshot, evaluate_gates
from ops.grok_ops.keys import DISPATCHER_PATH, KeyGuardError, render_authorized_keys_line, validate_authorized_key_line
from ops.grok_ops.sudoers import SUDO_PATHS, render_sudoers, validate_sudoers_text, visudo_check
from ops.grok_ops.tokens import TOKEN_TTL

_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGrokOpsSelfTestKeyMaterialOnly grok-ops"
_SHA = "c" * 40


def _check(name: str, fn) -> str | None:
    try:
        fn()
    except Exception as exc:
        return f"{name}: {exc}"
    return None


def _checks() -> list[tuple[str, object]]:
    line = render_authorized_keys_line(_PUBKEY)
    sudoers = render_sudoers()

    def _parse(raw: str, verb: str) -> None:
        assert parse_command(raw).verb == verb

    def _rejected(raw: str, reason: str) -> None:
        try:
            parse_command(raw)
        except CommandRejected as exc:
            if exc.reason != reason:
                raise AssertionError(exc.reason) from exc
            return
        raise AssertionError("accepted")

    def _audit_fields() -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = AuditLog(Path(directory) / "audit.log")
            log.append(actor="grok-ops", verb="status", sha="", token_id="", result="ok")
            row = Path(directory, "audit.log").read_text(encoding="utf-8")
            for field in ("actor", "ts", "verb", "sha", "token_id", "result"):
                if f'"{field}"' not in row:
                    raise AssertionError(field)

    def _guard_drop(option: str) -> None:
        mutated = line.replace(option, "dropped")
        try:
            validate_authorized_key_line(mutated)
        except KeyGuardError:
            return
        raise AssertionError("guard accepted a mutated key")

    checks = [
        ("preflight", lambda: _parse("preflight", "preflight")),
        ("backup", lambda: _parse("backup", "backup")),
        ("baseline-reset-dry-run", lambda: _parse("baseline-reset-dry-run", "baseline-reset-dry-run")),
        ("baseline-reset", lambda: _parse("baseline-reset", "baseline-reset")),
        ("promote", lambda: _parse(f"promote {_SHA}", "promote")),
        ("verify", lambda: _parse("verify", "verify")),
        ("rollback", lambda: _parse("rollback rel-1", "rollback")),
        ("status", lambda: _parse("status", "status")),
        ("unknown-verb", lambda: _rejected("shell", "unknown-verb")),
        ("semicolon", lambda: _rejected("status; id", "injection")),
        ("ampersands", lambda: _rejected("status && id", "injection")),
        ("backtick", lambda: _rejected("status `id`", "injection")),
        ("command-substitution", lambda: _rejected("status $(id)", "injection")),
        ("sudoers-count", lambda: _assert_len(sudoers)),
        ("sudoers-no-wildcard", lambda: _assert_absent(sudoers, "*")),
        ("sudoers-no-shell", lambda: (_assert_absent(sudoers, "/bin/bash"), _assert_absent(sudoers, "SETENV"))),
        ("visudo", lambda: visudo_check(sudoers)),
        ("key-command", lambda: _assert_present(line, "command=")),
        ("key-no-pty", lambda: _assert_present(line, "no-pty")),
        ("key-no-port-forwarding", lambda: _assert_present(line, "no-port-forwarding")),
        ("key-no-agent-forwarding", lambda: _assert_present(line, "no-agent-forwarding")),
        ("key-no-x11", lambda: _assert_present(line, "no-X11-forwarding")),
        ("key-restrict", lambda: _assert_present(line, "restrict")),
        ("key-drop-restrict", lambda: _guard_drop("restrict,")),
        ("key-command-path", lambda: _guard_drop(DISPATCHER_PATH)),
        ("token-ttl", lambda: _assert_ttl()),
        ("authorize-not-sudo", lambda: _assert_absent(sudoers, "afs-ops-authorize")),
        ("audit-fields", _audit_fields),
        ("no-force-lock", lambda: _assert_absent(" ".join(promote_argv("/sbin/atomic", _SHA)), "--force-lock")),
        ("baseline-path", lambda: _assert_equal(BASELINE_RESET_REL, "scripts/ops/baseline_reset.sh")),
        ("refusal-live", lambda: _assert_refusal("posture-live")),
        ("refusal-flat", lambda: _assert_refusal("flat-unreadable")),
        ("refusal-lock", lambda: _assert_refusal("deploy-lock")),
        ("refusal-shadow", lambda: _assert_refusal("shadow-active")),
    ]
    return checks


def _assert_len(sudoers: str) -> None:
    validate_sudoers_text(sudoers)
    if len(SUDO_PATHS) != 9:
        raise AssertionError(len(SUDO_PATHS))


def _assert_present(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(needle)


def _assert_absent(text: str, needle: str) -> None:
    if needle in text:
        raise AssertionError(needle)


def _assert_equal(left: str, right: str) -> None:
    if left != right:
        raise AssertionError(left)


def _assert_ttl() -> None:
    if int(TOKEN_TTL.total_seconds()) != 7200:
        raise AssertionError(TOKEN_TTL)


def _assert_refusal(code: str) -> None:
    status = evaluate_gates(
        env_text="LIVE_TRADING_ENABLED=true\nTRADOVATE_ENV=demo\n",
        env_unreadable=False,
        lock_exists=code == "deploy-lock",
        variants_text="VARIANTS_START = None\n" if code != "shadow-active" else "VARIANTS_START = object()\n",
        variants_unreadable=False,
        flat=None if code == "flat-unreadable" else FlatSnapshot(True, 0, True, 0),
    )
    if code not in status.refusals:
        raise AssertionError(status.refusals)


def run_self_test() -> tuple[int, int, list[str]]:
    checks = _checks()
    failures = [item for item in (_check(name, fn) for name, fn in checks) if item]
    total = len(checks)
    passed = total - len(failures)
    return passed, total, failures


def main() -> int:
    passed, total, failures = run_self_test()
    for failure in failures:
        print(failure)
    print(f"{passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
