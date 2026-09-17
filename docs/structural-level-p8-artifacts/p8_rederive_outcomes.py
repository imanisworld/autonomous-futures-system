#!/usr/bin/env python3
"""P8 — independent outcome re-derivation for the three R7-seeded rows.

Standalone: imports only json/sys. Does NOT import strategy.shadow_setups,
replay/, research/ or scripts/. The rules below are transcribed from the frozen
P2 spec + prereg §8.1/§9.8 and re-implemented here:

  * forward window = the candles in the SAME day file after B0 (candles[idx+1:])
  * resting entry: fills on the first forward bar with low <= entry <= high
  * from the fill bar onward, per bar: LONG target_hit = high >= target,
    stop_hit = low <= stop (SHORT mirrored)
  * both hit on one bar -> STOP (pessimistic; intrabar order unknowable)
  * target-only hit ON the fill bar -> ignored (flag), continue on later bars
  * no exit by end of file -> OPEN ; no fill -> NO_FILL
  * pnl_ticks = signed (exit - entry)/tick ; exit at the bracket price
  * net R = (pnl_ticks*tv - 2*tv - 1.48) / (stop_ticks*tv)   [pinned cost model]
"""
import json, sys

ECON = {"MNQ": (0.25, 0.5), "MES": (0.25, 1.25)}
COMMISSION_RT = 1.48
SLIP_TICKS_RT = 2.0

CORPUS = "<checkout>/data/replay_polygon_v2"
R5 = "<checkout>/logs/structural_level_r5_2026_09_17/structural_level_r5"

SEEDS = [
    "shadow_setups|MNQ|2026-02-04T14:30:00+00:00|strat_22_reversal_observed|SHORT|25333.25",
    "shadow_setups|MNQ|2025-06-16T00:15:00+00:00|impulse_first_pullback_observed|LONG|21919.75",
    "shadow_setups|MES|2026-04-10T00:45:00+00:00|ema_pullback_trend|LONG|6864.0",
]


def load_day(inst, day):
    with open(f"{CORPUS}/{inst}/{inst}_{day}.jsonl") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    # sanity: file sorted by timestamp, one instrument
    ts = [r["timestamp"] for r in rows]
    assert ts == sorted(ts), "day file not sorted"
    assert {r["instrument"] for r in rows} == {inst}
    return rows


def resolve(direction, entry, stop, target, fwd, tick):
    is_long = direction == "LONG"
    fill = None
    for i, b in enumerate(fwd):
        if b["low"] <= entry <= b["high"]:
            fill = i
            break
    if fill is None:
        return {"result": "NO_FILL", "entry_filled": False, "exit_reason": "NO_FILL",
                "exit_price": None, "pnl_ticks": None, "bars_to_fill": None, "bars_to_exit": None,
                "fill_bar_target_ambiguous_ignored": False}
    flag = False
    for j in range(fill, len(fwd)):
        h, l = fwd[j]["high"], fwd[j]["low"]
        t_hit = h >= target if is_long else l <= target
        s_hit = l <= stop if is_long else h >= stop
        if t_hit and s_hit:
            won = False
        elif t_hit:
            if j == fill:
                flag = True
                continue
            won = True
        elif s_hit:
            won = False
        else:
            continue
        ex = target if won else stop
        pnl = ((ex - entry) if is_long else (entry - ex)) / tick
        return {"result": "WIN" if won else "LOSS", "entry_filled": True,
                "exit_reason": "TARGET_HIT" if won else "STOP_HIT", "exit_price": round(ex, 4),
                "pnl_ticks": round(pnl, 2), "bars_to_fill": fill + 1, "bars_to_exit": j + 1,
                "fill_bar_target_ambiguous_ignored": flag}
    return {"result": "OPEN", "entry_filled": True, "exit_reason": "EOD_OPEN", "exit_price": None,
            "pnl_ticks": None, "bars_to_fill": fill + 1, "bars_to_exit": None,
            "fill_bar_target_ambiguous_ignored": flag}


def net_r(inst, entry, stop, pnl_ticks):
    tick, tv = ECON[inst]
    stop_ticks = abs(entry - stop) / tick
    net = pnl_ticks * tv - SLIP_TICKS_RT * tv - COMMISSION_RT
    return net / (stop_ticks * tv), stop_ticks, pnl_ticks / stop_ticks


def main():
    all_ok = True
    report = []
    for key in SEEDS:
        _, inst, bar_ts, strat, direction, entry_s = key.split("|")
        cand = sealed = None
        with open(f"{R5}/{inst}/candidates.jsonl") as fh:
            for l in fh:
                if f'"candidate_key": "{key}"' in l:
                    cand = json.loads(l); break
        with open(f"{R5}/{inst}/outcomes.sealed.jsonl") as fh:
            for l in fh:
                if f'"candidate_key": "{key}"' in l:
                    sealed = json.loads(l)["outcome"]; break
        assert cand and sealed, key
        day = bar_ts[:10]
        rows = load_day(inst, day)
        idx = [r["timestamp"] for r in rows].index(bar_ts)
        assert idx == cand["idx_in_day_file"], (idx, cand["idx_in_day_file"])
        assert cand["journal_day_file"] == day
        fwd = rows[idx + 1:]
        tick, tv = ECON[inst]
        mine = resolve(direction, cand["entry"], cand["stop"], cand["target"], fwd, tick)
        diffs = {k: (mine.get(k), sealed.get(k)) for k in sealed if mine.get(k) != sealed.get(k)}
        extra = set(mine) ^ set(sealed)
        ok = not diffs and not extra
        all_ok &= ok
        r = None
        if mine["pnl_ticks"] is not None:
            r = net_r(inst, cand["entry"], cand["stop"], mine["pnl_ticks"])
        # path trace for the report
        trace = []
        for j, b in enumerate(fwd[: (mine["bars_to_exit"] or 0) + 1]):
            trace.append({"i": j + 1, "ts": b["timestamp"], "h": b["high"], "l": b["low"]})
        report.append({"candidate_key": key, "day_file": day, "idx_in_day_file": idx, "n_forward_bars": len(fwd),
                       "bracket": {"entry": cand["entry"], "stop": cand["stop"], "target": cand["target"]},
                       "independent": mine, "sealed": sealed, "field_diffs": diffs, "extra_fields": sorted(extra),
                       "net_R": None if r is None else round(r[0], 4), "stop_ticks": None if r is None else r[1],
                       "gross_R": None if r is None else round(r[2], 4), "path_trace": trace, "AGREE": ok})
        print(f"{'AGREE' if ok else 'MISMATCH'}  {key}")
        print(f"   independent: {mine}")
        print(f"   sealed     : {sealed}")
        if r:
            print(f"   stop_ticks={r[1]:.2f} gross_R={r[2]:.4f} net_R={r[0]:.4f}")
        for t in trace:
            print(f"   fwd[{t['i']}] {t['ts']} H {t['h']} L {t['l']}")
    print("ALL ROWS AGREE:", all_ok)
    out = sys.argv[1] if len(sys.argv) > 1 else None
    if out:
        with open(out, "w") as fh:
            json.dump({"all_rows_agree": all_ok, "rows": report}, fh, indent=1)


if __name__ == "__main__":
    main()
