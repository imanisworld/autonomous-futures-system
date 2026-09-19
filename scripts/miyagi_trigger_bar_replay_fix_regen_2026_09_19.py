from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from research.bars_12hr_miyagi_loader import load_5m_day
from research.replay_12hr_miyagi_honest_fill import build_sensitivity, run_replay

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "docs" / "strategy-rules" / "evidence_12hr_miyagi"
STUDY_START = date(2024, 7, 2)
STUDY_END = date(2026, 6, 26)
SOURCE_SUFFIX = "_corrected_2026-09-18"
OUT_SUFFIX = "_trigger_bar_corrected_2026-09-19"
PREREG_COMMIT = "38da280e2435f53387e034e1f1fca57040a70233"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-root", type=Path, required=True)
    args = ap.parse_args()

    manifest = {
        "status": "RESEARCH_REPLAY_FIX_ONLY",
        "prereg_commit": PREREG_COMMIT,
        "study_range": {"start": STUDY_START.isoformat(), "end": STUDY_END.isoformat()},
        "instruments": {},
    }

    for instrument in ("MNQ", "MES"):
        src = EVIDENCE_DIR / f"{instrument.lower()}_results{SOURCE_SUFFIX}.json"
        old = json.loads(src.read_text())
        candidates = old["candidates"]
        replay_signals = [
            {
                "date": date.fromisoformat(c["date"]),
                "instrument": c["instrument"],
                "direction": c["direction"],
                "entry_trigger": c["entry_trigger"],
                "stop": c["stop"],
                "target": c["target"],
                "target_2": c["target_2"],
            }
            for c in candidates
        ]

        bars = []
        source_hashes = {}
        for sig in replay_signals:
            day = sig["date"]
            p = args.cache_root / instrument / f"{instrument}_{day.isoformat()}.jsonl"
            day_bars = load_5m_day(args.cache_root, instrument, day)
            bars.extend(day_bars)
            if p.exists():
                source_hashes[p.name] = sha256(p)

        base_case = run_replay(
            replay_signals,
            bars,
            study_start=STUDY_START,
            study_end=STUDY_END,
            slippage_ticks=2.0,
        )
        sensitivity = build_sensitivity(
            replay_signals,
            bars,
            study_start=STUDY_START,
            study_end=STUDY_END,
        )

        result = {
            "instrument": instrument,
            "study_range": old["study_range"],
            "candidate_count": len(candidates),
            "candidates": candidates,
            "invalidations": old["invalidations"],
            "granularity_ambiguity": old["granularity_ambiguity"],
            "source_evidence_file": src.name,
            "source_evidence_sha256": sha256(src),
            "source_bar_sha256": source_hashes,
            "base_case_slippage_ticks": 2,
            "base_case": base_case,
            "slippage_sensitivity": {
                k: {
                    "overall": v["overall"],
                    "halves": v["halves"],
                    "directions": v["directions"],
                    "by_month": v["by_month"],
                    "by_year": v["by_year"],
                }
                for k, v in sensitivity.items()
            },
        }
        out = EVIDENCE_DIR / f"{instrument.lower()}_results{OUT_SUFFIX}.json"
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

        manifest["instruments"][instrument] = {
            "candidate_count": len(candidates),
            "candidate_identity_sha256": hashlib.sha256(
                json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "source_evidence_sha256": result["source_evidence_sha256"],
            "output_file": out.name,
            "output_sha256": sha256(out),
            "base_case": {
                "fills": base_case["overall"]["fills"],
                "wins": base_case["overall"]["wins"],
                "losses": base_case["overall"]["losses"],
                "net_pnl": base_case["overall"]["net_pnl"],
                "profit_factor": base_case["overall"]["profit_factor"],
                "max_drawdown": base_case["overall"]["max_drawdown"],
            },
        }

    manifest_path = EVIDENCE_DIR / "trigger_bar_corrected_manifest_2026-09-19.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(manifest_path)


if __name__ == "__main__":
    main()
