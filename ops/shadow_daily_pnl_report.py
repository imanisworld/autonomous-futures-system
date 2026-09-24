#!/usr/bin/env python3
"""Daily shadow P&L report (read-only, evidence only).

Adds up one trading day of SHADOW_OUTCOME rows from the engine journal
(`journal_<UTC date>.jsonl`) into dollars: per market and per strategy,
before and after costs. Answers "how did the shadow do today?" without a
hand-run script.

Never grants execution eligibility. Never changes a rule. Reads journals only.

Day = the row's `candidate_day` (the trading day the candidate belongs to), so
the evening Asia session that starts on the previous UTC date is included.
Rows are de-duplicated by `candidate_key` (last row wins).

Costs: MNQ/MES use the proven forward-campaign assumptions (1 tick slippage,
$1.48 commission per round trip) — same model as gate_condition_report.
Other instruments are reported gross only. OPEN (unresolved at end of day) and
NO_FILL rows are counted but carry no dollars.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.futures_contracts import TICK_VALUE  # noqa: E402
from notifications import plain_english as pe  # noqa: E402
from ops.gate_condition_report import (  # noqa: E402
    COMMISSION_DOLLARS,
    COSTED_INSTRUMENTS,
    SLIPPAGE_TICKS,
    _post_discord,
    net_dollars,
)

ET = ZoneInfo("America/New_York")


def _empty() -> dict:
    return {"closed": 0, "wins": 0, "losses": 0, "open": 0, "no_fill": 0, "gross_usd": 0.0, "net_usd": None}


def load_outcomes(log_dir: str | Path, day: date) -> list[dict]:
    """SHADOW_OUTCOME rows whose candidate_day == day, de-duplicated by candidate_key."""
    wanted = day.isoformat()
    latest: dict[str, dict] = {}
    for offset in (-1, 0, 1):
        path = Path(log_dir) / f"journal_{(day + timedelta(days=offset)).isoformat()}.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "SHADOW_OUTCOME" or row.get("candidate_day") != wanted:
                    continue
                key = row.get("candidate_key") or row.get("event_id") or line
                latest[key] = row
    return list(latest.values())


def _add(bucket: dict, inst: str, result: str, ticks: float | None) -> None:
    if result == "OPEN":
        bucket["open"] += 1
        return
    if result not in ("WIN", "LOSS") or ticks is None:
        bucket["no_fill"] += 1
        return
    bucket["closed"] += 1
    bucket["wins" if result == "WIN" else "losses"] += 1
    tick_value = TICK_VALUE.get(inst)
    gross = float(ticks) * tick_value if tick_value is not None else 0.0
    bucket["gross_usd"] = round(bucket["gross_usd"] + gross, 2)
    net = net_dollars(inst, gross, tick_value)
    if net is not None:
        bucket["net_usd"] = round((bucket["net_usd"] or 0.0) + net, 2)


def build_report(rows: list[dict], day: date) -> dict:
    by_inst: dict[str, dict] = defaultdict(_empty)
    by_strat: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(_empty))
    total = _empty()
    for row in rows:
        inst = str(row.get("instrument") or "?")
        outcome = row.get("shadow_outcome") or {}
        result = str(outcome.get("result") or "")
        ticks = outcome.get("pnl_ticks")
        strat = str(row.get("strategy") or "?")
        for bucket in (by_inst[inst], by_strat[inst][strat], total):
            _add(bucket, inst, result, ticks)
    # Total net only counts costed markets; say so rather than mixing.
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "day": day.isoformat(),
        "candidates": len(rows),
        "total": total,
        "by_instrument": {k: by_inst[k] for k in sorted(by_inst)},
        "by_strategy": {k: {s: v[s] for s in sorted(v)} for k, v in sorted(by_strat.items())},
        "cost_model": {
            "instruments": list(COSTED_INSTRUMENTS),
            "commission_dollars": COMMISSION_DOLLARS,
            "slippage_ticks": SLIPPAGE_TICKS,
            "note": "total net_usd covers costed instruments only; others gross only",
        },
        "authority": "evidence_only",
    }


def _dollars(b: dict) -> str:
    if b["net_usd"] is not None:
        return f"{pe.money(round(b['net_usd']))[:-3]} after costs"
    return f"{pe.money(round(b['gross_usd']))[:-3]} before costs"


def _line(b: dict) -> str:
    extra = []
    if b["open"]:
        extra.append(f"{b['open']} still open")
    if b["no_fill"]:
        extra.append(f"{b['no_fill']} never filled")
    tail = f" ({', '.join(extra)})" if extra else ""
    return f"{b['closed']} trades, {b['wins']} won, {b['losses']} lost, {_dollars(b)}{tail}"


def format_digest(report: dict, *, top: int = 3) -> str:
    lines = [f"🧮 **Shadow P&L · {pe.et_date(report['day'])}**"]
    if not report["candidates"]:
        lines.append("No shadow trades recorded for this day.")
    else:
        lines.append(f"All markets: {_line(report['total'])}")
        for inst, b in report["by_instrument"].items():
            lines.append(f"{pe.market(inst)}: {_line(b)}")
        ranked = [
            (b["net_usd"] if b["net_usd"] is not None else b["gross_usd"], inst, s)
            for inst, strats in report["by_strategy"].items()
            for s, b in strats.items()
            if b["closed"]
        ]
        ranked.sort()
        if ranked:
            best = [f"{inst} {s} {pe.money(round(v))[:-3]}" for v, inst, s in reversed(ranked[-top:]) if v > 0]
            worst = [f"{inst} {s} {pe.money(round(v))[:-3]}" for v, inst, s in ranked[:top] if v < 0]
            lines.append(f"Best: {' · '.join(best) if best else 'none positive'}")
            lines.append(f"Worst: {' · '.join(worst) if worst else 'none negative'}")
    lines.append("[shadow only · 1 contract each · no orders · no rule change]")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument("--day", default=None, help="trading day YYYY-MM-DD (default: today in New York)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the digest")
    parser.add_argument("--discord", action="store_true", help="post the digest to DISCORD_ROUTE_DAILY_REPORT / DISCORD_WEBHOOK_URL")
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.day) if args.day else datetime.now(ET).date()
    report = build_report(load_outcomes(args.log_dir, day), day)
    try:
        out_dir = Path(args.log_dir)
        text = json.dumps(report, indent=2)
        (out_dir / f"shadow_daily_pnl_{day.isoformat()}.json").write_text(text)
        (out_dir / "shadow_daily_pnl_latest.json").write_text(text)
    except OSError:
        pass
    digest = format_digest(report)
    print(json.dumps(report, indent=2) if args.json else digest)
    if args.discord:
        url = os.getenv("DISCORD_ROUTE_DAILY_REPORT") or os.getenv("DISCORD_WEBHOOK_URL")
        if url:
            print("discord:", "ok" if _post_discord(url, digest) else "FAILED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
