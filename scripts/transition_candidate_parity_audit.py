#!/usr/bin/env python3
"""Reproduce Transition preserved-candidate identity from the 5m corpus.

Read-only research audit. No strategy/risk/runtime/deployment behavior changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import sys
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from config.settings import load_config
from context.transition_400t_30m_research import STARTING_BALANCE, isolated_config
from replay.replay_engine import ReplayCandleLoader, ReplayEngine
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine
from strategy.transition_failed_breakdown_reclaim import (
    detect_transition_failed_breakdown_reclaim,
    detect_transition_geometry,
)



def _dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))



def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _corpus_fingerprint(corpus: Path) -> dict:
    paths = sorted(corpus.glob("MNQ_*.jsonl"))
    digest = hashlib.sha256()
    total_bytes = 0
    for path in paths:
        data = path.read_bytes()
        total_bytes += len(data)
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
    return {"files": len(paths), "bytes": total_bytes, "sha256": digest.hexdigest()}

def build_report(*, corpus: Path, saved_path: Path) -> dict:
    saved = json.loads(saved_path.read_text(encoding="utf-8"))["candidates"]
    saved_set = {
        _dt(row["ts"])
        for row in saved
        if str(row.get("instrument", "MNQ")).upper() == "MNQ"
    }

    loader = ReplayCandleLoader()
    actual: set[datetime] = set()
    geometry: set[datetime] = set()
    conditions_at_saved: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    terminal_blockers: Counter[str] = Counter()

    with tempfile.TemporaryDirectory(prefix="transition_candidate_parity_") as tmp:
        base_config = load_config()
        decision_config = isolated_config(base_config)
        engine = ReplayEngine(config=base_config, log_dir=tmp)
        decision_engine = DecisionEngine(config=decision_config)
        history: deque[dict] = deque(maxlen=8)
        for path in sorted(corpus.glob("MNQ_*.jsonl")):
            prev = prev_prev = None
            for candle in loader.load_jsonl(path):
                state = engine._market_state_from_candle(candle, prev, prev_prev)
                current = _dt(candle.timestamp)
                history.append(
                    {
                        "ts": candle.timestamp,
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                    }
                )
                recent = [
                    bar
                    for bar in history
                    if 0 <= (current.date() - _dt(bar["ts"]).date()).days < 3
                ]
                if current in saved_set:
                    conditions_at_saved[str(state.market_condition)] += 1
                    state.bar_history_15m = list(recent)
                    decision = decision_engine.evaluate(
                        state,
                        DailyState(
                            account_balance=STARTING_BALANCE,
                            account_peak_balance=STARTING_BALANCE,
                        ),
                    )
                    decisions[decision.decision] += 1
                    if decision.decision != "TRADE":
                        terminal = (decision.failed_gates or ["NO_GATE_RECORDED"])[-1]
                        terminal_blockers[terminal] += 1
                if detect_transition_failed_breakdown_reclaim(state, recent) is not None:
                    actual.add(current)
                if detect_transition_geometry(state, recent) is not None:
                    geometry.add(current)
                prev_prev = prev
                prev = candle

    return {
        "corpus": str(corpus),
        "corpus_fingerprint": _corpus_fingerprint(corpus),
        "saved_path": str(saved_path),
        "saved_sha256": _sha256(saved_path),
        "saved_candidates": len(saved_set),
        "legacy_condition_detector": {
            "candidates": len(actual),
            "intersection": len(actual & saved_set),
            "saved_recall": len(actual & saved_set) / len(saved_set) if saved_set else None,
            "precision": len(actual & saved_set) / len(actual) if actual else None,
            "saved_only": len(saved_set - actual),
            "detector_only": len(actual - saved_set),
        },
        "objective_geometry": {
            "candidates": len(geometry),
            "intersection": len(geometry & saved_set),
            "saved_recall": len(geometry & saved_set) / len(saved_set) if saved_set else None,
            "precision": len(geometry & saved_set) / len(geometry) if geometry else None,
            "saved_only": len(saved_set - geometry),
            "geometry_only": len(geometry - saved_set),
        },
        "replay_market_conditions_at_saved_candidates": dict(conditions_at_saved),
        "full_decision_engine": {
            "decisions": dict(decisions),
            "terminal_blockers": dict(terminal_blockers),
        },
        "verdict": (
            "GEOMETRY_EXACT_CONDITION_LABEL_DIVERGES"
            if geometry == saved_set and actual != saved_set
            else "PARITY_NOT_PROVEN"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "data/replay_polygon_5m/MNQ",
    )
    parser.add_argument(
        "--saved",
        type=Path,
        default=ROOT / "logs/missed_move_transition_MNQ_costed.json",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = build_report(corpus=args.corpus, saved_path=args.saved)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["verdict"] == "GEOMETRY_EXACT_CONDITION_LABEL_DIVERGES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
