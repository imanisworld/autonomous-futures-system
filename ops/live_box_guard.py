"""Read-only live-box drift guard."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_EXPECTED_EVIDENCE_SOURCE = "active_box_journal_and_status"
DEFAULT_EXPECTED_STATUS_PATHS = ("/status/today", "/status/broker-account")
UNSET_PIN = "<unset>"

# Environment values in this list bypass the risk_rules.yaml fingerprint and can
# change which setups trade, how they enter, position size, or how paper/live
# positions exit. Keep this explicit so adding an operational knob is deliberate.
PROOF_CRITICAL_RUNTIME_OVERRIDES = (
    "PAPER_MODE",
    "LIVE_TRADING_ENABLED",
    "BROKER",
    "TRADOVATE_ENV",
    "STARTING_BALANCE",
    "REQUIRE_TRENDING_CONDITION",
    "VWAP_ENTRY_MAX_DISTANCE_TICKS",
    "MOMENTUM_ENTRY_REANCHOR",
    "STRATEGY_FALLBACK_ENABLED",
    "FIVE_MIN_FEED_ENABLED",
    "SCHEDULE_MODE",
    "PRIMARY_DECISION_TF",
    "EXPECTED_TIMEFRAME_MINUTES",
    "MAX_CONTRACTS_HARD_CAP",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
    "SIGNA_GATE_ENFORCED",
    "STRICT_DIRECTIONAL_ALIGNMENT",
    "HTF_DIRECTION_MODE",
    "HTF_DIRECTION_SOURCE",
    "BLOCK_RESTRICTED_REGIME",
    "LIVE_QUOTE_ENABLED",
    "FILL_SLIPPAGE_TICKS",
    "FILL_PESSIMISTIC_BOTH_HIT",
    "BREAKEVEN_AT_1R",
    "RUNNER_MODE",
    "RUNNER_SHADOW_ENABLED",
    "RUNNER_LIVE_ENABLED",
    "RUNNER_ACTIVATION_R",
    "RUNNER_TRAIL_R",
)
WEBHOOK_SECRET_ENV_NAMES = (
    "WEBHOOK_SECRET",
    "TRADINGVIEW_WEBHOOK_SECRET",
    "TRADINGVIEW_WEBHOOK_SECRET_NEXT",
)


@dataclass(frozen=True)
class Comparison:
    name: str
    observed: str | None
    expected: str | None
    ok: bool
    required: bool = True
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "observed": self.observed,
            "expected": self.expected,
            "ok": self.ok,
            "required": self.required,
            "detail": self.detail,
        }


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _short(value: str | None, chars: int = 12) -> str:
    return value[:chars] if value else "unknown"


def _cmp(name: str, observed: str | None, expected: str | None, *, required: bool = True) -> Comparison:
    if expected is None:
        return Comparison(
            name=name,
            observed=observed,
            expected=None,
            ok=not required,
            required=required,
            detail="expected value is not pinned",
        )
    return Comparison(
        name=name,
        observed=observed,
        expected=expected,
        ok=observed == expected,
        required=required,
        detail="matches" if observed == expected else "mismatch",
    )


def _path_cmp(name: str, observed: str | Path | None, expected: str | None, *, required: bool = True) -> Comparison:
    observed_str = str(Path(observed).resolve()) if observed else None
    expected_str = str(Path(expected).resolve()) if expected else None
    return _cmp(name, observed_str, expected_str, required=required)


def _runtime_override_report() -> tuple[list[dict[str, Any]], list[Comparison], list[str]]:
    overrides: list[dict[str, Any]] = []
    comparisons: list[Comparison] = []
    unpinned: list[str] = []
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        observed = _env(name)
        pin_name = f"EXPECTED_PROOF_{name}"
        expected_raw = _env(pin_name)
        expected = None if expected_raw == UNSET_PIN else expected_raw
        pinned = expected_raw is not None

        if pinned:
            matches = observed == expected
            comparisons.append(Comparison(
                name=f"runtime_override:{name}",
                observed=observed,
                expected=expected_raw,
                ok=matches,
                required=True,
                detail="matches" if matches else "mismatch",
            ))
        elif observed is not None:
            # An active, unpinned override makes the proof box irreproducible.
            unpinned.append(name)

        overrides.append(
            {
                "name": name,
                "observed": observed,
                "active": observed is not None,
                "pin": pin_name,
                "pinned": pinned,
                "expected": expected_raw,
                "ok": (observed == expected) if pinned else observed is None,
            }
        )
    return overrides, comparisons, unpinned


def _env_bool_fail_closed(name: str) -> bool:
    """Mirror config parsing while treating unknown/blank values as disabled."""
    value = _env(name)
    return bool(value and value.lower() in {"1", "true", "yes"})


def _security_runtime_report(
    *,
    manual_controls_enabled: bool | None = None,
) -> dict[str, Any]:
    """Describe security-sensitive runtime state without returning secret material."""
    manual_from_runtime = manual_controls_enabled is not None
    manual_enabled = (
        bool(manual_controls_enabled)
        if manual_from_runtime
        else _env_bool_fail_closed("ENABLE_MANUAL_EXECUTION_CONTROLS")
    )

    secret_values = {name: _env(name) for name in WEBHOOK_SECRET_ENV_NAMES}
    configured = [name for name, value in secret_values.items() if value is not None]
    missing = [name for name, value in secret_values.items() if value is None]
    primary_configured = secret_values["WEBHOOK_SECRET"] is not None
    staged_names = [
        name for name in WEBHOOK_SECRET_ENV_NAMES[1:]
        if secret_values[name] is not None
    ]
    distinct_values = {value for value in secret_values.values() if value is not None}
    rotation_ready = primary_configured and any(
        secret_values[name] != secret_values["WEBHOOK_SECRET"]
        for name in staged_names
    )
    duplicate_configured = len(distinct_values) < len(configured)

    return {
        "ok": (not manual_enabled) and primary_configured,
        "status": "error" if manual_enabled or not primary_configured else (
            "ok" if rotation_ready else "warn"
        ),
        "manual_endpoint": {
            "effectively_inert": not manual_enabled,
            "effective_controls_enabled": manual_enabled,
            "env_name": "ENABLE_MANUAL_EXECUTION_CONTROLS",
            "env_present": _env("ENABLE_MANUAL_EXECUTION_CONTROLS") is not None,
            "evidence": "loaded_runtime_config" if manual_from_runtime else "process_environment",
        },
        "webhook_secret_rotation": {
            "primary_configured": primary_configured,
            "rotation_ready": rotation_ready,
            "configured_env_names": configured,
            "missing_env_names": missing,
            "configured_count": len(configured),
            "distinct_configured_count": len(distinct_values),
            "duplicate_configured": duplicate_configured,
            "next_slot_configured": secret_values["TRADINGVIEW_WEBHOOK_SECRET_NEXT"] is not None,
        },
        "redaction": (
            "Secret values, hashes, prefixes, and lengths are intentionally omitted."
        ),
        "limitations": (
            "Repo/process evidence can verify the loaded manual-control flag and "
            "presence/distinctness of this process's secret env values. It cannot "
            "prove which service unit, proxy, container, or TradingView alert is "
            "active, nor that a staged secret has been deployed to TradingView."
        ),
    }


def live_box_drift_report(
    *,
    repo_root: str | Path | None = None,
    risk_rules_path: str | Path = "risk_rules.yaml",
    log_dir: str | Path = "logs",
    for_date: date | None = None,
    manual_controls_enabled: bool | None = None,
) -> dict[str, Any]:
    root = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    risk_path = Path(risk_rules_path)
    if not risk_path.is_absolute():
        risk_path = root / risk_path
    risk_path = risk_path.resolve()
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = root / log_path
    log_path = log_path.resolve()
    journal_path = log_path / f"journal_{(for_date or date.today()).isoformat()}.jsonl"

    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _git(root, "rev-parse", "HEAD")
    dirty = _git(root, "status", "--porcelain")
    identity_source = "git"
    if branch is None and commit is None:
        # Atomic release dirs carry no .git — the shipped manifest is the
        # release identity, and file truth is enforced by ops/release_integrity.
        try:
            import json
            manifest = json.loads(
                (root / "release_manifest.json").read_text(encoding="utf-8")
            )
            repo_info = manifest.get("repo") or {}
            branch = repo_info.get("branch")
            commit = repo_info.get("commit")
            identity_source = "release_manifest"
            dirty = None  # no git worktree to be dirty
        except (OSError, ValueError):
            identity_source = "unavailable"
    config_sha = _sha256(risk_path)
    dirty_state = "dirty" if dirty else "clean"

    expected_branch = _env("EXPECTED_LIVE_BRANCH")
    expected_commit = _env("EXPECTED_LIVE_COMMIT")
    expected_config_sha = _env("EXPECTED_RISK_RULES_SHA256")
    expected_repo_root = _env("EXPECTED_LIVE_REPO_ROOT")
    expected_evidence_source = _env("EXPECTED_RUNTIME_EVIDENCE_SOURCE") or DEFAULT_EXPECTED_EVIDENCE_SOURCE
    expected_journal_dir = _env("EXPECTED_RUNTIME_JOURNAL_DIR")
    expected_status_paths = tuple(
        part.strip()
        for part in (_env("EXPECTED_RUNTIME_STATUS_PATHS") or ",".join(DEFAULT_EXPECTED_STATUS_PATHS)).split(",")
        if part.strip()
    )
    runtime_overrides, override_comparisons, unpinned_overrides = _runtime_override_report()
    security_runtime = _security_runtime_report(
        manual_controls_enabled=manual_controls_enabled,
    )

    comparisons = [
        _cmp("branch", branch, expected_branch),
        _cmp("commit", commit, expected_commit),
        _cmp("risk_rules_sha256", config_sha, expected_config_sha),
        _path_cmp("repo_root", root, expected_repo_root),
        _path_cmp("journal_dir", log_path, expected_journal_dir),
        _cmp("runtime_evidence_source", _env("RUNTIME_EVIDENCE_SOURCE") or DEFAULT_EXPECTED_EVIDENCE_SOURCE, expected_evidence_source),
        _cmp("status_paths", ",".join(expected_status_paths), ",".join(DEFAULT_EXPECTED_STATUS_PATHS), required=False),
        *override_comparisons,
    ]

    failed = [item for item in comparisons if item.required and not item.ok]
    missing_pins = [item.name for item in comparisons if item.required and item.expected is None]
    mismatches = [item.name for item in failed if item.expected is not None]
    dirty_problem = dirty_state != "clean"
    security_error = security_runtime["status"] == "error"
    security_warn = security_runtime["status"] == "warn"
    ok = (
        not failed
        and not dirty_problem
        and not unpinned_overrides
        and not security_error
        and not security_warn
    )
    status = "ok" if ok else "warn" if (
        (missing_pins or unpinned_overrides or security_warn)
        and not mismatches
        and not dirty_problem
        and not security_error
    ) else "error"

    if status == "ok":
        summary = (
            f"Live box guard verified branch {branch}, commit {_short(commit)}, "
            f"risk_rules {_short(config_sha)}, and evidence journal {journal_path}."
        )
    elif (missing_pins or unpinned_overrides or security_warn) and not mismatches and not dirty_problem:
        bits = []
        if missing_pins:
            bits.append(f"missing expected pin(s): {', '.join(missing_pins)}")
        if unpinned_overrides:
            bits.append(f"active unpinned runtime override(s): {', '.join(unpinned_overrides)}")
        if security_warn:
            bits.append("webhook secret rotation is not ready")
        summary = "Live box guard cannot fully verify drift; " + "; ".join(bits) + "."
    else:
        bits = []
        if mismatches:
            bits.append(f"mismatch: {', '.join(mismatches)}")
        if missing_pins:
            bits.append(f"missing pin: {', '.join(missing_pins)}")
        if dirty_problem:
            bits.append("git worktree is dirty")
        if unpinned_overrides:
            bits.append(f"active unpinned runtime override: {', '.join(unpinned_overrides)}")
        if security_error:
            bits.append("security-sensitive runtime state is unsafe")
        summary = "Live box drift guard failed: " + "; ".join(bits) + "."

    return {
        "ok": ok,
        "status": status,
        "summary": summary,
        "repo_root": str(root),
        "identity_source": identity_source,
        "branch": branch,
        "commit": commit,
        "risk_rules_path": str(risk_path),
        "risk_rules_sha256": config_sha,
        "git_dirty": dirty_problem,
        "git_dirty_count": len(dirty.splitlines()) if dirty else 0,
        "runtime_evidence_source": _env("RUNTIME_EVIDENCE_SOURCE") or DEFAULT_EXPECTED_EVIDENCE_SOURCE,
        "journal_dir": str(log_path),
        "journal_path": str(journal_path),
        "status_paths": list(expected_status_paths),
        "comparisons": [item.as_dict() for item in comparisons],
        "proof_critical_runtime_overrides": runtime_overrides,
        "active_runtime_overrides": [
            item["name"] for item in runtime_overrides if item["active"]
        ],
        "unpinned_runtime_overrides": unpinned_overrides,
        "security_runtime": security_runtime,
        "missing_pins": missing_pins,
        "mismatches": mismatches,
        "next_step": (
            "Set EXPECTED_LIVE_BRANCH, EXPECTED_LIVE_COMMIT, and "
            "EXPECTED_RISK_RULES_SHA256 on the active box after config freeze; "
            "pin active proof-critical overrides with EXPECTED_PROOF_<NAME> "
            f"(use {UNSET_PIN} to pin an override absent); "
            "keep ENABLE_MANUAL_EXECUTION_CONTROLS disabled and configure "
            "WEBHOOK_SECRET plus a distinct rotation alias; "
            "run doctor/status before live preflight."
        ),
    }
