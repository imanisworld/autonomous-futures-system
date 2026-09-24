"""Shared data plumbing: 5m bars, contract segments, ET session fields, TF aggregation."""
import os
import numpy as np
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
TICK = 0.25
MULT = {"MNQ": 2.0, "MES": 5.0}
COMM = 1.48
ROLLS = ["2024-09-12", "2024-12-12", "2025-03-13", "2025-06-12", "2025-09-11",
         "2025-12-11", "2026-03-12", "2026-06-11"]
OOS_FIRST_DAY = date(2025, 9, 11)


class Bars:
    pass


def load(inst):
    z = np.load(os.path.join(HERE, "..", "cache", f"{inst}_5m.npz"))
    b = Bars()
    b.inst = inst
    b.t, b.o, b.h, b.l, b.c, b.v = (z[k] for k in ("t", "o", "h", "l", "c", "v"))
    n = len(b.t)
    seam_ts = [int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp()) for d in ROLLS]
    cuts = sorted({int(np.searchsorted(b.t, s)) for s in seam_ts} | {0, n})
    cuts = [c for c in cuts if 0 <= c <= n]
    b.seg_bounds = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]
    b.seg_id = np.zeros(n, dtype=np.int64)
    b.seg_end = np.zeros(n, dtype=np.int64)
    for k, (s, e) in enumerate(b.seg_bounds):
        b.seg_id[s:e] = k
        b.seg_end[s:e] = e
    # ET fields
    et_min = np.zeros(n, dtype=np.int64)
    tday = np.zeros(n, dtype=np.int64)  # ordinal of trading day
    open930 = np.zeros(n, dtype=np.int64)  # unix ts of 09:30 ET on the bar's trading day
    cache = {}
    for i, ts in enumerate(b.t):
        dt = datetime.fromtimestamp(int(ts), ET)
        et_min[i] = dt.hour * 60 + dt.minute
        td = (dt + timedelta(hours=6)).date()
        tday[i] = td.toordinal()
        if td not in cache:
            cache[td] = int(datetime(td.year, td.month, td.day, 9, 30, tzinfo=ET).timestamp())
        open930[i] = cache[td]
    b.et_min, b.tday, b.open930 = et_min, tday, open930
    b.rth = (et_min >= 570) & (et_min < 960)
    b.entry_ok = (et_min >= 570) & (et_min <= 950)
    # session end bar: last RTH bar of the same ET calendar date (and same segment)
    sess_end = np.full(n, -1, dtype=np.int64)
    et_date = (b.t - 0).astype(np.int64)
    keys = {}
    for i in range(n):
        if b.rth[i]:
            k = (int(b.seg_id[i]), datetime.fromtimestamp(int(b.t[i]), ET).date())
            keys[k] = i  # ascending → last wins
    for i in range(n):
        if b.rth[i]:
            k = (int(b.seg_id[i]), datetime.fromtimestamp(int(b.t[i]), ET).date())
            sess_end[i] = keys[k]
    b.sess_end = sess_end
    days = np.unique(tday)
    b.days = days
    b.is_oos_bar = tday >= OOS_FIRST_DAY.toordinal()
    return b


def aggregate(b, seg, minutes):
    """Epoch-aligned TF bars from 5m bars inside one segment. Returns dict of arrays;
    'tc' = bucket start + TF (confirmation time)."""
    s, e = seg
    t = b.t[s:e]
    key = t // (minutes * 60)
    uk, first = np.unique(key, return_index=True)
    last = np.r_[first[1:], len(t)] - 1
    o = b.o[s:e][first]
    c = b.c[s:e][last]
    h = np.maximum.reduceat(b.h[s:e], first)
    l = np.minimum.reduceat(b.l[s:e], first)
    v = np.add.reduceat(b.v[s:e], first)
    tc = (uk + 1) * minutes * 60
    return {"o": o, "h": h, "l": l, "c": c, "v": v, "tc": tc}


def true_range(a):
    h, l, c = a["h"], a["l"], a["c"]
    pc = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    return tr


def rma(x, n):
    """Pine ta.rma / Wilder ATR: SMA seed at index n-1, NaN before."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = x[:n].mean()
    a = 1.0 / n
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def rolling_median(x, w, minp):
    out = np.full(len(x), np.nan)
    for i in range(len(x)):
        lo = max(0, i - w + 1)
        if i - lo + 1 >= minp:
            out[i] = np.median(x[lo:i + 1])
    return out
