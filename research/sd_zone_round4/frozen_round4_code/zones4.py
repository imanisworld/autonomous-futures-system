"""Round-4 detector (PREREG_FROZEN.md §1-4): reversal candle B creates the zone.
STRICT/LOOSE pattern (round-3 R1 geometry + range-engulfing close), FULL/EDGE zone, ALL/NY context."""
import numpy as np
from common import TICK, true_range, rma

DEFS = {
    "S_FULL": ("STRICT", "FULL", "ALL"),
    "S_EDGE": ("STRICT", "EDGE", "ALL"),
    "L_FULL": ("LOOSE", "FULL", "ALL"),
    "L_EDGE": ("LOOSE", "EDGE", "ALL"),
    "L_FULL_NY": ("LOOSE", "FULL", "NY"),
    "L_EDGE_NY": ("LOOSE", "EDGE", "NY"),
}


def life_end(b):
    """life[j] = last bar index (inclusive) a zone created at bar j may live: the 15:55 ET bar
    (sess_end) of the first RTH bar at index >= j+1 in the same segment; -1 if none."""
    n = len(b.t)
    nxt = np.full(n + 1, -1, dtype=np.int64)
    for (s, e) in b.seg_bounds:
        cur = -1
        for j in range(e - 1, s - 1, -1):
            if b.rth[j]:
                cur = j
            nxt[j] = cur
    life = np.full(n, -1, dtype=np.int64)
    for (s, e) in b.seg_bounds:
        for j in range(s, e - 1):
            r = nxt[j + 1]
            if r >= 0 and r < e:
                life[j] = b.sess_end[r]
    return life


def detect(b, name, life=None):
    pat, zk, ctx = DEFS[name]
    if life is None:
        life = life_end(b)
    rows = []
    n_width = 0
    for (s, e) in b.seg_bounds:
        o, h, l, c = b.o[s:e], b.h[s:e], b.l[s:e], b.c[s:e]
        n = e - s
        if n < 25:
            continue
        atr = rma(true_range({"o": o, "h": h, "l": l, "c": c}), 20)
        body = np.abs(c - o)
        for i in range(2, n):
            A, B = i - 1, i
            if c[A] > o[A] and c[B] < o[B]:
                kind = -1
            elif c[A] < o[A] and c[B] > o[B]:
                kind = 1
            else:
                continue
            if kind == -1:
                if not (h[A] > h[i - 2] and l[A] >= l[i - 2]): continue
                if not (l[B] < l[A] and h[B] <= h[A]): continue
                if not c[B] < l[A]: continue
            else:
                if not (l[A] < l[i - 2] and h[A] <= h[i - 2]): continue
                if not (h[B] > h[A] and l[B] >= l[A]): continue
                if not c[B] > h[A]: continue
            th = atr[i - 2]
            if not np.isfinite(th) or th <= 0: continue
            if body[B] < 1.5 * th: continue
            if pat == "STRICT" and body[A] < 1.5 * th: continue
            if ctx == "NY":
                em = b.et_min[s + i]
                if not (570 <= em <= 655): continue
            if zk == "FULL":
                top, bot = h[B], l[B]
            else:
                if kind == 1:
                    bot, top = min(l[A], l[B]), min(o[B], c[B])
                else:
                    bot, top = max(o[B], c[B]), max(h[A], h[B])
            if top - bot < TICK - 1e-9:
                n_width += 1
                continue
            rows.append((s + i, kind, top, bot, c[B], life[s + i]))
    z = np.array(rows, dtype=np.float64).reshape(-1, 6)
    Z = {"i": z[:, 0].astype(np.int64), "kind": z[:, 1].astype(np.int64), "top": z[:, 2], "bot": z[:, 3],
         "cc": z[:, 4], "life": z[:, 5].astype(np.int64)}
    return Z, n_width
