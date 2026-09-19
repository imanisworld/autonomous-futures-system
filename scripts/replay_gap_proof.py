#!/usr/bin/env python3
"""Build replay gap-proof artifacts without guessing strategy dependency windows.

The dependency source is strategy-produced JSON using
`strategy_dependency_windows_v1`: it binds strategy, instrument and code SHA to
a `windows` map of paper_order_id -> earliest dependency timestamp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

SCHEMA_VERSION = "replay_gap_proof_v1"
TERMINAL_RESULTS = {"WIN", "LOSS", "BREAKEVEN"}

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git_head(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def journal_index(paths: list[Path]):
    trades, outcomes = {}, {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            if row.get("decision") == "TRADE":
                oid = str(row.get("paper_order_id") or "").strip()
                if oid:
                    if oid in trades:
                        raise ValueError(f"duplicate TRADE paper_order_id {oid}")
                    trades[oid] = row
            if row.get("type") == "OUTCOME":
                outcome = row.get("outcome") or {}
                oid = str(outcome.get("paper_order_id") or "").strip()
                if oid:
                    if oid in outcomes:
                        raise ValueError(f"duplicate OUTCOME paper_order_id {oid}")
                    outcomes[oid] = row
    return trades, outcomes

def build_gap_proof(*, repo_root: Path, strategy: str, instrument: str, manifest_path: Path,
                    journal_paths: list[Path], dependency_windows_path: Path,
                    code_sha: str | None = None) -> dict:
    manifest = load_json(manifest_path)
    root = instrument.strip().upper()
    if str(manifest.get("instrument") or "").strip().upper() != root:
        raise ValueError("manifest instrument does not match requested instrument")
    dependency_doc = load_json(dependency_windows_path)
    if not isinstance(dependency_doc, dict):
        raise ValueError("dependency windows source must be a JSON object")
    if dependency_doc.get("schema_version") != "strategy_dependency_windows_v1":
        raise ValueError("dependency windows schema_version must be strategy_dependency_windows_v1")
    if str(dependency_doc.get("strategy") or "").strip() != strategy.strip():
        raise ValueError("dependency windows strategy does not match requested strategy")
    if str(dependency_doc.get("instrument") or "").strip().upper() != root:
        raise ValueError("dependency windows instrument does not match requested instrument")
    expected_sha = code_sha or git_head(repo_root)
    if str(dependency_doc.get("code_sha") or "").strip() != expected_sha:
        raise ValueError("dependency windows code_sha does not match replay code sha")
    dependencies = dependency_doc.get("windows")
    if not isinstance(dependencies, dict) or not dependencies:
        raise ValueError("dependency windows source.windows must be a non-empty object")
    trades, outcomes = journal_index(journal_paths)
    rows = []
    for raw_oid, raw_start in sorted(dependencies.items()):
        oid, dependency_start = str(raw_oid).strip(), str(raw_start).strip()
        if not oid or not dependency_start:
            raise ValueError("dependency windows contain an empty id or timestamp")
        trade, outcome_row = trades.get(oid), outcomes.get(oid)
        if trade is None or outcome_row is None:
            raise ValueError(f"{oid}: matching TRADE and OUTCOME rows are required")
        outcome = outcome_row.get("outcome") or {}
        if str(outcome.get("result") or "").upper() not in TERMINAL_RESULTS:
            raise ValueError(f"{oid}: outcome is not terminal/resolved")
        audit = outcome.get("execution_audit") or {}
        signal = str(trade.get("bar_ts") or outcome.get("signal_timestamp") or "").strip()
        entry = str(audit.get("historical_entry_bar_ts") or "").strip()
        exit_ts = str(audit.get("historical_resolution_bar_ts") or "").strip()
        if not signal or not entry or not exit_ts:
            raise ValueError(f"{oid}: historical signal/entry/resolution timestamps missing")
        rows.append({
            "paper_order_id": oid,
            "dependency_start_timestamp": dependency_start,
            "signal_timestamp": signal,
            "entry_timestamp": entry,
            "exit_timestamp": exit_ts,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "code_sha": code_sha or git_head(repo_root),
        "dataset_manifest_sha256": sha256(manifest_path),
        "instrument": root,
        "dependency_windows_source": {
            "path": str(dependency_windows_path),
            "sha256": sha256(dependency_windows_path),
        },
        "source_journals": [
            {"path": str(path), "sha256": sha256(path)} for path in journal_paths
        ],
        "resolved_outcomes": rows,
    }

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--journal", action="append", required=True)
    parser.add_argument("--dependency-windows", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--code-sha", default=None)
    args = parser.parse_args(argv)
    proof = build_gap_proof(
        repo_root=Path(args.repo_root).resolve(),
        strategy=args.strategy,
        instrument=args.instrument,
        manifest_path=Path(args.manifest).resolve(),
        journal_paths=[Path(x).resolve() for x in args.journal],
        dependency_windows_path=Path(args.dependency_windows).resolve(),
        code_sha=args.code_sha,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "output": str(output),
        "resolved_outcomes": len(proof["resolved_outcomes"]),
        "sha256": sha256(output),
    }, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
