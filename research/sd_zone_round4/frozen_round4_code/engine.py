"""Vectorised first-touch / reaction / trade engine on 5m bars (PREREG §3-4)."""
import numpy as np
from common import TICK, MULT, COMM, aggregate, true_range, rolling_median
# round-3: round-1 zone detectors not used (build_zones below is dead code here)

HZ = 288


class Sparse:
    def __init__(self, x, op):
        self.op = op
        self.levels = [x.astype(np.float64)]
        k = 1
        while (1 << k) <= len(x):
            p = self.levels[-1]
            half = 1 << (k - 1)
            nxt = op(p[:-half], p[half:]) if len(p) > half else p[:0]
            self.levels.append(nxt)
            k += 1

    def query(self, a, b):
        """op over [a, b] inclusive (vectorised; requires a <= b)."""
        ln = b - a + 1
        k = np.floor(np.log2(np.maximum(ln, 1))).astype(np.int64)
        out = np.empty(len(a))
        for kk in np.unique(k):
            m = k == kk
            lv = self.levels[kk]
            out[m] = self.op(lv[a[m]], lv[b[m] - (1 << kk) + 1])
        return out


def first_hit(sp, s, e, thr, le):
    """First index in [s, e) where x <= thr (le=True, sp=min-table) or x >= thr
    (le=False, sp=max-table). Returns e where none."""
    pos = s.copy()
    for k in range(len(sp.levels) - 1, -1, -1):
        step = 1 << k
        lv = sp.levels[k]
        can = pos + step <= e
        idx = np.where(can, pos, 0)
        idx = np.minimum(idx, len(lv) - 1)
        vals = lv[idx]
        clear = (vals > thr) if le else (vals < thr)
        adv = can & clear
        pos = np.where(adv, pos + step, pos)
    lv0 = sp.levels[0]
    ok = pos < e
    v = lv0[np.minimum(pos, len(lv0) - 1)]
    hit = ok & ((v <= thr) if le else (v >= thr))
    return np.where(hit, pos, e)


class Market:
    def __init__(self, b):
        self.b = b
        self.minL = Sparse(b.l, np.minimum)
        self.maxH = Sparse(b.h, np.maximum)
        self.minC = Sparse(b.c, np.minimum)
        self.maxC = Sparse(b.c, np.maximum)


def build_zones(b, name):
    """Real zones for one definition: arrays kind, top, bot, s0 (first 5m idx), segend,
    tc, dist, mtr, conf_close. Also count of zones dropped by the side rule."""
    tf = DEF_TF[name]
    rows = []
    dropped_side = dropped_width = 0
    for seg in b.seg_bounds:
        a = aggregate(b, seg, tf)
        mtr = rolling_median(true_range(a), 120, 30)
        for (i, kind, top, bot) in DETECTORS[name](a):
            if top - bot < TICK - 1e-9:
                dropped_width += 1
                continue
            cc = a["c"][i]
            if (kind == 1 and not cc > top) or (kind == -1 and not cc < bot):
                dropped_side += 1
                continue
            tc = int(a["tc"][i])
            s0 = int(np.searchsorted(b.t, tc))
            if s0 >= seg[1]:
                continue
            dist = (cc - top) if kind == 1 else (bot - cc)
            rows.append((kind, top, bot, s0, seg[1], tc, dist, mtr[i], cc))
    z = np.array(rows, dtype=np.float64).reshape(-1, 9)
    Z = {"kind": z[:, 0].astype(int), "top": z[:, 1], "bot": z[:, 2], "s0": z[:, 3].astype(np.int64),
         "segend": z[:, 4].astype(np.int64), "tc": z[:, 5].astype(np.int64), "dist": z[:, 6],
         "mtr": z[:, 7], "cc": z[:, 8]}
    return Z, dropped_side, dropped_width


def random_zones(b, Z, rng):
    """Null: same kind, width, distance; random 5m bar in the same segment."""
    n = len(Z["kind"])
    segstart = np.array([b.seg_bounds[b.seg_id[s]][0] for s in Z["s0"]]) if n else np.zeros(0, int)
    segend = Z["segend"]
    r = segstart + (rng.random(n) * (segend - segstart - 1)).astype(np.int64)
    cc = b.c[r]
    W = Z["top"] - Z["bot"]
    top = np.where(Z["kind"] == 1, cc - Z["dist"], cc + Z["dist"] + W)
    bot = top - W
    return {"kind": Z["kind"], "top": top, "bot": bot, "s0": r + 1, "segend": segend,
            "tc": b.t[r] + 300, "dist": Z["dist"], "mtr": Z["mtr"], "cc": cc}


def measure(mk, Z):
    """Measure A + B per zone. Returns dict of per-zone arrays."""
    b = mk.b
    kind, top, bot, s0, se = Z["kind"], Z["top"], Z["bot"], Z["s0"], Z["segend"]
    n = len(kind)
    W = top - bot
    dem = kind == 1
    s0 = np.minimum(s0, se)
    # first touch
    t_d = first_hit(mk.minL, s0, se, top, True)
    t_s = first_hit(mk.maxH, s0, se, bot, False)
    touch = np.where(dem, t_d, t_s)
    touched = touch < se
    tt = np.minimum(touch, len(b.t) - 1)
    hz = np.minimum(touch + 1 + HZ, se)
    # break: close beyond far edge, from the touch bar
    br_d = first_hit(mk.minC, touch, hz, bot - 1e-9, True)
    br_s = first_hit(mk.maxC, touch, hz, top + 1e-9, False)
    brk = np.where(dem, br_d, br_s)
    res = {}
    for lab, dist in (("W", W), ("MTR", Z["mtr"])):
        dist = np.where(np.isfinite(dist), dist, np.inf)
        sc_d = first_hit(mk.maxH, touch + 1, hz, top + dist, False)
        sc_s = first_hit(mk.minL, touch + 1, hz, bot - dist, True)
        sc = np.where(dem, sc_d, sc_s)
        res["succ_" + lab] = touched & (sc < hz) & (sc <= brk)
    # MFE / MAE (widths), window touch+1 .. end, end = brk if brk<hz else hz-1
    end = np.where(brk < hz, brk, hz - 1)
    ok = touched & (end >= touch + 1)
    a1 = np.where(ok, touch + 1, 0)
    e1 = np.where(ok, end, 0)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    if ok.any():
        mxh = mk.maxH.query(a1[ok], e1[ok]); mnl = mk.minL.query(a1[ok], e1[ok])
        mnl0 = np.minimum(mnl, b.l[tt[ok]]); mxh0 = np.maximum(mxh, b.h[tt[ok]])
        d = dem[ok]
        mfe[ok] = np.where(d, mxh - top[ok], bot[ok] - mnl) / W[ok]
        mae[ok] = np.where(d, top[ok] - mnl0, mxh0 - bot[ok]) / W[ok]
    res.update(touched=touched, touch=touch, mfe=mfe, mae=mae)
    # ---- Measure B
    ent_ok = touched & b.entry_ok[tt]
    fill = ent_ok & np.where(dem, b.l[tt] <= top - TICK, b.h[tt] >= bot + TICK)
    entry = np.where(dem, top, bot)
    stopL = np.where(dem, bot - TICK, top + TICK)
    R = np.abs(entry - stopL)
    tgt = np.where(dem, entry + 2 * R, entry - 2 * R)
    E = b.sess_end[tt]
    E1 = np.where(fill, E + 1, touch + 1)
    tf_ = np.where(fill, touch, 0)
    st_d = first_hit(mk.minL, tf_, E1, stopL, True)
    st_s = first_hit(mk.maxH, tf_, E1, stopL, False)
    stI = np.where(dem, st_d, st_s)
    tg_d = first_hit(mk.maxH, tf_ + 1, E1, tgt + TICK, False)
    tg_s = first_hit(mk.minL, tf_ + 1, E1, tgt - TICK, True)
    tgI = np.where(dem, tg_d, tg_s)
    stopped = stI < E1
    stop_first = stopped & (stI <= tgI)
    tgt_hit = ~stop_first & (tgI < E1)
    Ec = np.maximum(E, 0)
    exit_px = np.where(stop_first, np.where(dem, stopL - TICK, stopL + TICK),
                       np.where(tgt_hit, tgt, b.c[Ec]))
    exitI = np.where(stop_first, stI, np.where(tgt_hit, tgI, Ec))
    pts = np.where(dem, exit_px - entry, entry - exit_px)
    pnl = pts * MULT[b.inst] - COMM
    res.update(fill=fill, pnl=np.where(fill, pnl, np.nan), exitI=exitI, E=E,
               preopen=Z["tc"] < b.open930[tt], rth_touch=touched & b.rth[tt])
    return res


def one_position(fill_idx, tc, exitI, pnl):
    """Greedy one-position filter. Returns boolean mask of taken trades."""
    order = np.lexsort((tc, fill_idx))
    taken = np.zeros(len(fill_idx), dtype=bool)
    last_exit = -1
    for k in order:
        if fill_idx[k] > last_exit:
            taken[k] = True
            last_exit = exitI[k]
    return taken
