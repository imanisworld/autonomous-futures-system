#!/usr/bin/env python3
"""Read-only Daily/4H horizon compatibility study for the frozen clean shadow cohort.

The prior controlled geometry study froze the clean V1-EPOCH-2 AHEAD population.
This study keeps recorded target geometry, entry, stops, contract, costs and
observed path fixed and changes only the holding horizon for Daily and 4H_RTH
rows: same-session close versus the close of exactly one additional RTH session.

No V1 policy or runtime state is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from alert_ranker.paper_v1 import entry_geometry_state

START = "2026-09-16T16:47:46"
END_EXCLUSIVE = "2026-09-18"
TARGET_TIMEFRAMES = ("1D", "4H_RTH")
STUDY_ID = "OPTIONS_SHADOW_HORIZON_COMPATIBILITY_V0_1"


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _entry_geometry(direction: str, inputs: dict[str, Any], contract: dict[str, Any]) -> str | None:
    stored = contract.get("paper_entry_geometry") or inputs.get("paper_entry_geometry")
    if stored:
        return str(stored)
    price = _float(inputs.get("price"))
    stop = _float(
        contract.get("stop")
        if contract.get("stop") is not None
        else inputs.get("underlying_invalidation") or inputs.get("stop")
    )
    target = _float(
        contract.get("target")
        if contract.get("target") is not None
        else inputs.get("target_1") or inputs.get("target")
    )
    return entry_geometry_state(direction, price, stop, target)


def _hit(direction: str, price: float, level: float, kind: str) -> bool:
    if direction == "LONG":
        return price >= level if kind == "target" else price <= level
    if direction == "SHORT":
        return price <= level if kind == "target" else price >= level
    raise ValueError(f"unsupported direction: {direction}")


def _summarize(rows: list[dict[str, Any]], horizon_key: str) -> dict[str, Any]:
    selected = [row[horizon_key] for row in rows]
    counts = Counter(item["resolution"] for item in selected)
    pnl = [item["pnl_dollars"] for item in selected]
    return {
        "n": len(selected),
        "resolution_counts": dict(sorted(counts.items())),
        "censored_n": counts["CENSORED_AT_HORIZON"],
        "forced_horizon_pnl_all_rows": round(sum(pnl), 2),
        "forced_horizon_avg_pnl": round(sum(pnl) / len(pnl), 2) if pnl else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument(
        "--geometry-result",
        default="data/options_shadow_target_geometry_controlled_2026_09_18/result.json",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    geometry = json.loads(Path(args.geometry_result).read_text())
    if geometry.get("study") != "OPTIONS_SHADOW_TARGET_GEOMETRY_CONTROLLED_V0_1":
        raise SystemExit("unexpected geometry result identity")
    frozen_ahead_ids = {int(row["shadow_id"]) for row in geometry["rows"]}
    if len(frozen_ahead_ids) != 47:
        raise SystemExit(f"geometry result must contain 47 AHEAD rows, got {len(frozen_ahead_ids)}")

    db_path = Path(args.db).resolve()
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    raw_rows = connection.execute(
        """select id,timestamp,ticker,direction,status,setup_inputs_json,
                  selected_contract_json,outcome_json
           from options_shadow_journal
          where timestamp>=? and timestamp<?
          order by timestamp,id""",
        (START, END_EXCLUSIVE),
    ).fetchall()

    observed_ahead_ids: set[int] = set()
    selected: list[tuple[Any, ...]] = []
    geometry_counts: Counter[str | None] = Counter()

    for row in raw_rows:
        inputs = json.loads(row[5])
        contract = json.loads(row[6])
        geometry_state = _entry_geometry(row[3], inputs, contract)
        geometry_counts[geometry_state] += 1
        if geometry_state == "AHEAD":
            observed_ahead_ids.add(int(row[0]))
        timeframe = inputs.get("setup_timeframe") or inputs.get("timeframe")
        if int(row[0]) in frozen_ahead_ids and timeframe in TARGET_TIMEFRAMES:
            selected.append((row, inputs, contract))

    if observed_ahead_ids != frozen_ahead_ids:
        raise SystemExit("current DB AHEAD membership does not match frozen geometry result")
    if len(raw_rows) != 117 or geometry_counts["AHEAD"] != 47:
        raise SystemExit(
            f"frozen cohort mismatch rows={len(raw_rows)} geometry={dict(geometry_counts)}"
        )

    timeframe_counts = Counter(
        (inputs.get("setup_timeframe") or inputs.get("timeframe"))
        for _, inputs, _ in selected
    )
    if len(selected) != 24 or timeframe_counts != Counter({"4H_RTH": 14, "1D": 10}):
        raise SystemExit(
            f"Daily/4H population mismatch n={len(selected)} counts={dict(timeframe_counts)}"
        )

    tickers = sorted({row[0][2] for row in selected})
    scan_cache: dict[str, list[dict[str, Any]]] = {}
    for ticker in tickers:
        scan_rows = connection.execute(
            """select timestamp,raw_json
                 from scans
                where ticker=? and timestamp>=?
                order by timestamp,id""",
            (ticker, START),
        ).fetchall()
        collapsed: dict[datetime, dict[str, Any]] = {}
        for timestamp, raw_json in scan_rows:
            payload = json.loads(raw_json)
            price = _float(payload.get("price"))
            session_close = payload.get("session_close")
            session_date = payload.get("session_date")
            if price is None:
                continue
            when = _dt(timestamp)
            candidate = {
                "timestamp": timestamp,
                "time": when,
                "price": price,
                "session_date": str(session_date) if session_date else None,
                "session_close": str(session_close) if session_close else None,
            }
            prior = collapsed.get(when)
            if prior and abs(prior["price"] - price) > 1e-9:
                raise SystemExit(f"conflicting scan price {ticker} {timestamp}")
            if prior:
                # Prefer the richer duplicate when one scan carries session metadata.
                if not prior["session_close"] and candidate["session_close"]:
                    collapsed[when] = candidate
            else:
                collapsed[when] = candidate
        scan_cache[ticker] = [collapsed[key] for key in sorted(collapsed)]

    def next_session_close(ticker: str, current_close: str) -> str:
        cutoff = _dt(current_close)
        candidates = [
            item
            for item in scan_cache[ticker]
            if item["time"] > cutoff
            and item["session_date"]
            and item["session_close"]
            and _dt(item["session_close"]) > cutoff
        ]
        if not candidates:
            raise SystemExit(f"missing next RTH session for {ticker} after {current_close}")
        first_session = candidates[0]["session_date"]
        closes = {
            item["session_close"]
            for item in candidates
            if item["session_date"] == first_session
        }
        if len(closes) != 1:
            raise SystemExit(
                f"ambiguous next-session close for {ticker} {first_session}: {sorted(closes)}"
            )
        return next(iter(closes))

    def observations(
        shadow_id: int,
        ticker: str,
        entry_timestamp: str,
        horizon: str,
    ) -> list[dict[str, Any]]:
        marks = connection.execute(
            """select timestamp,bid,ask,error
                 from options_contract_marks
                where shadow_id=?
                order by timestamp,id""",
            (shadow_id,),
        ).fetchall()
        entry_time = _dt(entry_timestamp)
        horizon_time = _dt(horizon)
        scans = scan_cache[ticker]
        out: list[dict[str, Any]] = []
        for timestamp, bid, ask, error in marks:
            mark_time = _dt(timestamp)
            if mark_time < entry_time or mark_time > horizon_time or error or bid is None:
                continue
            nearest = min(
                scans,
                key=lambda item: abs((item["time"] - mark_time).total_seconds()),
                default=None,
            )
            if nearest is None or abs((nearest["time"] - mark_time).total_seconds()) > 2:
                continue
            out.append(
                {
                    "timestamp": timestamp,
                    "underlying": nearest["price"],
                    "bid": _float(bid),
                    "ask": _float(ask),
                }
            )
        return out

    def resolve(
        row: tuple[Any, ...],
        inputs: dict[str, Any],
        contract: dict[str, Any],
        horizon: str,
    ) -> dict[str, Any]:
        shadow_id, entry_timestamp, ticker, direction = row[:4]
        stop = _float(
            contract.get("stop")
            if contract.get("stop") is not None
            else inputs.get("underlying_invalidation") or inputs.get("stop")
        )
        target = _float(
            contract.get("target")
            if contract.get("target") is not None
            else inputs.get("target_1") or inputs.get("target")
        )
        entry_ask = _float(contract.get("entry_ask") or inputs.get("option_ask"))
        premium_stop = _float(contract.get("premium_stop") or inputs.get("premium_stop"))
        quantity = int(contract.get("contracts") or inputs.get("contracts") or 1)
        if None in (stop, target, entry_ask, premium_stop):
            raise SystemExit(f"missing fixed trade input for shadow_id={shadow_id}")

        path = observations(int(shadow_id), ticker, entry_timestamp, horizon)
        if not path:
            raise SystemExit(f"missing executable observation path shadow_id={shadow_id}")

        resolution = "CENSORED_AT_HORIZON"
        exit_bid = path[-1]["bid"]
        exit_timestamp = path[-1]["timestamp"]
        for point in path[1:]:
            underlying_stop = _hit(direction, point["underlying"], stop, "stop")
            premium_stop_hit = point["bid"] <= premium_stop
            stop_hit = underlying_stop or premium_stop_hit
            target_hit = _hit(direction, point["underlying"], target, "target")
            if stop_hit and target_hit:
                resolution = "STOP_AMBIGUOUS"
                exit_bid = point["bid"]
                exit_timestamp = point["timestamp"]
                break
            if stop_hit:
                resolution = "STOP"
                exit_bid = point["bid"]
                exit_timestamp = point["timestamp"]
                break
            if target_hit:
                resolution = "TARGET"
                exit_bid = point["bid"]
                exit_timestamp = point["timestamp"]
                break

        return {
            "resolution": resolution,
            "exit_timestamp": exit_timestamp,
            "exit_bid": exit_bid,
            "pnl_dollars": round((exit_bid - entry_ask) * 100 * quantity, 2),
            "observation_count": len(path),
        }

    evidence_inputs: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    structural_keys: Counter[str] = Counter()

    for row, inputs, contract in selected:
        shadow_id, entry_timestamp, ticker, direction = row[:4]
        timeframe = inputs.get("setup_timeframe") or inputs.get("timeframe")
        setup_type = inputs.get("setup_type")
        baseline_horizon = str(inputs["session_close"])
        extended_horizon = next_session_close(ticker, baseline_horizon)
        baseline = resolve(row, inputs, contract, baseline_horizon)
        extended = resolve(row, inputs, contract, extended_horizon)

        if baseline["resolution"] == "CENSORED_AT_HORIZON":
            if extended["observation_count"] <= baseline["observation_count"]:
                raise SystemExit(
                    f"no post-close evidence for censored row shadow_id={shadow_id}"
                )
            if _dt(extended["exit_timestamp"]) <= _dt(baseline_horizon):
                raise SystemExit(
                    f"extended horizon did not add post-close evidence shadow_id={shadow_id}"
                )

        structural_key = "|".join(
            [
                str(ticker),
                str(timeframe),
                str(setup_type),
                str(direction),
                str(inputs.get("setup_entry_trigger")),
                str(inputs.get("session_date")),
            ]
        )
        structural_keys[structural_key] += 1

        fixed_input = {
            "shadow_id": int(shadow_id),
            "entry_timestamp": entry_timestamp,
            "ticker": ticker,
            "direction": direction,
            "timeframe": timeframe,
            "setup_type": setup_type,
            "entry_underlying": _float(inputs.get("price")),
            "underlying_stop": _float(
                contract.get("stop")
                if contract.get("stop") is not None
                else inputs.get("underlying_invalidation") or inputs.get("stop")
            ),
            "recorded_target_1": _float(
                contract.get("target")
                if contract.get("target") is not None
                else inputs.get("target_1") or inputs.get("target")
            ),
            "contract": contract.get("contract"),
            "entry_ask": _float(contract.get("entry_ask") or inputs.get("option_ask")),
            "premium_stop": _float(contract.get("premium_stop") or inputs.get("premium_stop")),
            "quantity": int(contract.get("contracts") or inputs.get("contracts") or 1),
            "same_session_close": baseline_horizon,
            "next_rth_session_close": extended_horizon,
            "extended_observations": observations(
                int(shadow_id), ticker, entry_timestamp, extended_horizon
            ),
        }
        evidence_inputs.append(fixed_input)
        result_rows.append(
            {
                "shadow_id": int(shadow_id),
                "ticker": ticker,
                "direction": direction,
                "timeframe": timeframe,
                "setup_type": setup_type,
                "structural_key": structural_key,
                "same_session": baseline,
                "plus_one_rth_session": extended,
            }
        )

    by_timeframe: dict[str, Any] = {}
    for timeframe in TARGET_TIMEFRAMES:
        tf_rows = [row for row in result_rows if row["timeframe"] == timeframe]
        same = _summarize(tf_rows, "same_session")
        extended = _summarize(tf_rows, "plus_one_rth_session")
        by_timeframe[timeframe] = {
            "same_session": same,
            "plus_one_rth_session": extended,
            "pnl_delta": round(
                extended["forced_horizon_pnl_all_rows"]
                - same["forced_horizon_pnl_all_rows"],
                2,
            ),
        }

    overall_same = _summarize(result_rows, "same_session")
    overall_extended = _summarize(result_rows, "plus_one_rth_session")
    transitions = Counter(
        f'{row["same_session"]["resolution"]}->{row["plus_one_rth_session"]["resolution"]}'
        for row in result_rows
    )

    unique_structural = len(structural_keys)
    daily_structural = len(
        {
            row["structural_key"]
            for row in result_rows
            if row["timeframe"] == "1D"
        }
    )
    h4_structural = len(
        {
            row["structural_key"]
            for row in result_rows
            if row["timeframe"] == "4H_RTH"
        }
    )

    result = {
        "study": STUDY_ID,
        "scope": "research_only_no_v1_tuning",
        "population": {
            "source_geometry_study": geometry["study"],
            "source_geometry_input_sha256": geometry["input_sha256"],
            "source_ahead_rows": 47,
            "daily_h4_rows": len(result_rows),
            "timeframe_counts": dict(sorted(timeframe_counts.items())),
            "unique_structural_keys": unique_structural,
            "unique_structural_keys_by_timeframe": {
                "1D": daily_structural,
                "4H_RTH": h4_structural,
            },
        },
        "controls": {
            "changed_variable": "holding horizon only",
            "target": "unchanged recorded target_1",
            "entry": "unchanged first-sight underlying price",
            "stop": "unchanged underlying invalidation and premium stop",
            "contract": "unchanged selected contract and quantity",
            "costs": "unchanged ASK entry / BID exit, no added fee",
            "baseline_horizon": "same RTH session close",
            "extended_horizon": "close of exactly one additional RTH session",
            "path": "same retained option marks matched to same-time scanner underlying prices",
            "same_observation_ambiguity": "STOP_AMBIGUOUS pessimistic if stop and target are both true",
            "censoring": "explicit; unresolved rows marked to last executable BID by each fixed horizon",
        },
        "input_sha256": _sha256(evidence_inputs),
        "overall": {
            "same_session": overall_same,
            "plus_one_rth_session": overall_extended,
            "pnl_delta": round(
                overall_extended["forced_horizon_pnl_all_rows"]
                - overall_same["forced_horizon_pnl_all_rows"],
                2,
            ),
            "resolution_transitions": dict(sorted(transitions.items())),
        },
        "by_timeframe": by_timeframe,
        "rows": result_rows,
        "limitations": [
            "This is a 24-row Daily/4H subset of the frozen clean shadow cohort, not a 212R-specific option population.",
            "Rows are not fully independent; the 10 Daily rows represent 7 structural keys because some setups were observed in more than one evidence lane/first-sight row.",
            "The one-extra-session horizon is the minimal common extension available for every row; this is not an optimization sweep and does not establish an optimal hold period.",
            "Observed scanner/contract snapshots do not reveal price order between snapshots.",
            "No fee/slippage overlay, target change, contract reselection or V1 policy change is introduced.",
            "Small samples cannot establish positive expectancy or promotion eligibility.",
        ],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    print(
        json.dumps(
            {
                "input_sha256": result["input_sha256"],
                "population": result["population"],
                "overall": result["overall"],
                "by_timeframe": result["by_timeframe"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
