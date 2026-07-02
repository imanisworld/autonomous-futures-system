"""Build a deterministic, secret-free release manifest for the futures service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES, UNSET_PIN


SCHEMA_VERSION = 1
EXCLUDED_PREFIXES = ("data/", "logs/", ".git/", ".private-companion/")
EXCLUDED_NAMES = {".env", ".env.local"}


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL
    ).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_files(root: Path) -> list[str]:
    tracked = _git(root, "ls-files", "-z").split("\0")
    return sorted(
        name
        for name in tracked
        if name
        and Path(name).name not in EXCLUDED_NAMES
        and not name.startswith(EXCLUDED_PREFIXES)
        and (root / name).is_file()
    )


def _safe_runtime_overrides() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        observed = os.getenv(name)
        pin = os.getenv(f"EXPECTED_PROOF_{name}")
        result[name] = {
            "observed": observed,
            "expected": pin,
            "pinned": pin is not None,
            "matches": (
                observed is None if pin == UNSET_PIN else observed == pin
            ) if pin is not None else observed is None,
        }
    return result


def _safe_config_summary(rules: dict[str, Any]) -> dict[str, Any]:
    trading = rules.get("trading_mode") or {}
    limits = rules.get("daily_limits") or {}
    strategy = rules.get("strategy") or {}
    instruments = rules.get("instruments") or {}
    return {
        "trading_mode": {
            "live_trading_enabled": trading.get("live_trading_enabled"),
            "paper_mode": trading.get("paper_mode"),
        },
        "allowed_instruments": instruments.get("allowed"),
        "daily_limits": {
            "max_trades_per_day": limits.get("max_trades_per_day"),
            "max_consecutive_losses": limits.get("max_consecutive_losses"),
            "max_daily_loss": limits.get("max_daily_loss"),
        },
        "enabled_concepts": strategy.get("enabled_concepts") or [],
        "disabled_concepts_per_instrument": (
            strategy.get("disabled_concepts_per_instrument") or {}
        ),
    }


def build_release_manifest(
    repo_root: str | Path,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    risk_path = root / "risk_rules.yaml"
    rules = yaml.safe_load(risk_path.read_text(encoding="utf-8")) or {}
    files = {
        name: _sha256(root / name)
        for name in _release_files(root)
    }
    dirty_lines = [
        line for line in _git(root, "status", "--porcelain").splitlines() if line
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "repo": {
            "branch": (
                os.getenv("RELEASE_BRANCH")
                or _git(root, "rev-parse", "--abbrev-ref", "HEAD")
            ),
            "commit": _git(root, "rev-parse", "HEAD"),
            "dirty": bool(dirty_lines),
            "dirty_paths": dirty_lines,
        },
        "risk_rules_sha256": _sha256(risk_path),
        "source_files": files,
        "source_file_count": len(files),
        "config": _safe_config_summary(rules),
        "proof_critical_runtime_overrides": _safe_runtime_overrides(),
    }
    fingerprint_payload = dict(payload)
    fingerprint_payload.pop("generated_at")
    canonical = json.dumps(
        fingerprint_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload["fingerprint_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    manifest = build_release_manifest(args.repo_root)
    if manifest["repo"]["dirty"] and not args.allow_dirty:
        print("release manifest refused: working tree is dirty", file=__import__("sys").stderr)
        return 2
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
