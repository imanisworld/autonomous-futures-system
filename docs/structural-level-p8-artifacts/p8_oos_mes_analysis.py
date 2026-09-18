#!/usr/bin/env python3
"""P8 — post-R7 outcome opening: preregistered TF outcome analysis over the frozen R5/R6 artifacts.

Read-only research tool (prereg v1.5 `docs/prereg-dynamic-structural-level-attribution-2026-09-16.md`
§6–§10; operator authorisation 2026-09-17 "GO — POST-R7 OUTCOME OPENING / P8 VERIFICATION").

* Imports nothing from webhook/, strategy/, execution/, replay/, research/ or scripts/ — only the
  standard library and numpy. Contract economics are transcribed from `config/futures_contracts`
  (MNQ tick 0.25 / $0.50, MES tick 0.25 / $1.25) and asserted below.
* Refuses to run unless every frozen input hashes to the R5 / R6 record (``--expect`` pins).
* Joins ``candidates.jsonl`` + ``features.jsonl`` + ``outcomes.sealed.jsonl`` on ``candidate_key``
  only; nothing is regenerated, no definition, threshold, label or bracket is changed.
* Outcome-blind pre-commitments (fold boundaries, H4 age tertiles from fold 1) are computed from
  candidates/features **before** the outcome file is read, and recorded in the output.
* Every exclusion (NO_FILL, OPEN, NOT_APPLICABLE, NOT_AVAILABLE, gap-contaminated anchor,
  roll-contaminated anchor, NOT_TESTABLE cells) is counted, never silent.

Usage:
  python3 scripts/structural_level_p8_outcome_analysis.py --r5-dir <r5> --r6-dir <r6> --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

TOOL = "slp8-outcome-analysis-v1"
INSTRUMENTS = ("MES",)
ECON = {"MNQ": (0.25, 0.5), "MES": (0.25, 1.25)}       # tick, tick value (config/futures_contracts)
COMMISSION_RT = 1.48                                    # pinned cost model (prereg §8.1)
SLIP_TICKS_RT = 2.0
N_PERM = 10_000
N_BOOT = 2_000
SEED = 20260917
MIN_CONTRAST_TOTAL = 40                                  # prereg §7
MIN_CONTRAST_LEVEL = 15
MIN_STRATUM = 30
MIN_H4_BIN = 12
MIN_H2_BIN = 12
H2_AGE_BINS = ((2, 3), (4, 5), (6, 8))
H4_TC_BINS = ((0, 0), (1, 2), (3, 10**9))
ET = ZoneInfo("America/New_York")

FROZEN = {
    "MES": {
        "candidates": "10ffb5abc68b877b208ebf4baf463f39a26710b511ace60697ee3dacde9cb65f",
        "features": "4b8a12a2ef98810c9e40182bb512015f831cebe5f75b0beb4124af7e75308c4f",
        "outcomes": "00c5b46086d6f167f719671013c728d93fc1821e73f26890750081ea56b80143",
    },
}

# prereg §9.5 per-feature roll-contamination windows, in CME trading days after the seam day
ROLL_WINDOW_DAYS = {
    "PDH": 1, "PDL": 1, "PDC_BAR": 1, "ONH": 1, "ONL": 1,
    "NY_ORB_H": 1, "NY_ORB_L": 1, "LDN_ORB_H": 1, "LDN_ORB_L": 1,
    "LC_ZONE_1H_DEMAND": 10, "LC_ZONE_1H_SUPPLY": 10, "LC_ZONE_4H_DEMAND": 10, "LC_ZONE_4H_SUPPLY": 10,
}
ROLL_WEEK_LEVELS = {"PWH", "PWL"}   # roll week and the following week

# Frozen contrasts (prereg §6): (label set, frozen direction, uses anchor field)
HYPS = {
    "H1": {"desc": "sweep→reclaim (T) vs plain touch/break (F) on A_H", "anchor": "anchor"},
    "H2": {"desc": "break→retest→hold (T) vs age-matched accepted-not-retested (F) on A_2", "anchor": "anchor"},
    "H3": {"desc": "wick-reject (T) vs proximity-only (F) on A_H", "anchor": "anchor"},
    "H4": {"desc": "test-count bins / age tertiles trend (two-sided) on A_H", "anchor": "anchor"},
    "H5": {"desc": "cluster ≥2 (T) vs cluster ==1 (F) on A_H", "anchor": "anchor"},
    "H6": {"desc": "target before opposing level (T) vs beyond (F)", "anchor": "opposing"},
}


# ── helpers ───────────────────────────────────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def cme_trading_day(ts: datetime):
    """(ET time + 6h).date() — roll at 18:00 ET (prereg §3)."""
    return (ts.astimezone(ET) + timedelta(hours=6)).date()


def iso_week(d) -> tuple[int, int]:
    return d.isocalendar()[:2]


def net_r(inst: str, entry: float, stop: float, pnl_ticks: float) -> tuple[float, float, float]:
    tick, tv = ECON[inst]
    stop_ticks = abs(entry - stop) / tick
    net_usd = pnl_ticks * tv - SLIP_TICKS_RT * tv - COMMISSION_RT
    return net_usd / (stop_ticks * tv), net_usd, stop_ticks


def q(a, p):
    return float(np.quantile(a, p)) if len(a) else None


def fmt(x, nd=4):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def holm(pvals: dict[str, float | None]) -> dict[str, float | None]:
    items = [(k, v) for k, v in pvals.items() if v is not None]
    m = len(items)
    items.sort(key=lambda kv: kv[1])
    out, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[k] = running
    for k, v in pvals.items():
        if v is None:
            out[k] = None
    return out


# ── loading (outcome-blind part first) ─────────────────────────────────────────────────────────

def load_candidates_features(r5_dir: str, r6_dir: str, inst: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with open(os.path.join(r5_dir, inst, "candidates.jsonl")) as fh:
        for line in fh:
            c = json.loads(line)
            for k in ("outcome", "result", "pnl_ticks", "exit_price", "exit_reason"):
                if k in c:
                    sys.exit(f"[p8] candidates row carries outcome-like field {k!r} — refusing")
            if c["instrument"] != inst:
                sys.exit(f"[p8] candidates instrument mismatch {c['instrument']} != {inst}")
            key = c["candidate_key"]
            if key in rows:
                sys.exit(f"[p8] duplicate candidate_key in candidates: {key}")
            ts = parse_ts(c["bar_ts"])
            rows[key] = {
                "key": key, "ts": ts, "session": c["session"], "strategy": c["strategy"],
                "direction": c["direction"], "entry": float(c["entry"]), "stop": float(c["stop"]),
                "target": float(c["target"]), "contract": c.get("contract"),
                "roll_date": c.get("contract_roll_utc_date"), "is_roll_utc_day": c.get("is_roll_utc_day"),
                "regime": c.get("reconstructed_market_condition"), "tday": cme_trading_day(ts),
            }
    n_feat = 0
    with open(os.path.join(r6_dir, inst, "features.jsonl")) as fh:
        for line in fh:
            f = json.loads(line)
            key = f["candidate_key"]
            if key not in rows:
                sys.exit(f"[p8] feature key not in candidates: {key}")
            r = rows[key]
            if "hyp" in r:
                sys.exit(f"[p8] duplicate feature key: {key}")
            # sanity: identity fields agree
            if (f["instrument"], f["strategy"], f["direction"]) != (inst, r["strategy"], r["direction"]) or \
               abs(float(f["entry"]) - r["entry"]) > 1e-9:
                sys.exit(f"[p8] feature/candidate identity mismatch on {key}")
            hyp = {}
            for h, v in f["hypotheses"].items():
                lvl_name = v.get(HYPS[h]["anchor"])
                lvl = f["levels"].get(lvl_name) if lvl_name else None
                hyp[h] = {
                    "label": v["label"], "level": lvl_name,
                    "gap": bool(lvl and lvl.get("gap_contaminated")),
                    "break_age": v.get("break_age"), "test_count": v.get("test_count"),
                    "age_hours": v.get("age_hours"), "cluster": v.get("cluster"),
                    "room_R": v.get("room_R"), "target_rel": v.get("target_rel"),
                }
            r["hyp"] = hyp
            r["mtr15"] = f.get("mtr15")
            r["session_p1"] = f.get("session_p1")
            n_feat += 1
    if n_feat != len(rows):
        sys.exit(f"[p8] feature rows {n_feat} != candidate rows {len(rows)}")
    return rows


def roll_contaminated(r: dict, level: str | None, tday_index: dict, roll_tdays: list) -> bool:
    """prereg §9.5: is the row's B0 inside the level's post-roll contamination window?"""
    if not level:
        return False
    td = r["tday"]
    for rd in roll_tdays:                       # rd = CME trading day containing the seam
        if td < rd:
            continue
        if level in ROLL_WEEK_LEVELS:
            wk_roll, wk_row = iso_week(rd), iso_week(td)
            nxt = iso_week(rd + timedelta(days=7))
            if wk_row in (wk_roll, nxt):
                return True
        else:
            win = ROLL_WINDOW_DAYS.get(level)
            if win is None:
                continue
            if tday_index[td] - tday_index[rd] <= win:
                return True
    return False


def load_outcomes(r5_dir: str, inst: str, rows: dict[str, dict]) -> Counter:
    census = Counter()
    with open(os.path.join(r5_dir, inst, "outcomes.sealed.jsonl")) as fh:
        for line in fh:
            o = json.loads(line)
            key = o["candidate_key"]
            if key not in rows:
                sys.exit(f"[p8] outcome key not in candidates: {key}")
            r = rows[key]
            if "result" in r:
                sys.exit(f"[p8] duplicate outcome key: {key}")
            oc = o["outcome"]
            r["result"] = oc["result"]
            r["pnl_ticks"] = oc.get("pnl_ticks")
            r["bars_to_exit"] = oc.get("bars_to_exit")
            r["bars_to_fill"] = oc.get("bars_to_fill")
            r["fb_ignored"] = bool(oc.get("fill_bar_target_ambiguous_ignored"))
            census[oc["result"]] += 1
            if r["result"] in ("WIN", "LOSS"):
                r["net_R"], r["net_usd"], r["stop_ticks"] = net_r(inst, r["entry"], r["stop"], float(r["pnl_ticks"]))
                r["gross_R"] = float(r["pnl_ticks"]) / r["stop_ticks"]
            else:
                r["net_R"] = r["net_usd"] = r["gross_R"] = None
    missing = [k for k, r in rows.items() if "result" not in r]
    if missing:
        sys.exit(f"[p8] {len(missing)} candidates without outcome row (join < 100%)")
    return census


# ── statistics ────────────────────────────────────────────────────────────────────────────────

def perm_mean_diff(rng, r: np.ndarray, lab: np.ndarray, strata: np.ndarray, direction: str) -> tuple[float, float]:
    """Permutation p for mean(T)-mean(F); labels shuffled within strata. direction: 'T_better'|'two_sided'."""
    obs = r[lab].mean() - r[~lab].mean()
    n = len(r)
    sum_all = r.sum()
    n_t = int(lab.sum())
    sum_t = np.zeros(N_PERM)
    for s in np.unique(strata):
        m = strata == s
        rs, ls = r[m], lab[m]
        if ls.all() or (~ls).all() or len(ls) < 2:
            sum_t += rs[ls].sum()
            continue
        perm = rng.permuted(np.tile(ls, (N_PERM, 1)), axis=1)
        sum_t += perm @ rs
    mean_t = sum_t / n_t
    mean_f = (sum_all - sum_t) / (n - n_t)
    null = mean_t - mean_f
    if direction == "T_better":
        p = (np.sum(null >= obs) + 1) / (N_PERM + 1)
    else:
        p = (np.sum(np.abs(null) >= abs(obs)) + 1) / (N_PERM + 1)
    return float(obs), float(p)


def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a))
    ranks[order] = np.arange(1, len(a) + 1)
    # average ties
    sa = a[order]
    i = 0
    while i < len(sa):
        j = i
        while j + 1 < len(sa) and sa[j + 1] == sa[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return ranks


def perm_spearman_np(rng, r: np.ndarray, binidx: np.ndarray, strata: np.ndarray) -> tuple[float, float]:
    """Two-sided permutation Spearman between ordered bin index and net R; bins shuffled within strata."""
    rr = _rank(r)
    rr = (rr - rr.mean()) / rr.std()
    rb = _rank(binidx.astype(float))
    obs = float(np.mean(rr * (rb - rb.mean()) / rb.std()))
    null = np.zeros(N_PERM)
    for s in np.unique(strata):
        m = strata == s
        if m.sum() < 2:
            null += (rr[m] * rb[m]).sum() if m.sum() else 0.0
            continue
        perm = rng.permuted(np.tile(rb[m], (N_PERM, 1)), axis=1)
        null += perm @ rr[m]
    null = (null / len(r) - rb.mean() * rr.mean()) / rb.std()
    p = (np.sum(np.abs(null) >= abs(obs)) + 1) / (N_PERM + 1)
    return obs, float(p)


def day_bootstrap(rng, r: np.ndarray, lab: np.ndarray, days: np.ndarray) -> tuple[float, float]:
    """95% percentile CI of mean(T)-mean(F) under a trading-day block bootstrap (multinomial day weights)."""
    ud, inv = np.unique(days, return_inverse=True)
    D = len(ud)
    st = np.bincount(inv, weights=r * lab, minlength=D)
    nt = np.bincount(inv, weights=lab.astype(float), minlength=D)
    sf = np.bincount(inv, weights=r * (~lab), minlength=D)
    nf = np.bincount(inv, weights=(~lab).astype(float), minlength=D)
    W = rng.multinomial(D, np.full(D, 1.0 / D), size=N_BOOT).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        eff = (W @ st) / (W @ nt) - (W @ sf) / (W @ nf)
    eff = eff[np.isfinite(eff)]
    return float(np.quantile(eff, 0.025)), float(np.quantile(eff, 0.975))


def contrast_block(rng, rows: list[dict], h: str, direction: str, fold_of, strata_key, extra_strata=None):
    """Full preregistered TF contrast for a T/F hypothesis on the eligible rows given."""
    r = np.array([x["net_R"] for x in rows])
    lab = np.array([x["hyp"][h]["label"] == "T" for x in rows])
    usd = np.array([x["net_usd"] for x in rows])
    strata = np.array([strata_key(x) + ((extra_strata(x),) if extra_strata else ()) for x in rows], dtype=object)
    strata = np.array([str(s) for s in strata])
    days = np.array([str(x["tday"]) for x in rows])
    out = {"n": len(rows), "n_T": int(lab.sum()), "n_F": int((~lab).sum())}
    out["mean_R_T"], out["mean_R_F"] = fmt(r[lab].mean()), fmt(r[~lab].mean())
    out["median_R_T"], out["median_R_F"] = fmt(np.median(r[lab])), fmt(np.median(r[~lab]))
    out["win_rate_T"] = fmt(np.mean([x["result"] == "WIN" for x in rows if x["hyp"][h]["label"] == "T"]))
    out["win_rate_F"] = fmt(np.mean([x["result"] == "WIN" for x in rows if x["hyp"][h]["label"] != "T"]))
    testable = len(rows) >= MIN_CONTRAST_TOTAL and lab.sum() >= MIN_CONTRAST_LEVEL and (~lab).sum() >= MIN_CONTRAST_LEVEL
    out["testable"] = bool(testable)
    if not testable:
        out["status"] = "NOT_TESTABLE"
        return out
    obs, p = perm_mean_diff(rng, r, lab, strata, direction)
    out["effect_R"], out["p_perm"] = fmt(obs), fmt(p, 5)
    out["ci95_dayblock"] = [fmt(v) for v in day_bootstrap(rng, r, lab, days)]
    # outliers: ex-top-1 / ex-top-5 by |net_usd|
    for k in (1, 5):
        keep = np.ones(len(r), bool)
        keep[np.argsort(-np.abs(usd))[:k]] = False
        if lab[keep].sum() and (~lab[keep]).sum():
            out[f"effect_ex_top{k}"] = fmt(r[keep & lab].mean() - r[keep & ~lab].mean())
    # session × family fixed-effect adjustment (B1 partial): demean within cell
    cell = np.array([f"{x['session']}|{x['strategy']}" for x in rows])
    adj = r.copy()
    for c in np.unique(cell):
        m = cell == c
        adj[m] = r[m] - r[m].mean()
    out["effect_adj_session_family"] = fmt(adj[lab].mean() - adj[~lab].mean())
    # walk-forward folds
    folds = np.array([fold_of(x) for x in rows])
    wf = []
    for f in (1, 2, 3):
        m = folds == f
        if lab[m].sum() and (~lab[m]).sum():
            wf.append({"fold": f, "n_T": int(lab[m].sum()), "n_F": int((~lab[m]).sum()),
                       "effect_R": fmt(r[m & lab].mean() - r[m & ~lab].mean())})
        else:
            wf.append({"fold": f, "n_T": int(lab[m].sum()), "n_F": int((~lab[m]).sum()), "effect_R": None})
    out["walk_forward"] = wf
    sign = 1 if direction == "T_better" else (1 if obs >= 0 else -1)
    fe = [w["effect_R"] for w in wf if w["effect_R"] is not None]
    out["walk_forward_pass"] = bool(len(fe) == 3 and sum(1 for e in fe if e * sign > 0) >= 2 and min(e * sign for e in fe) >= -0.05)
    # strata (session cells, n ≥ 30) and family cells
    cells = {}
    for s in ("asian", "london", "new_york"):
        m = np.array([x["session"] == s for x in rows])
        if m.sum() >= MIN_STRATUM and lab[m].sum() and (~lab[m]).sum():
            cells[s] = {"n": int(m.sum()), "n_T": int(lab[m].sum()), "effect_R": fmt(r[m & lab].mean() - r[m & ~lab].mean())}
        else:
            cells[s] = {"n": int(m.sum()), "n_T": int(lab[m].sum()), "effect_R": None}
    out["session_cells"] = cells
    fams = {}
    for fam in sorted({x["strategy"] for x in rows}):
        m = np.array([x["strategy"] == fam for x in rows])
        e = fmt(r[m & lab].mean() - r[m & ~lab].mean()) if (lab[m].sum() and (~lab[m]).sum()) else None
        fams[fam] = {"n": int(m.sum()), "n_T": int(lab[m].sum()), "n_F": int((~lab[m]).sum()), "effect_R": e}
    out["family_cells"] = fams
    out["families_same_sign_n30"] = int(sum(1 for v in fams.values() if v["n"] >= MIN_STRATUM and v["effect_R"] is not None and v["effect_R"] * sign > 0))
    out["families_n30"] = int(sum(1 for v in fams.values() if v["n"] >= MIN_STRATUM and v["effect_R"] is not None))
    # NO_FILL / OPEN rate per contrast level is added by the caller (needs unresolved rows)
    out["status"] = "TESTED"
    return out


# ── main ──────────────────────────────────────────────────────────────────────────────────────

def main() -> None:
    global N_PERM
    ap = argparse.ArgumentParser()
    ap.add_argument("--r5-dir", required=True)
    ap.add_argument("--r6-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-perm", type=int, default=N_PERM)
    args = ap.parse_args()
    N_PERM = args.n_perm
    os.makedirs(args.out, exist_ok=True)
    rng = np.random.default_rng(SEED)
    result = {"tool": TOOL, "seed": SEED, "n_perm": N_PERM, "n_boot": N_BOOT, "cost_model":
              {"commission_rt_usd": COMMISSION_RT, "slip_ticks_rt": SLIP_TICKS_RT, "econ": ECON},
              "frozen_inputs": {}, "instruments": {}}

    # 1. frozen identities (fail closed)
    for inst in INSTRUMENTS:
        got = {"candidates": sha256_file(os.path.join(args.r5_dir, inst, "candidates.jsonl")),
               "features": sha256_file(os.path.join(args.r6_dir, inst, "features.jsonl")),
               "outcomes": sha256_file(os.path.join(args.r5_dir, inst, "outcomes.sealed.jsonl"))}
        for k, v in got.items():
            if v != FROZEN[inst][k]:
                sys.exit(f"P8 BLOCKED: FROZEN ARTIFACT DRIFT — {inst} {k} {v} != {FROZEN[inst][k]}")
        result["frozen_inputs"][inst] = got
    print("[p8] frozen artifact check: all 6 hashes match", flush=True)

    for inst in INSTRUMENTS:
        R: dict = {}
        rows = load_candidates_features(args.r5_dir, args.r6_dir, inst)
        print(f"[p8] {inst}: {len(rows)} candidates+features joined (outcome file not yet read)", flush=True)
        ordered = sorted(rows.values(), key=lambda x: (x["ts"], x["key"]))
        # outcome-blind pre-commitments
        n = len(ordered)
        b1, b2 = ordered[n // 3]["ts"], ordered[(2 * n) // 3]["ts"]
        for i, x in enumerate(ordered):
            x["fold"] = 1 if x["ts"] < b1 else (2 if x["ts"] < b2 else 3)
        ages_f1 = np.array([x["hyp"]["H4"]["age_hours"] for x in ordered
                            if x["fold"] == 1 and x["hyp"]["H4"]["label"] == "APPLICABLE" and x["hyp"]["H4"]["age_hours"] is not None])
        t1, t2 = float(np.quantile(ages_f1, 1 / 3)), float(np.quantile(ages_f1, 2 / 3))
        tdays = sorted({x["tday"] for x in ordered})
        tday_index = {d: i for i, d in enumerate(tdays)}
        roll_dates = sorted({x["roll_date"] for x in ordered if x["roll_date"]})
        roll_tdays = []
        for rd in roll_dates:
            seam = datetime.fromisoformat(rd).replace(tzinfo=timezone.utc)   # seam = UTC midnight of roll_utc_date
            td = cme_trading_day(seam)
            if td not in tday_index:                                          # snap to the next trading day present
                td = next((d for d in tdays if d >= td), None)
            if td is not None:
                roll_tdays.append(td)
        R["precommit"] = {"fold_boundaries_utc": [b1.isoformat(), b2.isoformat()],
                          "fold_rows": dict(Counter(x["fold"] for x in ordered)),
                          "h4_age_tertiles_hours_fold1": [round(t1, 4), round(t2, 4)], "h4_age_fold1_n": int(len(ages_f1)),
                          "roll_utc_dates": roll_dates, "roll_cme_trading_days": [str(d) for d in roll_tdays],
                          "trading_days": len(tdays)}
        for x in ordered:
            for h in HYPS:
                x["hyp"][h]["roll"] = roll_contaminated(x, x["hyp"][h]["level"], tday_index, roll_tdays)
        print(f"[p8] {inst}: folds at {b1.isoformat()} / {b2.isoformat()}; H4 age tertiles {t1:.3f}/{t2:.3f} h", flush=True)

        # 2. open outcomes
        census = load_outcomes(args.r5_dir, inst, rows)
        term = [x for x in ordered if x["result"] in ("WIN", "LOSS")]
        rr = np.array([x["net_R"] for x in term])
        gr = np.array([x["gross_R"] for x in term])
        usd = np.array([x["net_usd"] for x in term])
        cum = np.cumsum(rr)
        dd = cum - np.maximum.accumulate(cum)
        losses = np.sort(usd[usd < 0])
        R["population"] = {
            "candidates": n, "outcomes": sum(census.values()), "join_rate": 1.0,
            "result_counts": dict(census), "terminal": len(term),
            "win": census["WIN"], "loss": census["LOSS"], "breakeven": 0,
            "win_rate_terminal": fmt(census["WIN"] / len(term)),
            "no_fill_rate": fmt(census["NO_FILL"] / n), "open_rate": fmt(census["OPEN"] / n),
            "fill_bar_target_ambiguous_ignored": int(sum(x["fb_ignored"] for x in ordered)),
            "mean_gross_R": fmt(gr.mean()), "mean_net_R": fmt(rr.mean()), "median_net_R": fmt(np.median(rr)),
            "sum_net_R": fmt(rr.sum(), 2), "sum_net_usd_1lot": fmt(usd.sum(), 2),
            "net_R_quantiles": {p: fmt(q(rr, float(p))) for p in ("0.05", "0.25", "0.5", "0.75", "0.95")},
            "net_R_min": fmt(rr.min()), "net_R_max": fmt(rr.max()),
            "stop_ticks_median": fmt(np.median([x["stop_ticks"] for x in term]), 2),
            "sequential_1lot_max_drawdown_R": fmt(dd.min(), 2),
            "loss_concentration": {"share_of_total_loss_usd_worst_1pct": fmt(losses[: max(1, len(losses) // 100)].sum() / losses.sum()),
                                   "share_of_total_loss_usd_worst_5pct": fmt(losses[: max(1, len(losses) // 20)].sum() / losses.sum()),
                                   "n_losses": int(len(losses))},
            "note": "rows overlap in time (many candidates per bar); the sequential curve is a descriptive sum of independent 1-lot brackets, not a portfolio path",
        }
        # by session / family / direction / fold / regime
        def group(keyf):
            g = defaultdict(list)
            for x in ordered:
                g[keyf(x)].append(x)
            out = {}
            for k in sorted(g, key=str):
                xs = g[k]
                t = [x for x in xs if x["result"] in ("WIN", "LOSS")]
                out[str(k)] = {"n": len(xs), "terminal": len(t), "no_fill": sum(x["result"] == "NO_FILL" for x in xs),
                               "open": sum(x["result"] == "OPEN" for x in xs),
                               "win": sum(x["result"] == "WIN" for x in t),
                               "win_rate": fmt(np.mean([x["result"] == "WIN" for x in t])) if t else None,
                               "mean_net_R": fmt(np.mean([x["net_R"] for x in t])) if t else None,
                               "median_net_R": fmt(np.median([x["net_R"] for x in t])) if t else None}
            return out
        R["by_session"] = group(lambda x: x["session"])
        R["by_family"] = group(lambda x: x["strategy"])
        R["by_direction"] = group(lambda x: x["direction"])
        R["by_fold"] = group(lambda x: x["fold"])
        R["by_regime"] = group(lambda x: x["regime"])
        R["by_family_session"] = group(lambda x: f"{x['strategy']}|{x['session']}")

        # 3. hypotheses
        strata_key = lambda x: (x["session"], x["strategy"])
        fold_of = lambda x: x["fold"]
        H = {}
        pvals = {}
        for h in ("H1", "H2", "H3", "H5", "H6"):
            lab_rows = [x for x in ordered if x["hyp"][h]["label"] in ("T", "F")]
            excl = Counter()
            elig = []
            for x in lab_rows:
                hh = x["hyp"][h]
                if hh["gap"]:
                    excl["anchor_gap_contaminated"] += 1
                elif hh["roll"]:
                    excl["anchor_roll_contaminated"] += 1
                elif x["result"] == "NO_FILL":
                    excl["NO_FILL"] += 1
                elif x["result"] == "OPEN":
                    excl["OPEN"] += 1
                else:
                    elig.append(x)
            label_census = dict(Counter(x["hyp"][h]["label"] for x in ordered))
            fillable = [x for x in lab_rows if not x["hyp"][h]["gap"] and not x["hyp"][h]["roll"]]
            nofill = {}
            for L in ("T", "F"):
                xs = [x for x in fillable if x["hyp"][h]["label"] == L]
                nofill[L] = {"n": len(xs), "no_fill_rate": fmt(np.mean([x["result"] == "NO_FILL" for x in xs])) if xs else None,
                             "open_rate": fmt(np.mean([x["result"] == "OPEN" for x in xs])) if xs else None}
            block = {"desc": HYPS[h]["desc"], "label_census": label_census, "exclusions": dict(excl),
                     "eligible_terminal": len(elig), "unresolved_rates_by_label": nofill}
            if h == "H2":
                # age-matched: equal-weight across frozen age bins with ≥ MIN_H2_BIN per level; permutation within bin × strata
                bins = {}
                per_bin = []
                kept = []
                for lo, hi in H2_AGE_BINS:
                    xs = [x for x in elig if x["hyp"][h]["break_age"] is not None and lo <= x["hyp"][h]["break_age"] <= hi]
                    nT = sum(x["hyp"][h]["label"] == "T" for x in xs)
                    nF = len(xs) - nT
                    e = None
                    if nT and nF:
                        e = fmt(np.mean([x["net_R"] for x in xs if x["hyp"][h]["label"] == "T"]) -
                                np.mean([x["net_R"] for x in xs if x["hyp"][h]["label"] != "T"]))
                    ok = nT >= MIN_H2_BIN and nF >= MIN_H2_BIN
                    bins[f"{lo}-{hi}"] = {"n_T": nT, "n_F": nF, "effect_R": e, "used": ok}
                    if ok:
                        per_bin.append(e)
                        for x in xs:
                            x["_h2bin"] = f"{lo}-{hi}"
                        kept.extend(xs)
                block["age_bins"] = bins
                block["dropped_rows_bins_below_min"] = len(elig) - len(kept)
                if kept:
                    c = contrast_block(rng, kept, h, "T_better", fold_of, strata_key, extra_strata=lambda x: x["_h2bin"])
                    c["effect_R_equal_weight_bins"] = fmt(np.mean(per_bin)) if per_bin else None
                    c["note"] = "permutation p is for the pooled mean difference with labels shuffled within age-bin × session × family strata; the equal-weight-bin effect is the preregistered point estimate"
                else:
                    c = {"n": 0, "status": "NOT_TESTABLE"}
                block["contrast"] = c
            else:
                block["contrast"] = contrast_block(rng, elig, h, "T_better", fold_of, strata_key) if elig else {"n": 0, "status": "NOT_TESTABLE"}
            # level identity (descriptive)
            ident = defaultdict(lambda: {"T": [], "F": []})
            for x in elig:
                ident[x["hyp"][h]["level"]][x["hyp"][h]["label"]].append(x["net_R"])
            block["level_identity"] = {lv: {"n_T": len(v["T"]), "n_F": len(v["F"]),
                                            "mean_R_T": fmt(np.mean(v["T"])) if v["T"] else None,
                                            "mean_R_F": fmt(np.mean(v["F"])) if v["F"] else None}
                                       for lv, v in sorted(ident.items(), key=lambda kv: str(kv[0]))}
            H[h] = block
            pvals[h] = block["contrast"].get("p_perm")
        # H4: trends among APPLICABLE eligible rows
        h = "H4"
        lab_rows = [x for x in ordered if x["hyp"][h]["label"] == "APPLICABLE"]
        excl = Counter()
        elig = []
        for x in lab_rows:
            hh = x["hyp"][h]
            if hh["gap"]:
                excl["anchor_gap_contaminated"] += 1
            elif hh["roll"]:
                excl["anchor_roll_contaminated"] += 1
            elif x["result"] == "NO_FILL":
                excl["NO_FILL"] += 1
            elif x["result"] == "OPEN":
                excl["OPEN"] += 1
            else:
                elig.append(x)
        block = {"desc": HYPS[h]["desc"], "label_census": dict(Counter(x["hyp"][h]["label"] for x in ordered)),
                 "exclusions": dict(excl), "eligible_terminal": len(elig)}
        def h4_trend(name, binf, labels):
            xs = [x for x in elig if binf(x) is not None]
            bi = np.array([binf(x) for x in xs])
            r = np.array([x["net_R"] for x in xs])
            strata = np.array([f"{x['session']}|{x['strategy']}" for x in xs])
            binstats = {}
            for i, lbl in enumerate(labels):
                m = bi == i
                binstats[lbl] = {"n": int(m.sum()), "mean_net_R": fmt(r[m].mean()) if m.sum() else None,
                                 "win_rate": fmt(np.mean([xs[j]["result"] == "WIN" for j in np.where(m)[0]])) if m.sum() else None}
            ok = all(v["n"] >= MIN_H4_BIN for v in binstats.values()) and len(xs) >= MIN_CONTRAST_TOTAL
            o = {"bins": binstats, "n": len(xs), "testable": bool(ok)}
            if ok:
                rho, p = perm_spearman_np(rng, r, bi, strata)
                o["spearman_rho"], o["p_perm_two_sided"] = fmt(rho), fmt(p, 5)
                folds = np.array([x["fold"] for x in xs])
                o["walk_forward"] = []
                for f in (1, 2, 3):
                    m = folds == f
                    if m.sum() >= MIN_CONTRAST_TOTAL and len(np.unique(bi[m])) > 1:
                        rho_f = float(np.corrcoef(_rank(r[m]), _rank(bi[m].astype(float)))[0, 1])
                        o["walk_forward"].append({"fold": f, "n": int(m.sum()), "rho": fmt(rho_f)})
            return o
        def tc_bin(x):
            tc = x["hyp"][h]["test_count"]
            if tc is None:
                return None
            for i, (lo, hi) in enumerate(H4_TC_BINS):
                if lo <= tc <= hi:
                    return i
            return None
        def age_bin(x):
            a = x["hyp"][h]["age_hours"]
            if a is None:
                return None
            return 0 if a <= t1 else (1 if a <= t2 else 2)
        block["test_count"] = h4_trend("test_count", tc_bin, ["0", "1-2", ">=3"])
        block["age"] = h4_trend("age", age_bin, [f"<= {t1:.2f}h", f"<= {t2:.2f}h", f"> {t2:.2f}h"])
        ps = [b["p_perm_two_sided"] for b in (block["test_count"], block["age"]) if b.get("p_perm_two_sided") is not None]
        block["p_bonferroni2"] = fmt(min(1.0, 2 * min(ps)), 5) if ps else None
        H[h] = block
        pvals[h] = block["p_bonferroni2"]
        adj = holm(pvals)
        for h in H:
            H[h]["p_holm"] = fmt(adj[h], 5) if adj[h] is not None else None
        # "supported" evaluation (prereg §10) for T/F hypotheses
        for h in ("H1", "H2", "H3", "H5", "H6"):
            c = H[h]["contrast"]
            crit = {}
            if c.get("status") == "TESTED":
                eff = c.get("effect_R_equal_weight_bins", c["effect_R"]) if h == "H2" else c["effect_R"]
                crit = {"holm_p_lt_0.05": bool(H[h]["p_holm"] is not None and H[h]["p_holm"] < 0.05),
                        "walk_forward": bool(c["walk_forward_pass"]),
                        "effect_ge_0.10R": bool(eff is not None and eff >= 0.10),
                        "ex_top5_ge_0.05R": bool(c.get("effect_ex_top5") is not None and c["effect_ex_top5"] >= 0.05),
                        "strata_consistent": bool(sum(1 for v in c["session_cells"].values() if v["effect_R"] is not None and v["effect_R"] > 0) >= 2),
                        "not_confined_to_one_family": bool(c["families_same_sign_n30"] >= 2),
                        "incremental_adj_ge_0.10R": bool(c["effect_adj_session_family"] is not None and c["effect_adj_session_family"] >= 0.10)}
                crit["SUPPORTED_in_sample"] = all(crit.values())
            H[h]["supported_criteria"] = crit
        R["hypotheses"] = H
        R["holm_family_TF"] = {"raw": pvals, "holm": {k: fmt(v, 5) if v is not None else None for k, v in adj.items()}}
        result["instruments"][inst] = R
        print(f"[p8] {inst}: terminal {len(term)} WR {census['WIN']/len(term):.4f} mean net R {rr.mean():.4f}", flush=True)
        for h in ("H1", "H2", "H3", "H5", "H6"):
            c = H[h]["contrast"]
            print(f"      {h}: n={c.get('n')} T={c.get('n_T')} F={c.get('n_F')} effect={c.get('effect_R')} p={c.get('p_perm')} holm={H[h]['p_holm']} wf={c.get('walk_forward_pass')}", flush=True)
        print(f"      H4: tc rho={H['H4']['test_count'].get('spearman_rho')} p={H['H4']['test_count'].get('p_perm_two_sided')}; age rho={H['H4']['age'].get('spearman_rho')} p={H['H4']['age'].get('p_perm_two_sided')}; bonf2={H['H4']['p_bonferroni2']} holm={H['H4']['p_holm']}", flush=True)

    # Descriptive cross-instrument summary. For an OOS single-instrument wrapper,
    # preserve the same schema without assuming a second instrument exists.
    pooled = {}
    for h in ("H1", "H2", "H3", "H5", "H6"):
        pairs = [
            (
                i,
                result["instruments"][i]["hypotheses"][h]["contrast"].get("effect_R"),
                result["instruments"][i]["hypotheses"][h]["contrast"].get("n", 0),
            )
            for i in INSTRUMENTS
        ]
        valid = [(i, e, n) for i, e, n in pairs if e is not None and n]
        if valid:
            pooled[h] = {
                "effect_R_n_weighted": fmt(sum(e * n for _, e, n in valid) / sum(n for _, _, n in valid)),
                "per_instrument": {i: e for i, e, _ in valid},
                "sign_agreement": len({e > 0 for _, e, _ in valid}) <= 1,
            }
        else:
            pooled[h] = {
                "effect_R_n_weighted": None,
                "per_instrument": {i: e for i, e, _ in pairs},
                "sign_agreement": None,
            }
    result["pooled_descriptive"] = pooled
    with open(os.path.join(args.out, "p8_results.json"), "w") as fh:
        json.dump(result, fh, indent=1, sort_keys=True, default=str)
    print(f"[p8] wrote {os.path.join(args.out, 'p8_results.json')}")


if __name__ == "__main__":
    main()
