#!/usr/bin/env python3
"""
scripts/corpus_v1_orphan_resolution.py

Read-only follow-up to the "23 non-WIN/LOSS orb_reclaim attempts" finding in
docs/strategy-validation-pass-2026-07-24.md. The operator's HOLD verdict on
that finding: excluding those 23 from win-rate/PF/expectancy math is not
neutral, because they are non-random missing data (all one strategy, all one
deterministic cause) and could bias the reported numbers.

Root cause (confirmed by reading replay/replay_engine.py directly): its main
per-day loop only scans `for future_idx in range(idx + 1, len(candles))`
where `candles` is that single day's candle file. `orb_reclaim` is not in
`execution/day_only_exit.py`'s DAY_ONLY_STRATEGIES set (only
strat_4hr_retrigger is), so the *strategy's own design* has no rule that
flattens it at day end -- replay/live would otherwise carry an open
orb_reclaim position forward until stop/target hits. The orphaning here is a
mechanical side effect of the day-sliced replay architecture (a fresh
PaperBroker per day file, no cross-day carry-forward -- see
memory/project_replay_identity_propagation_pr332.md), not a reflection of any
real flatten-at-EOD rule for this strategy. That makes "carry forward" the
right choice for reproducing the *strategy/replay* rule, not an arbitrary
analytical convention -- "forced close at day boundary" or "mark-to-market at
day boundary" would each impose an exit event the strategy's own design never
calls for.

IMPORTANT SCOPE LIMIT (operator correction, 2026-07-25): this does NOT prove
anything about live Tradovate broker order-management fidelity. Bracket
child orders (stop/target) submitted without an explicit GTC time-in-force
default to Day and can expire at session close, leaving a position open but
*unprotected* -- this happened for real (see
execution/tradovate_broker.py:802-826, MES 2026-07-21, fixed by making GTC
explicit; also memory/project_mes_orphan_incident.md). Carry-forward here
answers "what does the strategy/replay rule say should happen," not "what
would the live broker actually have done to this exact position" -- those
are different questions, and only the first is in scope for this script.

This script resolves each orphan (across ALL strategies, not hardcoded to
orb_reclaim -- see below) by literally continuing the replay: it restores
the exact position (direction/entry/stop/target/contracts already recorded
in the TRADE decision row) into a PaperBroker built from the SAME production
config every other Corpus v1 trade used (config.load_config() -- same
slippage/runner/breakeven settings, no new fill-model assumptions), then
feeds it subsequent days' real candle bars (already-downloaded, no new
Polygon pull) via the same broker.resolve_position() call replay_engine.py
itself uses, one bar at a time, until a Fill resolves it or the corpus's
candle data runs out. It does not modify replay_engine.py, PaperBroker, or
any strategy/gate code -- this is analysis-script orchestration of existing,
already-audited production fill logic, not a new implementation.

Caveat, disclosed rather than hidden: the entry price used is the TRADE
decision's *requested* setup.entry, not the slippage-adjusted fill price
PaperBroker would have recorded had this resolved normally (that price only
exists post-fill, which never happened for these orphans inside the original
per-day run). This is a small approximation (order of the configured
slippage_ticks) that could not be avoided without re-deriving the fill from
the entry bar itself, which is out of scope for this pass.

Orphan-finding is deliberately re-implemented here (not delegated to
adaptive.journal_reader.JournalReader) for one reason: TradeRecord does not
expose paper_order_id, and this script needs it as a stable join key for
merging resolutions back into scripts/corpus_v1_raw_trades.jsonl (see
scripts/corpus_v1_apply_orphan_correction.py). The finder logic below is a
faithful copy of JournalReader._trades_for_day's own open-position branch
(same conditions, same no-FIFO fail-closed semantics) -- `--verify` cross
checks its count against JournalReader's own open-trade count as a
self-consistency proof, not an assumption.

Usage:
    python3 scripts/corpus_v1_orphan_resolution.py
    python3 scripts/corpus_v1_orphan_resolution.py --out scripts/corpus_v1_orphan_resolution.json --verify
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptive.journal_reader import JournalReader  # noqa: E402
from config.settings import load_config  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402
from replay.candle_loader import ReplayCandleLoader  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
LOGS_BASE = "logs/replay_corpus_v1"
CANDLES_BASE = "data/replay_corpus_v1"
CORPUS_END = _date.fromisoformat("2026-07-23")
MAX_LOOKAHEAD_DAYS = 30  # generous; real resolutions land within a handful of days


def _find_orphans() -> list[dict]:
    """Every genuinely-open trade, any strategy -- real paper_order_id, no
    matching OUTCOME row anywhere in the day's file. Re-implements
    JournalReader._trades_for_day's exact open-position branch (approved
    TRADE decision, no non-terminal inline outcome, real order id, id not in
    the day's outcomes_by_order_id map) so paper_order_id itself can be
    carried through as the join key -- TradeRecord does not expose it.
    Generalized beyond orb_reclaim per the operator's 2026-07-25 note that
    the closure record covers the whole corpus, not one strategy; empirically
    all 23 in this corpus are orb_reclaim (see --verify), but the finder
    itself makes no such assumption."""
    orphans = []
    for instr in INSTRUMENTS:
        log_dir = Path(LOGS_BASE) / instr
        for path in sorted(log_dir.glob("journal_*.jsonl")):
            day = path.stem.replace("journal_", "")
            entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            outcome_ids = {
                (e.get("outcome") or {}).get("paper_order_id")
                for e in entries
                if e.get("type") == "OUTCOME" and (e.get("outcome") or {}).get("paper_order_id")
            }
            for e in entries:
                if e.get("decision") != "TRADE":
                    continue
                if (e.get("risk_check") or {}).get("result") != "APPROVED":
                    continue
                inline = e.get("outcome") or {}
                if inline.get("result") in ("WIN", "LOSS", "BREAKEVEN"):
                    continue  # already resolved inline (e.g. strat_212/122 pre_resolved)
                order_id = e.get("paper_order_id")
                if not order_id:
                    continue  # unjoinable_legacy -- a different bucket, not ours
                if order_id in outcome_ids:
                    continue  # has a real outcome row -- not orphaned
                setup = e.get("setup") or {}
                orphans.append({
                    "date": day,
                    "instrument": e.get("instrument", instr),
                    "strategy": setup.get("strategy", "unknown"),
                    "direction": setup.get("direction"),
                    "entry": setup.get("entry"),
                    "stop": setup.get("stop"),
                    "target": setup.get("target"),
                    "contracts": int(setup.get("contracts") or 1),
                    "paper_order_id": order_id,
                })
    return orphans


def _verify_against_journal_reader(orphans: list[dict]) -> None:
    """Self-consistency proof: the count and (date, instrument, strategy) set
    found above must exactly match what the already-audited JournalReader
    reports as open (result is None, not unjoinable_legacy) -- proves the
    re-implementation above didn't silently diverge from the canonical
    identity-join semantics."""
    expected = set()
    for instr in INSTRUMENTS:
        log_dir = Path(LOGS_BASE) / instr
        reader = JournalReader(log_dir)
        for path in sorted(log_dir.glob("journal_*.jsonl")):
            day = _date.fromisoformat(path.stem.replace("journal_", ""))
            for r in reader._trades_for_day(day):
                if r.result is None and not r.unjoinable_legacy:
                    expected.add((r.date, r.instrument, r.strategy))

    got = {(o["date"], o["instrument"], o["strategy"]) for o in orphans}
    if got != expected:
        only_ours = got - expected
        only_reader = expected - got
        raise AssertionError(
            "Orphan finder diverges from JournalReader's own open-trade set: "
            f"only-in-finder={only_ours} only-in-JournalReader={only_reader}"
        )
    print(f"[orphan_resolution] --verify passed: {len(orphans)} orphans match JournalReader exactly")


def _candle_path(instr: str, day: _date) -> Path:
    return Path(CANDLES_BASE) / instr / f"{instr}_{day.isoformat()}.jsonl"


def _make_broker(config) -> PaperBroker:
    """Identical construction to replay/replay_engine.py's ReplayEngine.run()
    -- same fill model as every other Corpus v1 trade, no new assumptions."""
    return PaperBroker(
        starting_balance=config.position_sizing.starting_balance,
        slippage_ticks=float(getattr(config, "fill_slippage_ticks", 0.0) or 0.0),
        pessimistic_both_hit=bool(getattr(config, "fill_pessimistic_both_hit", False)),
        breakeven_at_1r=bool(getattr(config, "breakeven_at_1r", True)),
        runner_mode=bool(getattr(config, "runner_mode", False)),
        runner_activation_r=float(getattr(config, "runner_activation_r", 1.0) or 1.0),
        runner_trail_r=float(getattr(config, "runner_trail_r", 0.5) or 0.5),
        entry_fill_model=str(getattr(config, "entry_fill_model", "market") or "market"),
        entry_tolerance_ticks_by_root=dict(
            getattr(config, "entry_tolerance_ticks_by_root", {}) or {}
        ),
    )


def resolve_orphan(orphan: dict, config) -> dict:
    broker = _make_broker(config)
    broker.restore_position(
        instrument=orphan["instrument"],
        direction=orphan["direction"],
        entry=orphan["entry"],
        stop=orphan["stop"],
        target=orphan["target"],
        contracts=orphan["contracts"],
    )

    loader = ReplayCandleLoader()
    entry_day = _date.fromisoformat(orphan["date"])
    day = entry_day + timedelta(days=1)
    bars_scanned = 0
    days_scanned = 0

    while day <= CORPUS_END and (day - entry_day).days <= MAX_LOOKAHEAD_DAYS:
        path = _candle_path(orphan["instrument"], day)
        if path.exists():
            days_scanned += 1
            for c in loader.load_jsonl(path):
                bars_scanned += 1
                fill = broker.resolve_position(NextBarOHLC(open=c.open, high=c.high, low=c.low))
                if fill is not None:
                    return {
                        **orphan,
                        "resolution": "RESOLVED",
                        "resolved_on": day.isoformat(),
                        "days_to_resolve": (day - entry_day).days,
                        "bars_scanned": bars_scanned,
                        "result": fill.result,
                        "exit_price": fill.exit_price,
                        "pnl_ticks": fill.pnl_ticks,
                        "pnl_dollars": fill.pnl_dollars,
                    }
        day += timedelta(days=1)

    return {
        **orphan,
        "resolution": "STILL_UNRESOLVED",
        "resolved_on": None,
        "days_to_resolve": None,
        "bars_scanned": bars_scanned,
        "days_scanned": days_scanned,
        "result": None,
        "exit_price": None,
        "pnl_ticks": None,
        "pnl_dollars": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Carry-forward resolution of day-boundary replay orphans (any strategy)")
    parser.add_argument("--out", default=None)
    parser.add_argument("--verify", action="store_true",
                         help="Cross-check the orphan set against JournalReader's own open-trade count before resolving")
    args = parser.parse_args()

    config = load_config()
    orphans = _find_orphans()
    print(f"[orphan_resolution] found {len(orphans)} orphans across all strategies (real identity, no outcome row)\n")

    if args.verify:
        _verify_against_journal_reader(orphans)

    results = [resolve_orphan(o, config) for o in orphans]

    resolved = [r for r in results if r["resolution"] == "RESOLVED"]
    unresolved = [r for r in results if r["resolution"] != "RESOLVED"]

    print(f"{'Date':<12} {'Instr':<5} {'Strategy':<14} {'Dir':<5} {'->Resolved':<12} {'Days':>5} {'Result':<6} {'PnL':>10}")
    print("-" * 85)
    for r in results:
        pnl_str = "" if r["pnl_dollars"] is None else f"${r['pnl_dollars']:.2f}"
        print(f"{r['date']:<12} {r['instrument']:<5} {r['strategy']:<14} {r['direction']:<5} "
              f"{str(r['resolved_on']):<12} {str(r['days_to_resolve']):>5} "
              f"{str(r['result']):<6} {pnl_str:>10}")

    wins = sum(1 for r in resolved if r["result"] == "WIN")
    losses = sum(1 for r in resolved if r["result"] == "LOSS")
    net = sum(r["pnl_dollars"] or 0.0 for r in resolved)
    print(f"\nResolved: {len(resolved)}/{len(results)} (wins={wins} losses={losses} net=${net:,.2f})")
    if unresolved:
        print(f"Still unresolved after {MAX_LOOKAHEAD_DAYS}-day lookahead or corpus end: {len(unresolved)}")
        for r in unresolved:
            print(f"  {r['date']} {r['instrument']} -- scanned {r['days_scanned']} days / {r['bars_scanned']} bars")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
        print(f"\n[orphan_resolution] wrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
