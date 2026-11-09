"""The audit-lane runtime-pins verb reports ENTRY_FILL_MODEL and nothing else new."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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
    assert '/usr/bin/python3 -B - "/proc/$pid/environ"' in patched
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


def test_install_refuses_non_root_without_writing(tmp_path: Path):
    dispatcher = tmp_path / "afs-audit-dispatcher"
    dispatcher.write_text(_dispatcher(), encoding="utf-8")
    before = dispatcher.read_bytes()
    with pytest.raises(PermissionError, match="root"):
        install_runtime_pins(
            dispatcher,
            module_source=REPO / "ops" / "grok_audit_runtime_pins.py",
            dest_lib=tmp_path / "lib",
            euid=1000,
            stamp="20261109T000000Z",
        )
    assert dispatcher.read_bytes() == before
    assert not (tmp_path / "lib").exists()


def test_install_as_root_copies_the_module_and_patches_the_verb(tmp_path: Path):
    dispatcher = tmp_path / "afs-audit-dispatcher"
    original = _dispatcher()
    dispatcher.write_text(original, encoding="utf-8")
    dest = tmp_path / "lib"
    backup = install_runtime_pins(
        dispatcher,
        module_source=REPO / "ops" / "grok_audit_runtime_pins.py",
        dest_lib=dest,
        euid=0,
        stamp="20261109T000000Z",
    )
    installed = dest / "runtime_pins.py"
    assert installed.read_bytes() == (REPO / "ops" / "grok_audit_runtime_pins.py").read_bytes()
    assert "import runtime_pins" in dispatcher.read_text(encoding="utf-8")
    assert Path(backup).read_text(encoding="utf-8") == original
    assert SCANNER_CASE in dispatcher.read_text(encoding="utf-8")


def test_cli_rejects_any_dispatcher_path_except_the_vps_one(capsys):
    assert main(["install", "--dispatcher", "/tmp/afs-audit-dispatcher"]) == 2
    assert "usage:" in capsys.readouterr().err
