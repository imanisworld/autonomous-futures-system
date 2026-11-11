#!/usr/bin/env python3
"""One authorized AFS-0523 reproduction-gate structural gate attempt at main ac334f5 (Imani GO t953u). No other scoring entrypoints."""
from __future__ import annotations

import os

# Set the Oct 4 cap before any repo imports. Required since #1053 / #1267.
os.environ["MAX_CONTRACTS_HARD_CAP"] = "1"

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Refuse to inherit market-data keys so this process cannot spend on vendor APIs.
for key in (
    "DATABENTO_API_KEY",
    "POLYGON_API_KEY",
    "POLYGON_API_KEY_BACKUP",
    "DATABENTO_KEY",
):
    os.environ.pop(key, None)

REPO = Path("/workspace/afs-gate-322-ac334f5")
EVIDENCE = Path("/workspace/afs-shared/evidence/afs0523_structural_gate_2026-11-11")
sys.path.insert(0, str(REPO))

from research.afs0523_322_repro_wrapper import (  # noqa: E402
    DATA_MANIFEST_SHA256,
    EOD_DATE,
    GATE_ARTIFACT,
    REPRO_EXPECTED_COUNT,
    RUN_AUTHORIZED,
    TRIAL_ID,
    TRIAL_LEDGER,
    run_reproduction_gate,
    verify_frozen_modules,
)

# load_dotenv is not called at import, but strip again in case a later import did.
for key in (
    "DATABENTO_API_KEY",
    "POLYGON_API_KEY",
    "POLYGON_API_KEY_BACKUP",
    "DATABENTO_KEY",
):
    os.environ.pop(key, None)
# Cap must still be exactly "1" after imports.
os.environ["MAX_CONTRACTS_HARD_CAP"] = "1"


def ts() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def main() -> int:
    print(f"{ts()} start authorized run_reproduction_gate structural attempt", flush=True)
    print(f"{ts()} repo={REPO}", flush=True)
    print(f"{ts()} TRIAL_ID={TRIAL_ID}", flush=True)
    print(f"{ts()} RUN_AUTHORIZED={RUN_AUTHORIZED}", flush=True)
    print(f"{ts()} DATA_MANIFEST_SHA256={DATA_MANIFEST_SHA256}", flush=True)
    print(f"{ts()} GATE_ARTIFACT={GATE_ARTIFACT}", flush=True)
    print(f"{ts()} artifact_exists_before={GATE_ARTIFACT.exists()}", flush=True)
    print(f"{ts()} MAX_CONTRACTS_HARD_CAP_set={os.environ.get('MAX_CONTRACTS_HARD_CAP') == '1'}", flush=True)
    print(f"{ts()} MAX_CONTRACTS_HARD_CAP={os.environ.get('MAX_CONTRACTS_HARD_CAP')!r}", flush=True)
    print(f"{ts()} databento_key_in_env={bool(os.environ.get('DATABENTO_API_KEY'))}", flush=True)
    print(f"{ts()} polygon_key_in_env={bool(os.environ.get('POLYGON_API_KEY'))}", flush=True)
    print(f"{ts()} trial_ledger={TRIAL_LEDGER}", flush=True)

    print(f"{ts()} calling verify_frozen_modules()", flush=True)
    verify_frozen_modules()
    print(f"{ts()} verify_frozen_modules_ok", flush=True)

    data_root = Path("/workspace/afs-shared/data/databento_afs0523_2026-11-09")
    research_15m = Path("reproduction/research_15m")
    audit_5m = Path("reproduction/audit_5m")
    print(
        f"{ts()} calling run_reproduction_gate("
        f"data_root={data_root!s}, research_15m={research_15m!s}, audit_5m={audit_5m!s})",
        flush=True,
    )
    try:
        payload = run_reproduction_gate(data_root, research_15m, audit_5m)
    except Exception:
        print(f"{ts()} run_reproduction_gate RAISED", flush=True)
        traceback.print_exc()
        return 2

    print(f"{ts()} run_reproduction_gate returned", flush=True)
    summary = {
        "pass": payload.get("pass"),
        "comparisons": payload.get("comparisons"),
        "outcome_records": payload.get("outcome_records"),
        "mismatch_count": len(payload.get("mismatches") or []),
        "mismatches_head": (payload.get("mismatches") or [])[:8],
        "data_gap_count": payload.get("data_gap_count"),
        "data_gaps": payload.get("data_gaps"),
        "invalid_entry_bracket_count": payload.get("invalid_entry_bracket_count"),
        "invalid_entry_brackets": payload.get("invalid_entry_brackets"),
        "gap_open_count": payload.get("gap_open_count"),
        "error": payload.get("error"),
        "claude_review": payload.get("claude_review"),
        "replay_ledger": payload.get("replay_ledger"),
        "replay_ledger_sha256": payload.get("replay_ledger_sha256"),
        "gate_artifact_path": payload.get("gate_artifact"),
        "research_15m_sha256": payload.get("research_15m_sha256"),
        "audit_5m_sha256": payload.get("audit_5m_sha256"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "data_root": payload.get("data_root"),
        "pulled_files": payload.get("pulled_files"),
        "prior_attempts": payload.get("prior_attempts"),
        "scoring_profile": payload.get("scoring_profile"),
        "expected_candidate_count": REPRO_EXPECTED_COUNT,
        "eod_date": EOD_DATE,
    }
    print(json.dumps(summary, indent=2, default=str), flush=True)
    out = EVIDENCE / "return_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"{ts()} wrote {out}", flush=True)
    print(f"{ts()} artifact_exists_after={GATE_ARTIFACT.exists()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
