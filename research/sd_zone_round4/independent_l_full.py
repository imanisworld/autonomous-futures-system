"""Independent re-implementation of Round 4 LOOSE FULL / LOOSE EDGE (H2, H15) from the
PREREG_FROZEN.md text only (plain loops; none of the archived engine/zones code is imported).
Usage: independent_l_full.py <cache_dir> <ledger_dir>
Compares its taken-trade list (creation bar, entry bar, pnl) with the archived ledgers."""
import sys
import numpy as np
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
TICK, COMM = 0.25, 1.48
MULT = {"MNQ": 2.0, "MES": 5.0}
ROLLS = ["2024-09-12", "2024-12-12", "2025-03-13", "2025-06-12", "2025-09-11",
         "2025-12-11", "2026-03-12", "2026-06-11"]
CACHE, LEDGERS = sys.argv[1], sys.argv[2]


def bars(inst):
    z = np.load(f"{CACHE}/{inst}_5m.npz")
    t, o, h, l, c = (z[k] for k in "tohlc")
    n = len(t)
    seams = [int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()) for d in ROLLS]
    seg = np.searchsorted(np.array(seams), t, side="right")  # segment id = seams passed
    dts = [datetime.fromtimestamp(int(x), ET) for x in t]
    etmin = np.array([d.hour * 60 + d.minute for d in dts])
    etdate = [d.date() for d in dts]
    rth = (etmin >= 570) & (etmin < 960)
    entry_ok = (etmin >= 570) & (etmin <= 950)
    # last RTH bar per (segment, ET date)
    last_rth = {}
    for i in range(n):
        if rth[i]:
            last_rth[(seg[i], etdate[i])] = i
    return dict(t=t, o=o, h=h, l=l, c=c, n=n, seg=seg, etmin=etmin, etdate=etdate, rth=rth,
                entry_ok=entry_ok, last_rth=last_rth)


def atr20(b):
    """Wilder RMA(20) of 5m true range, restarted at each segment (causal)."""
    out = np.full(b["n"], np.nan)
    for s in np.unique(b["seg"]):
        idx = np.flatnonzero(b["seg"] == s)
        h, l, c = b["h"][idx], b["l"][idx], b["c"][idx]
        tr = np.empty(len(idx))
        tr[0] = h[0] - l[0]
        for j in range(1, len(idx)):
            tr[j] = max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1]))
        if len(idx) < 20:
            continue
        a = tr[:20].mean()
        out[idx[19]] = a
        for j in range(20, len(idx)):
            a = a / 20 * 19 + tr[j] / 20
            out[idx[j]] = a
    return out


def lifetime_end(b, i):
    """15:55 ET bar of the first RTH session with any bar after i (same segment)."""
    for j in range(i + 1, b["n"]):
        if b["seg"][j] != b["seg"][i]:
            return -1
        if b["rth"][j]:
            return b["last_rth"][(b["seg"][j], b["etdate"][j])]
    return -1


def zones(b, atr, edge):
    o, h, l, c, seg = b["o"], b["h"], b["l"], b["c"], b["seg"]
    out = []
    for i in range(2, b["n"]):
        if not (seg[i] == seg[i - 1] == seg[i - 2]):
            continue
        A = i - 1
        th = atr[i - 2]
        if not np.isfinite(th) or th <= 0:
            continue
        if abs(c[i] - o[i]) < 1.5 * th:  # LOOSE: only the reversal candle must be big
            continue
        if c[A] < o[A] and c[i] > o[i] and l[A] < l[i - 2] and h[A] <= h[i - 2] \
                and h[i] > h[A] and l[i] >= l[A] and c[i] > h[A]:
            d = 1
            top, bot = (min(o[i], c[i]), min(l[A], l[i])) if edge else (h[i], l[i])
        elif c[A] > o[A] and c[i] < o[i] and h[A] > h[i - 2] and l[A] >= l[i - 2] \
                and l[i] < l[A] and h[i] <= h[A] and c[i] < l[A]:
            d = -1
            top, bot = (max(h[A], h[i]), max(o[i], c[i])) if edge else (h[i], l[i])
        else:
            continue
        if top - bot < TICK - 1e-9:
            continue
        out.append((i, d, top, bot))
    return out


def trades(b, zs, mult_r, inst):
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    cands = []
    for (i, d, top, bot) in zs:
        life = lifetime_end(b, i)
        if life < i + 1:
            continue
        # 1. arming (leave first)
        outside = (c[i] > top) if d == 1 else (c[i] < bot)
        arm = i if outside else None
        j = i + 1
        while arm is None and j <= life:
            if (c[j] > top) if d == 1 else (c[j] < bot):
                arm = j
            elif (c[j] < bot) if d == 1 else (c[j] > top):
                break
            j += 1
        if arm is None:
            continue
        # 2. touch
        k = next((x for x in range(arm + 1, life + 1) if ((l[x] <= top) if d == 1 else (h[x] >= bot))), None)
        if k is None:
            continue

        def closes_through(x):
            return (c[x] < bot) if d == 1 else (c[x] > top)

        def closes_out(x):
            return (c[x] > top) if d == 1 else (c[x] < bot)
        # 3. hold
        if closes_through(k):
            continue
        if closes_out(k):
            hb = k
        elif k + 1 <= life and not closes_through(k + 1) and closes_out(k + 1):
            hb = k + 1
        else:
            continue
        e = hb + 1
        if e > life or not b["entry_ok"][e]:
            continue
        entry = o[e] + TICK * d
        if d == 1:
            stop = min(bot, l[k], l[hb]) - TICK
            R = entry - stop
        else:
            stop = max(top, h[k], h[hb]) + TICK
            R = stop - entry
        if R <= 1e-9:
            continue
        tgt = entry + d * mult_r * R
        E = b["last_rth"][(b["seg"][e], b["etdate"][e])]
        px, xi = c[E], E
        for x in range(e, E + 1):
            hit_stop = (l[x] <= stop) if d == 1 else (h[x] >= stop)
            hit_tgt = (h[x] >= tgt + TICK) if d == 1 else (l[x] <= tgt - TICK)
            if hit_stop:
                px, xi = stop - d * TICK, x
                break
            if hit_tgt:
                px, xi = tgt, x
                break
        pnl = d * (px - entry) * MULT[inst] - COMM
        cands.append((e, int(b["t"][i]), i, xi, pnl))
    cands.sort()
    taken, last_exit = [], -1
    for (e, _, i, xi, pnl) in cands:
        if e > last_exit:
            taken.append((i, e, round(pnl, 6)))
            last_exit = xi
    return taken


for inst in ("MNQ", "MES"):
    b = bars(inst)
    atr = atr20(b)
    for name, edge in (("L_FULL", False), ("L_EDGE", True)):
        zs = zones(b, atr, edge)
        L = np.load(f"{LEDGERS}/ledger_{inst}_{name}.npz")
        for meas, m in (("H2", 2.0), ("H15", 1.5)):
            mine = trades(b, zs, m, inst)
            tk = L[f"{meas}_taken"]
            theirs = sorted(zip(L["i"][tk].tolist(), L[f"{meas}_bar"][tk].tolist(),
                                np.round(L[f"{meas}_pnl"][tk], 6).tolist()))
            same = sorted(mine) == theirs
            print(f"{inst} {name} {meas}: independent zones={len(zs)} archived zones={len(L['i'])} "
                  f"trades mine={len(mine)} archived={len(theirs)} net mine={sum(p for *_, p in mine):.2f} "
                  f"archived={sum(p for *_, p in theirs):.2f} IDENTICAL={same}")
