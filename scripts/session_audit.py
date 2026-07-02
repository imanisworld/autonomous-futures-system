#!/usr/bin/env python3
"""Reproducible session-audit report from journal_*.jsonl files.

Regenerates the 4-section forensic report:
  1. All TRADE decisions with direction, 15m trend, signa daily_direction/status,
     previous-day context, and outcome classification (filled win/loss amount,
     IOC-CANCELLED, PHANTOM-CLEARED via auto-reconcile, ORDER_IDS presence).
  2. Order-failure breakdown + per-setup fill rates.
  3. RISK_REJECTED full detail + CONFIG_BLOCKED summary + NO_TRADE failed_gates
     aggregates per day/instrument + near-misses (formed setup or non-empty
     shadow_candidates) with their blocking gates.
  4. Missed-long analysis: bars with LONG shadow candidates or formed LONG setups
     that produced no trade, grouped by strategy and blocking gate, with
     ema_pullback_trend and *_observed strategies flagged as non-executable.

Usage:
  python3 scripts/session_audit.py journal_2026-06-24.jsonl [more.jsonl ...]
  python3 scripts/session_audit.py --journal-dir logs/ --from 2026-06-24 --to 2026-07-01
  ... [--output report.md]

No network. Stdlib only. Numeric ids of 8+ digits are masked to last-4 in output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Shadow strategies that are not executable by the live engine (observe-only)
# and/or documented losers -- flagged separately in Section 4.
KNOWN_LOSER_STRATEGIES = {
    "ema_pullback_trend": "known loser (31% WR backtest), shadow-only / non-executable",
}
OBSERVED_SUFFIX = "_observed"

ID_MASK_RE = re.compile(r"\d{8,}")


def mask_ids(text: str) -> str:
    """Mask any 8+ digit run (broker order/account ids) down to its last 4 digits."""
    return ID_MASK_RE.sub(lambda m: "…" + m.group()[-4:], text)


def day_of(path: Path) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else path.stem


def load_entries(paths):
    entries = []
    for p in paths:
        day = day_of(p)
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                e["_day"] = day
                entries.append(e)
    entries.sort(key=lambda e: (e.get("ts") or ""))
    return entries


# ---------------------------------------------------------------- pairing


def pair_followers(trades, events):
    """Greedily pair each TRADE with the nearest *following* event (same
    instrument), consuming each event at most once. Returns {id(trade): event}."""
    paired = {}
    used = set()
    for t in sorted(trades, key=lambda e: e.get("ts") or ""):
        best = None
        for ev in events:
            if id(ev) in used:
                continue
            if ev.get("instrument") != t.get("instrument"):
                continue
            if (ev.get("ts") or "") < (t.get("ts") or ""):
                continue
            if best is None or (ev["ts"] < best["ts"]):
                best = ev
        if best is not None:
            used.add(id(best))
            paired[id(t)] = best
    return paired


def classify_outcome(outcome_event):
    """Classify a paired OUTCOME event into a report label."""
    if outcome_event is None:
        return "NO-OUTCOME", None
    o = outcome_event.get("outcome") or {}
    result = (o.get("result") or "").upper()
    reason = o.get("exit_reason") or ""
    pnl = o.get("pnl_dollars")
    if "auto-reconcile" in reason:
        return "PHANTOM-CLEARED", pnl
    if result == "CANCELLED":
        return "IOC-CANCELLED", pnl
    if result == "WIN":
        return "FILLED-WIN", pnl
    if result == "LOSS":
        return "FILLED-LOSS", pnl
    return result or "UNKNOWN", pnl


# ---------------------------------------------------------------- helpers


def fmt_money(v):
    if v is None:
        return "?"
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def prev_day_summary(ctx):
    pd = (ctx or {}).get("previous_day") or {}
    close = (ctx or {}).get("close")
    pdc = pd.get("close")
    if close is not None and pdc is not None:
        vs_pdc = "close>pdc" if close > pdc else ("close<pdc" if close < pdc else "close=pdc")
    else:
        vs_pdc = "vs_pdc=?"
    return (
        f"vs_pdh={pd.get('price_vs_pdh')} vs_pdl={pd.get('price_vs_pdl')} "
        f"pdc={pd.get('close')} {vs_pdc}"
    )


def direction_summary(entry):
    setup = entry.get("setup") or {}
    context = entry.get("context") or {}
    htf = context.get("htf") or {}
    daily = setup.get("daily_direction") or htf.get("daily_direction")
    four_hour = setup.get("four_hour_direction") or htf.get("four_hour_direction")
    primary = setup.get("htf_primary_direction")
    if primary is None:
        primary = "LONG" if daily == "UP" else "SHORT" if daily == "DOWN" else (
            "LONG" if four_hour == "UP" else "SHORT" if four_hour == "DOWN" else None
        )
    role = setup.get("direction_role")
    if role is None:
        direction = setup.get("direction")
        if primary is None:
            role = "UNRESOLVED"
        elif direction == primary:
            role = "PRIMARY"
        else:
            role = "UNRESOLVED"
    return role, primary, daily, four_hour


def is_near_miss(e):
    return (
        bool(e.get("setup"))
        or bool(e.get("shadow_candidates"))
        or bool(e.get("candidate_audit"))
    )


# ---------------------------------------------------------------- sections


def section1(trades, outcome_by_trade, orderids_by_trade, out):
    out.append("## 1. TRADE decisions")
    out.append("")
    if not trades:
        out.append("_No TRADE decisions in range._")
        out.append("")
        return
    out.append(f"{len(trades)} TRADE decisions.")
    out.append("")
    for t in trades:
        s = t.get("setup") or {}
        c = t.get("context") or {}
        tr = c.get("trend") or {}
        sig = c.get("signa") or {}
        label, pnl = classify_outcome(outcome_by_trade.get(id(t)))
        oi = orderids_by_trade.get(id(t))
        ts = (t.get("ts") or "")[:16].replace("T", " ")
        out.append(
            f"### {ts} {t.get('instrument')} {s.get('strategy')} {s.get('direction')}"
        )
        out.append(
            f"- entry={s.get('entry')} stop={s.get('stop')} target={s.get('target')} "
            f"rr={s.get('rr_ratio')} contracts={s.get('contracts')}"
        )
        role, primary, daily, four_hour = direction_summary(t)
        out.append(
            f"- direction role: {role} | primary={primary} | "
            f"daily={daily} 4H={four_hour}"
        )
        out.append(
            f"- 15m trend: {tr.get('direction')}/{tr.get('strength')} | "
            f"condition={t.get('market_condition')} regime={t.get('regime')}"
        )
        out.append(
            f"- signa: daily_direction={sig.get('daily_direction')} "
            f"grade={sig.get('grade')} status={t.get('signa_status')}"
        )
        out.append(f"- previous day: {prev_day_summary(c)}")
        gates = t.get("failed_gates") or []
        if gates:
            out.append(f"- failed_gates (non-blocking at TRADE time): {', '.join(gates)}")
        if label in ("FILLED-WIN", "FILLED-LOSS"):
            o = (outcome_by_trade.get(id(t)) or {}).get("outcome") or {}
            out.append(
                f"- outcome: **{label}** {fmt_money(pnl)} "
                f"(exit_reason={o.get('exit_reason')}, exit={o.get('exit_price')})"
            )
        else:
            out.append(f"- outcome: **{label}**")
        if oi:
            ids = oi.get("order_ids") or {}
            masked = {k: mask_ids(str(v)) for k, v in ids.items() if k != "instrument"}
            out.append(f"- order ids logged: yes ({masked})")
        else:
            out.append("- order ids logged: NO")
        out.append("")


def section2(trades, outcome_by_trade, out):
    out.append("## 2. Order-failure breakdown + per-setup fill rates")
    out.append("")
    labels = Counter()
    per_strat = defaultdict(lambda: Counter())
    pnl_by_strat = defaultdict(float)
    for t in trades:
        strat = (t.get("setup") or {}).get("strategy") or "?"
        label, pnl = classify_outcome(outcome_by_trade.get(id(t)))
        labels[label] += 1
        per_strat[strat][label] += 1
        per_strat[strat]["total"] += 1
        if pnl:
            pnl_by_strat[strat] += pnl
    out.append("### Outcome breakdown (all TRADE decisions)")
    out.append("")
    total = len(trades) or 1
    for label, n in labels.most_common():
        out.append(f"- {label}: {n} ({100.0 * n / total:.0f}%)")
    out.append("")
    out.append("### Per-setup fill rates")
    out.append("")
    out.append(
        "| strategy | trades | filled W | filled L | IOC-cancelled | phantom | no-outcome | fill rate | net $ |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|")
    for strat in sorted(per_strat, key=lambda s: -per_strat[s]["total"]):
        c = per_strat[strat]
        filled = c["FILLED-WIN"] + c["FILLED-LOSS"]
        tot = c["total"]
        out.append(
            f"| {strat} | {tot} | {c['FILLED-WIN']} | {c['FILLED-LOSS']} | "
            f"{c['IOC-CANCELLED']} | {c['PHANTOM-CLEARED']} | {c['NO-OUTCOME']} | "
            f"{100.0 * filled / tot:.0f}% | {fmt_money(pnl_by_strat[strat])} |"
        )
    out.append("")


def section3(entries, out):
    out.append("## 3. Rejections and gate aggregates")
    out.append("")

    # -- RISK_REJECTED full detail
    rejected = [e for e in entries if e.get("decision") == "RISK_REJECTED"]
    out.append(f"### RISK_REJECTED ({len(rejected)})")
    out.append("")
    for e in rejected:
        s = e.get("setup") or {}
        c = e.get("context") or {}
        tr = c.get("trend") or {}
        ts = (e.get("ts") or "")[:16].replace("T", " ")
        out.append(f"- **{ts} {e.get('instrument')}** {s.get('strategy')} {s.get('direction')}")
        out.append(f"  - reason: {e.get('reason')}")
        out.append(
            f"  - setup: entry={s.get('entry')} stop={s.get('stop')} "
            f"target={s.get('target')} rr={s.get('rr_ratio')}"
        )
        out.append(
            f"  - trend={tr.get('direction')}/{tr.get('strength')} "
            f"regime={e.get('regime')} failed_gates={e.get('failed_gates')}"
        )
    if not rejected:
        out.append("_None._")
    out.append("")

    # -- CONFIG_BLOCKED summary
    blocked = [e for e in entries if e.get("decision") == "CONFIG_BLOCKED"]
    out.append(f"### CONFIG_BLOCKED ({len(blocked)})")
    out.append("")
    if blocked:
        by_day = defaultdict(list)
        for e in blocked:
            by_day[e["_day"]].append(e)
        for day in sorted(by_day):
            evs = by_day[day]
            reasons = Counter(e.get("config_block") for e in evs)
            tfs = Counter((e.get("expected_timeframe"), e.get("received_timeframe")) for e in evs)
            insts = Counter(e.get("instrument") for e in evs)
            tss = sorted(e.get("ts") or "" for e in evs)
            out.append(
                f"- {day}: {len(evs)} events | blocks={dict(reasons)} | "
                f"tf(expected,received)={dict(tfs)} | instruments={dict(insts)} | "
                f"span {tss[0][:16]} -> {tss[-1][:16]}"
            )
    else:
        out.append("_None._")
    out.append("")

    # -- NO_TRADE failed_gates aggregates per day/instrument
    out.append("### NO_TRADE failed_gates aggregates (per day / instrument)")
    out.append("")
    days = sorted({e["_day"] for e in entries})
    for day in days:
        day_es = [e for e in entries if e["_day"] == day and e.get("decision") == "NO_TRADE"]
        if not day_es:
            continue
        total = Counter()
        nearmiss = Counter()
        gates = defaultdict(Counter)
        for e in day_es:
            inst = e.get("instrument")
            total[inst] += 1
            if is_near_miss(e):
                nearmiss[inst] += 1
            for g in e.get("failed_gates") or []:
                gates[inst][g] += 1
        out.append(
            f"**{day}** — NO_TRADE totals {dict(total)}; "
            f"near-miss (formed setup or shadow candidates) {dict(nearmiss) or '{}'}"
        )
        for inst in sorted(gates):
            top = ", ".join(f"{g}={n}" for g, n in gates[inst].most_common(12))
            out.append(f"- {inst} gates: {top}")
        out.append("")

    # -- near-misses with a formed setup
    out.append("### Near-misses with a formed setup (NO_TRADE bars)")
    out.append("")
    any_nm = False
    for day in days:
        rows = [
            e
            for e in entries
            if e["_day"] == day and e.get("decision") == "NO_TRADE" and e.get("setup")
        ]
        if not rows:
            continue
        any_nm = True
        out.append(f"**{day}** ({len(rows)} formed-setup near-misses)")
        agg = Counter(
            (e.get("instrument"), (e["setup"] or {}).get("strategy"), (e["setup"] or {}).get("direction"))
            for e in rows
        )
        for (inst, strat, direction), n in agg.most_common():
            out.append(f"- {inst} {strat} {direction}: {n}")
        gate_ct = Counter(g for e in rows for g in (e.get("failed_gates") or []))
        out.append(f"- blocking gates: {dict(gate_ct)}")
        out.append("")
    if not any_nm:
        out.append("_None._")
        out.append("")


def section4(entries, out):
    out.append("## 4. Missed-long analysis")
    out.append("")
    out.append(
        "Bars with a LONG shadow candidate or a formed LONG setup that produced no trade, "
        "grouped by strategy and blocking gate."
    )
    out.append("")
    # collect (day, ts, inst, strategy, gates, trend, decision, flag)
    per_strat = defaultdict(lambda: {"count": 0, "gates": Counter(), "days": set(), "bars": []})
    for e in entries:
        if e.get("decision") == "TRADE" or not e.get("decision"):
            continue
        gates = tuple(e.get("failed_gates") or [])
        c = e.get("context") or {}
        trend = (c.get("trend") or {}).get("direction")
        ts = (e.get("ts") or "")[:16].replace("T", " ")
        long_strats = []
        s = e.get("setup") or {}
        if s and (s.get("direction") or "").upper() == "LONG":
            long_strats.append((s.get("strategy") or "?", "formed setup"))
        for cand in e.get("shadow_candidates") or []:
            if (cand.get("direction") or "").upper() == "LONG":
                long_strats.append((cand.get("strategy") or "?", "shadow candidate"))
        for cand in e.get("candidate_audit") or []:
            if (cand.get("direction") or "").upper() == "LONG":
                long_strats.append(
                    (cand.get("strategy") or "?", "ranked candidate audit")
                )
        for strat, kind in dict.fromkeys(long_strats):
            rec = per_strat[strat]
            rec["count"] += 1
            rec["days"].add(e["_day"])
            for g in gates:
                rec["gates"][g] += 1
            rec["bars"].append((ts, e.get("instrument"), kind, gates, trend, e.get("decision")))

    if not per_strat:
        out.append("_No missed LONG opportunities in range._")
        out.append("")
        return

    def flag_for(strat):
        if strat in KNOWN_LOSER_STRATEGIES:
            return KNOWN_LOSER_STRATEGIES[strat]
        if strat.endswith(OBSERVED_SUFFIX):
            return "non-executable (observe-only shadow strategy)"
        return None

    executable = {s: r for s, r in per_strat.items() if not flag_for(s)}
    nonexec = {s: r for s, r in per_strat.items() if flag_for(s)}

    out.append("### By strategy (executable strategies)")
    out.append("")
    if executable:
        for strat in sorted(executable, key=lambda s: -executable[s]["count"]):
            r = executable[strat]
            gate_str = ", ".join(f"{g}={n}" for g, n in r["gates"].most_common()) or "(no gates recorded)"
            out.append(f"- **{strat}**: {r['count']} missed LONG bars across {len(r['days'])} day(s)")
            out.append(f"  - blocking gates: {gate_str}")
            for ts, inst, kind, gates, trend, dec in r["bars"]:
                out.append(
                    f"  - {ts} {inst} [{kind}] trend={trend} decision={dec} gates={list(gates)}"
                )
    else:
        out.append("_None._")
    out.append("")

    out.append("### Non-executable / known-loser strategies (flagged separately)")
    out.append("")
    if nonexec:
        for strat in sorted(nonexec, key=lambda s: -nonexec[s]["count"]):
            r = nonexec[strat]
            gate_str = ", ".join(f"{g}={n}" for g, n in r["gates"].most_common()) or "(no gates recorded)"
            out.append(
                f"- **{strat}** — {flag_for(strat)}: {r['count']} LONG bars "
                f"across {len(r['days'])} day(s)"
            )
            out.append(f"  - blocking gates on those bars: {gate_str}")
    else:
        out.append("_None._")
    out.append("")


# ---------------------------------------------------------------- main


def build_report(paths):
    entries = load_entries(paths)
    trades = [e for e in entries if e.get("decision") == "TRADE"]
    outcomes = [e for e in entries if e.get("type") == "OUTCOME"]
    orderids = [e for e in entries if e.get("type") == "ORDER_IDS"]
    outcome_by_trade = pair_followers(trades, outcomes)
    orderids_by_trade = pair_followers(trades, orderids)

    days = sorted({e["_day"] for e in entries})
    out = []
    title_range = f"{days[0]} to {days[-1]}" if days else "(empty)"
    out.append(f"# Session audit report — {title_range}")
    out.append("")
    out.append(
        f"Generated by `scripts/session_audit.py` from {len(paths)} journal file(s); "
        f"{len(entries)} entries, {len(trades)} TRADE decisions, "
        f"{len(outcomes)} OUTCOME events, {len(orderids)} ORDER_IDS events."
    )
    out.append("")
    section1(trades, outcome_by_trade, orderids_by_trade, out)
    section2(trades, outcome_by_trade, out)
    section3(entries, out)
    section4(entries, out)
    return mask_ids("\n".join(out) + "\n")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("journals", nargs="*", help="journal_*.jsonl paths")
    ap.add_argument("--journal-dir", help="directory containing journal_*.jsonl")
    ap.add_argument("--from", dest="date_from", help="inclusive start date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="inclusive end date YYYY-MM-DD")
    ap.add_argument("--output", help="write markdown here instead of stdout")
    return ap.parse_args(argv)


def resolve_paths(args):
    paths = [Path(p) for p in args.journals]
    if args.journal_dir:
        paths.extend(sorted(Path(args.journal_dir).glob("journal_*.jsonl")))
    if args.date_from or args.date_to:
        lo = args.date_from or "0000-00-00"
        hi = args.date_to or "9999-99-99"
        paths = [p for p in paths if lo <= day_of(p) <= hi]
    paths = sorted(set(paths))
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise SystemExit(f"journal file(s) not found: {', '.join(map(str, missing))}")
    if not paths:
        raise SystemExit("no journal files given (pass paths or --journal-dir)")
    return paths


def main(argv=None):
    args = parse_args(argv)
    report = build_report(resolve_paths(args))
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"wrote {args.output} ({len(report.split())} words)", file=sys.stderr)
    else:
        sys.stdout.write(report)


if __name__ == "__main__":
    main()
