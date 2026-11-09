"""The audit-lane runtime-pins verb reports ENTRY_FILL_MODEL and nothing else new."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

import ops.grok_audit_runtime_pins as pins
from ops.grok_audit_runtime_pins import (
    EXPECTED_RUNTIME_PINS_PYTHON,
    RUNTIME_PIN_KEYS,
    apply_settings_default,
    code_default_from_settings,
    fill_model_entry_from_yaml,
    format_runtime_pins,
    install_runtime_pins,
    main,
    patch_dispatcher_text,
    report_from_environ_path,
    restore_checked,
)

REPO = Path(__file__).resolve().parents[1]

OLD_BODY = """\
import sys

raw = open(sys.argv[1], "rb").read().split(b"\\0")
env = {}
for item in raw:
    if b"=" in item:
        k, v = item.split(b"=", 1)
        env[k.decode(errors="replace")] = v.decode(errors="replace")

allowed = [
    "BROKER",
    "TRADOVATE_ENV",
    "PAPER_MODE",
    "LIVE_TRADING_ENABLED",
    "SCHEDULE_MODE",
    "MAX_CONTRACTS_HARD_CAP",
    "WIDE_STOP_LEDGER_MODE",
    "WIDE_STOP_LEDGER_EXECUTION_ROUTE",
    "WIDE_STOP_DEMO_EXECUTION_ENABLED",
    "BLOCK_RESTRICTED_REGIME",
]

for key in allowed:
    print(f"{key}={env.get(key, '<unset>')}")

actual = env.get("TRADOVATE_EXPECTED_ACCOUNT_ID")
proof = env.get("EXPECTED_TRADOVATE_ACCOUNT_ID")

print(f"TRADOVATE_ACCOUNT_PIN_SET={bool(actual)}")
print(f"EXPECTED_ACCOUNT_PIN_SET={bool(proof)}")
print(
    "ACCOUNT_PIN_MATCH="
    + ("true" if actual and proof and actual == proof else "false")
)\
"""

SCANNER_CASE = """\
    scanner-runtime-pins)
        python3 - "/proc/$pid/environ" <<'PY'
allowed = ["OPTIONS_SCANNER_ENABLED", "OPTIONS_PAPER_V1_COLLECTION_ENABLED"]
print("kept")
PY
        ;;
"""


def _dispatcher(body: str = OLD_BODY) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'cmd="${1:-help}"\n'
        'case "$cmd" in\n'
        "    runtime-pins)\n"
        '        pid="$(/usr/bin/systemctl show futures-bot.service -p MainPID --value)"\n'
        '        test "$pid" -gt 0\n'
        "\n"
        '        /usr/bin/python3 - "/proc/$pid/environ" <<\'PYENV\'\n'
        f"{body}\n"
        "PYENV\n"
        "        ;;\n"
        f"{SCANNER_CASE}"
        "esac\n"
    )


def _output_keys(text: str) -> list[str]:
    return [line.split("=", 1)[0] for line in text.splitlines() if line]


def test_allowlist_adds_only_entry_fill_model():
    assert RUNTIME_PIN_KEYS[-1] == "ENTRY_FILL_MODEL"
    assert "WEBHOOK_SECRET" not in RUNTIME_PIN_KEYS
    assert "TRADOVATE_PASSWORD" not in RUNTIME_PIN_KEYS
    assert "TRADOVATE_EXPECTED_ACCOUNT_ID" not in RUNTIME_PIN_KEYS
    assert RUNTIME_PIN_KEYS.count("ENTRY_FILL_MODEL") == 1


def test_process_value_is_reported_and_other_env_keys_do_not_leak():
    secret = "webhook-secret-value"
    password = "tradovate-password-value"
    api_key = "polygon-api-key-value"
    account = "account-id-should-not-print"
    extra = "unlisted-env-value"
    text = format_runtime_pins(
        {
            "BROKER": "tradovate",
            "ENTRY_FILL_MODEL": "ioc_limit",
            "WEBHOOK_SECRET": secret,
            "TRADOVATE_PASSWORD": password,
            "POLYGON_API_KEY": api_key,
            "TRADOVATE_EXPECTED_ACCOUNT_ID": account,
            "EXPECTED_TRADOVATE_ACCOUNT_ID": account,
            "SOME_OTHER_ENV": extra,
        },
        resolved_default="market",
    )
    assert "ENTRY_FILL_MODEL=ioc_limit" in text.splitlines()
    assert "BROKER=tradovate" in text.splitlines()
    assert "ENTRY_FILL_MODEL_RESOLVED_DEFAULT=market" in text.splitlines()
    assert "TRADOVATE_ACCOUNT_PIN_SET=True" in text.splitlines()
    assert "EXPECTED_ACCOUNT_PIN_SET=True" in text.splitlines()
    assert "ACCOUNT_PIN_MATCH=true" in text.splitlines()
    for leaked in (secret, password, api_key, account, extra, "WEBHOOK_SECRET", "SOME_OTHER_ENV"):
        assert leaked not in text
    assert _output_keys(text) == [
        *RUNTIME_PIN_KEYS,
        "ENTRY_FILL_MODEL_RESOLVED_DEFAULT",
        "TRADOVATE_ACCOUNT_PIN_SET",
        "EXPECTED_ACCOUNT_PIN_SET",
        "ACCOUNT_PIN_MATCH",
    ]


def test_unset_entry_fill_model_prints_unset_and_the_settings_default():
    text = format_runtime_pins({}, resolved_default="market")
    assert "ENTRY_FILL_MODEL=<unset>" in text.splitlines()
    assert "ENTRY_FILL_MODEL_RESOLVED_DEFAULT=market" in text.splitlines()
    assert "TRADOVATE_ACCOUNT_PIN_SET=False" in text.splitlines()
    assert "ACCOUNT_PIN_MATCH=false" in text.splitlines()


def test_shipped_settings_default_stays_market_and_yaml_has_no_override():
    source = (REPO / "config" / "settings.py").read_text(encoding="utf-8")
    rules = (REPO / "risk_rules.yaml").read_text(encoding="utf-8")
    absent, empty_fallback = code_default_from_settings(source)
    assert absent == "market"
    assert empty_fallback == "market"
    assert fill_model_entry_from_yaml(rules) is None
    assert apply_settings_default(absent, empty_fallback, None) == "market"


def test_yaml_override_is_the_resolved_default_without_using_process_env():
    rules = """\
fill_model:
  slippage_ticks: 1
  entry_fill_model: "IoC_Limit"  # not a secret
other:
  entry_fill_model: market
  webhook_secret: should-not-be-read
"""
    assert fill_model_entry_from_yaml(rules) == "IoC_Limit"
    assert apply_settings_default("market", "market", "IoC_Limit") == "ioc_limit"


def test_null_yaml_uses_the_or_market_fallback_not_a_different_code_default():
    rules = "fill_model:\n  entry_fill_model: null\n"
    assert fill_model_entry_from_yaml(rules) == ""
    assert apply_settings_default("ioc_limit", "market", "") == "market"


def test_report_reads_the_process_environ_and_release_files(tmp_path: Path):
    release = tmp_path / "release"
    (release / "config").mkdir(parents=True)
    settings = '''entry_fill_model=str(
    os.getenv("ENTRY_FILL_MODEL", fill_model.get("entry_fill_model", "market"))
    or "market"
).strip().lower()
'''
    (release / "config" / "settings.py").write_text(settings, encoding="utf-8")
    (release / "risk_rules.yaml").write_text(
        "fill_model:\n  entry_fill_model: ioc_limit\n",
        encoding="utf-8",
    )
    proc = tmp_path / "proc" / "42"
    proc.mkdir(parents=True)
    os.symlink(release, proc / "cwd")
    secret = "do-not-leak-this-token"
    (proc / "environ").write_bytes(
        b"\0".join(
            [
                b"ENTRY_FILL_MODEL=stop_market",
                b"BROKER=paper",
                f"WEBHOOK_SECRET={secret}".encode(),
                b"",
            ]
        )
    )
    text = report_from_environ_path(str(proc / "environ"))
    assert "ENTRY_FILL_MODEL=stop_market" in text.splitlines()
    assert "ENTRY_FILL_MODEL_RESOLVED_DEFAULT=ioc_limit" in text.splitlines()
    assert "BROKER=paper" in text.splitlines()
    assert secret not in text
    assert "WEBHOOK_SECRET" not in text


def test_unreadable_settings_still_reports_the_process_value(tmp_path: Path):
    proc = tmp_path / "proc" / "7"
    proc.mkdir(parents=True)
    os.symlink(tmp_path / "missing-release", proc / "cwd")
    (proc / "environ").write_bytes(b"ENTRY_FILL_MODEL=market\0")
    text = report_from_environ_path(str(proc / "environ"))
    assert "ENTRY_FILL_MODEL=market" in text.splitlines()
    assert "ENTRY_FILL_MODEL_RESOLVED_DEFAULT=<unreadable>" in text.splitlines()


def test_patch_inserts_the_helper_and_leaves_the_scanner_verb_alone():
    original = _dispatcher()
    patched = patch_dispatcher_text(original)
    assert patched != original
    assert "import runtime_pins" in patched
    assert "/usr/local/lib/afs-runtime-pins" in patched
    assert 'cd / && /usr/bin/python3 -I -B - "/proc/$pid/environ"' in patched
    assert '/usr/bin/python3 - "/proc/$pid/environ"' not in patched
    assert "BLOCK_RESTRICTED_REGIME" not in patched.split("scanner-runtime-pins)", 1)[0]
    assert SCANNER_CASE in patched
    assert patch_dispatcher_text(patched) == patched


def test_patch_refuses_an_unexpected_allowlist():
    original = _dispatcher(OLD_BODY.replace('"BROKER",', '"BROKER",\n    "WEBHOOK_SECRET",'))
    with pytest.raises(ValueError, match="dispatcher left unchanged"):
        patch_dispatcher_text(original)


def test_expected_block_matches_the_fixture():
    assert EXPECTED_RUNTIME_PINS_PYTHON == OLD_BODY
    assert "ENTRY_FILL_MODEL" not in EXPECTED_RUNTIME_PINS_PYTHON


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install(dispatcher: Path, tmp_path: Path, **kwargs):
    expect = kwargs.pop("expect_sha256", _sha256(dispatcher))
    return install_runtime_pins(
        dispatcher,
        module_source=REPO / "ops" / "grok_audit_runtime_pins.py",
        dest_lib=kwargs.pop("dest_lib", tmp_path / "lib"),
        euid=kwargs.pop("euid", 0),
        stamp=kwargs.pop("stamp", "20261109T000000Z"),
        expect_sha256=expect,
        backup_dir=kwargs.pop("backup_dir", tmp_path / "backups"),
    )


def test_install_refuses_non_root_without_writing(tmp_path: Path):
    dispatcher = tmp_path / "afs-audit-dispatcher"
    dispatcher.write_text(_dispatcher(), encoding="utf-8")
    before = dispatcher.read_bytes()
    with pytest.raises(PermissionError, match="root"):
        _install(dispatcher, tmp_path, euid=1000)
    assert dispatcher.read_bytes() == before
    assert not (tmp_path / "lib").exists()


def test_install_as_root_copies_the_module_and_patches_the_verb(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pins, "_assert_lib_dir", lambda path: None)
    dispatcher = tmp_path / "afs-audit-dispatcher"
    original = _dispatcher()
    dispatcher.write_text(original, encoding="utf-8")
    dispatcher.chmod(0o750)
    result = _install(dispatcher, tmp_path)
    installed = tmp_path / "lib" / "runtime_pins.py"
    assert installed.read_bytes() == (REPO / "ops" / "grok_audit_runtime_pins.py").read_bytes()
    assert "import runtime_pins" in dispatcher.read_text(encoding="utf-8")
    backup = Path(result.backup)
    assert backup.read_bytes() == original.encode("utf-8")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(dispatcher.stat().st_mode) == 0o750
    assert "/usr/local/sbin" not in str(backup)
    assert result.dispatcher_sha256 == _sha256(dispatcher)
    assert result.module_sha256 == _sha256(installed)
    assert result.original_mode == 0o750
    assert SCANNER_CASE in dispatcher.read_text(encoding="utf-8")


def test_cli_rejects_any_dispatcher_path_except_the_vps_one(capsys):
    assert main(["install", "--dispatcher", "/tmp/afs-audit-dispatcher"]) == 2
    assert "usage:" in capsys.readouterr().err
    assert main(["install", "--dispatcher", "/usr/local/sbin/afs-audit-dispatcher"]) == 2


def test_cli_rejects_a_tmp_dispatcher_even_with_expect_sha256(capsys):
    assert main([
        "install",
        "--dispatcher",
        "/tmp/afs-audit-dispatcher",
        "--expect-sha256",
        "ab" * 32,
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""


def test_control_characters_cannot_forge_output_lines():
    forged = "market\nACCOUNT_PIN_MATCH=true\nEVIL=1"
    text = format_runtime_pins({"ENTRY_FILL_MODEL": forged, "BROKER": "paper\r\x1b[2K"}, "market")
    assert text.count("ACCOUNT_PIN_MATCH=") == 1
    assert "ACCOUNT_PIN_MATCH=false" in text.splitlines()
    assert "ACCOUNT_PIN_MATCH=true" not in text
    assert "EVIL=" not in text
    assert "ENTRY_FILL_MODEL=<unprintable>" in text.splitlines()
    assert "BROKER=<unprintable>" in text.splitlines()
    assert "\x1b" not in text
    ansi = format_runtime_pins({}, "\x1b[31mmarket")
    assert "ENTRY_FILL_MODEL_RESOLVED_DEFAULT=<unprintable>" in ansi.splitlines()
    assert "\x1b" not in ansi


def test_duplicate_environ_keys_keep_the_first_value():
    raw = b"ENTRY_FILL_MODEL=first\0ENTRY_FILL_MODEL=second\0BROKER=paper\0"
    assert pins.parse_environ(raw)["ENTRY_FILL_MODEL"] == "first"


def test_empty_environ_does_not_fall_back_to_the_caller(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENTRY_FILL_MODEL", "from-caller")
    monkeypatch.setenv("BROKER", "from-caller")
    proc = tmp_path / "proc" / "9"
    proc.mkdir(parents=True)
    os.symlink(tmp_path / "missing-release", proc / "cwd")
    (proc / "environ").write_bytes(b"")
    text = report_from_environ_path(str(proc / "environ"))
    assert "ENTRY_FILL_MODEL=<unset>" in text.splitlines()
    assert "BROKER=<unset>" in text.splitlines()
    assert "from-caller" not in text


def test_newline_and_overlong_defaults_are_unreadable():
    assert apply_settings_default("market", "market", "mar\nket") == "<unreadable>"
    assert apply_settings_default("market", "market", "m" * 65) == "<unreadable>"
    assert apply_settings_default("market", "market", "m" * 64) == "m" * 64


def test_symlinked_settings_is_not_followed(tmp_path: Path):
    outside = tmp_path / "outside-settings.py"
    outside.write_text(
        'entry_fill_model=str(\n'
        '    os.getenv("ENTRY_FILL_MODEL", fill_model.get("entry_fill_model", "outside_marker"))\n'
        '    or "market"\n'
        ').strip().lower()\n',
        encoding="utf-8",
    )
    release = tmp_path / "release"
    (release / "config").mkdir(parents=True)
    (release / "config" / "settings.py").symlink_to(outside)
    (release / "risk_rules.yaml").write_text("fill_model:\n", encoding="utf-8")
    proc = tmp_path / "proc" / "3"
    proc.mkdir(parents=True)
    os.symlink(release, proc / "cwd")
    (proc / "environ").write_bytes(b"ENTRY_FILL_MODEL=market\0")
    text = report_from_environ_path(str(proc / "environ"))
    assert "ENTRY_FILL_MODEL_RESOLVED_DEFAULT=<unreadable>" in text.splitlines()
    assert "outside_marker" not in text


def test_environ_over_the_cap_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pins, "_MAX_ENVIRON", 32)
    proc = tmp_path / "proc" / "4"
    proc.mkdir(parents=True)
    os.symlink(tmp_path / "missing-release", proc / "cwd")
    (proc / "environ").write_bytes(b"ENTRY_FILL_MODEL=" + b"X" * 64)
    with pytest.raises(OSError, match="too large"):
        report_from_environ_path(str(proc / "environ"))


def test_patch_refuses_two_copies_of_the_expected_block():
    with pytest.raises(ValueError, match="dispatcher left unchanged"):
        patch_dispatcher_text(_dispatcher() + _dispatcher())


def test_install_refuses_a_sha256_mismatch(tmp_path: Path):
    dispatcher = tmp_path / "afs-audit-dispatcher"
    payload = _dispatcher().encode("utf-8")
    dispatcher.write_bytes(payload)
    with pytest.raises(ValueError, match="sha256"):
        _install(dispatcher, tmp_path, expect_sha256="0" * 64)
    assert dispatcher.read_bytes() == payload
    assert not (tmp_path / "lib").exists()
    assert not (tmp_path / "backups").exists()


def test_install_refuses_a_user_owned_lib_dir(tmp_path: Path):
    dispatcher = tmp_path / "afs-audit-dispatcher"
    dispatcher.write_text(_dispatcher(), encoding="utf-8")
    before = dispatcher.read_bytes()
    lib = tmp_path / "lib"
    lib.mkdir()
    with pytest.raises(ValueError, match="root:root"):
        _install(dispatcher, tmp_path, dest_lib=lib)
    assert dispatcher.read_bytes() == before


def test_lib_dir_rejects_group_writable_even_when_root_owned(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    real = os.lstat(lib)
    fake = os.stat_result((
        stat.S_IFDIR | 0o775,
        real.st_ino,
        real.st_dev,
        real.st_nlink,
        0,
        0,
        real.st_size,
        real.st_atime,
        real.st_mtime,
        real.st_ctime,
    ))
    original = Path.lstat

    def lstat(self):
        if self == lib:
            return fake
        return original(self)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="group or world writable"):
        pins._assert_lib_dir(lib)


def test_install_refuses_a_symlink_dispatcher(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pins, "_assert_lib_dir", lambda path: None)
    real = tmp_path / "real-dispatcher"
    payload = _dispatcher().encode("utf-8")
    real.write_bytes(payload)
    link = tmp_path / "afs-audit-dispatcher"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        _install(link, tmp_path, expect_sha256=hashlib.sha256(payload).hexdigest())
    assert real.read_bytes() == payload
    assert not (tmp_path / "lib").exists()
    assert not (tmp_path / "backups").exists()


def test_install_refuses_a_backup_inside_sbin(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pins, "_assert_lib_dir", lambda path: None)
    dispatcher = tmp_path / "afs-audit-dispatcher"
    dispatcher.write_text(_dispatcher(), encoding="utf-8")
    with pytest.raises(ValueError, match="outside /usr/local/sbin"):
        _install(dispatcher, tmp_path, backup_dir=Path("/usr/local/sbin/backups"))
    assert "import runtime_pins" not in dispatcher.read_text(encoding="utf-8")


def test_bash_n_failure_restores_the_dispatcher(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pins, "_assert_lib_dir", lambda path: None)
    monkeypatch.setattr(pins, "patch_dispatcher_text", lambda text: text + "\n(\n")
    dispatcher = tmp_path / "afs-audit-dispatcher"
    payload = _dispatcher().encode("utf-8")
    dispatcher.write_bytes(payload)
    with pytest.raises(ValueError, match="bash -n"):
        _install(dispatcher, tmp_path)
    assert dispatcher.read_bytes() == payload


def test_root_install_records_the_original_owner(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(pins, "_assert_lib_dir", lambda path: None)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: calls.append((uid, gid)))
    dispatcher = tmp_path / "afs-audit-dispatcher"
    dispatcher.write_text(_dispatcher(), encoding="utf-8")
    identity = dispatcher.stat()
    _install(dispatcher, tmp_path)
    assert (identity.st_uid, identity.st_gid) in calls


def test_rollback_restores_only_when_the_backup_sha_matches(tmp_path: Path):
    dispatcher = tmp_path / "afs-audit-dispatcher"
    original = _dispatcher().encode("utf-8")
    dispatcher.write_bytes(original)
    dispatcher.chmod(0o750)
    backup = tmp_path / "backup"
    backup.write_bytes(original)
    expect = hashlib.sha256(original).hexdigest()
    dispatcher.write_bytes(b"patched\n")
    with pytest.raises(ValueError, match="sha256"):
        restore_checked(
            backup,
            dispatcher,
            expect_sha256="ab" * 32,
            mode=0o750,
            uid=os.getuid(),
            gid=os.getgid(),
            euid=0,
        )
    assert dispatcher.read_bytes() == b"patched\n"
    after = restore_checked(
        backup,
        dispatcher,
        expect_sha256=expect,
        mode=0o750,
        uid=os.getuid(),
        gid=os.getgid(),
        euid=0,
    )
    assert after == expect
    assert dispatcher.read_bytes() == original
    assert stat.S_IMODE(dispatcher.stat().st_mode) == 0o750
    assert backup.read_bytes() == original


def test_patched_interpreter_ignores_a_cwd_stdlib_shadow(tmp_path: Path):
    patched = patch_dispatcher_text(_dispatcher())
    assert "cd / && /usr/bin/python3 -I -B -" in patched
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "ast.py").write_text("import sys\nsys.exit(9)\n", encoding="utf-8")
    completed = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", "-"],
        input="import ast\nprint(ast.__file__)\n",
        text=True,
        cwd=evil,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "evil" not in completed.stdout
    shadowed = subprocess.run(
        ["/usr/bin/python3", "-B", "-"],
        input="import ast\nprint('shadowed')\n",
        text=True,
        cwd=evil,
        capture_output=True,
        check=False,
    )
    assert shadowed.returncode == 9


@pytest.mark.parametrize("char", ["\u0085", "\u2028", "\u009b", "\u202e"])
def test_non_ascii_nonprintable_characters_are_not_emitted(char: str):
    text = format_runtime_pins({"ENTRY_FILL_MODEL": f"market{char}MORE"}, "market")
    assert char not in text
    assert "MORE" not in text
    assert "ENTRY_FILL_MODEL=<unprintable>" in text.splitlines()
    assert apply_settings_default("market", "market", f"market{char}x") == "<unreadable>"


def test_settings_symlink_inside_the_release_is_not_followed(tmp_path: Path):
    release = tmp_path / "release"
    (release / "config").mkdir(parents=True)
    real = release / "config" / "settings_real.py"
    real.write_text(
        "entry_fill_model=str(\n"
        '    os.getenv("ENTRY_FILL_MODEL", fill_model.get("entry_fill_model", "symlink_followed"))\n'
        '    or "market"\n'
        ").strip().lower()\n",
        encoding="utf-8",
    )
    (release / "config" / "settings.py").symlink_to(real.name)
    (release / "risk_rules.yaml").write_text("fill_model:\n", encoding="utf-8")
    proc = tmp_path / "proc" / "11"
    proc.mkdir(parents=True)
    os.symlink(release, proc / "cwd")
    (proc / "environ").write_bytes(b"ENTRY_FILL_MODEL=market\0")
    text = report_from_environ_path(str(proc / "environ"))
    assert "ENTRY_FILL_MODEL_RESOLVED_DEFAULT=<unreadable>" in text.splitlines()
    assert "symlink_followed" not in text


def test_rollback_refuses_non_root(tmp_path: Path):
    backup = tmp_path / "backup"
    dispatcher = tmp_path / "afs-audit-dispatcher"
    payload = b"original-dispatcher\n"
    backup.write_bytes(payload)
    dispatcher.write_bytes(b"patched\n")
    with pytest.raises(PermissionError, match="root"):
        restore_checked(
            backup,
            dispatcher,
            expect_sha256=hashlib.sha256(payload).hexdigest(),
            mode=0o750,
            uid=os.getuid(),
            gid=os.getgid(),
            euid=1000,
        )
    assert dispatcher.read_bytes() == b"patched\n"
    assert backup.read_bytes() == payload


def test_rollback_rejects_a_sha_mismatch_after_the_write(tmp_path: Path, monkeypatch):
    backup = tmp_path / "backup"
    dispatcher = tmp_path / "afs-audit-dispatcher"
    payload = b"original-dispatcher\n"
    backup.write_bytes(payload)
    dispatcher.write_bytes(b"patched\n")

    def write_other(path: Path, data: bytes, *, mode: int, uid: int, gid: int) -> None:
        path.write_bytes(b"not-the-backup\n")

    monkeypatch.setattr(pins, "_replace_file", write_other)
    with pytest.raises(ValueError, match="after rollback"):
        restore_checked(
            backup,
            dispatcher,
            expect_sha256=hashlib.sha256(payload).hexdigest(),
            mode=0o750,
            uid=os.getuid(),
            gid=os.getgid(),
            euid=0,
        )
