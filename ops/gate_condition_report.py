#!/usr/bin/env python3
"""Market-condition gate evidence report (read-only, evidence only).

Answers one standing question for the 2026-09-30 review: *should MNQ be allowed
to trade when the engine does NOT label the market TRENDING?* The live gate
(`require_trending_condition`, deployed 2026-06-19 on ~2 weeks of live tape:
RANGE_BOUND bucket net -$146 vs TRENDING +$288) has never been re-tested on
2026 tape. The cross-instrument observation campaign already records every
gate-free shadow candidate with its `market_condition` and an honest-fill
OUTCOME, so the comparison is free: bucket resolved outcomes by
instrument x market_condition and let the numbers accumulate.

Never grants execution eligibility. Never changes a rule. Reads
`cross_instrument_observation_v1.jsonl` only.

Costs: MNQ/MES use the proven forward-campaign assumptions (1 tick slippage,
$1.48 commission per round trip). Other instruments carry no cost proof and
are reported gross only, flagged as such.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.forward_evidence_campaign import COMMISSION_DOLLARS, SLIPPAGE_TICKS  # noqa: E402

CAMPAIGN_FILE = "cross_instrument_observation_v1.jsonl"
COSTED_INSTRUMENTS = ("MNQ", "MES")
# Campaign armed 2026-09-16 12:17Z; everything before that is a different epoch.
DEFAULT_SINCE = "2026-09-16T12:17:19+00:00"
# Only strategies that are, or could be, executable through the live engine.
# The *_observed lanes are the shadow mirrors of the same PDF-defined setups.
CONDITIONS = ("TRENDING", "RANGE_BOUND", "DEAD", "CHOPPY")


def _empty_bucket() -> dict:
    return {"n": 0, "wins": 0, "losses": 0, "r": 0.0, "gross_usd": 0.0, "net_usd": None}


def load_rows(log_dir: str | Path) -> list[dict]:
    path = Path(log_dir) / CAMPAIGN_FILE
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def net_dollars(instrument: str, gross: float, tick_value: float | None) -> float | None:
    """Apply the proven cost model where one exists; otherwise None (gross only)."""
    if instrument not in COSTED_INSTRUMENTS or tick_value is None:
        return None
    return round(gross - COMMISSION_DOLLARS - SLIPPAGE_TICKS * tick_value, 2)


def build_report(rows: list[dict], *, since: str = DEFAULT_SINCE, instruments: tuple[str, ...] | None = None) -> dict:
    cands = {
        r["candidate_id"]: r
        for r in rows
        if r.get("record_type") == "CANDIDATE" and (r.get("signal_timestamp") or "") >= since
    }
    buckets: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(_empty_bucket))
    resolved = 0
    for r in rows:
        if r.get("record_type") != "OUTCOME" or r.get("result") not in ("WIN", "LOSS"):
            continue
        c = cands.get(r.get("candidate_id"))
        if c is None:
            continue
        inst = c.get("instrument")
        if instruments and inst not in instruments:
            continue
        cond = (c.get("market_condition") or "UNKNOWN").upper()
        b = buckets[inst][cond]
        b["n"] += 1
        b["wins" if r["result"] == "WIN" else "losses"] += 1
        b["r"] = round(b["r"] + float(r.get("pnl_r") or 0.0), 4)
        gross = float(r.get("gross_pnl_dollars_1_contract") or 0.0)
        b["gross_usd"] = round(b["gross_usd"] + gross, 2)
        net = net_dollars(inst, gross, c.get("tick_value_dollars"))
        if net is not None:
            b["net_usd"] = round((b["net_usd"] or 0.0) + net, 2)
        resolved += 1
    out: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since,
        "candidates": len(cands),
        "resolved": resolved,
        "cost_model": {
            "instruments": list(COSTED_INSTRUMENTS),
            "commission_dollars": COMMISSION_DOLLARS,
            "slippage_ticks": SLIPPAGE_TICKS,
            "note": "other instruments gross only — no cost proof",
        },
        "by_instrument": {},
        "verdict": None,
        "authority": "evidence_only",
    }
    for inst, conds in sorted(buckets.items()):
        out["by_instrument"][inst] = {cond: dict(conds[cond]) for cond in sorted(conds)}
    out["verdict"] = _verdict(out["by_instrument"].get("MNQ", {}))
    return out


def _verdict(mnq: dict) -> dict:
    """Mechanical, non-authoritative read of the MNQ RANGE_BOUND bucket.
    NOT_ENOUGH_DATA below 30 resolved; otherwise reports sign of net $ and R.
    A verdict here never changes a rule — the operator decides after 09-30."""
    rb = mnq.get("RANGE_BOUND") or _empty_bucket()
    tr = mnq.get("TRENDING") or _empty_bucket()
    min_n = 30
    if rb["n"] < min_n:
        state = "NOT_ENOUGH_DATA"
    elif (rb["net_usd"] or 0.0) > 0 and rb["r"] > 0:
        state = "RANGE_BOUND_POSITIVE"
    else:
        state = "RANGE_BOUND_NOT_POSITIVE"
    return {
        "state": state,
        "min_resolved_for_read": min_n,
        "mnq_range_bound": rb,
        "mnq_trending": tr,
        "rule_today": "require_trending_condition=True (MNQ trades only in TRENDING)",
    }


def format_digest(report: dict) -> str:
    v = report["verdict"]
    rb, tr = v["mnq_range_bound"], v["mnq_trending"]

    def line(label: str, b: dict) -> str:
        net = "n/a" if b["net_usd"] is None else f"${b['net_usd']:+.0f}"
        return f"{label}: n={b['n']} {b['wins']}W/{b['losses']}L {b['r']:+.1f}R net {net}"

    others = []
    for inst, conds in report["by_instrument"].items():
        if inst == "MNQ":
            continue
        n = sum(b["n"] for b in conds.values())
        r = sum(b["r"] for b in conds.values())
        others.append(f"{inst} n={n} {r:+.1f}R")
    return (
        f"**GATE EVIDENCE · MNQ trending-only rule** (since {report['since'][:10]}, evidence only)\n"
        f"{line('TRENDING (allowed)', tr)}\n"
        f"{line('RANGE_BOUND (blocked)', rb)}\n"
        f"Read: {v['state']} (needs ≥{v['min_resolved_for_read']} resolved RANGE_BOUND)\n"
        f"Others gross: {' · '.join(others) if others else '—'}\n"
        f"[read-only · no rule change · decision after 2026-09-30]"
    )


def _post_discord(url: str, content: str) -> bool:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"content": content}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "afs-gate-condition-report/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument("--since", default=DEFAULT_SINCE)
    parser.add_argument("--json", action="store_true", help="print JSON instead of the digest")
    parser.add_argument("--out", default=None, help="write JSON report here (default: <log-dir>/gate_condition_report_latest.json)")
    parser.add_argument("--discord", action="store_true", help="post the digest to DISCORD_ROUTE_DAILY_REPORT / DISCORD_WEBHOOK_URL")
    args = parser.parse_args(argv)

    report = build_report(load_rows(args.log_dir), since=args.since)
    out_path = Path(args.out) if args.out else Path(args.log_dir) / "gate_condition_report_latest.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2))
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
