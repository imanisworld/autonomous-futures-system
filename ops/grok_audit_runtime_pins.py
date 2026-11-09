#!/usr/bin/env python3
"""Read-only ENTRY_FILL_MODEL pin for the runtime-pins verb.

The futures-bot dispatcher prints an allowlist of process environment keys.
ENTRY_FILL_MODEL is one of those keys. This module also reports the default
``config/settings.py`` uses when that variable is absent.

It does not import ``config.settings``. ``load_config()`` calls ``load_dotenv``
and can raise ``LiveTradingBlockedError``. The default is read from the
release tree the running process is using: the ``os.getenv`` fallback in
``config/settings.py``, then ``risk_rules.yaml`` ``fill_model.entry_fill_model``.

Nothing here places an order, restarts a service, or opens ``.env``.
"""

from __future__ import annotations

import ast
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

RUNTIME_PIN_KEYS = (
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
    "ENTRY_FILL_MODEL",
)

UNSET = "<unset>"
UNREADABLE = "<unreadable>"
INSTALLED_LIB = Path("/usr/local/lib/afs-runtime-pins")
_DISPATCHER_BASENAME = re.compile(r"afs-[a-z0-9-]{1,64}")
_MAX_TEXT = 2_000_000
_MAX_ENVIRON = 1_000_000
_PATCH_REFUSAL = (
    "runtime-pins block was not the expected allowlist; dispatcher left unchanged"
)

# The inline Python the live runtime-pins verb carried before
# ENTRY_FILL_MODEL was added. The installer replaces this exact block.
EXPECTED_RUNTIME_PINS_PYTHON = """\
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

_OLD_INVOCATION = '/usr/bin/python3 - "/proc/$pid/environ" <<\'PYENV\''
_NEW_INVOCATION = '/usr/bin/python3 -B - "/proc/$pid/environ" <<\'PYENV\''
_NEW_PYTHON = """\
import sys

sys.path.insert(0, "/usr/local/lib/afs-runtime-pins")
import runtime_pins

sys.stdout.write(runtime_pins.report_from_environ_path(sys.argv[1]))\
"""

_STAMP = re.compile(r"[0-9]{8}T[0-9]{6}Z")
_BARE_SCALAR = re.compile(r"[A-Za-z0-9_./+-]+")


def parse_environ(raw: bytes) -> dict[str, str]:
    """Split a NUL-delimited process environ. Values are not filtered here."""
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode(errors="replace")] = value.decode(errors="replace")
    return env


def format_runtime_pins(env: Mapping[str, str], resolved_default: str) -> str:
    """Print the allowlist, the settings default, and account-pin booleans.

    Account ids are not printed. Keys outside ``RUNTIME_PIN_KEYS`` are not
    printed. ``resolved_default`` is the settings fallback, not the process value.
    """
    if "\n" in resolved_default or "\r" in resolved_default:
        resolved_default = UNREADABLE
    lines = [f"{key}={env.get(key, UNSET)}" for key in RUNTIME_PIN_KEYS]
    lines.append(f"ENTRY_FILL_MODEL_RESOLVED_DEFAULT={resolved_default}")
    actual = env.get("TRADOVATE_EXPECTED_ACCOUNT_ID")
    proof = env.get("EXPECTED_TRADOVATE_ACCOUNT_ID")
    lines.append(f"TRADOVATE_ACCOUNT_PIN_SET={bool(actual)}")
    lines.append(f"EXPECTED_ACCOUNT_PIN_SET={bool(proof)}")
    match = "true" if actual and proof and actual == proof else "false"
    lines.append(f"ACCOUNT_PIN_MATCH={match}")
    return "\n".join(lines) + "\n"


def code_default_from_settings(source: str) -> tuple[str, str]:
    """Return the ``.get`` default and the ``or`` fallback from settings.py.

    Matches ``str(os.getenv("ENTRY_FILL_MODEL", fill_model.get(..., "market")) or "market")``.
    """
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        if len(node.values) != 2:
            continue
        left, right = node.values
        if not isinstance(right, ast.Constant) or not isinstance(right.value, str):
            continue
        if not _is_entry_fill_getenv(left):
            continue
        get_call = left.args[1]
        if not isinstance(get_call, ast.Call):
            raise ValueError("ENTRY_FILL_MODEL fallback is not a call")
        default = _get_default_literal(get_call)
        found.append((default, right.value))
    if len(found) != 1:
        raise ValueError("ENTRY_FILL_MODEL default not found in settings.py")
    return found[0]


def apply_settings_default(
    code_default: str,
    empty_fallback: str,
    yaml_value: str | None,
) -> str:
    """Apply the settings.py expression when ``ENTRY_FILL_MODEL`` is unset.

    ``yaml_value is None`` means ``fill_model`` has no ``entry_fill_model`` key,
    so the ``.get`` default is used. A present empty value uses the ``or`` fallback.
    """
    chosen = code_default if yaml_value is None else yaml_value
    text = str(chosen or empty_fallback or "market").strip().lower()
    if "\n" in text or "\r" in text or len(text) > 64:
        return UNREADABLE
    return text or "market"


def fill_model_entry_from_yaml(text: str) -> str | None:
    """Return the ``fill_model.entry_fill_model`` scalar, or None if absent.

    An empty or null scalar is ``""`` so the caller can tell it from a missing key.
    Other sections are ignored. A tab, a duplicate, or a non-scalar raises ValueError.
    """
    lines = text.splitlines()
    found_sections = 0
    value: str | None = None
    seen = False
    index = 0
    while index < len(lines):
        indent, body = _yaml_body(lines[index])
        if body == "fill_model:" and indent == 0:
            found_sections += 1
            if found_sections > 1:
                raise ValueError("multiple fill_model sections")
            index += 1
            child_indent: int | None = None
            while index < len(lines):
                child_indent_now, child = _yaml_body(lines[index])
                if not child:
                    index += 1
                    continue
                if child_indent_now == 0:
                    break
                if child_indent is None:
                    child_indent = child_indent_now
                key, sep, rest = child.partition(":")
                if child_indent_now == child_indent and sep and key == "entry_fill_model":
                    if seen:
                        raise ValueError("duplicate entry_fill_model")
                    seen = True
                    value = _parse_scalar(rest.strip())
                index += 1
            continue
        index += 1
    if not seen:
        return None
    return value


def resolved_default_from_release(root: Path) -> str:
    """Resolve the default from one release tree. Does not read process environ."""
    settings = _read_release_text(root, "config/settings.py")
    rules = _read_release_text(root, "risk_rules.yaml")
    code_default, empty_fallback = code_default_from_settings(settings)
    yaml_value = fill_model_entry_from_yaml(rules)
    return apply_settings_default(code_default, empty_fallback, yaml_value)


def report_from_environ_path(environ_path: str) -> str:
    """Report pins for one ``/proc/<pid>/environ`` file.

    The release root is that process's ``cwd``. A missing or unreadable
    settings file still reports the process value and marks the default
    ``<unreadable>``.
    """
    path = Path(environ_path)
    raw = path.read_bytes()
    if len(raw) > _MAX_ENVIRON:
        raise OSError("environ too large")
    env = parse_environ(raw)
    try:
        root = Path(os.path.realpath(path.parent / "cwd"))
        default = resolved_default_from_release(root)
    except (OSError, ValueError, SyntaxError, UnicodeError):
        default = UNREADABLE
    return format_runtime_pins(env, default)


def patch_dispatcher_text(text: str) -> str:
    """Replace the inline runtime-pins Python with an import of this module.

    The scanner-runtime-pins verb is a different heredoc and is not edited.
    A dispatcher that does not contain the expected block is refused.
    """
    already = _NEW_PYTHON in text and _NEW_INVOCATION in text
    if already and EXPECTED_RUNTIME_PINS_PYTHON not in text:
        return text
    if text.count(EXPECTED_RUNTIME_PINS_PYTHON) != 1 or text.count(_OLD_INVOCATION) != 1:
        raise ValueError(_PATCH_REFUSAL)
    if text.find(_OLD_INVOCATION) > text.find(EXPECTED_RUNTIME_PINS_PYTHON):
        raise ValueError(_PATCH_REFUSAL)
    return (
        text.replace(_OLD_INVOCATION, _NEW_INVOCATION, 1)
        .replace(EXPECTED_RUNTIME_PINS_PYTHON, _NEW_PYTHON, 1)
    )


def install_runtime_pins(
    dispatcher: Path,
    *,
    module_source: Path,
    dest_lib: Path,
    euid: int,
    stamp: str,
) -> str:
    """Copy this module into ``dest_lib`` and patch one audit dispatcher.

    Returns the backup path. An already patched dispatcher is left in place
    and the module file is still refreshed. Non-root callers write nothing.
    """
    if euid != 0:
        raise PermissionError("install must run as root")
    if not _STAMP.fullmatch(stamp):
        raise ValueError("install stamp is not a UTC timestamp")
    if _DISPATCHER_BASENAME.fullmatch(dispatcher.name) is None:
        raise ValueError(
            "dispatcher basename must match afs-<name>; dispatcher left unchanged"
        )
    if dispatcher.is_symlink():
        raise ValueError("refusing to patch a symlink; dispatcher left unchanged")
    original = dispatcher.read_text(encoding="utf-8")
    patched = patch_dispatcher_text(original)
    source_bytes = module_source.read_bytes()
    if len(source_bytes) > _MAX_TEXT:
        raise ValueError("module source is too large")
    dest_lib.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_lib, 0o755)
    module_dest = dest_lib / "runtime_pins.py"
    if module_dest.is_symlink():
        raise ValueError("refusing to replace a symlinked runtime_pins.py")
    _write_bytes(module_dest, source_bytes, 0o644)
    if patched == original:
        return ""
    backup = dispatcher.with_name(f"{dispatcher.name}.bak-{stamp}")
    if backup.exists():
        raise ValueError(f"backup already exists: {backup.name}")
    _write_bytes(backup, original.encode("utf-8"), 0o755)
    _write_bytes(dispatcher, patched.encode("utf-8"), 0o755)
    check = subprocess.run(
        ["bash", "-n", str(dispatcher)],
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        _write_bytes(dispatcher, original.encode("utf-8"), 0o755)
        raise ValueError("patched dispatcher failed bash -n; original restored")
    return str(backup)


def main(argv: list[str] | None = None) -> int:
    """Install the helper onto the VPS dispatcher. Root only.

    The dispatcher must live in ``/usr/local/sbin`` and its basename must
    match ``afs-<name>``. The caller passes that path. This file does not
    name the account.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    usage = (
        "usage: python3 -B -m ops.grok_audit_runtime_pins "
        "install --dispatcher /usr/local/sbin/<afs-dispatcher>"
    )
    if len(args) != 3 or args[0] != "install" or args[1] != "--dispatcher":
        print(usage, file=sys.stderr)
        return 2
    dispatcher = Path(args[2])
    if dispatcher.parent != Path("/usr/local/sbin") or _DISPATCHER_BASENAME.fullmatch(dispatcher.name) is None:
        print(usage, file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        backup = install_runtime_pins(
            dispatcher,
            module_source=Path(__file__),
            dest_lib=INSTALLED_LIB,
            euid=os.geteuid(),
            stamp=stamp,
        )
    except (PermissionError, ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if backup:
        print(f"patched {args[2]}; backup={backup}")
    else:
        print(f"dispatcher already patched; refreshed {INSTALLED_LIB / 'runtime_pins.py'}")
    return 0


def _is_entry_fill_getenv(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "getenv" or len(node.args) < 2:
        return False
    name = node.args[0]
    return isinstance(name, ast.Constant) and name.value == "ENTRY_FILL_MODEL"


def _get_default_literal(node: ast.Call) -> str:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
        raise ValueError("ENTRY_FILL_MODEL fallback is not .get")
    if len(node.args) != 2:
        raise ValueError("entry_fill_model get() arity")
    key, default = node.args
    if not isinstance(key, ast.Constant) or key.value != "entry_fill_model":
        raise ValueError("entry_fill_model get() key")
    if not isinstance(default, ast.Constant) or not isinstance(default.value, str):
        raise ValueError("entry_fill_model code default is not a string")
    if not default.value:
        raise ValueError("entry_fill_model code default is empty")
    return default.value


def _yaml_body(line: str) -> tuple[int, str]:
    if "\t" in line:
        raise ValueError("tab in risk_rules.yaml")
    indent = len(line) - len(line.lstrip(" "))
    body = _strip_yaml_comment(line).strip()
    return indent, body


def _strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def _parse_scalar(raw: str) -> str:
    if raw in {"", "~", "null", "Null", "NULL"}:
        return ""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        inner = raw[1:-1]
        if raw[0] in inner:
            raise ValueError("unsupported entry_fill_model scalar")
        return inner
    if _BARE_SCALAR.fullmatch(raw):
        return raw
    raise ValueError("unsupported entry_fill_model scalar")


def _read_release_text(root: Path, relative: str) -> str:
    root_real = root.resolve()
    path = root_real / relative
    if path.is_symlink() or not path.is_file():
        raise OSError("release file is not a regular file")
    resolved = path.resolve()
    resolved.relative_to(root_real)
    if path.stat().st_size > _MAX_TEXT:
        raise OSError("release file is too large")
    return path.read_text(encoding="utf-8")


def _write_bytes(path: Path, data: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(temporary), flags, mode)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    if path.exists() or path.is_symlink():
        os.chmod(path, stat.S_IMODE(mode))
    os.replace(temporary, path)
    os.chmod(path, mode)


if __name__ == "__main__":
    raise SystemExit(main())
