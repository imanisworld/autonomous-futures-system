"""Synthetic checks for round-4 detector + hold engine (PREREG §11)."""
import numpy as np
from datetime import datetime, timedelta
from common import ET, Bars, TICK
from engine import Market
from zones4 import detect, life_end, DEFS
from engine4 import run_zones, HOLD, BROKEN, NOHOLD, NOTOUCH, NOARM, DEAD_PRE
from slow4 import slow_zone
import zones4

NAMES = {0: "NOLIFE", 1: "DEAD_PRE", 2: "NOARM", 3: "NOTOUCH", 4: "BROKEN", 5: "NOHOLD", 6: "HOLD"}


def make(bars, inst="MNQ"):
    b = Bars(); b.inst = inst
    t0 = datetime(2025, 1, 15, 8, 0, tzinfo=ET)
    n = len(bars)
    b.t = np.array([int((t0 + timedelta(minutes=5 * k)).timestamp()) for k in range(n)], np.int64)
    arr = np.array(bars, float)
    b.o, b.h, b.l, b.c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    b.v = np.ones(n)
    b.seg_bounds = [(0, n)]; b.seg_id = np.zeros(n, np.int64); b.seg_end = np.full(n, n)
    em = np.array([(datetime.fromtimestamp(int(x), ET).hour * 60 + datetime.fromtimestamp(int(x), ET).minute) for x in b.t])
    b.et_min = em; b.tday = np.full(n, 1); b.open930 = np.full(n, 0)
    b.rth = (em >= 570) & (em < 960); b.entry_ok = (em >= 570) & (em <= 950)
    last = np.flatnonzero(b.rth)[-1]
    b.sess_end = np.where(b.rth, last, -1)
    b.days = np.array([1]); b.is_oos_bar = np.zeros(n, bool)
    return b


def base(A_small=False):
    w = []
    for k in range(20):
        w.append((100.0, 100.5, 99.75, 100.25) if k % 2 == 0 else (100.25, 100.5, 99.75, 100.0))
    w.append((100.0, 100.5, 99.75, 100.25))            # i-2 (09:40)
    if A_small:
        w.append((98.5, 100.5, 97.75, 98.0))           # A red 2D, body 0.5 (small)
    else:
        w.append((100.4, 100.5, 97.75, 98.25))         # A red 2D, body 2.15 (big)
    w.append((98.5, 101.25, 98.5, 101.0))              # B green 2U, body 2.5, close > h_A
    return w


def pad(w, px, until=120):
    while len(w) < until:
        w.append((px, px + 0.25, px - 0.25, px))
    return w


def mirror(w):
    return [(300 - o, 300 - l, 300 - h, 300 - c) for (o, h, l, c) in w]


SC = {
 # EDGE zone demand = [97.75, 98.5]; FULL = [98.5, 101.25]
 "edge_hold_on_touch": (base() + [(101, 101.1, 100, 100.2), (100.2, 100.3, 98.3, 99.0), (99.0, 103.5, 98.9, 103.0)], 103.0),
 "edge_hold_next_bar": (base() + [(101, 101.1, 100, 100.2), (100.2, 100.3, 98.3, 98.4), (98.4, 99.2, 98.2, 99.0), (99.0, 99.2, 98.9, 99.1)], 99.1),
 "edge_touch_break": (base() + [(101, 101.1, 100, 100.2), (100.2, 100.3, 97.0, 97.5)], 97.5),
 "edge_no_hold": (base() + [(101, 101.1, 100, 100.2), (100.2, 100.3, 98.3, 98.4), (98.4, 98.45, 98.0, 98.0)], 98.2),
 "no_touch": (base() + [(101, 101.5, 100.9, 101.2)], 101.2),
 "full_never_leaves": (base() + [(101, 101.2, 100.6, 100.8)], 100.8),
 "full_hold": (base() + [(101, 101.7, 100.9, 101.5), (101.5, 101.6, 101.0, 101.4), (101.4, 103.0, 101.3, 102.0)], 102.0),
 "full_dead_pre": (base() + [(101, 101.1, 98.0, 98.2)], 98.2),
}
EXPECT = {  # (EDGE status, FULL status) -- FULL column follows the frozen arming rule
 "edge_hold_on_touch": ("HOLD", "NOTOUCH"),   # FULL: armed by the 103 close, never comes back
 "edge_hold_next_bar": ("HOLD", "DEAD_PRE"),  # FULL: 98.4 close below l_B before any arming close
 "full_never_leaves": ("NOTOUCH", "NOARM"),
 "edge_touch_break": ("BROKEN", "DEAD_PRE"),
 "edge_no_hold": ("NOHOLD", "DEAD_PRE"),
 "no_touch": ("NOTOUCH", "NOARM"),          # FULL: closes stay inside [l_B,h_B] -> never leaves
 "full_hold": ("NOTOUCH", "HOLD"),
 "full_dead_pre": ("NOHOLD", "DEAD_PRE"),    # EDGE: touch closes inside, next closes inside
}
bad = 0
for nm, (w, px) in SC.items():
    for side, ww in (("demand", pad(list(w), px)), ("supply", pad(mirror(w), 300 - px))):
        b = make(ww); mk = Market(b)
        for zk in ("EDGE", "FULL"):
            for dname in (f"S_{zk}", f"L_{zk}", f"L_{zk}_NY"):
                Z, nw = detect(b, dname)
                if 22 not in set(Z["i"].tolist()):
                    print("FAIL detect", nm, side, dname, Z["i"]); bad += 1; continue
            Z, _ = detect(b, f"S_{zk}")
            m = Z["i"] == 22
            Z = {kk: v[m] for kk, v in Z.items()}
            r = run_zones(mk, Z)
            st = NAMES[int(r["status"][0])]
            sl = slow_zone(b, int(Z["i"][0]), int(Z["kind"][0]), Z["top"][0], Z["bot"][0], Z["cc"][0], int(Z["life"][0]))
            exp = EXPECT[nm][0 if zk == "EDGE" else 1]
            h2 = r["H2"]["pnl"][0]; t2 = r["T2"]["pnl"][0]
            ok = st == exp and NAMES[sl["status"]] == st and \
                (np.isnan(h2) if sl["H2"] is None else abs(h2 - sl["H2"]) < 1e-6) and \
                (np.isnan(t2) if sl["T2"] is None else abs(t2 - sl["T2"]) < 1e-6)
            bad += not ok
            print(f"{'OK ' if ok else 'BAD'} {nm:20s} {side:7s} {zk}: zone=[{Z['bot'][0]:.2f},{Z['top'][0]:.2f}] status={st} (expect {exp}) "
                  f"entry_bar={int(r['entry_bar'][0]) if r['ent_ok'][0] else '-'} R={r['R'][0]:.2f} H2=${h2:.2f} T2=${t2:.2f} slowH2={sl['H2']} slowT2={sl['T2']}")
# STRICT vs LOOSE: small impulse candle
w = pad(base(A_small=True) + [(101, 101.5, 100.9, 101.2)], 101.2); b = make(w)
for dname in DEFS:
    Z, _ = detect(b, dname)
    exp = 0 if dname.startswith("S_") else 1
    ok = int((Z["i"] == 22).sum()) == exp; bad += not ok
    print(f"{'OK ' if ok else 'BAD'} small-A {dname}: {len(Z['i'])} (expect {exp})")
# NY context: shift whole scenario 2h later (B at 11:50) -> NY defs must not detect
w = [(100, 100.5, 99.75, 100.25)] * 24 + base() + [(101, 101.5, 100.9, 101.2)]; b = make(pad(w, 101.2))
for dname in DEFS:
    Z, _ = detect(b, dname)
    exp = 0 if dname.endswith("_NY") else 1
    ok = int((Z["i"] == 46).sum()) == exp; bad += not ok
    print(f"{'OK ' if ok else 'BAD'} late-B {dname}: {len(Z['i'])} (expect {exp})")
print("SYNTHETIC FAILURES:", bad)
