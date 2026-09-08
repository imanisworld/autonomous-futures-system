"""Inverse ORB — decision-time replay of the frozen 63-arm population through
the PRODUCTION execution path.

Supersedes `inverse_orb_canonical_ioc_proof_2026-09-07.json` (+$1,026.64 /
PF 5.28), which is retired as evidence for two independent reasons found by
the 2026-09-08 forensic audit:

  1. its fill reference was the OPEN of the 5-minute bar stamped
     ``bar_ts + 20m`` — one full 5m bar AFTER the 15-minute decision candle
     completes (``bar_ts`` is the candle's open; it closes at ``bar_ts + 15m``).
     That is the same arrival-bar look-ahead class the VWAP Hold
     reconciliation rejected on 2026-09-07;
  2. 37 of its 57 fills landed beyond their own mirrored stop and were held
     anyway, booking a profitable ``STOP_HIT`` labelled ``LOSS``. PaperBroker
     now refuses those fills on every entry path (#508).

Contract (everything else frozen, unchanged from the retired proof):
  * population: the frozen proof's 63 rows (fingerprint re-computed and
    asserted equal to the journal-derived ``f32b1b1d…2da2``);
  * decision_close_ts = bar_ts + 15m; the market is the OPEN of the 5m bar
    stamped exactly at decision_close_ts — never a later bar. Enforced by
    ``scripts.vwap_hold_evidence_package.assert_decision_time_reference``,
    which raises ``LookaheadError`` if the reference is later;
  * order = production ``context.mnq_orb_breakout_inverse_paper.mirror_order``;
  * fill = production ``PaperBroker(entry_fill_model="ioc_limit")`` with the
    lane's 8-tick marketable tolerance, 1 adverse tick, pessimistic same-bar,
    breakeven off — no duplicated fill math;
  * ``ENTRY_BRACKET_INVALID_AT_FILL`` (#508) is a rejection, not a trade;
  * resolution via ``PaperBroker.resolve_position`` over 5m bars at/after the
    arrival bar (the arrival bar itself included, stop-first);
  * 1 MNQ contract, $1.48 round-trip commission at the metrics layer;
  * result label follows signed net P&L.

Inputs: the committed proof JSON and the local 5-minute corpus
``data/replay_polygon_5m/MNQ`` (gitignored; override with
``AFS_REPLAY_5M_DIR``). Output: ``scripts/inverse_orb_decision_time_replay_2026-09-08.json``.

Run:  PYTHONPATH=. python3 scripts/inverse_orb_decision_time_replay.py
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from context.mnq_orb_breakout_inverse_paper import MARKETABLE_TICKS, mirror_order  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402
from scripts.vwap_hold_evidence_package import assert_decision_time_reference  # noqa: E402

PROOF = REPO / "scripts" / "inverse_orb_canonical_ioc_proof_2026-09-07.json"
OUT = REPO / "scripts" / "inverse_orb_decision_time_replay_2026-09-08.json"
DATA = Path(os.environ.get("AFS_REPLAY_5M_DIR") or (REPO / "data" / "replay_polygon_5m" / "MNQ"))

TICK = 0.25
COMMISSION_RT = 1.48
DECISION_OFFSET = timedelta(minutes=15)


def _ts(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))


def population_fingerprint(rows: list[dict]) -> str:
    """SHA-256 of the sorted (bar_ts, direction, entry, stop, target) tuples —
    the identity the 2026-09-07 proof recorded as ``fingerprint_sha256``."""
    tuples = sorted(
        (r["bar_ts"], r["source_direction"], r["source_entry"], r["source_stop"], r["source_target"])
        for r in rows
    )
    return hashlib.sha256(json.dumps(tuples).encode()).hexdigest()


def corpus_available() -> bool:
    return DATA.is_dir() and any(DATA.glob("MNQ_*.jsonl"))


class _Bars:
    def __init__(self) -> None:
        self._cache: dict[str, list[dict]] = {}

    def for_date(self, date: str) -> list[dict]:
        if date not in self._cache:
            path = DATA / f"MNQ_{date}.jsonl"
            if not path.exists():
                raise FileNotFoundError(
                    f"{path} missing — data/replay_polygon_5m is gitignored (.gitignore:25); "
                    "restore the local corpus or set AFS_REPLAY_5M_DIR"
                )
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            rows.sort(key=lambda b: b["timestamp"])
            self._cache[date] = rows
        return self._cache[date]


def replay_arm(row: dict, bars: _Bars) -> dict:
    decision_close = _ts(row["bar_ts"]) + DECISION_OFFSET
    day = bars.for_date(row["date"])
    idx = next((i for i, b in enumerate(day) if _ts(b["timestamp"]) == decision_close), None)
    rec = {
        "date": row["date"],
        "bar_ts": row["bar_ts"],
        "decision_close_ts": decision_close.isoformat(),
        "session": row["session"],
        "source_direction": row["source_direction"],
        "source_entry": row["source_entry"],
    }
    if idx is None:
        rec["status"] = "NO_ARRIVAL_BAR"
        return rec
    arrival = day[idx]
    # Hard look-ahead guard: the reference must be knowable at decision time.
    assert_decision_time_reference(
        {
            "status": "OK",
            "reference_ts": _ts(arrival["timestamp"]),
            "reference_price_field": "arrival_bar_open",
        },
        decision_ts=decision_close,
    )
    market = float(arrival["open"])
    risk = abs(row["source_entry"] - row["source_stop"])
    reward = abs(row["source_target"] - row["source_entry"])
    source = BracketOrder(
        instrument="MNQ",
        direction=row["source_direction"],
        entry=row["source_entry"],
        stop=row["source_stop"],
        target=row["source_target"],
        rr_ratio=round(reward / risk, 2) if risk else 0.0,
        strategy="orb_breakout",
        contracts=1,
        min_rr_ratio=2.0,
        max_stop_ticks=120.0,
    )
    inverse = mirror_order(source)
    broker = PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": MARKETABLE_TICKS},
    )
    fill = broker.execute_bracket(inverse, market_price=market)
    favourable = (market - inverse.entry) if inverse.direction == "SHORT" else (inverse.entry - market)
    rec.update(
        direction=inverse.direction,
        planned_entry=inverse.entry,
        stop=inverse.stop,
        target=inverse.target,
        arrival_ts=arrival["timestamp"],
        market=market,
        favourable_detachment_ticks=round(favourable / TICK, 1),
    )
    if fill.result == "CANCELLED":
        rec.update(status=fill.exit_reason, fill=fill.entry_price)
        return rec
    resolved = None
    for bar in day[idx:]:
        resolved = broker.resolve_position(NextBarOHLC(open=bar["open"], high=bar["high"], low=bar["low"]))
        if resolved is not None:
            rec["exit_ts"] = bar["timestamp"]
            break
    if resolved is None:
        rec.update(status="UNRESOLVED_EOD", fill=fill.entry_price)
        return rec
    net = round(float(resolved.pnl_dollars) - COMMISSION_RT, 2)
    rec.update(
        status="FILLED",
        fill=resolved.entry_price,
        exit=resolved.exit_price,
        exit_reason=resolved.exit_reason,
        broker_result_label=resolved.result,
        gross=round(float(resolved.pnl_dollars), 2),
        net=net,
        result="WIN" if net > 0 else ("LOSS" if net < 0 else "BREAKEVEN"),
    )
    return rec


def summarise(rows: list[dict]) -> dict:
    filled = [r for r in rows if r["status"] == "FILLED"]
    nets = [r["net"] for r in filled]
    gp = sum(v for v in nets if v > 0)
    gl = -sum(v for v in nets if v < 0)
    equity = peak = dd = 0.0
    for v in nets:
        equity += v
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {
        "attempts": len(rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "fills": len(filled),
        "invalid_at_fill": sum(1 for r in rows if r["status"] == "ENTRY_BRACKET_INVALID_AT_FILL"),
        "no_fills": sum(1 for r in rows if r["status"] == "ENTRY_NOT_FILLED"),
        "wins": sum(1 for v in nets if v > 0),
        "losses": sum(1 for v in nets if v < 0),
        "gross": round(sum(r["gross"] for r in filled), 2),
        "commission": round(COMMISSION_RT * len(filled), 2),
        "net": round(sum(nets), 2),
        "pf": round(gp / gl, 4) if gl else None,
        "expectancy_per_fill": round(sum(nets) / len(filled), 4) if filled else None,
        "max_dd": round(dd, 2),
        "label_contradictions": sum(1 for r in filled if r["broker_result_label"] != r["result"]),
    }


def build_report() -> dict:
    proof = json.loads(PROOF.read_text())
    rows = proof["rows"]
    fingerprint = population_fingerprint(rows)
    frozen = proof["manifest"]["fingerprint_sha256"]
    if fingerprint != frozen:
        raise SystemExit(f"population fingerprint mismatch: {fingerprint} != frozen {frozen}")
    bars = _Bars()
    out_rows = [replay_arm(r, bars) for r in rows]
    mid = proof["halves"]["midpoint_day"]
    detach = [r["favourable_detachment_ticks"] for r in out_rows if "favourable_detachment_ticks" in r]
    return {
        "generated_for": "inverse ORB frozen 63-arm population, decision-time replay",
        "supersedes": PROOF.name,
        "population_fingerprint": fingerprint,
        "population_n": len(rows),
        "contract": {
            **proof["manifest"]["inverse_contract"],
            "arrival_reference": "open of the 5m bar stamped bar_ts+15m (decision close); look-ahead asserted",
            "retired_reference": "open of the 5m bar stamped bar_ts+20m (one 5m bar late)",
            "bracket_validity_at_fill": "PaperBroker ENTRY_BRACKET_INVALID_AT_FILL (#508), rejection",
            "result_label": "signed net P&L",
            "execution_path": "context.mnq_orb_breakout_inverse_paper.mirror_order -> execution.paper_broker.PaperBroker",
        },
        "overall": summarise(out_rows),
        "H1": summarise([r for r in out_rows if r["date"] < mid]),
        "H2": summarise([r for r in out_rows if r["date"] >= mid]),
        "midpoint_day": mid,
        "sessions": {s: summarise([r for r in out_rows if r["session"] == s]) for s in ("asian", "london", "new_york")},
        "favourable_detachment_ticks": {"median": statistics.median(detach), "max": max(detach)},
        "rows": out_rows,
    }


def main() -> None:
    if not corpus_available():
        raise SystemExit(f"no 5m bar files under {DATA}; the corpus is gitignored and not in a fresh clone")
    report = build_report()
    OUT.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("population_fingerprint", "overall", "H1", "H2")}, indent=1))
    print("->", OUT.relative_to(REPO))


if __name__ == "__main__":
    main()
