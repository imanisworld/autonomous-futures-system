"""Paper/shadow forward tracking for CONTROL_R3_v2, V-A, V-B, and V-C.

This module logs hypothetical positions. It does not place orders, talk to a
broker, or enable a schedule. Activation is a separate operator step: leave
``VARIANTS_START`` unset and the driver writes nothing.

The frozen files ``trend_shadow.py``, ``shadow_daily.py``,
``FROZEN_RULES_R3.md``, ``va_predict.py``, and ``vc_predict.py`` are imported
or hashed only. They are not edited here. V-C uses ``trend_shadow.cont()``
for the continuous series and ``trend_shadow.run()`` as the unchanged R3
reference. MNQ dated 2026-06-29 or later is sealed against backtest, fit, and
parameter search. Tests use synthetic bars only.

Evaluation spec (documented only; no early decision is implemented):

- Primary look: 165 closed CONTROL_R3_v2 trades (about 38–46 months).
- The primary look is UNDERPOWERED up front (MDE ≈ $1,944–2,139/month
  versus a mean of $1,306).
- Test, later, not here: sign-flip permutation of monthly D_m (variant basket
  minus control basket), statistic mean(D_m), 10,000 flips.
- All three variants V-A, V-B, and V-C use two-sided alpha = 0.0167
  (0.00833 per tail).
- Falsified = p < 0.0167 AND mean < 0.
- p < 0.0167 AND mean > 0 is at most PROMISING BUT UNPROVEN. Nothing is
  validated and nothing is adopted automatically.
- Otherwise INCONCLUSIVE.
- Tally stays at 433. Interim looks are KILL stops only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent

# post-hoc, loss-motivated proposals; QA AFS-0479; do not tune
# Alpha amendment REV5 / QA AFS-0484; do not tune.
# VARIANTS_START stays None until a separate activation sets the first
# session after merge. None means the driver does not track and no schedule runs.
VARIANTS_START = None
CAP_USD = 300.0
KILL_MAX_DD = 10_400.0
KILL_CLOSED_PNL = -3_000.0
KILL_MIN_TRADES = 20
KILL_WEEKS = 52
EVAL_PERMUTATIONS = 10_000
EVAL_PRIMARY_TRADES = 165
EVAL_ALPHA = 0.0167
EVAL_ALPHA_BY_ARM = {"V-A": 0.0167, "V-B": 0.0167, "V-C": 0.0167}

ARM_CONTROL = "CONTROL_R3_v2"
ARM_VA = "V-A"
ARM_VB = "V-B"
ARM_VC = "V-C"
ARMS = (ARM_CONTROL, ARM_VA, ARM_VB, ARM_VC)
VARIANT_ARMS = (ARM_VA, ARM_VB, ARM_VC)

FROZEN_HASHES = {
    _HERE / "trend_shadow.py": "92a666eaa2b57e148f345ae9e0b7e6dbf3c58bb4fab48852960e3c79c3d37973",
    _HERE / "shadow_daily.py": "b20d29f22a6701d1f9f27388ce431d4b6f36d62f68cd2059826aff15e265b737",
    _HERE / "FROZEN_RULES_R3.md": "5b00285c5d973c183a453092a3137b4a53229a20d2783d49ed5c1fb6b53332af",
    _HERE / "va_predict.py": "6afeac36129f887455c4b1bdf178754e7a3c79209693b82ffac10f6374264950",
    _HERE / "vc_predict.py": "a450da2bdb00e53f3ef40fb849192d48d204c7aff25af452ca212cae38df285f",
}

VARIANTS_LOG_NAME = "shadow_variants_log.jsonl"
SUMMARY_NAME = "SUMMARY_VARIANTS.md"
R3_LOG_NAME = "shadow_log.jsonl"
_ET = ZoneInfo("America/New_York")


def _load_trend_shadow():
    """Load frozen ``trend_shadow`` so ``cont`` and ``run`` stay byte-identical.

    The frozen module reads two discovery CSVs at import. When those files are
    absent (repository tests), an empty frame lets the callables load. When the
    CSVs exist, the frozen read is unchanged.
    """
    path = _HERE / "trend_shadow.py"
    spec = importlib.util.spec_from_file_location("trend_shadow", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load frozen trend_shadow from {path}")
    module = importlib.util.module_from_spec(spec)
    real_read_csv = pd.read_csv

    def _read_csv(csv_path, *args, **kwargs):
        target = Path(csv_path)
        if target.name in {"daily_r3.csv", "daily_shadow.csv"} and not target.exists():
            return pd.DataFrame(columns=["ts", "tk", "root", "o", "h", "l", "c", "v"])
        return real_read_csv(csv_path, *args, **kwargs)

    pd.read_csv = _read_csv
    sys.modules["trend_shadow"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("trend_shadow", None)
        raise
    finally:
        pd.read_csv = real_read_csv
    return module


trend_shadow = _load_trend_shadow()
cont = trend_shadow.cont
run = trend_shadow.run


def verify_frozen_hashes(expected=None):
    """Exit 2 with HASH_MISMATCH when any frozen sha256 differs."""
    mapping = FROZEN_HASHES if expected is None else expected
    bad = []
    for path, digest in mapping.items():
        file_path = Path(path)
        if not file_path.is_file():
            bad.append(str(file_path))
            continue
        got = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if got != digest:
            bad.append(str(file_path))
    if bad:
        print("HASH_MISMATCH", bad)
        sys.exit(2)
    return None


def cap_points(root):
    point_value, _tick = trend_shadow.SPEC[root]
    return CAP_USD / point_value


def prepare_frame(frame):
    out = frame.copy()
    if "roll" not in out.columns:
        out["roll"] = False
    if "tk" not in out.columns:
        out["tk"] = "TEST"
    if "adj" not in out.columns:
        out["adj"] = 0.0
    if "ro" not in out.columns:
        out["ro"] = out["o"].to_numpy()
    if "rc" not in out.columns:
        out["rc"] = out["c"].to_numpy()
    if "diff" not in out.columns:
        out["diff"] = 0.0
    return out


def stop_components(arm, side, ext, atr, entry_fill, point_value):
    """Return (stop, frozen trail, cap or giveback or None).

    V-B never loosens past cap_px. V-C never loosens past the frozen trail.
    Giveback is inactive until ext has moved through the entry fill, which
    cannot happen on the entry bar because ext is seeded with the open.
    """
    frozen = ext - side * 3.0 * atr
    if arm == ARM_VB:
        cap_px = entry_fill - side * (CAP_USD / point_value)
        if side > 0:
            return max(cap_px, frozen), frozen, cap_px
        return min(cap_px, frozen), frozen, cap_px
    if arm == ARM_VC:
        favourable = ext > entry_fill if side > 0 else ext < entry_fill
        if not favourable:
            return frozen, frozen, None
        giveback = entry_fill + 0.5 * (ext - entry_fill)
        if side > 0:
            return max(frozen, giveback), frozen, giveback
        return min(frozen, giveback), frozen, giveback
    return frozen, frozen, None


def confirmed_sign(sig, index, start):
    """Forward V-A sign. At start, c is s only when the last two bars agree.

    Otherwise c is 0 until two consecutive bars share a sign. A one-bar
    change keeps the previous c. ``start`` None treats the series as not yet
    confirmed (c stays 0 until two bars agree).
    """
    values = np.zeros(len(index), dtype=float)
    start_ts = None if start is None else pd.Timestamp(start)
    if start_ts is None:
        init_i = None
    else:
        prior = [i for i, label in enumerate(index) if pd.Timestamp(label) <= start_ts]
        init_i = prior[-1] if prior else None
    if init_i is None:
        prev_c = 0.0
        begin = 0
    else:
        if init_i >= 1 and _signs_agree(sig.iloc[init_i], sig.iloc[init_i - 1]):
            prev_c = float(sig.iloc[init_i])
        else:
            prev_c = 0.0
        values[init_i] = prev_c
        begin = init_i + 1
    for i in range(begin, len(index)):
        current = sig.iloc[i]
        previous = sig.iloc[i - 1] if i else np.nan
        if _signs_agree(current, previous):
            prev_c = float(current)
        values[i] = prev_c
    return pd.Series(values, index=index)


def _signs_agree(current, previous):
    if pd.isna(current) or pd.isna(previous):
        return False
    return float(current) == float(previous)


def _liquidation(side, px, entry_px, rolls, slippage, point_value):
    commission = trend_shadow.COM
    round_trip = commission * 2 + 2 * slippage * point_value
    return (side * (px - side * slippage - entry_px) * point_value - commission * 2) - rolls * round_trip


def run_arm(frame, root, arm, start=None):
    """One instrument, fresh start, no inherited position.

    Bar i acts at that bar's open using the active sign and ATR from bar i-1.
    When ``start`` is set, the first signal bar is the first bar after
    ``start``, and the first entry is the following open. The position begins
    flat. Pre-start bars warm the 100-bar sign and ATR20 only.
    """
    series = prepare_frame(frame)
    point_value, tick = trend_shadow.SPEC[root]
    slippage = 2 * tick
    true_range = np.maximum(
        series.h - series.l,
        np.maximum(abs(series.h - series.c.shift()), abs(series.l - series.c.shift())),
    )
    atr = true_range.rolling(20).mean().shift(1)
    raw_sign = np.sign(series.c - series.c.shift(100))
    active = confirmed_sign(raw_sign, series.index, start) if arm == ARM_VA else raw_sign
    index = list(series.index)
    start_ts = None if start is None else pd.Timestamp(start)
    pos = 0
    entry = None
    extreme = None
    stopped_sig = 0
    trades = []
    events = []
    bars = []
    stops = []

    def close(i, px, why):
        nonlocal pos
        pnl = _liquidation(pos, px, entry["px"], entry["rolls"], slippage, point_value)
        trade = dict(
            root=root,
            entry=str(entry["d"].date()),
            exit=str(index[i].date()),
            side=pos,
            net=round(pnl, 2),
            why=why,
            inherited=entry.get("inh", False),
            entry_raw=entry["raw"],
            entry_tk=entry["tk"],
            exit_raw=round(px - series.adj.iloc[i], 4),
            exit_tk=series.tk.iloc[i],
            exit_px=px,
            rolls=entry["rolls"],
        )
        trades.append(trade)
        events.append(dict(kind="exit", date=index[i], **trade))
        pos = 0

    for i in range(1, len(index)):
        label = index[i]
        row = series.iloc[i]
        sign = active.iloc[i - 1]
        atr_value = atr.iloc[i - 1]
        if np.isnan(sign) or np.isnan(atr_value):
            continue
        if start_ts is not None and pd.Timestamp(index[i - 1]) <= start_ts:
            continue
        if pos and sign == -pos:
            close(i, row.o, "flip")
        if not pos and sign != 0 and sign != stopped_sig:
            pos = int(sign)
            entry = dict(
                d=label,
                i=i,
                px=row.o + pos * slippage,
                rolls=0,
                raw=round(row.ro + pos * slippage, 4),
                tk=row.tk,
            )
            extreme = row.o
            stopped_sig = 0
            entry_event = dict(
                kind="entry",
                date=label,
                root=root,
                side=pos,
                inherited=False,
                entry_raw=entry["raw"],
                entry_tk=row.tk,
                entry_px=entry["px"],
            )
            if arm == ARM_VB:
                entry["cap_px"] = entry["px"] - pos * (CAP_USD / point_value)
                entry_event["cap_px"] = entry["cap_px"]
            events.append(entry_event)
        stopped_this_bar = False
        if pos:
            if bool(row.roll) and i != entry["i"]:
                entry["rolls"] += 1
                events.append(dict(kind="roll", date=label, root=root, to_tk=row.tk))
            stop, frozen, extra = stop_components(
                arm, pos, extreme, float(atr_value), entry["px"], point_value
            )
            stops.append(
                dict(date=label, stop=stop, frozen=frozen, extra=extra, side=pos, ext=extreme)
            )
            if (pos > 0 and row.l <= stop) or (pos < 0 and row.h >= stop):
                px = min(row.o, stop) if pos > 0 else max(row.o, stop)
                stopped_sig = pos
                close(i, px, "stop")
                stopped_this_bar = True
            else:
                extreme = max(extreme, row.c) if pos > 0 else min(extreme, row.c)
        if not stopped_this_bar and sign != stopped_sig and stopped_sig and sign != 0:
            stopped_sig = 0 if sign == -stopped_sig else stopped_sig
        mark = 0.0
        if pos:
            mark = _liquidation(pos, row.c, entry["px"], entry["rolls"], slippage, point_value)
        bars.append(
            dict(
                date=label,
                mark=mark,
                pos=pos,
                stopped_sig=int(stopped_sig),
                entry=None if not pos else str(entry["d"].date()),
            )
        )

    last_i = len(index) - 1
    last = series.iloc[last_i]
    signal_today = active.iloc[last_i]
    state = dict(
        root=root,
        last_bar=str(index[last_i].date()),
        tk=last.tk,
        close_raw=float(last.rc),
        signal_today=None if np.isnan(signal_today) else int(signal_today),
        pos=pos,
        stopped_sig=int(stopped_sig),
        pending_next_open=None,
    )
    if pos:
        state.update(
            entry=str(entry["d"].date()),
            entry_raw=entry["raw"],
            entry_tk=entry["tk"],
            inherited=False,
            mark_net=round(float(bars[-1]["mark"]) if bars else 0.0, 2),
        )
    sign_now = state["signal_today"]
    if pos and sign_now == -pos:
        state["pending_next_open"] = f"flip to {sign_now:+d}"
    elif not pos and sign_now not in (0, None) and sign_now != stopped_sig:
        state["pending_next_open"] = f"enter {sign_now:+d}"
    return dict(trades=trades, events=events, bars=bars, stops=stops, state=state)


def control_fresh_trades(frame, root):
    """Frozen ``run()`` trades. A fresh series has no inherited position."""
    prepared = prepare_frame(frame)
    trades, _events, _state = run(prepared, root)
    return trades


def build_frames():
    """Continuous series via frozen ``cont()``. Empty discovery data fails closed."""
    frames = {}
    for root in trend_shadow.SPEC:
        try:
            series = cont(root)
        except Exception as exc:
            print(f"NO_DATA {root} {exc}")
            sys.exit(1)
        if series is None or len(series) == 0:
            print(f"NO_DATA {root}")
            sys.exit(1)
        frames[root] = series
    return frames


def scan_kills(bars_by_root, trades_by_root, start):
    """First session a variant arm trips a risk stop. Control is not scanned."""
    dates = sorted(
        {
            pd.Timestamp(bar["date"])
            for rows in bars_by_root.values()
            for bar in rows
        }
    )
    latest = {root: 0.0 for root in bars_by_root}
    marks_on = {}
    for root, rows in bars_by_root.items():
        for bar in rows:
            marks_on.setdefault(pd.Timestamp(bar["date"]), []).append((root, bar["mark"]))
    closed_on = {}
    for rows in trades_by_root.values():
        for trade in rows:
            closed_on.setdefault(pd.Timestamp(trade["exit"]), []).append(trade["net"])
    cumulative = 0.0
    n_closed = 0
    peak = 0.0
    max_dd = 0.0
    start_ts = None if start is None else pd.Timestamp(start)
    horizon = None if start_ts is None else start_ts + pd.Timedelta(weeks=KILL_WEEKS)
    for label in dates:
        for net in closed_on.get(label, []):
            cumulative += net
            n_closed += 1
        for root, mark in marks_on.get(label, []):
            latest[root] = mark
        equity = cumulative + sum(latest.values())
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        reasons = []
        if max_dd > KILL_MAX_DD:
            reasons.append("max_dd")
        if n_closed >= KILL_MIN_TRADES and cumulative <= KILL_CLOSED_PNL:
            reasons.append("closed_pnl")
        if horizon is not None and label >= horizon and cumulative <= 0:
            reasons.append("weeks_52")
        if reasons:
            return label, "+".join(reasons)
    return None, None


def _truncate(result, kill_date):
    limit = pd.Timestamp(kill_date)

    def kept(value):
        return pd.Timestamp(value) <= limit

    trades = [
        trade
        for trade in result["trades"]
        if kept(trade["exit"]) and kept(trade["entry"])
    ]
    events = [event for event in result["events"] if kept(event["date"])]
    bars = [bar for bar in result["bars"] if kept(bar["date"])]
    stops = [row for row in result["stops"] if kept(row["date"])]
    state = dict(result["state"])
    if bars:
        last = bars[-1]
        state["pos"] = last["pos"]
        state["last_bar"] = str(pd.Timestamp(last["date"]).date())
        state["mark_net"] = round(float(last["mark"]), 2) if last["pos"] else None
        if not last["pos"]:
            state["pending_next_open"] = None
    return dict(trades=trades, events=events, bars=bars, stops=stops, state=state, killed=True)


def run_campaign(frames, start, arms=ARMS):
    """Run every arm from a shared fresh start. Kill stops do not touch control."""
    prepared = {root: prepare_frame(frame) for root, frame in frames.items()}
    by_arm = {}
    for arm in arms:
        by_arm[arm] = {
            root: run_arm(frame, root, arm, start=start) for root, frame in prepared.items()
        }
    kills = {}
    if start is not None:
        for arm in arms:
            if arm == ARM_CONTROL:
                continue
            kill_date, reason = scan_kills(
                {root: result["bars"] for root, result in by_arm[arm].items()},
                {root: result["trades"] for root, result in by_arm[arm].items()},
                start,
            )
            if kill_date is None:
                continue
            kills[arm] = {"date": kill_date, "reason": reason}
            for root in by_arm[arm]:
                by_arm[arm][root] = _truncate(by_arm[arm][root], kill_date)
    return {"arms": by_arm, "kills": kills}


def month_end_marks(bars):
    last = {}
    for bar in bars:
        period = pd.Period(pd.Timestamp(bar["date"]), freq="M")
        last[period] = float(bar["mark"])
    return last


def closed_by_month(trades):
    totals = {}
    for trade in trades:
        period = pd.Period(pd.Timestamp(trade["exit"]), freq="M")
        totals[period] = totals.get(period, 0.0) + float(trade["net"])
    return totals


def monthly_instrument_pnl(start, month_end_marks_map, closed_map):
    """Instrument calendar-month P&L.

    Monthly P&L is closed P&L plus the change in the month-end mark. The mark
    is the liquidation value at the last bar labelled in the month, so the
    round-trip cost sits in the entry month and each roll cost sits in the
    roll month. The first partial month is excluded; its closing mark is the
    baseline for the first full month. Months in the span with zero P&L stay.
    """
    start_ts = pd.Timestamp(start)
    start_month = pd.Period(start_ts, freq="M")
    marks = {pd.Period(key, freq="M"): float(value) for key, value in month_end_marks_map.items()}
    closed = {pd.Period(key, freq="M"): float(value) for key, value in closed_map.items()}
    if not marks and not closed:
        return {}
    last = max(set(marks) | set(closed))
    partial = start_ts.day != 1
    previous = float(marks.get(start_month, 0.0)) if partial else 0.0
    month = start_month + 1 if partial else start_month
    if month > last:
        return {}
    out = {}
    while month <= last:
        end_mark = float(marks[month]) if month in marks else previous
        out[month] = float(closed.get(month, 0.0)) + (end_mark - previous)
        previous = end_mark
        month += 1
    return out


def attribution_from_arm(arm_results, start):
    per_root = {}
    for root, result in arm_results.items():
        per_root[root] = monthly_instrument_pnl(
            start, month_end_marks(result["bars"]), closed_by_month(result["trades"])
        )
    months = set()
    for series in per_root.values():
        months |= set(series)
    basket = {
        month: sum(series.get(month, 0.0) for series in per_root.values())
        for month in sorted(months, key=lambda period: (period.year, period.month))
    }
    return {"instruments": per_root, "basket": basket}


def monthly_difference(variant_basket, control_basket):
    months = sorted(
        set(variant_basket) | set(control_basket),
        key=lambda period: (period.year, period.month),
    )
    return {
        month: float(variant_basket.get(month, 0.0)) - float(control_basket.get(month, 0.0))
        for month in months
    }


def _session(label):
    return f"session opening {pd.Timestamp(label).date()} 18:00 ET"


def _date_text(label):
    return str(pd.Timestamp(label).date())


def records_for_instrument(arm, root, result):
    records = []
    for event in result["events"]:
        label = _date_text(event["date"])
        kind = event["kind"]
        if kind == "entry":
            records.append(
                dict(
                    key=f"{arm}|entry|{root}|{label}",
                    rule=arm,
                    type="entry",
                    instrument=root,
                    session_label=label,
                    session=_session(label),
                    side=event["side"],
                    inherited=event["inherited"],
                    fill_raw=event["entry_raw"],
                    contract=event["entry_tk"],
                )
            )
        elif kind == "exit":
            records.append(
                dict(
                    key=f"{arm}|exit|{root}|{label}",
                    rule=arm,
                    type="exit",
                    instrument=root,
                    session_label=label,
                    session=_session(label),
                    side=event["side"],
                    why=event["why"],
                    entry_label=event["entry"],
                    fill_raw=event["exit_raw"],
                    contract=event["exit_tk"],
                    net_after_costs=event["net"],
                    rolls=event["rolls"],
                )
            )
        elif kind == "roll":
            records.append(
                dict(
                    key=f"{arm}|roll|{root}|{label}",
                    rule=arm,
                    type="roll",
                    instrument=root,
                    session_label=label,
                    to_contract=event["to_tk"],
                )
            )
    state = result["state"]
    last = state["last_bar"]
    records.append(
        dict(
            key=f"{arm}|signal|{root}|{last}",
            rule=arm,
            type="signal",
            instrument=root,
            bar_label=last,
            bar=f"session opened {last} 18:00 ET (completed)",
            contract=state["tk"],
            close_raw=state["close_raw"],
            signal=state["signal_today"],
            rule_position=state["pos"],
            stopped_flat_until_flip=state["stopped_sig"] or None,
            pending_next_open=state["pending_next_open"],
        )
    )
    if state["pos"]:
        records.append(
            dict(
                key=f"{arm}|mark|{root}|{last}",
                rule=arm,
                type="mark",
                instrument=root,
                bar_label=last,
                side=state["pos"],
                entry_raw=state.get("entry_raw"),
                close_raw=state["close_raw"],
                mark_net_after_costs=state.get("mark_net"),
            )
        )
    return records


def kill_record(arm, kill):
    label = _date_text(kill["date"])
    return dict(
        key=f"{arm}|kill|ALL|{label}",
        rule=arm,
        type="kill",
        instrument="ALL",
        session_label=label,
        reason=kill["reason"],
        note="risk stop, not edge evidence",
    )


def log_records(campaign_result):
    records = []
    for arm, by_root in campaign_result["arms"].items():
        for root, result in by_root.items():
            records.extend(records_for_instrument(arm, root, result))
        if arm in campaign_result["kills"]:
            records.append(kill_record(arm, campaign_result["kills"][arm]))
    return records


def append_log(path, records):
    """Append new records. Duplicate keys are skipped. Never writes the R3 log."""
    target = Path(path)
    if target.name == R3_LOG_NAME:
        raise RuntimeError("refusing to write shadow_log.jsonl")
    seen = set()
    if target.exists():
        for line in target.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["key"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    written = []
    with target.open("a") as handle:
        for record in records:
            if record["key"] in seen:
                continue
            handle.write(json.dumps(record, default=str) + "\n")
            seen.add(record["key"])
            written.append(record)
    return written


def render_summary(campaign_result, updated):
    lines = [
        "# Variant shadow summary (forward evidence only, nothing here is proven)\n",
        f"Updated {updated} ET. Paper/shadow only. No orders.\n",
        "Costs: $1.48/side + 2 ticks/side. Control is CONTROL_R3_v2, a fresh start with no inherited position.\n",
        "\n| Arm | Instrument | Closed trades | Net closed (after costs) | Open position | Open mark (after costs) |\n",
        "|---|---|---|---|---|---|\n",
    ]
    for arm, by_root in campaign_result["arms"].items():
        for root, result in by_root.items():
            closed = result["trades"]
            net = sum(trade["net"] for trade in closed)
            state = result["state"]
            if state["pos"]:
                side = "long" if state["pos"] > 0 else "short"
                opened = f"{side} since {state.get('entry')}"
                mark = f"${state.get('mark_net', 0.0):,.2f}"
            elif state.get("pending_next_open"):
                opened = f"pending: {state['pending_next_open']}"
                mark = "-"
            else:
                opened = "flat"
                mark = "-"
            lines.append(
                f"| {arm} | {root} | {len(closed)} | ${net:,.2f} | {opened} | {mark} |\n"
            )
    for arm, kill in campaign_result["kills"].items():
        label = _date_text(kill["date"])
        lines.append(
            f"\n{arm} killed {label} ({kill['reason']}). Risk stop only; not edge evidence. Control keeps tracking.\n"
        )
    return "".join(lines)


def write_summary(path, campaign_result, updated=None):
    stamp = updated or datetime.now(_ET).isoformat(timespec="seconds")
    Path(path).write_text(render_summary(campaign_result, stamp))


def main(argv=None):
    """Verify frozen hashes, then track only if activation has set a start."""
    del argv
    verify_frozen_hashes()
    if VARIANTS_START is None:
        print("NOT_ACTIVATED")
        return 0
    result = run_campaign(build_frames(), VARIANTS_START)
    here = Path.cwd()
    append_log(here / VARIANTS_LOG_NAME, log_records(result))
    write_summary(here / SUMMARY_NAME, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
