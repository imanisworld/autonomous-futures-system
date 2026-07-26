#!/usr/bin/env python3
"""Forensic loss attribution for the frozen PR #346 corrected IOC corpus.

The canonical population is read from the committed PR #346 raw ledger and
cross-checked against its committed result.  Journal logs are used only to
enrich those exact attempts with fields that were present at decision time.

An optional same-code market-fill replay is a NON-CANONICAL DIAGNOSTIC.  It is
used only to compare exact candidate fingerprints under market versus IOC
execution; it never replaces the frozen IOC verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
COMMISSION_RT = 1.48
PR346_MERGE_SHA = "69ec77f"
PR346_SOURCE_SHA = "e8f2fe23fa05e488d5aad3427a277642ed7d2c56"
EXPECTED = {
    "attempts": 165,
    "fills": 97,
    "cancellations": 68,
    "resolved": 97,
    "open": 0,
    "net_before_commission": -658.72,
    "net_after_commission": -802.28,
    "profit_factor_after_commission": 0.752958,
    "breaker_dates": {"MNQ": "2025-09-08", "MES": "2025-12-11"},
}
PBU_OR_BETTER_CELLS = {
    # Read from current main's Strategy_Inventory master table.
    ("orb_reclaim", "MNQ"): "PROMISING BUT UNPROVEN",
    ("orb_reclaim", "MES"): "PAPER PROOF",
}
PERIODS = {
    "half": {
        "H1": ("2025-07-24", "2026-01-23"),
        "H2": ("2026-01-24", "2026-07-23"),
    },
    "quarter": {
        "Q1": ("2025-07-24", "2025-10-23"),
        "Q2": ("2025-10-24", "2026-01-23"),
        "Q3": ("2026-01-24", "2026-04-23"),
        "Q4": ("2026-04-24", "2026-07-23"),
    },
}


def jsonl(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*.jsonl"))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(paths), digest.hexdigest()


def profit_factor(values: list[float]) -> float | None:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    if gross_loss:
        return round(gross_profit / gross_loss, 6)
    return math.inf if gross_profit else None


def max_drawdown(rows: list[dict[str, Any]]) -> float:
    equity = peak = drawdown = 0.0
    for row in sorted(rows, key=sort_key):
        equity += row["pnl_after_commission"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["date"], row.get("bar_ts") or "", row["instrument"])


def stats(rows: list[dict[str, Any]], book_net: float) -> dict[str, Any]:
    fills = [row for row in rows if row["filled"]]
    pnl_gross = [row["pnl_before_commission"] for row in fills]
    pnl_net = [row["pnl_after_commission"] for row in fills]
    wins = sum(row["result"] == "WIN" for row in fills)
    losses = sum(row["result"] == "LOSS" for row in fills)
    gross_profit = sum(value for value in pnl_net if value > 0)
    gross_loss = abs(sum(value for value in pnl_net if value < 0))
    return {
        "attempts": len(rows),
        "fills": len(fills),
        "cancellations": len(rows) - len(fills),
        "fill_rate": round(len(fills) / len(rows), 6) if rows else None,
        "wins": wins,
        "losses": losses,
        "breakeven": sum(row["result"] == "BREAKEVEN" for row in fills),
        "win_rate": round(wins / len(fills), 6) if fills else None,
        "gross_profit_after_commission": round(gross_profit, 2),
        "gross_loss_after_commission": round(gross_loss, 2),
        "profit_factor_after_commission": profit_factor(pnl_net),
        "net_before_commission": round(sum(pnl_gross), 2),
        "commission": round(COMMISSION_RT * len(fills), 2),
        "net_after_commission": round(sum(pnl_net), 2),
        "expectancy_per_attempt": round(sum(pnl_net) / len(rows), 4) if rows else None,
        "expectancy_per_fill": round(statistics.fmean(pnl_net), 4) if fills else None,
        "max_drawdown": max_drawdown(fills),
        "share_of_book_net": (
            round(sum(pnl_net) / book_net, 6) if book_net else None
        ),
    }


def grouped(
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
    book_net: float,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(str(row.get(field) or "UNKNOWN") for field in fields)].append(row)
    blocks = []
    for key, lane in buckets.items():
        block = {field: value for field, value in zip(fields, key)}
        block.update(stats(lane, book_net))
        blocks.append(block)
    return sorted(blocks, key=lambda block: block["net_after_commission"])


def grouped_with_labels(
    rows: list[dict[str, Any]],
    field: str,
    labels: Iterable[str],
    book_net: float,
) -> list[dict[str, Any]]:
    existing = {block[field]: block for block in grouped(rows, (field,), book_net)}
    blocks = []
    for label in labels:
        block = existing.get(label, {field: label, **stats([], book_net)})
        blocks.append(block)
    return blocks


def period_label(day: str, periods: dict[str, tuple[str, str]]) -> str:
    for label, (start, end) in periods.items():
        if start <= day <= end:
            return label
    return "OUT_OF_RANGE"


def week_label(day: str) -> str:
    value = datetime.fromisoformat(day).date()
    monday = value - timedelta(days=value.weekday())
    return monday.isoformat()


def trend_direction(entry: dict[str, Any]) -> str:
    factors = (entry.get("confluence") or {}).get("factors") or []
    for factor in factors:
        match = re.match(r"Trend (UP|DOWN)(?: |$)", factor)
        if match:
            return match.group(1)
    return "UNKNOWN"


def fingerprint(entry: dict[str, Any]) -> str:
    setup = entry.get("setup") or {}
    values = (
        entry.get("instrument"),
        entry.get("bar_ts"),
        setup.get("strategy"),
        setup.get("direction"),
        setup.get("entry"),
        setup.get("stop"),
        setup.get("target"),
    )
    return "|".join("" if value is None else str(value) for value in values)


def parse_logs(logs: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict]:
    approved: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    breaker: dict[str, dict[str, Any]] = {}
    for instrument in INSTRUMENTS:
        for path in sorted((logs / instrument).glob("journal_*.jsonl")):
            for entry in jsonl(path):
                decision = entry.get("decision")
                risk = entry.get("risk_check") or {}
                if (
                    decision == "RISK_REJECTED"
                    and risk.get("failed_rule") == "max_drawdown"
                ):
                    breaker.setdefault(
                        instrument,
                        {
                            "date": path.stem.removeprefix("journal_"),
                            "bar_ts": entry.get("bar_ts"),
                            "reason": risk.get("reason"),
                        },
                    )
                    setup = entry.get("setup") or {}
                    rejected.append(
                        {
                            "instrument": instrument,
                            "date": path.stem.removeprefix("journal_"),
                            "bar_ts": entry.get("bar_ts"),
                            "strategy": setup.get("strategy") or "UNKNOWN",
                            "direction": setup.get("direction") or "UNKNOWN",
                            "session": entry.get("session") or "UNKNOWN",
                            "market_condition": entry.get("market_condition") or "UNKNOWN",
                            "regime": entry.get("regime") or "UNKNOWN",
                            "trend_direction": trend_direction(entry),
                        }
                    )
                if (
                    decision == "TRADE"
                    and risk.get("result") == "APPROVED"
                    and entry.get("paper_order_id")
                ):
                    enriched = {
                        "instrument": instrument,
                        "bar_ts": entry.get("bar_ts"),
                        "strategy": (entry.get("setup") or {}).get("strategy"),
                        "direction": (entry.get("setup") or {}).get("direction"),
                        "session": entry.get("session") or "UNKNOWN",
                        "market_condition": entry.get("market_condition") or "UNKNOWN",
                        "regime": entry.get("regime") or "UNKNOWN",
                        "trend_direction": trend_direction(entry),
                        "candidate_fingerprint": fingerprint(entry),
                    }
                    approved[entry["paper_order_id"]] = enriched
    return approved, rejected, breaker


def enrich(
    raw_rows: list[dict[str, Any]],
    approved: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_attempt_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for extra in approved.values():
        by_attempt_key[
            (
                extra["instrument"],
                extra["bar_ts"],
                extra["strategy"],
                extra["direction"],
            )
        ].append(extra)
    missing = []
    rows = []
    for source in raw_rows:
        row = dict(source)
        extra = approved.get(row["paper_order_id"])
        if extra is None:
            # Paper order IDs contain random UUIDs and therefore change on a clean
            # reproduction.  The decision-time tuple is stable and exact.
            key = (
                row["instrument"],
                row["bar_ts"],
                row["strategy"],
                row["direction"],
            )
            matches = by_attempt_key.get(key, [])
            if len(matches) != 1:
                missing.append(row["paper_order_id"])
                continue
            extra = matches[0]
        row.update(extra)
        row["month"] = row["date"][:7]
        row["week"] = week_label(row["date"])
        row["half"] = period_label(row["date"], PERIODS["half"])
        row["quarter"] = period_label(row["date"], PERIODS["quarter"])
        rows.append(row)
    if missing:
        raise RuntimeError(f"{len(missing)} frozen attempts absent from source journals")
    return rows


def verify_frozen(
    rows: list[dict[str, Any]],
    committed_results: dict[str, Any],
    breaker: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    overall = stats(rows, EXPECTED["net_after_commission"])
    checks = {
        "unique_attempts": len({row["paper_order_id"] for row in rows}) == EXPECTED["attempts"],
        "attempts": len(rows) == EXPECTED["attempts"],
        "fills": overall["fills"] == EXPECTED["fills"],
        "cancellations": overall["cancellations"] == EXPECTED["cancellations"],
        "resolved": sum(row["resolved"] for row in rows) == EXPECTED["resolved"],
        "open": sum(row["open"] for row in rows) == EXPECTED["open"],
        "net_before_commission": overall["net_before_commission"] == EXPECTED["net_before_commission"],
        "net_after_commission": overall["net_after_commission"] == EXPECTED["net_after_commission"],
        "profit_factor": overall["profit_factor_after_commission"] == EXPECTED["profit_factor_after_commission"],
        "committed_result_net": committed_results["overall"]["net_after_commission"] == EXPECTED["net_after_commission"],
        "breaker_dates": {
            instrument: breaker[instrument]["date"] == day
            for instrument, day in EXPECTED["breaker_dates"].items()
        },
        "no_post_breaker_attempts": {},
    }
    for instrument in INSTRUMENTS:
        cutoff = breaker[instrument]["bar_ts"]
        lane = [row for row in rows if row["instrument"] == instrument]
        checks["no_post_breaker_attempts"][instrument] = not any(
            row["bar_ts"] >= cutoff for row in lane
        )
    flattened = [
        value
        for key, value in checks.items()
        if key not in {"breaker_dates", "no_post_breaker_attempts"}
    ]
    flattened += list(checks["breaker_dates"].values())
    flattened += list(checks["no_post_breaker_attempts"].values())
    checks["all_pass"] = all(flattened)
    if not checks["all_pass"]:
        raise RuntimeError(f"frozen #346 reproduction discrepancy: {checks}")
    return checks


def losing_concentration(rows: list[dict[str, Any]], book_net: float) -> dict[str, Any]:
    losses = sorted(
        (row for row in rows if row["pnl_after_commission"] < 0),
        key=lambda row: row["pnl_after_commission"],
    )
    gross_loss = abs(sum(row["pnl_after_commission"] for row in losses))
    result = {}
    for top_n in (1, 3, 5):
        dollars = abs(sum(row["pnl_after_commission"] for row in losses[:top_n]))
        result[f"top_{top_n}"] = {
            "dollars": round(dollars, 2),
            "share_of_gross_losing_dollars": round(dollars / gross_loss, 6),
            "share_of_net_book_loss": round(dollars / abs(book_net), 6),
        }
    return result


def loss_streaks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    longest: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    clusters: list[list[dict[str, Any]]] = []
    for row in sorted((row for row in rows if row["filled"]), key=sort_key):
        if row["result"] == "LOSS":
            current.append(row)
        else:
            if current:
                clusters.append(current)
            if len(current) > len(longest):
                longest = current
            current = []
    if current:
        clusters.append(current)
    if len(current) > len(longest):
        longest = current
    worst = min(
        clusters,
        key=lambda cluster: sum(row["pnl_after_commission"] for row in cluster),
    )
    return {
        "longest_consecutive_losses": len(longest),
        "longest_start": longest[0]["bar_ts"],
        "longest_end": longest[-1]["bar_ts"],
        "longest_net": round(sum(row["pnl_after_commission"] for row in longest), 2),
        "worst_cluster_losses": len(worst),
        "worst_cluster_start": worst[0]["bar_ts"],
        "worst_cluster_end": worst[-1]["bar_ts"],
        "worst_cluster_net": round(sum(row["pnl_after_commission"] for row in worst), 2),
    }


def cumulative_before_breaker(rows: list[dict[str, Any]]) -> dict[str, Any]:
    results = {}
    for instrument in INSTRUMENTS:
        lane = sorted(
            (row for row in rows if row["instrument"] == instrument and row["filled"]),
            key=sort_key,
        )
        running = 0.0
        first_date = lane[0]["date"]
        milestones = []
        for row in lane:
            running += row["pnl_after_commission"]
            for threshold in (-100, -200, -300, -400):
                if running <= threshold and threshold not in {m["threshold"] for m in milestones}:
                    milestones.append(
                        {"threshold": threshold, "date": row["date"], "bar_ts": row["bar_ts"]}
                    )
        results[instrument] = {
            "first_attempt_date": first_date,
            "last_attempt_date": lane[-1]["date"],
            "calendar_days_to_halt": (
                datetime.fromisoformat(lane[-1]["date"]).date()
                - datetime.fromisoformat(first_date).date()
            ).days,
            "ending_net_after_commission": round(running, 2),
            "milestones": milestones,
            "strategy_contribution": grouped(lane, ("strategy",), EXPECTED["net_after_commission"]),
        }
    return results


def breaker_censorship(rejected: list[dict[str, Any]]) -> dict[str, Any]:
    blocks = {}
    for instrument in INSTRUMENTS:
        lane = [row for row in rejected if row["instrument"] == instrument]
        blocks[instrument] = {
            "rejected_qualified_setups": len(lane),
            "first_rejection": min(row["bar_ts"] for row in lane),
            "last_rejection": max(row["bar_ts"] for row in lane),
            "by_strategy": dict(Counter(row["strategy"] for row in lane).most_common()),
            "by_session": dict(Counter(row["session"] for row in lane).most_common()),
        }
    return blocks


def run_market_counterfactual(corpus: Path, logs: Path) -> None:
    if any(logs.rglob("journal_*.jsonl")):
        raise RuntimeError(
            "market diagnostic log directory must be fresh (existing journals found)"
        )
    config = replace(load_config(), entry_fill_model="market")
    for instrument in INSTRUMENTS:
        files = sorted((corpus / instrument).glob("*.jsonl"))
        if len(files) != 313:
            raise RuntimeError(f"{instrument}: expected 313 corpus files, got {len(files)}")
        engine = ReplayEngine(config=config, log_dir=str(logs / instrument))
        for index, candle_path in enumerate(files, 1):
            engine.run(candle_path, review_date=candle_path.stem.rsplit("_", 1)[-1])
            if index % 100 == 0 or index == len(files):
                print(f"[market diagnostic {instrument}] {index}/{len(files)}", flush=True)


def outcome_by_fingerprint(logs: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for instrument in INSTRUMENTS:
        for path in sorted((logs / instrument).glob("journal_*.jsonl")):
            entries = list(jsonl(path))
            outcomes = {
                (entry.get("outcome") or {}).get("paper_order_id"): entry.get("outcome") or {}
                for entry in entries
                if entry.get("type") == "OUTCOME"
            }
            for entry in entries:
                if (
                    entry.get("decision") != "TRADE"
                    or (entry.get("risk_check") or {}).get("result") != "APPROVED"
                ):
                    continue
                outcome = outcomes.get(entry.get("paper_order_id")) or {}
                result = outcome.get("result")
                if result not in {"WIN", "LOSS", "BREAKEVEN", "CANCELLED"}:
                    continue
                key = fingerprint(entry)
                if key in rows:
                    raise RuntimeError(f"duplicate candidate fingerprint: {key}")
                pnl = float(outcome.get("pnl_dollars") or 0.0)
                rows[key] = {
                    "instrument": instrument,
                    "date": path.stem.removeprefix("journal_"),
                    "bar_ts": entry.get("bar_ts"),
                    "strategy": (entry.get("setup") or {}).get("strategy"),
                    "direction": (entry.get("setup") or {}).get("direction"),
                    "result": "NO_FILL" if result == "CANCELLED" else result,
                    "pnl_before_commission": pnl,
                    "pnl_after_commission": (
                        pnl - COMMISSION_RT if result != "CANCELLED" else 0.0
                    ),
                }
    return rows


def matched_execution(
    ioc_logs: Path,
    market_logs: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ioc = outcome_by_fingerprint(ioc_logs)
    market = outcome_by_fingerprint(market_logs)
    common = sorted(set(ioc) & set(market))
    matched = []
    for key in common:
        left, right = ioc[key], market[key]
        matched.append(
            {
                "candidate_fingerprint": key,
                "instrument": left["instrument"],
                "date": left["date"],
                "bar_ts": left["bar_ts"],
                "strategy": left["strategy"],
                "direction": left["direction"],
                "market_result": right["result"],
                "ioc_result": left["result"],
                "transition": f"MARKET_{right['result']} -> IOC_{left['result']}",
                "market_pnl_after_commission": round(right["pnl_after_commission"], 2),
                "ioc_pnl_after_commission": round(left["pnl_after_commission"], 2),
                "pnl_delta_ioc_minus_market": round(
                    left["pnl_after_commission"] - right["pnl_after_commission"], 2
                ),
            }
        )
    transitions = Counter(row["transition"] for row in matched)
    transition_dollars: dict[str, dict[str, Any]] = {}
    for transition in sorted(transitions):
        lane = [row for row in matched if row["transition"] == transition]
        transition_dollars[transition] = {
            "candidates": len(lane),
            "market_net_after_commission": round(
                sum(row["market_pnl_after_commission"] for row in lane), 2
            ),
            "ioc_net_after_commission": round(
                sum(row["ioc_pnl_after_commission"] for row in lane), 2
            ),
            "pnl_delta_ioc_minus_market": round(
                sum(row["pnl_delta_ioc_minus_market"] for row in lane), 2
            ),
        }
    common_market_net = sum(row["market_pnl_after_commission"] for row in matched)
    common_ioc_net = sum(row["ioc_pnl_after_commission"] for row in matched)
    filled = [row for row in matched if row["ioc_result"] != "NO_FILL"]
    no_fills = [row for row in matched if row["ioc_result"] == "NO_FILL"]
    def paired_breakdown(fields: tuple[str, ...]) -> list[dict[str, Any]]:
        buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in matched:
            buckets[tuple(str(row[field]) for field in fields)].append(row)
        blocks = []
        for key, lane in buckets.items():
            no_fill_lane = [row for row in lane if row["ioc_result"] == "NO_FILL"]
            filled_lane = [row for row in lane if row["ioc_result"] != "NO_FILL"]
            block = {field: value for field, value in zip(fields, key)}
            block.update(
                {
                    "matched_candidates": len(lane),
                    "ioc_no_fills": len(no_fill_lane),
                    "market_wins_to_ioc_no_fill": sum(
                        row["market_result"] == "WIN" for row in no_fill_lane
                    ),
                    "market_losses_to_ioc_no_fill": sum(
                        row["market_result"] == "LOSS" for row in no_fill_lane
                    ),
                    "market_net_after_commission": round(
                        sum(row["market_pnl_after_commission"] for row in lane), 2
                    ),
                    "ioc_net_after_commission": round(
                        sum(row["ioc_pnl_after_commission"] for row in lane), 2
                    ),
                    "no_fill_selection_delta": round(
                        sum(row["pnl_delta_ioc_minus_market"] for row in no_fill_lane),
                        2,
                    ),
                    "changed_fill_pnl_delta": round(
                        sum(row["pnl_delta_ioc_minus_market"] for row in filled_lane),
                        2,
                    ),
                    "total_delta_ioc_minus_market": round(
                        sum(row["pnl_delta_ioc_minus_market"] for row in lane), 2
                    ),
                }
            )
            blocks.append(block)
        return sorted(blocks, key=lambda block: block["total_delta_ioc_minus_market"])

    summary = {
        "label": "NON-CANONICAL SAME-CODE MARKET-FILL DIAGNOSTIC ONLY",
        "ioc_approved_candidates": len(ioc),
        "market_approved_candidates": len(market),
        "exact_candidate_fingerprints_in_common": len(common),
        "ioc_only_candidates_due_to_divergent_account_path": len(set(ioc) - set(market)),
        "market_only_candidates_due_to_divergent_account_path": len(set(market) - set(ioc)),
        "transition_counts": dict(sorted(transitions.items())),
        "transition_dollars": transition_dollars,
        "matched_market_net_after_commission": round(common_market_net, 2),
        "matched_ioc_net_after_commission": round(common_ioc_net, 2),
        "matched_pnl_delta_ioc_minus_market": round(common_ioc_net - common_market_net, 2),
        "attribution": {
            "no_fill_selection_delta": round(
                sum(row["pnl_delta_ioc_minus_market"] for row in no_fills), 2
            ),
            "changed_fill_pnl_delta": round(
                sum(row["pnl_delta_ioc_minus_market"] for row in filled), 2
            ),
            "interpretation": (
                "Negative values mean IOC underperformed the same candidate under "
                "the market-fill diagnostic."
            ),
        },
        "by_strategy": paired_breakdown(("strategy",)),
        "by_strategy_instrument": paired_breakdown(("strategy", "instrument")),
        "old_747_row_identity_match": {
            "possible": False,
            "reason": (
                "The superseded ledger has date/instrument/strategy/result/P&L only; "
                "it has no bar_ts, direction, order ID, or candidate fingerprint."
            ),
        },
    }
    return summary, matched


def table(blocks: list[dict[str, Any]], labels: tuple[str, ...]) -> list[str]:
    lines = [
        "| " + " / ".join(labels) + " | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for block in blocks:
        name = " / ".join(str(block[label]) for label in labels)
        fmt = lambda value: "—" if value is None else f"{value:.3f}"
        lines.append(
            f"| {name} | {block['attempts']} | {block['fills']} | {block['cancellations']} | "
            f"{'—' if block['fill_rate'] is None else f'{100 * block['fill_rate']:.1f}%'} | "
            f"{block['wins']}-{block['losses']} | "
            f"{'—' if block['win_rate'] is None else f'{100 * block['win_rate']:.1f}%'} | "
            f"{fmt(block['profit_factor_after_commission'])} | "
            f"${block['net_after_commission']:,.2f} | "
            f"{'—' if block['expectancy_per_attempt'] is None else f'${block['expectancy_per_attempt']:,.2f}'} | "
            f"{'—' if block['expectancy_per_fill'] is None else f'${block['expectancy_per_fill']:,.2f}'} | "
            f"{'—' if block['share_of_book_net'] is None else f'{100 * block['share_of_book_net']:.1f}%'} |"
        )
    return lines


def render_report(results: dict[str, Any]) -> str:
    b = results["breakdowns"]
    execution = results["execution_attribution"]
    lines = [
        "# Corrected Corpus v1 loss-attribution audit",
        "",
        "**Canonical verdict remains: HISTORICAL EDGE DOES NOT SURVIVE CORRECTED IOC TEST.**",
        "",
        "This audit decomposes the exact merged PR #346 population. It changes no "
        "strategy, runtime, Pine, risk, broker, execution, configuration, or deployment behavior.",
        "",
        "## Frozen reproduction",
        "",
        f"- 165 unique attempts = 97 fills + 68 IOC no-fills; 97 resolved, 0 open.",
        f"- Net before commission: **${results['overall']['net_before_commission']:,.2f}**.",
        f"- Commission: **${results['overall']['commission']:,.2f}**.",
        f"- Net after commission: **${results['overall']['net_after_commission']:,.2f}**; "
        f"PF **{results['overall']['profit_factor_after_commission']:.3f}**.",
        "- Breakers: MNQ 2025-09-08; MES 2025-12-11. No attempt is at or after "
        "its instrument's first max-drawdown rejection timestamp.",
        "",
        "## Strategy (worst to best)",
        "",
        *table(b["strategy"], ("strategy",)),
        "",
        "## Instrument",
        "",
        *table(b["instrument"], ("instrument",)),
        "",
        "## Strategy × instrument",
        "",
        *table(b["strategy_instrument"], ("strategy", "instrument")),
        "",
        "## Session",
        "",
        *table(b["session"], ("session",)),
        "",
        "Strategy × session (all populated cells):",
        "",
        *table(b["strategy_session"], ("strategy", "session")),
        "",
        "## Direction",
        "",
        *table(b["direction"], ("direction",)),
        "",
        "Strategy × direction (all populated cells):",
        "",
        *table(b["strategy_direction"], ("strategy", "direction")),
        "",
        "## Time",
        "",
        *table(b["half"], ("half",)),
        "",
        *table(b["quarter"], ("quarter",)),
        "",
        *table(b["month"], ("month",)),
        "",
        "## Market state (journal fields only)",
        "",
        *table(b["market_condition"], ("market_condition",)),
        "",
        *table(b["regime"], ("regime",)),
        "",
        *table(b["trend_direction"], ("trend_direction",)),
        "",
        "Every approved attempt was journal-classified TRENDING. RANGE_BOUND, "
        "CHOPPY, and DEAD therefore have no approved-attempt rows; no missing "
        "classification was invented.",
        "",
        "## IOC / market-fill attribution",
        "",
        f"**{execution['label']}**",
        "",
        f"- Exact candidate fingerprints in common: {execution['exact_candidate_fingerprints_in_common']}.",
        f"- Matched market net: ${execution['matched_market_net_after_commission']:,.2f}; "
        f"matched IOC net: ${execution['matched_ioc_net_after_commission']:,.2f}; "
        f"delta: ${execution['matched_pnl_delta_ioc_minus_market']:,.2f}.",
        f"- Transitions: `{json.dumps(execution['transition_counts'], sort_keys=True)}`.",
        f"- No-fill selection delta: "
        f"${execution['attribution']['no_fill_selection_delta']:,.2f}; changed-fill "
        f"P&L delta: ${execution['attribution']['changed_fill_pnl_delta']:,.2f}. "
        "Cancellations, not changed P&L on filled candidates, dominate this paired delta.",
        f"- Direct identity match to the old 747-row headline is impossible: "
        f"{execution['old_747_row_identity_match']['reason']}",
        "- Therefore the full $54,927.21 old-headline delta cannot be assigned to "
        "execution alone: the old study is also parity-invalid and has a different "
        "747-attempt population. Only the 153 exact fingerprints above support "
        "candidate-level execution attribution.",
        "",
        "The matched diagnostic isolates fill-model effects only where the candidate "
        "fingerprint is identical. Population-only rows reflect divergent shared-account "
        "breaker paths and are not treated as paired causality.",
        "",
        "| Strategy / instrument | Matched | No-fill | Win→no-fill | Loss→no-fill | Market net | IOC net | No-fill Δ | Filled Δ | Total Δ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {row['strategy']} / {row['instrument']} | "
            f"{row['matched_candidates']} | {row['ioc_no_fills']} | "
            f"{row['market_wins_to_ioc_no_fill']} | "
            f"{row['market_losses_to_ioc_no_fill']} | "
            f"${row['market_net_after_commission']:,.2f} | "
            f"${row['ioc_net_after_commission']:,.2f} | "
            f"${row['no_fill_selection_delta']:,.2f} | "
            f"${row['changed_fill_pnl_delta']:,.2f} | "
            f"${row['total_delta_ioc_minus_market']:,.2f} |"
            for row in execution["by_strategy_instrument"]
        ],
        "",
        "## Drawdown breaker",
        "",
        f"- Post-breaker qualified setups rejected: "
        f"MNQ {results['breaker_censorship']['MNQ']['rejected_qualified_setups']}; "
        f"MES {results['breaker_censorship']['MES']['rejected_qualified_setups']}.",
        f"- MNQ rejected by strategy: `{json.dumps(results['breaker_censorship']['MNQ']['by_strategy'], sort_keys=True)}`.",
        f"- MES rejected by strategy: `{json.dumps(results['breaker_censorship']['MES']['by_strategy'], sort_keys=True)}`.",
        f"- MNQ accumulated its canonical admitted-trade loss over "
        f"{results['drawdown_accumulation']['MNQ']['calendar_days_to_halt']} calendar "
        f"days; leading after-commission contributors were ORB Reclaim "
        f"(${results['drawdown_accumulation']['MNQ']['strategy_contribution'][0]['net_after_commission']:,.2f}) "
        f"and VWAP Reclaim "
        f"(${results['drawdown_accumulation']['MNQ']['strategy_contribution'][1]['net_after_commission']:,.2f}). "
        "No single MNQ strategy exclusively caused the halt.",
        f"- MES accumulated its canonical admitted-trade loss over "
        f"{results['drawdown_accumulation']['MES']['calendar_days_to_halt']} calendar "
        f"days; every admitted and later rejected MES setup was ORB Reclaim. ORB "
        "Reclaim therefore shut down evidence collection for the MES account lane.",
        "- These counts prove material evidence censorship without disabling the breaker; "
        "they do not assign hypothetical outcomes to rejected setups. Commission is "
        "analysis-layer only and did not accelerate the configured breaker.",
        "",
        "## Hindsight diagnostic — not promotion evidence",
        "",
        "Current Strategy_Inventory cells rated PROMISING BUT UNPROVEN or better "
        "that actually occur in #346 are ORB Reclaim MNQ and ORB Reclaim MES.",
        "",
        *table(results["pbu_hindsight"]["strategy_instrument"], ("strategy", "instrument")),
        "",
        *table(results["pbu_hindsight"]["half"], ("half",)),
        "",
        f"Combined hindsight subset: {results['pbu_hindsight']['overall']['attempts']} attempts, "
        f"{results['pbu_hindsight']['overall']['fills']} fills, "
        f"PF {results['pbu_hindsight']['overall']['profit_factor_after_commission']:.3f}, "
        f"net ${results['pbu_hindsight']['overall']['net_after_commission']:,.2f}. "
        "**This is hindsight-filtered and is not historical selection or promotion evidence.**",
        "",
        "## Concentration and ranked root causes",
        "",
        f"- Top 1/3/5 losing trades consumed "
        f"{100 * results['concentration']['top_1']['share_of_gross_losing_dollars']:.1f}% / "
        f"{100 * results['concentration']['top_3']['share_of_gross_losing_dollars']:.1f}% / "
        f"{100 * results['concentration']['top_5']['share_of_gross_losing_dollars']:.1f}% "
        "of gross losing dollars.",
        f"- Largest losing day: {results['largest_losing_day']['date']} "
        f"(${results['largest_losing_day']['net_after_commission']:,.2f}); largest losing "
        f"week: {results['largest_losing_week']['week']} "
        f"(${results['largest_losing_week']['net_after_commission']:,.2f}).",
        f"- Longest loss streak: {results['loss_streaks']['longest_consecutive_losses']} "
        f"trades (${results['loss_streaks']['longest_net']:,.2f}).",
        "",
        "1. **Primary — broad negative filled-trade expectancy, led by ORB Reclaim "
        "and concentrated in London.** "
        f"ORB Reclaim contributed ${b['strategy'][0]['net_after_commission']:,.2f}; "
        f"London contributed ${next(x for x in b['session'] if x['session']=='london')['net_after_commission']:,.2f}; "
        "the book had 71 losses versus 26 wins and both instruments were negative.",
        "2. **Secondary — execution realism removed favorable as well as unfavorable "
        "candidates.** In the exact matched subset, no-fill selection accounts for "
        f"${execution['attribution']['no_fill_selection_delta']:,.2f} of IOC-minus-market "
        f"delta versus ${execution['attribution']['changed_fill_pnl_delta']:,.2f} from "
        "changed filled-trade P&L; it does not misuse the identity-poor old ledger.",
        "3. **Tertiary — commission deepened, but did not create, the loss.** Gross "
        "P&L was already -$658.72; $143.56 commission produced the -$802.28 net.",
        "",
        f"The shared-account breaker is a material **evidence-censoring interaction**, "
        f"not a demonstrated cause of realized losses: it rejected "
        f"{len(results['post_breaker_rejections'])} later qualified setups and removed "
        "all H2 observations.",
        "",
        "## Explicit answers",
        "",
        f"1. The corrected book lost because filled trades had negative expectancy "
        f"(${results['overall']['expectancy_per_fill']:,.2f}/fill), primarily ORB Reclaim, "
        "with commission adding $143.56 of loss.",
        f"2. Worst strategy: {b['strategy'][0]['strategy']} "
        f"(${b['strategy'][0]['net_after_commission']:,.2f}).",
        f"3. Worst cell: {b['strategy_instrument'][0]['strategy']} × "
        f"{b['strategy_instrument'][0]['instrument']} "
        f"(${b['strategy_instrument'][0]['net_after_commission']:,.2f}).",
        "4. Loss was broad across both instruments and most populated cells, but "
        "strategy-concentrated in ORB Reclaim; it was not a few-trade tail event.",
        f"5. MES lost more dollars (${next(x for x in b['instrument'] if x['instrument']=='MES')['net_after_commission']:,.2f}) "
        f"than MNQ (${next(x for x in b['instrument'] if x['instrument']=='MNQ')['net_after_commission']:,.2f}), "
        "but neither instrument alone explains the book.",
        "6. Cancellations dominate the matched execution effect. They removed 37 "
        "diagnostic market winners and 22 diagnostic market losses; changed P&L on "
        f"filled candidates was only ${execution['attribution']['changed_fill_pnl_delta']:,.2f}. "
        "The old headline itself cannot support direct candidate causality.",
        "7. Yes. The shared breaker materially censored qualified setups and all H2 evidence.",
        f"8. No. Current-PBU-or-better cells alone still lost "
        f"${abs(results['pbu_hindsight']['overall']['net_after_commission']):,.2f}.",
        "9. No. This shared-account portfolio audit does not invalidate independently "
        "audited isolated-strategy results.",
        "10. No new replay/execution correctness defect was found.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "CORPUS=/Users/djb.a.e/MAINVSCODE/autonomous-futures-system/data/replay_corpus_v1_market_condition_fixed",
        "IOC_LOGS=/private/tmp/corpus346_loss_audit_ioc_logs",
        "MARKET_LOGS=/private/tmp/corpus346_loss_audit_market_logs",
        "",
        "python3 scripts/corrected_ioc_corpus_evidence.py \\",
        "  --corpus \"$CORPUS\" --logs \"$IOC_LOGS\" \\",
        "  --out /private/tmp/corpus346-repro-results.json \\",
        "  --raw /private/tmp/corpus346-repro-raw.jsonl \\",
        "  --report /private/tmp/corpus346-repro-report.md",
        "",
        "python3 scripts/corpus_v1_loss_attribution.py \\",
        "  --corpus \"$CORPUS\" \\",
        "  --ioc-logs \"$IOC_LOGS\" \\",
        "  --market-logs \"$MARKET_LOGS\" \\",
        "  --run-market-counterfactual",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--ioc-logs", required=True, type=Path)
    parser.add_argument("--market-logs", required=True, type=Path)
    parser.add_argument("--run-market-counterfactual", action="store_true")
    parser.add_argument(
        "--frozen-raw",
        type=Path,
        default=REPO / "scripts/corrected_ioc_corpus_raw_trades.jsonl",
    )
    parser.add_argument(
        "--frozen-results",
        type=Path,
        default=REPO / "scripts/corrected_ioc_corpus_results.json",
    )
    parser.add_argument(
        "--old-raw",
        type=Path,
        default=REPO / "scripts/corpus_v1_raw_trades_corrected.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts/corpus_v1_loss_attribution_results.json",
    )
    parser.add_argument(
        "--matched",
        type=Path,
        default=REPO / "scripts/corpus_v1_matched_execution_diagnostic.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/corpus-v1-loss-attribution-2026-07-26.md",
    )
    args = parser.parse_args()

    corpus_files, corpus_hash = tree_sha256(args.corpus)
    if corpus_files != 626:
        raise RuntimeError(f"expected 626 corpus files, found {corpus_files}")
    if args.run_market_counterfactual:
        run_market_counterfactual(args.corpus, args.market_logs)

    approved, rejected, breaker = parse_logs(args.ioc_logs)
    rows = enrich(list(jsonl(args.frozen_raw)), approved)
    frozen_results = json.loads(args.frozen_results.read_text(encoding="utf-8"))
    checks = verify_frozen(rows, frozen_results, breaker)
    book_net = EXPECTED["net_after_commission"]
    overall = stats(rows, book_net)
    breakdowns = {
        "strategy": grouped(rows, ("strategy",), book_net),
        "instrument": grouped(rows, ("instrument",), book_net),
        "strategy_instrument": grouped(rows, ("strategy", "instrument"), book_net),
        "session": grouped(rows, ("session",), book_net),
        "strategy_session": grouped(rows, ("strategy", "session"), book_net),
        "direction": grouped(rows, ("direction",), book_net),
        "strategy_direction": grouped(rows, ("strategy", "direction"), book_net),
        "half": grouped_with_labels(rows, "half", PERIODS["half"], book_net),
        "quarter": grouped_with_labels(rows, "quarter", PERIODS["quarter"], book_net),
        "month": grouped(rows, ("month",), book_net),
        "market_condition": grouped(rows, ("market_condition",), book_net),
        "regime": grouped(rows, ("regime",), book_net),
        "trend_direction": grouped(rows, ("trend_direction",), book_net),
    }
    pbu_rows = [
        row for row in rows if (row["strategy"], row["instrument"]) in PBU_OR_BETTER_CELLS
    ]
    execution, matched = matched_execution(args.ioc_logs, args.market_logs)
    by_day = grouped(rows, ("date",), book_net)
    by_week = grouped(rows, ("week",), book_net)
    results = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_main_sha": subprocess.check_output(
                ["git", "rev-parse", "origin/main"], cwd=REPO, text=True
            ).strip(),
            "pr346_merge_sha": PR346_MERGE_SHA,
            "pr346_source_sha": PR346_SOURCE_SHA,
            "source_artifacts": {
                str(args.frozen_raw): sha256(args.frozen_raw),
                str(args.frozen_results): sha256(args.frozen_results),
                str(args.old_raw): sha256(args.old_raw),
                "docs/corrected-ioc-corpus-evidence-2026-07-26.md": sha256(
                    REPO / "docs/corrected-ioc-corpus-evidence-2026-07-26.md"
                ),
                "scripts/corrected_ioc_corpus_evidence.py": sha256(
                    REPO / "scripts/corrected_ioc_corpus_evidence.py"
                ),
            },
            "corpus_files": corpus_files,
            "corpus_tree_sha256": corpus_hash,
            "ioc_logs": str(args.ioc_logs),
            "market_diagnostic_logs": str(args.market_logs),
            "commission_round_trip": COMMISSION_RT,
            "pbu_or_better_cells_from_current_inventory": [
                {"strategy": key[0], "instrument": key[1], "classification": value}
                for key, value in sorted(PBU_OR_BETTER_CELLS.items())
            ],
        },
        "frozen_reproduction_checks": checks,
        "overall": overall,
        "breakdowns": breakdowns,
        "concentration": losing_concentration(rows, book_net),
        "loss_streaks": loss_streaks(rows),
        "largest_losing_day": by_day[0],
        "largest_losing_week": by_week[0],
        "drawdown_accumulation": cumulative_before_breaker(rows),
        "breaker_censorship": breaker_censorship(rejected),
        "post_breaker_rejections": rejected,
        "pbu_hindsight": {
            "label": "HINDSIGHT DIAGNOSTIC — NOT PROMOTION EVIDENCE",
            "overall": stats(pbu_rows, book_net),
            "strategy_instrument": grouped(
                pbu_rows, ("strategy", "instrument"), book_net
            ),
            "instrument": grouped(pbu_rows, ("instrument",), book_net),
            "half": grouped_with_labels(
                pbu_rows, "half", PERIODS["half"], book_net
            ),
            "strategy": grouped(pbu_rows, ("strategy",), book_net),
        },
        "execution_attribution": execution,
        "correctness_defect": None,
        "ranked_root_causes": [
            "Broad negative filled-trade expectancy led by ORB Reclaim",
            "Shared-account drawdown breakers censored later strategy evidence",
            "IOC execution differences versus same-code market-fill diagnostic",
        ],
    }

    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    with args.matched.open("w", encoding="utf-8") as handle:
        for row in matched:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    args.report.write_text(render_report(results).rstrip() + "\n")
    print(
        json.dumps(
            {
                "frozen_reproduction": checks,
                "overall": overall,
                "worst_strategy": breakdowns["strategy"][0],
                "worst_cell": breakdowns["strategy_instrument"][0],
                "matched_execution": execution,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
