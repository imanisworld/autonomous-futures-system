#!/usr/bin/env python3
"""Read-only Asian/London session-hold location report.

Joins journaled ``shadow_candidates`` carrying the 2026-07-16 location block
to their causal ``SHADOW_OUTCOME`` rows, then compares held sessions with New
York.  Input journals are never modified; the report is written to stdout.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


HELD = {"asian", "london"}
SESSIONS = ("asian", "london", "new_york")
TERMINAL = {"WIN", "LOSS", "NO_FILL", "OPEN"}
TICK_VALUE = {"MES": 1.25, "MNQ": 0.50}
COMMISSION_RT = 1.24
SLIPPAGE_TICKS_RT = 2


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _records(paths: Iterable[Path]):
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: {exc}") from exc
                if isinstance(row, dict):
                    yield row


def _candidate_key(row: dict, candidate: dict) -> str:
    return "|".join(
        (
            "shadow_setups",
            str(row["instrument"]),
            str(row["ts"]),
            str(candidate["strategy"]),
            str(candidate["direction"]).upper(),
            str(float(candidate["entry"])),
        )
    )


def _r_multiple(candidate: dict, outcome: dict) -> float | None:
    result = outcome.get("result")
    if result == "LOSS":
        return -1.0
    if result != "WIN":
        return None
    entry = float(candidate["entry"])
    stop = float(candidate["stop"])
    target = float(candidate["target"])
    risk = abs(entry - stop)
    return abs(target - entry) / risk if risk else None


def _net_r(instrument: str, candidate: dict, gross_r: float | None) -> float | None:
    """Apply the project's $1.24 RT + two-tick RT honest-cost convention."""
    if gross_r is None:
        return None
    tick_value = TICK_VALUE[instrument]
    risk_dollars = abs(float(candidate["entry"]) - float(candidate["stop"])) / 0.25 * tick_value
    costs = COMMISSION_RT + SLIPPAGE_TICKS_RT * tick_value
    return gross_r - costs / risk_dollars if risk_dollars else None


def _pct(numerator: int, denominator: int) -> str:
    return "n/a" if not denominator else f"{100 * numerator / denominator:.1f}%"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:+.3f}{suffix}"


def _summary(rows: list[dict]) -> dict:
    results = Counter(r.get("result") or "UNRESOLVED" for r in rows)
    fills = results["WIN"] + results["LOSS"]
    rs = [r["r"] for r in rows if r.get("r") is not None]
    net_rs = [r["net_r"] for r in rows if r.get("net_r") is not None]
    return {
        "candidates": len(rows),
        "signal_bars": len({(r["ts"], r["instrument"]) for r in rows}),
        "joined": sum(results[k] for k in TERMINAL),
        "unresolved": results["UNRESOLVED"],
        "wins": results["WIN"],
        "losses": results["LOSS"],
        "no_fill": results["NO_FILL"],
        "open": results["OPEN"],
        "fills": fills,
        "win_rate": results["WIN"] / fills if fills else None,
        "mean_r_per_fill": _mean(rs),
        "mean_net_r_per_fill": _mean(net_rs),
        "mean_r_per_candidate": sum(rs) / len(rows) if rows else None,
        "mean_net_r_per_candidate": sum(net_rs) / len(rows) if rows else None,
        "mid": sum(bool(r.get("middle")) for r in rows),
        "blocked": sum(r.get("target_blocked") is True for r in rows),
        "late": sum(r.get("impulse") == "late_entry" for r in rows),
        "aligned": sum(r.get("alignment") == "aligned" for r in rows),
    }


def _table_line(label: str, s: dict) -> str:
    return (
        f"| {label} | {s['candidates']} | {s['signal_bars']} | {s['joined']} | "
        f"{s['wins']}/{s['losses']}/{s['no_fill']}/{s['open']} | "
        f"{_pct(s['wins'], s['fills'])} | {_fmt(s['mean_net_r_per_fill'], 'R')} | "
        f"{_fmt(s['mean_net_r_per_candidate'], 'R')} | {_pct(s['mid'], s['candidates'])} | "
        f"{_pct(s['blocked'], s['candidates'])} | {_pct(s['late'], s['candidates'])} |"
    )


def build_report(paths: list[Path], before: datetime | None) -> str:
    records = list(_records(paths))
    outcomes = {
        row.get("candidate_key"): row.get("shadow_outcome") or {}
        for row in records
        if row.get("type") == "SHADOW_OUTCOME"
        and row.get("lane") == "shadow_setups"
        and row.get("final") is True
    }
    hold_rows = [
        row
        for row in records
        if row.get("session") in HELD
        and "demo_execution_hold"
        in str(row.get("gate_reason") or row.get("reason") or "")
    ]

    candidates: list[dict] = []
    seen: set[str] = set()
    for row in records:
        session = row.get("session")
        loc = (row.get("context") or {}).get("location_context")
        if session not in SESSIONS or not isinstance(loc, dict):
            continue
        if before and _dt(str(row["ts"])) >= before:
            continue
        for candidate in row.get("shadow_candidates") or []:
            candidate_loc = candidate.get("location")
            if not isinstance(candidate_loc, dict):
                continue
            key = _candidate_key(row, candidate)
            if key in seen:
                continue
            seen.add(key)
            outcome = outcomes.get(key, {})
            gross_r = _r_multiple(candidate, outcome)
            candidates.append(
                {
                    "key": key,
                    "ts": row["ts"],
                    "instrument": row["instrument"],
                    "session": session,
                    "strategy": candidate["strategy"],
                    "direction": candidate["direction"],
                    "entry": candidate["entry"],
                    "result": outcome.get("result", "UNRESOLVED"),
                    "r": gross_r,
                    "net_r": _net_r(row["instrument"], candidate, gross_r),
                    "middle": candidate_loc.get("middle_of_range"),
                    "alignment": candidate_loc.get("direction_zone_alignment"),
                    "target_blocked": candidate_loc.get("target_blocked_by_opposing_zone"),
                    "impulse": (loc.get("impulse") or {}).get("phase"),
                }
            )

    by_session = {s: _summary([r for r in candidates if r["session"] == s]) for s in SESSIONS}
    held = [r for r in candidates if r["session"] in HELD]
    ny = [r for r in candidates if r["session"] == "new_york"]
    groups = {"held combined": _summary(held), "new_york": _summary(ny)}

    held_mid = _summary([r for r in held if r["middle"] is True])
    held_edge = _summary([r for r in held if r["middle"] is False])
    ny_mid = _summary([r for r in ny if r["middle"] is True])
    ny_edge = _summary([r for r in ny if r["middle"] is False])

    held_s = groups["held combined"]
    ny_s = groups["new_york"]
    direct = len(hold_rows)
    asian_r = by_session["asian"]["mean_net_r_per_fill"]
    london_r = by_session["london"]["mean_net_r_per_fill"]
    if direct:
        verdict = "MIXED"
        rationale = "Direct hold suppressions exist, but this small report does not assume their counterfactual result without a matching resolver lane."
    elif asian_r is not None and london_r is not None and asian_r * london_r < 0:
        verdict = "MIXED"
        rationale = "Asian and London had opposite after-cost expectancy signs, so one blanket policy combines a worthwhile-looking session with a harmful one."
    elif held_s["mean_net_r_per_fill"] is not None and held_s["mean_net_r_per_fill"] > 0:
        verdict = "TOO BLUNT"
        rationale = "Each populated held-session cohort had positive after-cost expectancy even though no executable candidate actually reached the hold gate."
    elif (
        held_s["mean_net_r_per_fill"] is not None
        and ny_s["mean_net_r_per_fill"] is not None
        and held_s["mean_net_r_per_fill"] < 0 <= ny_s["mean_net_r_per_fill"]
    ):
        verdict = "JUSTIFIED"
        rationale = "Held-session resolved fills were negative while New York was non-negative, with no observed worthwhile executable setup directly suppressed."
    else:
        verdict = "MIXED"
        rationale = "The session contrast is not clean enough for either blanket vindication or rejection, and no executable candidate reached the hold gate."

    examples = []
    for session in SESSIONS:
        for result in ("WIN", "LOSS"):
            match = next(
                (
                    r
                    for r in sorted(candidates, key=lambda item: item["ts"])
                    if r["session"] == session and r["result"] == result
                ),
                None,
            )
            if match:
                examples.append(match)

    lines = [
        "# Session-hold location report",
        "",
        f"**Verdict: {verdict}.** {rationale}",
        "",
        f"Evidence window: {len(paths)} journal files; candidate signal time before "
        f"{before.isoformat() if before else 'no cutoff'}. Inputs are read-only.",
        f"Direct hold-gate suppressions (`demo_execution_hold`): **{direct}**.",
        "",
        "## Session comparison",
        "",
        "W/L/NF/O = win/loss/no-fill/open. Net R applies $1.24 round-trip commission plus two ticks round-trip slippage to each fill. Net R/candidate counts no-fill and open as 0R; Net R/fill uses terminal wins/losses only.",
        "",
        "| Session | candidates | signal bars | joined | W/L/NF/O | fill WR | Net R/fill | Net R/candidate | mid-range | target blocked | late impulse |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for session in SESSIONS:
        lines.append(_table_line(session, by_session[session]))
    lines.append(_table_line("held combined", groups["held combined"]))
    lines.extend(
        [
            "",
            "## Mid-structure check",
            "",
            "| Cohort | candidates | signal bars | joined | W/L/NF/O | fill WR | Net R/fill | Net R/candidate | mid-range | target blocked | late impulse |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            _table_line("held: middle", held_mid),
            _table_line("held: not middle", held_edge),
            _table_line("NY: middle", ny_mid),
            _table_line("NY: not middle", ny_edge),
            "",
            "## Exact terminal examples",
            "",
            "| signal ts | session | instrument | strategy | result | gross R | net R | middle | alignment | blocked | impulse |",
            "|---|---|---|---|---:|---:|---:|---:|---|---:|---|",
        ]
    )
    for row in examples:
        lines.append(
            f"| {row['ts']} | {row['session']} | {row['instrument']} | {row['strategy']} | "
            f"{row['result']} | {_fmt(row['r'], 'R')} | {_fmt(row['net_r'], 'R')} | {row['middle']} | {row['alignment']} | "
            f"{row['target_blocked']} | {row['impulse']} |"
        )
    lines.extend(
        [
            "",
            "## Scope note",
            "",
            "The joined candidates are observe-only shadow setups, not proof that the executable engine would have approved them. "
            "The direct-suppression count is therefore reported separately. A zero direct count means the hold had no demonstrated opportunity cost in this window; it does not prove future opportunity cost is zero.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journals", nargs="+", type=Path, help="journal_*.jsonl files")
    parser.add_argument("--before", help="exclusive ISO-8601 candidate-time cutoff")
    args = parser.parse_args()
    before = _dt(args.before) if args.before else None
    print(build_report(sorted(args.journals), before), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
