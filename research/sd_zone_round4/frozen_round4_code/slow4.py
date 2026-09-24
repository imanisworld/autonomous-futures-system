"""Slow bar-by-bar reference engine for round 4 (literal reading of PREREG §5-6)."""
from common import TICK, MULT, COMM


def slow_zone(b, i, kind, top, bot, cc, life):
    dem = kind == 1
    res = {"status": None, "H2": None, "H15": None, "T2": None}
    if life < i + 1:
        res["status"] = 0; return res
    # arming
    if (dem and cc > top) or (not dem and cc < bot):
        a = i
    else:
        a = None
        for j in range(i + 1, life + 1):
            if (dem and b.c[j] > top) or (not dem and b.c[j] < bot): a = j; break
            if (dem and b.c[j] < bot) or (not dem and b.c[j] > top): res["status"] = 1; return res
        if a is None:
            res["status"] = 2; return res
    k = None
    for j in range(a + 1, life + 1):
        if (dem and b.l[j] <= top) or (not dem and b.h[j] >= bot): k = j; break
    if k is None:
        res["status"] = 3; return res
    # T2
    if b.entry_ok[k] and ((dem and b.l[k] <= top - TICK) or (not dem and b.h[k] >= bot + TICK)):
        ent = top if dem else bot
        stp = bot - TICK if dem else top + TICK
        res["T2"] = sim(b, dem, k, ent, stp, 2.0, target_on_first=False)
    hold = None
    for j in (k, k + 1):
        if j > life: break
        if (dem and b.c[j] < bot) or (not dem and b.c[j] > top):
            res["status"] = 4; return res
        if (dem and b.c[j] > top) or (not dem and b.c[j] < bot):
            hold = j; break
    if hold is None:
        res["status"] = 5; return res
    res["status"] = 6
    e = hold + 1
    if e > life or not b.entry_ok[e]:
        return res
    ent = b.o[e] + TICK if dem else b.o[e] - TICK
    if dem:
        stp = min(bot, b.l[k], b.l[hold]) - TICK
        if not ent - stp > 1e-9: return res
    else:
        stp = max(top, b.h[k], b.h[hold]) + TICK
        if not stp - ent > 1e-9: return res
    res["H2"] = sim(b, dem, e, ent, stp, 2.0, True)
    res["H15"] = sim(b, dem, e, ent, stp, 1.5, True)
    return res


def sim(b, dem, e, ent, stp, mult, target_on_first):
    R = abs(ent - stp)
    tgt = ent + mult * R if dem else ent - mult * R
    E = b.sess_end[e]
    px = None
    for j in range(e, E + 1):
        if (dem and b.l[j] <= stp) or (not dem and b.h[j] >= stp):
            px = stp - TICK if dem else stp + TICK; break
        if (j > e or target_on_first) and ((dem and b.h[j] >= tgt + TICK) or (not dem and b.l[j] <= tgt - TICK)):
            px = tgt; break
    if px is None:
        px = b.c[E]
    return ((px - ent) if dem else (ent - px)) * MULT[b.inst] - COMM
