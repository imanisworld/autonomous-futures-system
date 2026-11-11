"""Pure decisive-cell evaluator for the registered slice-success trial.

The rules are ``docs/prereg-322-afs0523-slice-success-2026-11-11.md`` §1.4–§1.7
and §1.13. This module does not score a slice, read a file, or authorize a run.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Literal, Mapping, NoReturn, Optional, Sequence, TypedDict

Market = Literal["NQ", "MNQ"]
Status = Literal["RESOLVED", "UNRESOLVED", "OPEN", "NO_FILL"]
CellStatus = Literal[
    "INSUFFICIENT",
    "FAIL",
    "WAIT_UNRESOLVED",
    "FAIL_DRAWDOWN",
    "WAIT_CI",
    "PASS",
]

COMMISSION_ROUND_TRIP_USD = 1.48
TICK_SIZE_PT = 0.25
TICK_VALUE_USD = 0.50
USD_PER_POINT = TICK_VALUE_USD / TICK_SIZE_PT
DECISIVE_SLIPPAGE_TICKS = 3.0
MIN_RESOLVED = 30
UNRESOLVED_MAX_FRACTION = 0.10
DD_CAP_R = 8.0
ALPHA_ONE_SIDED = 0.05
ACCOUNT_DD_FLOOR_USD = 450.0
ACCOUNT_SINGLE_LOSS_USD = 150.0
MNQ_SEAL_DATE = "2026-06-29"
_SHA256_HEX = 64


class TradeRecord(TypedDict):
    date: str
    cell_id: str
    market: Market
    model: Literal["honest_ioc"]
    slippage_ticks: float
    direction: Literal["LONG", "SHORT"]
    status: Status
    fill_entry: Optional[float]
    stop: float
    target: float
    net: Optional[float]
    harness_result: Optional[Literal["WIN", "LOSS", "BREAKEVEN"]]
    reason: Optional[str]


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{label} must be a finite number")
    return number


def _optional_finite(value: object, label: str) -> float | None:
    if value is None:
        return None
    return _finite(value, label)


def _r10(value: float) -> float:
    return round(float(value), 10)


def _usd(value: float) -> float:
    return round(float(value), 2)


def _constants() -> dict[str, float]:
    return {
        "COMMISSION_ROUND_TRIP_USD": COMMISSION_ROUND_TRIP_USD,
        "TICK_SIZE_PT": TICK_SIZE_PT,
        "TICK_VALUE_USD": TICK_VALUE_USD,
        "USD_PER_POINT": USD_PER_POINT,
        "DECISIVE_SLIPPAGE_TICKS": DECISIVE_SLIPPAGE_TICKS,
        "MIN_RESOLVED": MIN_RESOLVED,
        "UNRESOLVED_MAX_FRACTION": UNRESOLVED_MAX_FRACTION,
        "DD_CAP_R": DD_CAP_R,
        "ALPHA_ONE_SIDED": ALPHA_ONE_SIDED,
        "ACCOUNT_DD_FLOOR_USD": ACCOUNT_DD_FLOOR_USD,
        "ACCOUNT_SINGLE_LOSS_USD": ACCOUNT_SINGLE_LOSS_USD,
    }


def geometric_nets(
    direction: str,
    fill_entry: float,
    stop: float,
    target: float,
    slippage_ticks: float = DECISIVE_SLIPPAGE_TICKS,
) -> tuple[float, float]:
    """Target net and stop net in MNQ-equivalent dollars, commission on both."""
    slip = float(slippage_ticks) * TICK_SIZE_PT
    if direction == "LONG":
        win_points = (target - slip) - fill_entry
        loss_points = (stop - slip) - fill_entry
    elif direction == "SHORT":
        win_points = fill_entry - (target + slip)
        loss_points = fill_entry - (stop + slip)
    else:
        _fail(f"unsupported direction {direction!r}")
    win_net = win_points * USD_PER_POINT - COMMISSION_ROUND_TRIP_USD
    loss_net = loss_points * USD_PER_POINT - COMMISSION_ROUND_TRIP_USD
    return win_net, loss_net


def _binomial_upper_tail(k: int, n: int, probability: float) -> float:
    """Sum from i=k through n of Binomial(n, probability)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if probability <= 0.0:
        return 0.0
    if probability >= 1.0:
        return 1.0
    log_term = (
        math.log(math.comb(n, k))
        + k * math.log(probability)
        + (n - k) * math.log(1.0 - probability)
    )
    term = math.exp(log_term)
    total = term
    odds = probability / (1.0 - probability)
    for index in range(k, n):
        term *= (n - index) / (index + 1) * odds
        total += term
    return total


def clopper_pearson_lower(k: int, n: int, alpha: float = ALPHA_ONE_SIDED) -> float:
    """One-sided lower Clopper-Pearson bound. Two hundred bisection steps."""
    if n == 0 or k == 0:
        return 0.0
    if k < 0 or k > n:
        _fail(f"clopper_pearson_lower k={k} n={n} is outside 0..n")
    low = 0.0
    high = 1.0
    for _step in range(200):
        mid = (low + high) / 2.0
        if _binomial_upper_tail(k, n, mid) > alpha:
            high = mid
        else:
            low = mid
    return (low + high) / 2.0


def _seal_violation(market: str, day: str) -> bool:
    return market == "MNQ" and day >= MNQ_SEAL_DATE


def trade_records_from_ledger(
    ledger: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    slice_id: str,
    market: Market,
    slippage_key: str = "3tick",
    model_key: str = "ioc_close",
) -> list[TradeRecord]:
    """Build decisive honest-IOC rows from one wrapper ledger dict.

    The caller passes the inner slippage map, not the ``{"ledger": ...}`` wrapper.
    """
    if not isinstance(ledger, Mapping):
        _fail("ledger must be a mapping of slippage keys to rows")
    if model_key != "ioc_close":
        _fail("decisive records require model_key ioc_close")
    if market not in {"NQ", "MNQ"}:
        _fail(f"unsupported market {market!r}")
    if slippage_key not in ledger:
        _fail(f"ledger is missing {slippage_key}")
    rows = ledger[slippage_key]
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        _fail(f"{slippage_key} rows must be a sequence")
    slippage_ticks = float(slippage_key.removesuffix("tick"))
    cell_id = f"{slice_id}/honest_ioc/{slippage_key}"
    built: list[TradeRecord] = []
    for row in rows:
        if not isinstance(row, Mapping):
            _fail("ledger row must be a mapping")
        if "date" not in row:
            _fail("ledger row is missing date")
        day = str(row["date"])
        if _seal_violation(market, day):
            _fail(f"MNQ row {day} is on or after {MNQ_SEAL_DATE}")
        models = row.get("models")
        if not isinstance(models, Mapping) or model_key not in models:
            _fail(f"ledger row {day} is missing {model_key}")
        cell = models[model_key]
        if not isinstance(cell, Mapping):
            _fail(f"ledger row {day} model cell must be a mapping")
        status = cell.get("status")
        if status not in {"RESOLVED", "UNRESOLVED", "OPEN", "NO_FILL"}:
            _fail(f"ledger row {day} has unknown status {status!r}")
        reason = cell.get("reason")
        if reason is None:
            reason = cell.get("exit_reason")
        fill_entry = cell.get("fill_entry")
        net = _finite(cell.get("net"), "net") if status == "RESOLVED" else None
        if "direction" not in row or "stop" not in row or "target" not in row:
            _fail(f"ledger row {day} is missing direction, stop, or target")
        record: TradeRecord = {
            "date": day,
            "cell_id": cell_id,
            "market": market,
            "model": "honest_ioc",
            "slippage_ticks": slippage_ticks,
            "direction": row["direction"],
            "status": status,
            "fill_entry": None if fill_entry is None else _finite(fill_entry, "fill_entry"),
            "stop": _finite(row["stop"], "stop"),
            "target": _finite(row["target"], "target"),
            "net": net,
            "harness_result": cell.get("result") if status == "RESOLVED" else None,
            "reason": None if reason is None else str(reason),
        }
        built.append(record)
    return built


def _validate(trades: Sequence[TradeRecord], *, cell_id: str, market: Market) -> None:
    if market not in {"NQ", "MNQ"}:
        _fail(f"unsupported market {market!r}")
    seen: set[str] = set()
    for record in trades:
        if record.get("cell_id") != cell_id:
            _fail("trade cell_id does not match the cell")
        if record.get("market") != market:
            _fail("trade market does not match the cell")
        if record.get("model") != "honest_ioc":
            _fail("decisive cell requires model honest_ioc")
        if record.get("slippage_ticks") != DECISIVE_SLIPPAGE_TICKS:
            _fail("decisive cell requires slippage_ticks 3.0")
        day = record.get("date")
        if not isinstance(day, str) or not day:
            _fail("trade date is required")
        if day in seen:
            _fail(f"duplicate date {day}")
        seen.add(day)
        if _seal_violation(market, day):
            _fail(f"MNQ row {day} is on or after {MNQ_SEAL_DATE}")
        direction = record.get("direction")
        if direction not in {"LONG", "SHORT"}:
            _fail(f"unsupported direction {direction!r}")
        status = record.get("status")
        if status not in {"RESOLVED", "UNRESOLVED", "OPEN", "NO_FILL"}:
            _fail(f"unknown status {status!r}")
        _finite(record.get("stop"), "stop")
        _finite(record.get("target"), "target")
        fill_entry = _optional_finite(record.get("fill_entry"), "fill_entry")
        net = _optional_finite(record.get("net"), "net")
        if status in {"RESOLVED", "UNRESOLVED", "OPEN"} and fill_entry is None:
            _fail(f"{status} row {day} requires fill_entry")
        if status == "RESOLVED" and net is None:
            _fail(f"RESOLVED row {day} requires net")
        if status != "RESOLVED" and net is not None:
            _fail(f"{status} row {day} must not carry net")


def _empty_cell(cell_id: str, market: Market, counts: dict[str, int]) -> dict[str, Any]:
    return {
        "cell_id": cell_id,
        "market": market,
        "model": "honest_ioc",
        "slippage_ticks": DECISIVE_SLIPPAGE_TICKS,
        "status": "INSUFFICIENT",
        "decided_by_check": 1,
        "counts": counts,
        "unresolved_fraction": 0.0,
        "net_total_usd": 0.0,
        "wins_net": 0,
        "losses_net": 0,
        "zero_net_count": 0,
        "win_rate_net": None,
        "avg_net_win_usd": None,
        "avg_net_loss_usd": None,
        "be_geom": None,
        "be_real": None,
        "be": None,
        "lcb": 0.0,
        "r_usd": None,
        "max_dd_usd": 0.0,
        "max_dd_r": None,
        "harness_label_counts": {"WIN": 0, "LOSS": 0, "BREAKEVEN": 0},
        "unsafe_account": False if market == "MNQ" else None,
        "worst_net_loss_usd": None,
        "evaluator_sha256": "",
        "constants": _constants(),
    }


def evaluate_cell(trades: Sequence[TradeRecord], *, cell_id: str, market: Market) -> dict[str, Any]:
    """Score one honest-IOC 3-tick cell. The first matching check sets the status."""
    _validate(trades, cell_id=cell_id, market=market)
    candidates = len(trades)
    fills = sum(1 for row in trades if row["status"] in {"RESOLVED", "UNRESOLVED", "OPEN"})
    resolved_rows = [row for row in trades if row["status"] == "RESOLVED"]
    unresolved = sum(1 for row in trades if row["status"] == "UNRESOLVED")
    open_count = sum(1 for row in trades if row["status"] == "OPEN")
    no_fill = sum(1 for row in trades if row["status"] == "NO_FILL")
    counts = {
        "candidates": candidates,
        "fills": fills,
        "resolved": len(resolved_rows),
        "unresolved": unresolved,
        "open": open_count,
        "no_fill": no_fill,
    }
    fraction = 0.0 if fills == 0 else (unresolved + open_count) / fills
    if not resolved_rows:
        cell = _empty_cell(cell_id, market, counts)
        cell["unresolved_fraction"] = _r10(fraction)
        return cell

    ordered = sorted(resolved_rows, key=lambda row: row["date"])
    nets = [_finite(row["net"], "net") for row in ordered]
    wins = [net for net in nets if net > 0.0]
    losses = [net for net in nets if net <= 0.0]
    zeros = [net for net in nets if net == 0.0]
    net_total = round(sum(nets), 2)
    k = len(wins)
    n = len(nets)
    avg_win = None if not wins else _r10(sum(wins) / len(wins))
    avg_loss = None if not losses else _r10(sum(abs(net) for net in losses) / len(losses))
    if not losses:
        be_real = 0.0
    elif not wins:
        be_real = 1.0
    else:
        be_real = _r10(avg_loss / (avg_win + avg_loss))

    win_sum = 0.0
    loss_abs = 0.0
    loss_geoms: list[float] = []
    for row, _net in zip(ordered, nets):
        win_geom, loss_geom = geometric_nets(
            str(row["direction"]),
            _finite(row["fill_entry"], "fill_entry"),
            _finite(row["stop"], "stop"),
            _finite(row["target"], "target"),
        )
        win_sum += win_geom
        loss_abs += abs(loss_geom)
        loss_geoms.append(abs(loss_geom))
    r_value = statistics.median(loss_geoms)
    if r_value <= 0.0:
        _fail("R must be positive")
    be_geom = 1.0 if win_sum <= 0.0 else _r10(loss_abs / (win_sum + loss_abs))
    be = max(be_geom, be_real)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for net in nets:
        equity += net
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_dd:
            max_dd = drawdown
    max_dd_r = max_dd / r_value
    lcb = clopper_pearson_lower(k, n)
    labels = {"WIN": 0, "LOSS": 0, "BREAKEVEN": 0}
    for row in ordered:
        label = row.get("harness_result")
        if label in labels:
            labels[str(label)] += 1
    worst = min(nets)
    unsafe = None
    if market == "MNQ":
        unsafe = max_dd > ACCOUNT_DD_FLOOR_USD or any(net < -ACCOUNT_SINGLE_LOSS_USD for net in nets)

    if n < MIN_RESOLVED:
        status: CellStatus = "INSUFFICIENT"
        check = 1
    elif net_total <= 0.0:
        status = "FAIL"
        check = 2
    elif fraction > UNRESOLVED_MAX_FRACTION:
        status = "WAIT_UNRESOLVED"
        check = 3
    elif max_dd_r > DD_CAP_R:
        status = "FAIL_DRAWDOWN"
        check = 4
    elif _r10(lcb) <= be:
        status = "WAIT_CI"
        check = 5
    else:
        status = "PASS"
        check = 6

    return {
        "cell_id": cell_id,
        "market": market,
        "model": "honest_ioc",
        "slippage_ticks": DECISIVE_SLIPPAGE_TICKS,
        "status": status,
        "decided_by_check": check,
        "counts": counts,
        "unresolved_fraction": _r10(fraction),
        "net_total_usd": net_total,
        "wins_net": k,
        "losses_net": len(losses),
        "zero_net_count": len(zeros),
        "win_rate_net": _r10(k / n),
        "avg_net_win_usd": avg_win,
        "avg_net_loss_usd": avg_loss,
        "be_geom": be_geom,
        "be_real": be_real,
        "be": _r10(be),
        "lcb": _r10(lcb),
        "r_usd": _usd(r_value),
        "max_dd_usd": _usd(max_dd),
        "max_dd_r": _r10(max_dd_r),
        "harness_label_counts": labels,
        "unsafe_account": unsafe,
        "worst_net_loss_usd": None if worst >= 0.0 else _usd(worst),
        "evaluator_sha256": "",
        "constants": _constants(),
    }


def _sha256_ok(value: object) -> bool:
    return isinstance(value, str) and len(value) == _SHA256_HEX and all(
        character in "0123456789abcdef" for character in value
    )


def _require_decisive_cell(cell: Mapping[str, Any], market: Market) -> None:
    if not isinstance(cell, Mapping):
        _fail("cell must be a mapping")
    if cell.get("market") != market:
        _fail(f"combine_cells expected market {market}")
    if cell.get("model") != "honest_ioc" or cell.get("slippage_ticks") != DECISIVE_SLIPPAGE_TICKS:
        _fail("combine_cells requires honest IOC at 3 ticks")
    if not _sha256_ok(cell.get("evaluator_sha256")):
        _fail("evaluator_sha256 must be 64 lowercase hex characters")


def combine_cells(nq: dict, mnq: dict) -> dict[str, Any]:
    """Conjunctive verdict. A sha mismatch refuses the combine."""
    _require_decisive_cell(nq, "NQ")
    _require_decisive_cell(mnq, "MNQ")
    nq_sha = nq["evaluator_sha256"]
    mnq_sha = mnq["evaluator_sha256"]
    if nq_sha != mnq_sha:
        _fail("evaluator_sha256 mismatch")
    statuses = [nq["status"], mnq["status"]]
    if "FAIL" in statuses or "FAIL_DRAWDOWN" in statuses:
        overall = "FAIL"
    elif statuses == ["PASS", "PASS"]:
        overall = "PASS"
    else:
        overall = "WAIT"
    if "FAIL" in statuses:
        verdict = "OVERFIT"
    elif "FAIL_DRAWDOWN" in statuses:
        verdict = "FAIL — risk profile"
    elif overall == "PASS":
        verdict = "PROMISING BUT UNPROVEN"
    else:
        verdict = "WAIT"
    return {
        "overall": overall,
        "verdict": verdict,
        "cells": statuses,
        "evaluator_sha256": nq_sha,
        "unsafe_account": mnq.get("unsafe_account"),
    }
