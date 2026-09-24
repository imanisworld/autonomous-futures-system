"""Round-4 vectorised engine: arming, touch, HOLD (H2/H15) and touch-fill (T2), plus the
time-matched random-zone null (PREREG_FROZEN.md §5-7)."""
import numpy as np
from common import TICK, MULT, COMM
from engine import first_hit, one_position

EPS = 1e-9
# status codes
NOLIFE, DEAD_PRE, NOARM, NOTOUCH, BROKEN, NOHOLD, HOLD = range(7)


def run_zones(mk, Z):
    b = mk.b
    nb = len(b.t)
    i, kind, top, bot, cc, life = Z["i"], Z["kind"], Z["top"], Z["bot"], Z["cc"], Z["life"]
    n = len(i)
    dem = kind == 1
    L1 = np.where(life >= i + 1, life + 1, i + 1)  # exclusive end; empty when no life
    has_life = life >= i + 1
    armed0 = np.where(dem, cc > top, cc < bot)
    s1 = np.minimum(i + 1, L1)
    up_c = first_hit(mk.maxC, s1, L1, top + EPS, False)   # first close > top
    dn_c = first_hit(mk.minC, s1, L1, bot - EPS, True)    # first close < bot
    armJ = np.where(dem, up_c, dn_c)
    deadJ = np.where(dem, dn_c, up_c)
    arm_ok = armed0 | ((armJ < L1) & (armJ < deadJ))
    a = np.where(armed0, i, armJ)
    dead_pre = ~armed0 & (deadJ < L1) & (deadJ < armJ)
    st = np.full(n, NOARM)
    st[dead_pre] = DEAD_PRE
    st[~has_life] = NOLIFE
    arm_ok &= has_life
    ts = np.where(arm_ok, np.minimum(a + 1, L1), L1)
    k_d = first_hit(mk.minL, ts, L1, top, True)
    k_s = first_hit(mk.maxH, ts, L1, bot, False)
    k = np.where(dem, k_d, k_s)
    touched = arm_ok & (k < L1)
    st[arm_ok & ~touched] = NOTOUCH
    kk = np.minimum(k, nb - 1)
    ck = b.c[kk]
    brk0 = np.where(dem, ck < bot, ck > top)
    hold0 = np.where(dem, ck > top, ck < bot)
    k1 = np.minimum(k + 1, nb - 1)
    k1ok = (k + 1) < L1
    c1 = b.c[k1]
    brk1 = k1ok & np.where(dem, c1 < bot, c1 > top)
    hold1 = k1ok & np.where(dem, c1 > top, c1 < bot)
    mid = ~brk0 & ~hold0
    broken = touched & (brk0 | (mid & brk1))
    holdm = touched & (hold0 | (mid & hold1))
    st[touched] = NOHOLD
    st[broken] = BROKEN
    st[holdm] = HOLD
    hbar = np.where(hold0, k, k + 1)
    e = hbar + 1
    ee = np.minimum(e, nb - 1)
    ent_ok = holdm & (e < L1) & b.entry_ok[ee]
    hb = np.minimum(hbar, nb - 1)
    entry = np.where(dem, b.o[ee] + TICK, b.o[ee] - TICK)
    stopL = np.where(dem, np.minimum(bot, np.minimum(b.l[kk], b.l[hb])) - TICK,
                     np.maximum(top, np.maximum(b.h[kk], b.h[hb])) + TICK)
    R = np.where(dem, entry - stopL, stopL - entry)
    skipR = ent_ok & ~(R > EPS)
    ent_ok &= R > EPS
    out = {"status": st, "touch": k, "hold_bar": hbar, "arm": a, "entry_bar": e, "ent_ok": ent_ok,
           "skipR": skipR, "R": R, "entry": entry, "stopL": stopL}
    for mult, lab in ((2.0, "H2"), (1.5, "H15")):
        out[lab] = _trade(mk, dem, ent_ok, e, entry, stopL, R, mult, stop_from=e, tgt_from=e)
    # ---- T2 touch fill
    fill = touched & b.entry_ok[kk] & np.where(dem, b.l[kk] <= top - TICK, b.h[kk] >= bot + TICK)
    tent = np.where(dem, top, bot)
    tstop = np.where(dem, bot - TICK, top + TICK)
    tR = np.abs(tent - tstop)
    out["T2"] = _trade(mk, dem, fill, k, tent, tstop, tR, 2.0, stop_from=k, tgt_from=k + 1)
    return out


def _trade(mk, dem, ok, e, entry, stopL, R, mult, stop_from, tgt_from):
    b = mk.b
    nb = len(b.t)
    ee = np.minimum(e, nb - 1)
    tgt = np.where(dem, entry + mult * R, entry - mult * R)
    E = b.sess_end[ee]
    E1 = np.where(ok, E + 1, 0)
    sf = np.where(ok, stop_from, 0)
    tf = np.where(ok, np.minimum(tgt_from, E1), 0)
    st_d = first_hit(mk.minL, sf, E1, stopL, True)
    st_s = first_hit(mk.maxH, sf, E1, stopL, False)
    stI = np.where(dem, st_d, st_s)
    tg_d = first_hit(mk.maxH, tf, E1, tgt + TICK, False)
    tg_s = first_hit(mk.minL, tf, E1, tgt - TICK, True)
    tgI = np.where(dem, tg_d, tg_s)
    stop_first = ok & (stI < E1) & (stI <= tgI)
    tgt_hit = ok & ~stop_first & (tgI < E1)
    Ec = np.maximum(E, 0)
    px = np.where(stop_first, np.where(dem, stopL - TICK, stopL + TICK), np.where(tgt_hit, tgt, b.c[Ec]))
    exitI = np.where(stop_first, stI, np.where(tgt_hit, tgI, Ec))
    pnl = np.where(dem, px - entry, entry - px) * MULT[b.inst] - COMM
    return {"fill": ok, "bar": np.where(ok, e, -1), "pnl": np.where(ok, pnl, np.nan), "exitI": exitI,
            "stopped": stop_first, "target": tgt_hit}


class TimeGroups:
    """Bars grouped by (segment, ET minute) for the time-matched null."""
    def __init__(self, b):
        key = b.seg_id * 10000 + b.et_min
        self.order = np.argsort(key, kind="stable")
        sk = key[self.order]
        uk, start, cnt = np.unique(sk, return_index=True, return_counts=True)
        self.uk, self.start, self.cnt = uk, start, cnt
        self.key = key

    def draw(self, idx, rng):
        g = np.searchsorted(self.uk, self.key[idx])
        pos = self.start[g] + (rng.random(len(idx)) * self.cnt[g]).astype(np.int64)
        return self.order[pos]


def random_zones(b, Z, tg, life, rng):
    r = tg.draw(Z["i"], rng)
    cr = b.c[r]
    return {"i": r, "kind": Z["kind"], "top": cr + (Z["top"] - Z["cc"]), "bot": cr + (Z["bot"] - Z["cc"]),
            "cc": cr, "life": life[r]}


def take(res, tc):
    fill = res["fill"]
    idx = np.flatnonzero(fill)
    taken = np.zeros(len(fill), bool)
    if len(idx):
        t = one_position(res["bar"][idx], tc[idx], res["exitI"][idx], None)
        taken[idx[t]] = True
    return taken
