"""Backtest -> demo qualification gate (read-only).

This gate answers one narrow question:

    Is a strategy-only change proven well enough by canonical replay that the
    long duplicate internal-paper phase may be skipped and the change may move
    to Tradovate DEMO, subject to a separate runtime/release safety check?

It does NOT run a backtest, place an order, change config, deploy, restart a
service, or authorize live trading. It consumes an explicit evidence-facts JSON
file plus the existing project_check promotion gate and fails closed on missing
proof.

The direct-to-demo path is intentionally narrow. It is available only when:
- the change is strategy/parameter-only; risk/execution/broker/session-contract
  semantics are explicitly unchanged, and the git diff is mechanically limited
  to strategy/tests/docs paths;
- canonical replay uses the real engine path and matches runtime logic;
- candidate/direction/entry-stop-target/timeframe/causal parity is proven;
- the replay dataset/manifest, code SHA and risk_rules hash are pinned;
- contract/roll identity, futures session-day identity and feed integrity are
  proven;
- IOC/no-fill behavior, adverse slippage, commission, gap handling and
  pessimistic same-bar resolution are modeled;
- validation uses an untouched window, multiple months, walk-forward evidence,
  a pre-registered sample requirement of at least 30 resolved fills in every
  required validation cell, drawdown limits and concentration checks;
- a frozen golden-parity fixture set covers no-fill, same-bar ambiguity, gap,
  session-boundary and roll-boundary cases.

A PASS means "DEMO EVIDENCE ELIGIBLE", not "deploy now" and never "live
eligible". Runtime/deployed-state reconciliation remains mandatory before any
DEMO activation.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.project_check import gitutil
from ops.project_check.promotion import build_promotion_report, load_evidence_facts

MIN_RESOLVED_FILLS_PER_CELL = 30
REQUIRED_SLIPPAGE_STRESS_TICKS = {2, 3}
DEMO_CLASSIFICATIONS = {"VALIDATED", "PROMISING BUT UNPROVEN"}
DIRECT_TO_DEMO_ALLOWED_DIFF_PREFIXES = ("strategy/", "tests/", "docs/")

# External Pine/TradingView daily-identity fixtures proven in C14 (#646).
# Pin the bytes here (outside the direct-to-DEMO allowed strategy/tests/docs diff)
# so a strategy-only change cannot silently rewrite the proof fixture it relies on.
_C14_FIXTURE_SHA256 = {
    "MES": "5bba4743ae916ae0f37b7cd6c2b38c35e2dbb25cf773ebfed4fd5210e321d7e0",
    "MNQ": "beb1164079b8134f6228c4dd163395f18d87939e61ed34c204eca50b46c45985",
}


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _resolve_path(root: Path, raw: Any) -> Path | None:
    if raw in (None, ""):
        return None
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else root / path


def _require_true(section: dict[str, Any], key: str, blockers: list[str], prefix: str) -> None:
    if section.get(key) is not True:
        blockers.append(f"{prefix}.{key} must be explicitly true")


def _require_false(section: dict[str, Any], key: str, blockers: list[str], prefix: str) -> None:
    if section.get(key) is not False:
        blockers.append(f"{prefix}.{key} must be explicitly false")


def _require_nonempty(section: dict[str, Any], key: str, blockers: list[str], prefix: str) -> None:
    value = section.get(key)
    if value is None or not str(value).strip():
        blockers.append(f"{prefix}.{key} is required")


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_change_scope(root: Path, evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    scope = evidence.get("change_scope") or {}
    _require_true(scope, "strategy_or_parameter_only", blockers, "change_scope")
    for key in (
        "risk_policy_unchanged",
        "execution_path_unchanged",
        "broker_routing_unchanged",
        "session_contract_semantics_unchanged",
    ):
        _require_true(scope, key, blockers, "change_scope")
    _require_nonempty(scope, "base_sha", blockers, "change_scope")

    base_sha = str(scope.get("base_sha") or "").strip()
    current_head = gitutil.head_sha(root)
    changed_files: list[str] = []
    strategy_files: list[str] = []
    non_strategy_scope_files: list[str] = []
    if current_head is None:
        blockers.append("change_scope current repository HEAD could not be resolved")
    elif base_sha:
        if base_sha == current_head:
            blockers.append("change_scope.base_sha must be the pre-change commit, not current HEAD")
        out, error = gitutil.run_git(
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                f"{base_sha}...{current_head}",
            ],
            cwd=root,
        )
        if error is not None or out is None:
            blockers.append(
                "change_scope could not mechanically enumerate the base_sha...HEAD diff: "
                + (error or "unknown git error")
            )
        else:
            changed_files = [line.strip() for line in out.splitlines() if line.strip()]
            if not changed_files:
                blockers.append("change_scope base_sha...HEAD diff is empty; no strategy change is proven")
            strategy_files = [path for path in changed_files if path.startswith("strategy/")]
            if not strategy_files:
                blockers.append("change_scope diff contains no strategy/ file")
            non_strategy_scope_files = [
                path
                for path in changed_files
                if not path.startswith(DIRECT_TO_DEMO_ALLOWED_DIFF_PREFIXES)
            ]
            if non_strategy_scope_files:
                blockers.append(
                    "change_scope git diff is not strategy-only; direct-to-demo disallows: "
                    + ", ".join(non_strategy_scope_files)
                )
    return {
        **scope,
        "current_head": current_head,
        "changed_files": changed_files,
        "strategy_files": strategy_files,
        "non_strategy_scope_files": non_strategy_scope_files,
    }


def _check_canonical_replay(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    replay = evidence.get("canonical_replay") or {}
    for key in (
        "real_replay_engine",
        "real_decision_engine",
        "real_risk_engine",
        "real_paper_broker",
        "replay_live_logic_confirmed",
        "same_strategy_formula_confirmed",
    ):
        _require_true(replay, key, blockers, "canonical_replay")
    return replay


def _check_identity_parity(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    parity = evidence.get("identity_parity") or {}
    for key in (
        "candidate_identity_parity",
        "direction_parity",
        "entry_stop_target_parity",
        "timeframe_parity",
        "causal_data_availability",
    ):
        _require_true(parity, key, blockers, "identity_parity")
    _require_false(parity, "lookahead_or_partial_bar_dependency", blockers, "identity_parity")
    return parity



def _verify_session_day_identity(root: Path, instrument: str | None) -> dict[str, Any]:
    """Mechanically re-check current replay trade-date logic against pinned Pine rows."""
    root_symbol = str(instrument or "").strip().upper()
    expected_sha = _C14_FIXTURE_SHA256.get(root_symbol)
    if expected_sha is None:
        return {
            "ok": False,
            "instrument": root_symbol or None,
            "reason": (
                "no hash-pinned TradingView/Pine session-day fixture is registered "
                f"for {root_symbol or 'UNKNOWN'}"
            ),
        }

    fixture = root / "tests" / "fixtures" / "c14_pine_daily_identity" / f"{root_symbol}1_15m.csv"
    actual_sha = _sha256(fixture)
    if actual_sha != expected_sha:
        return {
            "ok": False,
            "instrument": root_symbol,
            "fixture_path": str(fixture),
            "expected_fixture_sha256": expected_sha,
            "actual_fixture_sha256": actual_sha,
            "reason": "C14 TradingView/Pine fixture bytes do not match the pinned proof",
        }

    try:
        from scripts.csv_to_replay import (
            CALENDAR_CME_EQUITY_INDEX,
            cme_trading_day,
            trading_day_calendar,
        )
    except Exception as exc:
        return {
            "ok": False,
            "instrument": root_symbol,
            "fixture_path": str(fixture),
            "reason": f"could not import current session-day implementation: {exc}",
        }

    if trading_day_calendar(root_symbol) != CALENDAR_CME_EQUITY_INDEX:
        return {
            "ok": False,
            "instrument": root_symbol,
            "fixture_path": str(fixture),
            "reason": "current replay no longer routes this instrument through the proven C14 calendar",
        }

    rows = 0
    mismatches: list[dict[str, str]] = []
    try:
        with fixture.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                rows += 1
                observed = datetime.fromisoformat(row["time"])
                pine_day = datetime.fromtimestamp(
                    int(row["time_tradingday"]) / 1000, tz=timezone.utc
                ).date()
                replay_day = cme_trading_day(observed, root_symbol)
                if replay_day != pine_day and len(mismatches) < 10:
                    mismatches.append(
                        {
                            "time": row["time"],
                            "pine_trade_date": pine_day.isoformat(),
                            "replay_trade_date": replay_day.isoformat(),
                        }
                    )
    except (OSError, ValueError, KeyError) as exc:
        return {
            "ok": False,
            "instrument": root_symbol,
            "fixture_path": str(fixture),
            "fixture_sha256": actual_sha,
            "reason": f"could not evaluate C14 fixture: {exc}",
        }

    return {
        "ok": rows > 0 and not mismatches,
        "instrument": root_symbol,
        "fixture_path": str(fixture),
        "fixture_sha256": actual_sha,
        "rows_checked": rows,
        "mismatches": mismatches,
        "reason": None if rows > 0 and not mismatches else "current replay disagrees with pinned Pine trade dates",
    }


def _verify_feed_manifest(
    manifest_path: Path | None,
    *,
    instrument: str | None,
) -> dict[str, Any]:
    """Verify frozen corpus files and require an explicit CME-hours gap ledger.

    A non-empty gap ledger is not itself a failure: exchange closures and known
    holes remain visible for downstream contamination rules. The gate proves
    that the frozen bytes and gap enumeration exist; it never turns missing data
    into synthetic bars.
    """
    if manifest_path is None:
        return {"ok": False, "reason": "dataset manifest path is missing"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "manifest_path": str(manifest_path), "reason": f"manifest unreadable: {exc}"}
    if not isinstance(manifest, dict):
        return {"ok": False, "manifest_path": str(manifest_path), "reason": "manifest must be a JSON object"}

    claimed_instrument = str(instrument or "").strip().upper()
    manifest_instrument = str(manifest.get("instrument") or "").strip().upper()
    problems: list[str] = []
    if not manifest_instrument:
        problems.append("manifest.instrument is required")
    elif claimed_instrument and manifest_instrument != claimed_instrument:
        problems.append(
            f"manifest.instrument={manifest_instrument} does not match claimed instrument {claimed_instrument}"
        )

    timeframe = _as_int(manifest.get("timeframe_minutes"))
    if timeframe is None or timeframe <= 0:
        problems.append("manifest.timeframe_minutes must be a positive integer")

    gap_ledger = manifest.get("gap_ledger_cme_hours")
    if not isinstance(gap_ledger, list):
        problems.append("manifest.gap_ledger_cme_hours must be an explicit list")

    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        problems.append("manifest.files must be a non-empty object of frozen replay files")
        files = {}

    hash_mismatches: list[dict[str, Any]] = []
    verified_rows = 0
    for rel, meta in files.items():
        if not isinstance(meta, dict):
            hash_mismatches.append({"path": str(rel), "reason": "file metadata must be an object"})
            continue
        expected = str(meta.get("sha256") or "").strip().lower()
        if len(expected) != 64:
            hash_mismatches.append({"path": str(rel), "reason": "missing/invalid sha256"})
            continue
        file_path = Path(str(rel))
        if not file_path.is_absolute():
            file_path = manifest_path.parent / file_path
        actual = _sha256(file_path)
        if actual != expected:
            hash_mismatches.append(
                {"path": str(file_path), "expected_sha256": expected, "actual_sha256": actual}
            )
        rows = _as_int(meta.get("rows"))
        if rows is None or rows < 0:
            hash_mismatches.append({"path": str(file_path), "reason": "rows must be a non-negative integer"})
        else:
            verified_rows += rows

    coverage = manifest.get("coverage")
    if isinstance(coverage, dict):
        expected_files = _as_int(coverage.get("files"))
        expected_rows = _as_int(coverage.get("rows"))
        if expected_files is not None and expected_files != len(files):
            problems.append(
                f"manifest.coverage.files={expected_files} does not match files object count {len(files)}"
            )
        if expected_rows is not None and expected_rows != verified_rows:
            problems.append(
                f"manifest.coverage.rows={expected_rows} does not match summed file rows {verified_rows}"
            )

    return {
        "ok": not problems and not hash_mismatches,
        "manifest_path": str(manifest_path),
        "manifest_instrument": manifest_instrument or None,
        "timeframe_minutes": timeframe,
        "files_verified": len(files),
        "rows_declared": verified_rows,
        "gap_runs_declared": len(gap_ledger) if isinstance(gap_ledger, list) else None,
        "problems": problems,
        "file_hash_mismatches": hash_mismatches[:20],
        "reason": None if not problems and not hash_mismatches else "frozen feed manifest proof failed",
    }



GAP_PROOF_SCHEMA_VERSION = "replay_gap_proof_v1"


def _parse_iso_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _manifest_gap_intervals(manifest_path: Path | None) -> tuple[list[tuple[datetime, datetime]], str | None]:
    if manifest_path is None:
        return [], "dataset manifest path is missing"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"dataset manifest unreadable: {exc}"
    timeframe = _as_int(manifest.get("timeframe_minutes"))
    ledger = manifest.get("gap_ledger_cme_hours")
    if timeframe is None or timeframe <= 0 or not isinstance(ledger, list):
        return [], "dataset manifest lacks a valid timeframe/gap ledger"
    intervals: list[tuple[datetime, datetime]] = []
    from datetime import timedelta
    for i, run in enumerate(ledger):
        if not isinstance(run, dict):
            return [], f"gap ledger row {i} is not an object"
        start = _parse_iso_utc(run.get("first_missing"))
        last = _parse_iso_utc(run.get("last_missing"))
        if start is None or last is None or last < start:
            return [], f"gap ledger row {i} has invalid timestamps"
        intervals.append((start, last + timedelta(minutes=timeframe)))
    return intervals, None


def _verify_gap_proof(
    root: Path,
    evidence: dict[str, Any],
    *,
    manifest_path: Path | None,
    manifest_sha256: str | None,
) -> dict[str, Any]:
    """Mechanically verify every counted resolved outcome avoids declared source gaps.

    The proof producer must supply its strategy-specific dependency start. The
    gate never guesses that lookback. Signal/entry/exit timestamps are explicit,
    ordered, and bound to the frozen manifest/code SHA. Any overlap with a
    declared manifest gap fails closed; scheduled-closure exceptions are not
    guessed here.
    """
    data = evidence.get("data_integrity") or {}
    raw_path = data.get("gap_proof_path")
    claimed_sha = str(data.get("gap_proof_sha256") or "").strip().lower()
    path = _resolve_path(root, raw_path)
    if path is None:
        return {"ok": False, "reason": "data_integrity.gap_proof_path is required"}
    actual_sha = _sha256(path)
    if actual_sha is None:
        return {"ok": False, "gap_proof_path": str(path), "reason": "gap proof is unreadable"}
    if len(claimed_sha) != 64 or actual_sha != claimed_sha:
        return {
            "ok": False,
            "gap_proof_path": str(path),
            "actual_gap_proof_sha256": actual_sha,
            "reason": "data_integrity.gap_proof_sha256 does not match the current gap-proof bytes",
        }
    try:
        proof = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "gap_proof_path": str(path), "reason": f"gap proof is not valid JSON: {exc}"}
    if not isinstance(proof, dict):
        return {"ok": False, "gap_proof_path": str(path), "reason": "gap proof must be a JSON object"}

    problems: list[str] = []
    if proof.get("schema_version") != GAP_PROOF_SCHEMA_VERSION:
        problems.append(f"schema_version must be {GAP_PROOF_SCHEMA_VERSION}")
    instrument = str((evidence.get("execution_context_claimed") or {}).get("instrument") or "").strip().upper()
    if str(proof.get("instrument") or "").strip().upper() != instrument:
        problems.append("gap proof instrument does not match execution_context_claimed.instrument")
    claimed_manifest = str(proof.get("dataset_manifest_sha256") or "").strip().lower()
    if not manifest_sha256 or claimed_manifest != str(manifest_sha256).strip().lower():
        problems.append("gap proof dataset_manifest_sha256 does not match the frozen dataset manifest")
    replay_sha = str((evidence.get("replay_provenance") or {}).get("code_sha") or "").strip()
    if str(proof.get("code_sha") or "").strip() != replay_sha:
        problems.append("gap proof code_sha does not match replay_provenance.code_sha")

    dependency_source = proof.get("dependency_windows_source")
    dependency_map: dict[str, str] = {}
    dependency_problem: str | None = None
    if not isinstance(dependency_source, dict):
        problems.append("gap proof dependency_windows_source must be an object")
    else:
        dependency_path = _resolve_path(root, dependency_source.get("path"))
        expected_dependency_sha = str(dependency_source.get("sha256") or "").strip().lower()
        actual_dependency_sha = _sha256(dependency_path) if dependency_path is not None else None
        if dependency_path is None or actual_dependency_sha is None:
            dependency_problem = "dependency windows source is unreadable"
        elif len(expected_dependency_sha) != 64 or actual_dependency_sha != expected_dependency_sha:
            dependency_problem = "dependency windows source sha256 mismatch"
        else:
            try:
                loaded_dependencies = json.loads(dependency_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                dependency_problem = f"dependency windows source is invalid JSON: {exc}"
            else:
                if not isinstance(loaded_dependencies, dict):
                    dependency_problem = "dependency windows source must be a JSON object"
                elif loaded_dependencies.get("schema_version") != "strategy_dependency_windows_v1":
                    dependency_problem = "dependency windows schema_version must be strategy_dependency_windows_v1"
                elif str(loaded_dependencies.get("code_sha") or "").strip() != replay_sha:
                    dependency_problem = "dependency windows code_sha does not match replay_provenance.code_sha"
                elif str(loaded_dependencies.get("strategy") or "").strip() != str(evidence.get("strategy") or "").strip():
                    dependency_problem = "dependency windows strategy does not match evidence.strategy"
                elif str(loaded_dependencies.get("instrument") or "").strip().upper() != instrument:
                    dependency_problem = "dependency windows instrument does not match execution_context_claimed.instrument"
                elif not isinstance(loaded_dependencies.get("windows"), dict):
                    dependency_problem = "dependency windows source.windows must be an object keyed by paper_order_id"
                else:
                    dependency_map = {
                        str(k).strip(): str(v).strip()
                        for k, v in loaded_dependencies["windows"].items()
                        if str(k).strip()
                    }
        if dependency_problem:
            problems.append(dependency_problem)

    journal_specs = proof.get("source_journals")
    journal_trades: dict[str, dict[str, Any]] = {}
    journal_outcomes: dict[str, dict[str, Any]] = {}
    journal_problems: list[str] = []
    if not isinstance(journal_specs, list) or not journal_specs:
        problems.append("gap proof source_journals must be a non-empty list")
        journal_specs = []
    for jidx, spec in enumerate(journal_specs):
        if not isinstance(spec, dict):
            journal_problems.append(f"source_journals[{jidx}] must be an object")
            continue
        journal_path = _resolve_path(root, spec.get("path"))
        expected_journal_sha = str(spec.get("sha256") or "").strip().lower()
        actual_journal_sha = _sha256(journal_path) if journal_path is not None else None
        if journal_path is None or actual_journal_sha is None:
            journal_problems.append(f"source_journals[{jidx}] is unreadable")
            continue
        if len(expected_journal_sha) != 64 or actual_journal_sha != expected_journal_sha:
            journal_problems.append(f"source_journals[{jidx}] sha256 mismatch")
            continue
        try:
            lines = journal_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            journal_problems.append(f"source_journals[{jidx}] read failed: {exc}")
            continue
        for lidx, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                journal_problems.append(
                    f"source_journals[{jidx}] line {lidx} is invalid JSON"
                )
                continue
            if not isinstance(record, dict):
                continue
            if record.get("decision") == "TRADE":
                oid = str(record.get("paper_order_id") or "").strip()
                if oid:
                    if oid in journal_trades:
                        journal_problems.append(f"duplicate TRADE paper_order_id {oid}")
                    else:
                        journal_trades[oid] = record
            if record.get("type") == "OUTCOME":
                outcome = record.get("outcome") or {}
                oid = str(outcome.get("paper_order_id") or "").strip()
                if oid:
                    if oid in journal_outcomes:
                        journal_problems.append(f"duplicate OUTCOME paper_order_id {oid}")
                    else:
                        journal_outcomes[oid] = record
    if journal_problems:
        problems.append(f"{len(journal_problems)} source-journal proof problem(s)")

    rows = proof.get("resolved_outcomes")
    if not isinstance(rows, list):
        problems.append("gap proof resolved_outcomes must be a list")
        rows = []
    expected_resolved = _as_int((evidence.get("execution") or {}).get("resolved_outcomes"))
    if expected_resolved is None:
        problems.append("execution.resolved_outcomes is required for gap proof")
    elif len(rows) != expected_resolved:
        problems.append(
            f"gap proof has {len(rows)} resolved outcomes but execution.resolved_outcomes={expected_resolved}"
        )

    gap_intervals, gap_error = _manifest_gap_intervals(manifest_path)
    if gap_error:
        problems.append(gap_error)

    seen_ids: set[str] = set()
    contaminated: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid_rows.append({"index": idx, "reason": "row must be an object"})
            continue
        order_id = str(row.get("paper_order_id") or "").strip()
        if not order_id:
            invalid_rows.append({"index": idx, "reason": "paper_order_id is required"})
            continue
        if order_id in seen_ids:
            invalid_rows.append({"index": idx, "paper_order_id": order_id, "reason": "duplicate paper_order_id"})
            continue
        seen_ids.add(order_id)
        source_dependency = dependency_map.get(order_id)
        if source_dependency is None:
            invalid_rows.append(
                {
                    "index": idx,
                    "paper_order_id": order_id,
                    "reason": "paper_order_id is missing from hash-bound dependency windows source",
                }
            )
            continue
        if str(row.get("dependency_start_timestamp") or "").strip() != source_dependency:
            invalid_rows.append(
                {
                    "index": idx,
                    "paper_order_id": order_id,
                    "reason": "dependency_start_timestamp does not match hash-bound dependency windows source",
                }
            )
            continue
        dep = _parse_iso_utc(row.get("dependency_start_timestamp"))
        signal = _parse_iso_utc(row.get("signal_timestamp"))
        entry = _parse_iso_utc(row.get("entry_timestamp"))
        exit_ = _parse_iso_utc(row.get("exit_timestamp"))
        if None in (dep, signal, entry, exit_):
            invalid_rows.append(
                {"index": idx, "paper_order_id": order_id, "reason": "all four timezone-aware timestamps are required"}
            )
            continue
        assert dep is not None and signal is not None and entry is not None and exit_ is not None
        if not (dep <= signal <= entry <= exit_):
            invalid_rows.append(
                {
                    "index": idx,
                    "paper_order_id": order_id,
                    "reason": "timestamps must satisfy dependency_start <= signal <= entry <= exit",
                }
            )
            continue
        trade = journal_trades.get(order_id)
        outcome_record = journal_outcomes.get(order_id)
        if trade is None or outcome_record is None:
            invalid_rows.append(
                {
                    "index": idx,
                    "paper_order_id": order_id,
                    "reason": "matching TRADE and OUTCOME rows are required in hash-bound source journals",
                }
            )
            continue
        outcome = outcome_record.get("outcome") or {}
        audit = outcome.get("execution_audit") or {}
        journal_signal = str(trade.get("bar_ts") or outcome.get("signal_timestamp") or "").strip()
        journal_entry = str(audit.get("historical_entry_bar_ts") or "").strip()
        journal_exit = str(audit.get("historical_resolution_bar_ts") or "").strip()
        if (
            _parse_iso_utc(journal_signal) != signal
            or _parse_iso_utc(journal_entry) != entry
            or _parse_iso_utc(journal_exit) != exit_
        ):
            invalid_rows.append(
                {
                    "index": idx,
                    "paper_order_id": order_id,
                    "reason": "signal/entry/exit timestamps do not match hash-bound replay journal",
                }
            )
            continue
        if str(outcome.get("result") or "").upper() not in {"WIN", "LOSS", "BREAKEVEN"}:
            invalid_rows.append(
                {
                    "index": idx,
                    "paper_order_id": order_id,
                    "reason": "gap proof row must bind to a terminal resolved replay outcome",
                }
            )
            continue

        overlaps = [
            {"gap_start": start.isoformat(), "gap_end_exclusive": end.isoformat()}
            for start, end in gap_intervals
            if dep < end and exit_ >= start
        ]
        if overlaps:
            contaminated.append(
                {
                    "paper_order_id": order_id,
                    "dependency_start_timestamp": dep.isoformat(),
                    "exit_timestamp": exit_.isoformat(),
                    "overlaps": overlaps[:20],
                }
            )

    if invalid_rows:
        problems.append(f"{len(invalid_rows)} gap-proof row(s) are invalid")
    if contaminated:
        problems.append(f"{len(contaminated)} counted resolved outcome(s) overlap declared dataset gaps")

    return {
        "ok": not problems,
        "gap_proof_path": str(path),
        "actual_gap_proof_sha256": actual_sha,
        "schema_version": proof.get("schema_version"),
        "resolved_outcomes_checked": len(rows),
        "unique_order_ids": len(seen_ids),
        "declared_gap_intervals": len(gap_intervals),
        "dependency_windows_checked": len(dependency_map),
        "dependency_windows_problem": dependency_problem,
        "source_journals_checked": len(journal_specs),
        "source_journal_problems": journal_problems[:20],
        "invalid_rows": invalid_rows[:20],
        "contaminated_outcomes": contaminated[:20],
        "problems": problems,
        "reason": None if not problems else "resolved-outcome gap proof failed",
    }


def _check_data_integrity(root: Path, evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    data = evidence.get("data_integrity") or {}
    for key in (
        "dataset_frozen",
        "contract_roll_identity_proven",
        "session_day_identity_proven",
        "feed_integrity_proven",
    ):
        _require_true(data, key, blockers, "data_integrity")

    _require_nonempty(data, "dataset_manifest_path", blockers, "data_integrity")
    _require_nonempty(data, "dataset_manifest_sha256", blockers, "data_integrity")
    _require_nonempty(data, "gap_proof_path", blockers, "data_integrity")
    _require_nonempty(data, "gap_proof_sha256", blockers, "data_integrity")

    manifest_path = _resolve_path(root, data.get("dataset_manifest_path"))
    expected = str(data.get("dataset_manifest_sha256") or "").strip().lower()
    actual = _sha256(manifest_path) if manifest_path is not None else None
    if manifest_path is not None and actual is None:
        blockers.append(f"data_integrity.dataset_manifest_path is unreadable: {manifest_path}")
    elif actual is not None and expected and actual != expected:
        blockers.append(
            "data_integrity.dataset_manifest_sha256 does not match the current manifest bytes"
        )

    instrument = str(
        (evidence.get("execution_context_claimed") or {}).get("instrument") or ""
    ).strip().upper() or None

    session_proof = _verify_session_day_identity(root, instrument)
    if not session_proof.get("ok"):
        blockers.append(
            "data_integrity.session_day_identity_proven lacks mechanical C14 fixture proof: "
            + str(session_proof.get("reason") or "unknown failure")
        )

    feed_proof = _verify_feed_manifest(manifest_path, instrument=instrument)
    if not feed_proof.get("ok"):
        blockers.append(
            "data_integrity.feed_integrity_proven lacks frozen manifest/file proof: "
            + str(feed_proof.get("reason") or "unknown failure")
        )

    gap_proof = _verify_gap_proof(
        root,
        evidence,
        manifest_path=manifest_path,
        manifest_sha256=actual,
    )
    if not gap_proof.get("ok"):
        blockers.append(
            "data_integrity resolved-outcome gap proof failed: "
            + str(gap_proof.get("reason") or "unknown failure")
        )

    return {
        **data,
        "resolved_manifest_path": str(manifest_path) if manifest_path else None,
        "actual_manifest_sha256": actual,
        "mechanical_session_day_identity": session_proof,
        "mechanical_feed_integrity": feed_proof,
        "resolved_outcome_gap_proof": gap_proof,
    }


def _check_execution_realism(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    realism = evidence.get("execution_realism") or {}
    for key in (
        "ioc_no_fill_modeled",
        "pessimistic_same_bar",
        "gap_through_modeled",
        "slippage_included",
        "commission_included",
        "slippage_stress_pass",
    ):
        _require_true(realism, key, blockers, "execution_realism")

    baseline_slippage = _as_float(realism.get("baseline_adverse_slippage_ticks"))
    if baseline_slippage is None or baseline_slippage < 1.0:
        blockers.append(
            "execution_realism.baseline_adverse_slippage_ticks must be >= 1 for direct-to-demo evidence"
        )

    commission = _as_float(realism.get("commission_round_turn_dollars"))
    if commission is None or commission <= 0:
        blockers.append(
            "execution_realism.commission_round_turn_dollars must be a positive explicit cost"
        )

    stress = realism.get("slippage_stress_ticks")
    stress_values: set[int] = set()
    if isinstance(stress, list):
        for item in stress:
            parsed = _as_int(item)
            if parsed is not None:
                stress_values.add(parsed)
    if not REQUIRED_SLIPPAGE_STRESS_TICKS.issubset(stress_values):
        blockers.append(
            "execution_realism.slippage_stress_ticks must include both 2-tick and 3-tick adverse stress"
        )
    return realism


def _check_validation(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    validation = evidence.get("validation") or {}
    for key in (
        "untouched_validation_window",
        "multiple_months_covered",
        "walk_forward_pass",
        "sample_requirement_pre_registered",
        "drawdown_within_pre_registered_limit",
        "concentration_check_pass",
        "session_filters_respected",
        "direction_coverage_requirement_met",
    ):
        _require_true(validation, key, blockers, "validation")

    required_per_cell = _as_int(validation.get("required_resolved_fills_per_cell"))
    minimum_cell = _as_int(validation.get("minimum_resolved_fills_in_required_cells"))
    if required_per_cell is None or required_per_cell < MIN_RESOLVED_FILLS_PER_CELL:
        blockers.append(
            "validation.required_resolved_fills_per_cell must be pre-registered at "
            f">= {MIN_RESOLVED_FILLS_PER_CELL}"
        )
    if minimum_cell is None or minimum_cell < 0:
        blockers.append(
            "validation.minimum_resolved_fills_in_required_cells must be a non-negative integer"
        )
    elif required_per_cell is not None and minimum_cell < required_per_cell:
        blockers.append(
            "validation.minimum_resolved_fills_in_required_cells="
            f"{minimum_cell} is below the pre-registered per-cell requirement {required_per_cell}"
        )

    # The contract is per required validation cell, so a single claimed minimum
    # cannot prove the population. Enumerate the required dimensions/cells and
    # mechanically check every required cell's resolved-fill count.
    dimensions = validation.get("required_cell_dimensions")
    if not isinstance(dimensions, list) or not dimensions or any(
        not isinstance(item, str) or not item.strip() for item in dimensions
    ):
        blockers.append(
            "validation.required_cell_dimensions must be a non-empty list of dimension names"
        )

    cells = validation.get("cells")
    if not isinstance(cells, list) or not cells:
        blockers.append("validation.cells must contain per-cell evidence")
        cells = []

    required_cells = 0
    observed_minimum: int | None = None
    for idx, cell in enumerate(cells):
        if not isinstance(cell, dict):
            blockers.append(f"validation.cells[{idx}] must be an object")
            continue
        if cell.get("required") is not True:
            continue
        required_cells += 1
        cell_id = str(cell.get("cell_id") or f"index_{idx}").strip()
        resolved = _as_int(cell.get("resolved_fills"))
        if resolved is None or resolved < 0:
            blockers.append(
                f"validation cell {cell_id} resolved_fills must be a non-negative integer"
            )
            continue
        observed_minimum = (
            resolved if observed_minimum is None else min(observed_minimum, resolved)
        )
        need = (
            required_per_cell
            if required_per_cell is not None
            and required_per_cell >= MIN_RESOLVED_FILLS_PER_CELL
            else MIN_RESOLVED_FILLS_PER_CELL
        )
        if resolved < need:
            blockers.append(
                f"validation cell {cell_id} has {resolved} resolved fills, below required {need}"
            )

    if required_cells == 0:
        blockers.append("validation.cells contains no required validation cells")

    if (
        minimum_cell is not None
        and observed_minimum is not None
        and minimum_cell != observed_minimum
    ):
        blockers.append(
            "validation.minimum_resolved_fills_in_required_cells does not match "
            f"the enumerated required-cell minimum {observed_minimum}"
        )

    return {
        **validation,
        "required_cell_count": required_cells,
        "observed_required_cell_minimum": observed_minimum,
    }


def _check_golden_parity(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    golden = evidence.get("golden_parity") or {}
    for key in (
        "fixture_set_frozen",
        "runtime_vs_replay_candidate_parity",
        "runtime_vs_replay_risk_parity",
        "runtime_vs_replay_order_intent_parity",
        "no_fill_case_covered",
        "same_bar_ambiguity_case_covered",
        "gap_case_covered",
        "session_boundary_case_covered",
        "roll_boundary_case_covered",
    ):
        _require_true(golden, key, blockers, "golden_parity")
    return golden


def _check_replay_provenance(root: Path, evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    provenance = evidence.get("replay_provenance") or {}
    _require_nonempty(provenance, "code_sha", blockers, "replay_provenance")
    _require_nonempty(provenance, "risk_rules_sha256", blockers, "replay_provenance")

    current_head = gitutil.head_sha(root)
    claimed_head = str(provenance.get("code_sha") or "").strip()
    if current_head is None:
        blockers.append("replay_provenance current repository HEAD could not be resolved")
    elif claimed_head and current_head != claimed_head:
        blockers.append(
            f"replay_provenance.code_sha={claimed_head} does not match current HEAD {current_head}"
        )

    risk_path = root / "risk_rules.yaml"
    actual_risk_hash = _sha256(risk_path)
    claimed_risk_hash = str(provenance.get("risk_rules_sha256") or "").strip().lower()
    if actual_risk_hash is None:
        blockers.append("replay_provenance current risk_rules.yaml could not be hashed")
    elif claimed_risk_hash and actual_risk_hash != claimed_risk_hash:
        blockers.append(
            "replay_provenance.risk_rules_sha256 does not match current risk_rules.yaml"
        )
    return {
        **provenance,
        "current_head": current_head,
        "actual_risk_rules_sha256": actual_risk_hash,
    }


def _check_execution_claims(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    claimed = evidence.get("execution_context_claimed") or {}
    for key in (
        "instrument",
        "entry_fill_model",
        "entry_tolerance_ticks",
        "contract_qty",
        "commission_slippage_assumptions",
    ):
        _require_nonempty(claimed, key, blockers, "execution_context_claimed")
    return claimed


def build_demo_qualification_report(
    *,
    strategy: str,
    repo_root: str | Path,
    evidence_path: str | Path,
) -> dict[str, Any]:
    """Build a fail-closed direct-to-DEMO evidence qualification report."""
    root = Path(repo_root)
    path = Path(evidence_path)
    evidence, load_error = load_evidence_facts(path)
    if evidence is None:
        return {
            "ok": False,
            "gate_pass": False,
            "demo_evidence_eligible": False,
            "strategy": strategy,
            "evidence_path": str(path),
            "evidence_load_error": load_error,
            "blockers": [load_error or "evidence facts unavailable"],
            "long_internal_paper_phase_waived": False,
            "fallback_to_existing_validation_path": True,
            "runtime_release_reconciliation_required": True,
            "demo_forward_validation_required": True,
            "live_trading_authorized": False,
        }

    blockers: list[str] = []
    promotion = build_promotion_report(
        strategy=strategy,
        repo_root=root,
        evidence_path=path,
    )
    if not promotion.get("gate_pass"):
        blockers.append("base promotion proof gate did not pass")
        blockers.extend(promotion.get("classification", {}).get("blockers") or [])

    effective_classification = promotion.get("classification", {}).get("effective_classification")
    if effective_classification not in DEMO_CLASSIFICATIONS:
        blockers.append(
            "effective classification must be VALIDATED or PROMISING BUT UNPROVEN for direct-to-demo"
        )

    scope = _check_change_scope(root, evidence, blockers)
    canonical = _check_canonical_replay(evidence, blockers)
    identity = _check_identity_parity(evidence, blockers)
    data = _check_data_integrity(root, evidence, blockers)
    realism = _check_execution_realism(evidence, blockers)
    validation = _check_validation(evidence, blockers)
    golden = _check_golden_parity(evidence, blockers)
    provenance = _check_replay_provenance(root, evidence, blockers)
    execution_claims = _check_execution_claims(evidence, blockers)

    blockers = list(dict.fromkeys(blockers))
    gate_pass = not blockers
    return {
        "ok": gate_pass,
        "gate_pass": gate_pass,
        "demo_evidence_eligible": gate_pass,
        "strategy": strategy,
        "evidence_path": str(path),
        "evidence_load_error": load_error,
        "effective_classification": effective_classification,
        "blockers": blockers,
        "base_promotion_gate_pass": bool(promotion.get("gate_pass")),
        "change_scope": scope,
        "canonical_replay": canonical,
        "identity_parity": identity,
        "data_integrity": data,
        "execution_realism": realism,
        "validation": validation,
        "golden_parity": golden,
        "replay_provenance": provenance,
        "execution_context_claimed": execution_claims,
        "execution_context_live_check": promotion.get("execution_context"),
        "accounting_identities": promotion.get("accounting_identities"),
        "long_internal_paper_phase_waived": gate_pass,
        "fallback_to_existing_validation_path": not gate_pass,
        "runtime_release_reconciliation_required": True,
        "demo_forward_validation_required": True,
        "live_trading_authorized": False,
        "verdict": "DEMO_EVIDENCE_ELIGIBLE" if gate_pass else "BLOCKED",
        "safety_boundary": (
            "A PASS waives only the long duplicate internal-paper evidence phase for a strategy-only "
            "change. It does not deploy, restart, arm, route to a broker, or authorize live trading. "
            "A separate current runtime/release safety reconciliation is still required before DEMO."
        ),
    }
