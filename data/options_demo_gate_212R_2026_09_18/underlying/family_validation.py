"""Outcome-independent unsupported-family validation. Population = every observer structural occurrence (selected by bar structure only).
Primary = 20-symbol audit universe; secondary = full 148-symbol observer corpus, reported separately. Read-only."""
import json, sys, csv, statistics, collections, os
from datetime import datetime, timedelta
D = sys.argv[1]; OUT = f"{D}/family_validation"; os.makedirs(OUT, exist_ok=True)
U20 = "AAPL,MSFT,NVDA,TSLA,SPY,QQQ,AMZN,GOOGL,PLTR,INTC,IWM,TLT,JPM,BAC,COIN,XOM,MRK,WMT,NFLX,GE".split(",")
ev = json.load(open(f"{D}/coverage_events.json")); oc = json.load(open(f"{D}/outcomes_2026-09-09_2026-09-15.json")); bars = json.load(open(f"{D}/bars.json"))
EPS = oc["episodes"]; SESS = sorted(set(e["session_date"] for e in EPS)); P = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))
FAMILIES = ["STRAT_222_REVERSAL", "STRAT_212_REVERSAL", "STRAT_222_CONTINUATION", "OTHER:strat_122", "OTHER:strat_outside_continuation", "STRAT_322_REVERSAL", "STRAT_312", "OTHER:strat_22_continuation", "OTHER:strat_inside_break", "STRAT_322_CONTINUATION", "STRAT_212_CONTINUATION"]
def view(e, kind, geom="nearest"):
    for v in e.get("views") or []:
        k = "first_sight" if v.get("entry_at") == e.get("first_sight_at") else "mechanical"
        if k == kind and v.get("geometry") == geom: return v
    return None
def bars_to(v, key):
    if not v or not v.get(key) or not v.get("entry_at"): return None
    return round((P(v[key]) + timedelta(minutes=5) - P(v["entry_at"])).total_seconds() / 1800, 2)
def clock_bucket(t): h = t[11:16]; return "opening_14:00" if h == "14:00" else ("early_14:30-16:00" if h <= "16:00" else "later")
def med(xs): xs = [x for x in xs if x is not None]; return round(statistics.median(xs), 3) if xs else None
def rate(xs): xs = [x for x in xs if x is not None]; return round(100 * sum(xs) / len(xs), 1) if xs else None

# ---------- PROOF: completeness of directional bars (20 symbols) ----------
def strat(cur, prev):
    if cur["h"] > prev["h"] and cur["l"] < prev["l"]: return "3"
    if cur["h"] <= prev["h"] and cur["l"] >= prev["l"]: return "1"
    return "2U" if cur["h"] > prev["h"] else "2D"
RAW = {(e["symbol"], e["bar_close"]) for e in ev}
missing = collections.Counter(); directional_total = 0; opening_dir = 0; opening_ev = 0
for s in U20:
    rows = sorted([b for b in bars["30m"].get(s, []) if "13:30" <= P(b["t"]).strftime("%H:%M") <= "19:30"], key=lambda b: b["t"])
    for i, b in enumerate(rows):
        day = P(b["t"]).strftime("%Y-%m-%d")
        if day not in SESS or i == 0: continue
        t = strat(b, rows[i - 1]); close = (P(b["t"]) + timedelta(minutes=30)).isoformat()
        if t in ("2U", "2D"):
            directional_total += 1
            if (s, close) not in RAW: missing[(s, close[11:16])] += 1
            if close[11:16] == "14:00": opening_dir += 1; opening_ev += (s, close) in RAW
print("=== PROOF: directional 30m RTH bars (20 symbols, sessions", SESS, ") =", directional_total, "| observer raw events for those symbols/sessions =", sum(1 for e in ev if e["symbol"] in U20), "| directional bars WITHOUT an observer event =", sum(missing.values()), dict(collections.Counter(k[1] for k in missing.elements()).most_common(5)))
print("    opening (14:00) directional bars", opening_dir, "with event", opening_ev)
print("    raw events per family (20):", collections.Counter(e["family"] for e in ev if e["symbol"] in U20).most_common())
print("    non-30m timeframe events:", sum(1 for e in ev if e.get("timeframe") != "30m"), "| symbols in corpus:", len(set(e["symbol"] for e in ev)), "| episodes total:", len(EPS), "| episodes 20:", sum(1 for e in EPS if e["symbol"] in U20))
fs_delay = [(P(e["first_sight_at"]) - P(e["first_bar_close"])).total_seconds() / 60 for e in EPS]
print("    first_sight_at minus bar close, minutes: min", round(min(fs_delay), 1), "median", round(statistics.median(fs_delay), 1), "max", round(max(fs_delay), 1), "| any first sight before bar close:", sum(1 for d in fs_delay if d < 0))

# ---------- A. FAMILY_POPULATION_MASTER (raw occurrences with episode id + episode outcome) ----------
EPI = {}
for e in EPS: EPI.setdefault((e["symbol"], e["family"], e["direction"], e["session_date"]), []).append(e)
def episode_for(event):
    cands = EPI.get((event["symbol"], event["family"], event["direction"], event["session_date"]), [])
    best = None
    for e in cands:
        fb = P(e["first_bar_close"]); bc = P(event["bar_close"])
        if fb <= bc and (bc - fb) <= timedelta(minutes=30 * (e["n_events"] - 1)) + timedelta(seconds=1):
            if best is None or P(e["first_bar_close"]) > P(best["first_bar_close"]): best = e
    return best
master = []; unmatched = 0
for ev_ in ev:
    e = episode_for(ev_)
    if not e: unmatched += 1
    m = view(e, "mechanical") if e else None; f = view(e, "first_sight") if e else None
    row = {"symbol": ev_["symbol"], "session_date": ev_["session_date"], "bar_time": ev_["bar_close"], "direction": ev_["direction"], "family": ev_["family"], "raw_occurrence_id": ev_["id"],
           "episode_id": (f"{e['symbol']}|{e['family']}|{e['direction']}|{e['first_bar_close']}" if e else None), "episode_first": (e["first_bar_close"] == ev_["bar_close"]) if e else None, "n_events_in_episode": e["n_events"] if e else None,
           "candle_sequence": ev_["sequence"], "trigger_level": ev_["entry_trigger"], "invalidation_level": ev_["invalidation"], "structural_risk": ev_["risk"],
           "setup_appeared": True, "triggered": bool(m and m.get("entry_at")), "trigger_time": m.get("entry_at") if m else None, "first_seen_time": ev_["first_sight_at"], "first_seen_delay_bars": round((P(ev_["first_sight_at"]) - P(ev_["bar_close"])).total_seconds() / 1800, 2),
           "SPY_context": ("aligned" if "spy" not in (ev_["alignment_failures"] or "").split(",") else "conflicting"), "QQQ_context": ("aligned" if "qqq" not in (ev_["alignment_failures"] or "").split(",") else "conflicting"),
           "daily_context": ("aligned" if "daily" not in (ev_["alignment_failures"] or "").split(",") else "conflicting"), "hourly_context": ("aligned" if "hourly" not in (ev_["alignment_failures"] or "").split(",") else "conflicting"),
           "MFE": m.get("mfe") if m else None, "MAE": m.get("mae") if m else None, "MFE_R": m.get("mfe_r") if m else None, "MAE_R": m.get("mae_r") if m else None,
           "hit_0_5R": bool(m and m.get("hit_0_5r_at")), "hit_1R": bool(m and m.get("hit_1_0r_at")), "hit_1_5R": bool(m and m.get("hit_1_5r_at")), "hit_2R": bool(m and m.get("hit_2_0r_at")), "invalidation_hit": bool(m and m.get("invalidation_hit_at")),
           "target_before_stop": (m.get("outcome") == "TARGET_FIRST") if m else None, "stop_before_target": (m.get("outcome") == "INVALIDATION_FIRST") if m else None,
           "bars_to_1R": bars_to(m, "hit_1_0r_at"), "bars_to_2R": bars_to(m, "hit_2_0r_at"), "bars_to_invalidation": bars_to(m, "invalidation_hit_at"), "horizon_final_R": m.get("close_r") if m else None,
           "mechanical_outcome": m.get("outcome") if m else None, "first_sight_outcome": f.get("outcome") if f else None, "first_sight_hit_1R": bool(f and f.get("hit_1_0r_at")), "first_sight_MFE_R": f.get("mfe_r") if f else None, "first_sight_final_R": f.get("close_r") if f else None,
           "in_20_universe": ev_["symbol"] in U20, "quality_flags": ";".join(e.get("quality_flags") or []) + (";" + ";".join(e.get("rr_quality_flags") or []) if e and e.get("rr_quality_flags") else "") if e else "", "source_file": "coverage_events.json+outcomes_2026-09-09_2026-09-15.json"}
    master.append(row)
with open(f"{OUT}/FAMILY_POPULATION_MASTER.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(master[0].keys())); w.writeheader(); [w.writerow(r) for r in master]
print("raw occurrences written:", len(master), "unmatched to an episode:", unmatched)

# ---------- episode-level table (primary stats) ----------
def ep_rows(pop):
    out = []
    for e in EPS:
        if not pop(e): continue
        m = view(e, "mechanical"); f = view(e, "first_sight")
        if not m: continue
        out.append({"symbol": e["symbol"], "session": e["session_date"], "family": e["family"], "dir": e["direction"], "clock": clock_bucket(e["first_bar_close"]), "n_events": e["n_events"], "risk": e["structural_risk"], "clean": e.get("clean"),
                    "spy": e.get("spy_aligned"), "qqq": e.get("qqq_aligned"), "daily": e.get("daily_aligned"), "hourly": e.get("hourly_aligned"), "align": e.get("alignment_ok"),
                    "m_out": m.get("outcome"), "m_05": bool(m.get("hit_0_5r_at")), "m_1": bool(m.get("hit_1_0r_at")), "m_15": bool(m.get("hit_1_5r_at")), "m_2": bool(m.get("hit_2_0r_at")), "m_inv": bool(m.get("invalidation_hit_at")), "m_mfe": m.get("mfe_r"), "m_mae": m.get("mae_r"), "m_close": m.get("close_r"),
                    "m_b1": bars_to(m, "hit_1_0r_at"), "m_b2": bars_to(m, "hit_2_0r_at"), "m_binv": bars_to(m, "invalidation_hit_at"), "m_fwd": m.get("forward_bars"),
                    "f_out": f.get("outcome") if f else None, "f_1": bool(f and f.get("hit_1_0r_at")), "f_2": bool(f and f.get("hit_2_0r_at")), "f_inv": bool(f and f.get("invalidation_hit_at")), "f_mfe": f.get("mfe_r") if f else None, "f_mae": f.get("mae_r") if f else None, "f_close": f.get("close_r") if f else None, "f_b1": bars_to(f, "hit_1_0r_at") if f else None, "f_priced": bool(f and f.get("entry_reference") is not None and f.get("outcome") not in ("FIRST_SIGHT_AFTER_CLOSE", "DATA_INVALID"))})
    return out
def within(b, k): return b is not None and b <= k
def summarize(rows, label):
    fam = collections.defaultdict(list)
    for r in rows: fam[r["family"]].append(r)
    # matched baseline: for each family episode, pool of OTHER-family episodes with same symbol, direction, clock bucket
    pool = collections.defaultdict(list)
    for r in rows: pool[(r["symbol"], r["dir"], r["clock"])].append(r)
    summ = []
    for f in FAMILIES:
        xs = fam.get(f, [])
        if not xs: summ.append({"population": label, "family": f, "evidence_status": "DATA_UNSUPPORTED", "appearances": 0}); continue
        base1 = []; base2 = []; basef = []; baseinv = []; basemfe = []; nb = 0
        for r in xs:
            others = [o for o in pool[(r["symbol"], r["dir"], r["clock"])] if o["family"] != f]
            if others: nb += 1; base1.append(sum(o["m_1"] for o in others) / len(others)); base2.append(sum(o["m_2"] for o in others) / len(others)); baseinv.append(sum(o["m_out"] == "INVALIDATION_FIRST" for o in others) / len(others)); basemfe.append(statistics.median([o["m_mfe"] for o in others if o["m_mfe"] is not None]) if any(o["m_mfe"] is not None for o in others) else None); fp = [o for o in others if o["f_priced"]]; basef.append(sum(o["f_1"] for o in fp) / len(fp) if fp else None)
        raw_n = sum(r["n_events"] for r in xs); fp = [r for r in xs if r["f_priced"]]
        d = {"population": label, "family": f, "appearances": raw_n, "independent_episodes": len(xs), "triggers": sum(1 for r in xs if r["m_out"] not in ("DATA_INVALID",)), "trigger_rate": 100.0, "symbols": len(set(r["symbol"] for r in xs)), "sessions": len(set(r["session"] for r in xs)), "long_count": sum(r["dir"] == "LONG" for r in xs), "short_count": sum(r["dir"] == "SHORT" for r in xs),
             "r05_rate": rate([r["m_05"] for r in xs]), "r1_rate": rate([r["m_1"] for r in xs]), "r15_rate": rate([r["m_15"] for r in xs]), "r2_rate": rate([r["m_2"] for r in xs]), "invalidation_first_rate": rate([r["m_out"] == "INVALIDATION_FIRST" for r in xs]), "target_first_rate": rate([r["m_out"] == "TARGET_FIRST" for r in xs]), "unresolved_rate": rate([r["m_out"] == "UNRESOLVED_AT_CLOSE" for r in xs]), "ambiguous_rate": rate([r["m_out"] == "AMBIGUOUS" for r in xs]),
             "median_MFE_R": med([r["m_mfe"] for r in xs]), "median_MAE_R": med([r["m_mae"] for r in xs]), "median_final_R": med([r["m_close"] for r in xs]), "median_bars_to_1R": med([r["m_b1"] for r in xs]), "r1_within_2bars": rate([within(r["m_b1"], 2) for r in xs]), "r1_within_4bars": rate([within(r["m_b1"], 4) for r in xs]), "r1_within_6bars": rate([within(r["m_b1"], 6) for r in xs]),
             "first_sight_priced_n": len(fp), "first_sight_1R_rate": rate([r["f_1"] for r in fp]), "first_sight_2R_rate": rate([r["f_2"] for r in fp]), "first_sight_inval_first_rate": rate([r["f_out"] == "INVALIDATION_FIRST" for r in fp]), "first_sight_median_MFE_R": med([r["f_mfe"] for r in fp]), "first_sight_median_final_R": med([r["f_close"] for r in fp]),
             "baseline_matched_n": nb, "baseline_1R_rate": (round(100 * statistics.mean(base1), 1) if base1 else None), "baseline_2R_rate": (round(100 * statistics.mean(base2), 1) if base2 else None), "baseline_inval_first_rate": (round(100 * statistics.mean(baseinv), 1) if baseinv else None), "baseline_median_MFE_R": med(basemfe), "baseline_first_sight_1R_rate": (round(100 * statistics.mean([b for b in basef if b is not None]), 1) if any(b is not None for b in basef) else None)}
        d["difference_vs_baseline_1R_pp"] = round(d["r1_rate"] - d["baseline_1R_rate"], 1) if d["r1_rate"] is not None and d["baseline_1R_rate"] is not None else None
        d["difference_vs_baseline_first_sight_1R_pp"] = round(d["first_sight_1R_rate"] - d["baseline_first_sight_1R_rate"], 1) if d["first_sight_1R_rate"] is not None and d["baseline_first_sight_1R_rate"] is not None else None
        # top concentration
        by_sym = collections.Counter(r["symbol"] for r in xs if r["m_1"]); by_day = collections.Counter(r["session"] for r in xs if r["m_1"]); tot1 = sum(r["m_1"] for r in xs) or 1
        d["top_symbol_share_of_1R_hits"] = round(100 * by_sym.most_common(1)[0][1] / tot1, 1) if by_sym else None; d["top_session_share_of_1R_hits"] = round(100 * by_day.most_common(1)[0][1] / tot1, 1) if by_day else None
        d["opening_bar_share"] = rate([r["clock"] == "opening_14:00" for r in xs]); d["r1_rate_opening"] = rate([r["m_1"] for r in xs if r["clock"] == "opening_14:00"]); d["r1_rate_later"] = rate([r["m_1"] for r in xs if r["clock"] != "opening_14:00"])
        n = len(xs); status = "INSUFFICIENT" if n < 30 else ("DESCRIPTIVE_ONLY" if n < 80 else "ENOUGH_FOR_FOLLOW-UP")
        d["evidence_status"] = status; summ.append(d)
    return summ, fam
ROWS20 = ep_rows(lambda e: e["symbol"] in U20); ROWS148 = ep_rows(lambda e: True)
S20, FAM20 = summarize(ROWS20, "PRIMARY_20"); S148, _ = summarize(ROWS148, "SECONDARY_148")
with open(f"{OUT}/FAMILY_SUMMARY.csv", "w", newline="") as fh:
    keys = sorted({k for d in S20 + S148 for k in d}); w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); [w.writerow(d) for d in S20 + S148]
for label, S in (("PRIMARY 20 symbols", S20), ("SECONDARY 148 symbols", S148)):
    print(f"\n=== FAMILY SUMMARY — {label} (independent episodes, mechanical view; session-close horizon) ===")
    print(f"{'family':32} {'raw':>4} {'ep':>4} {'sym':>3} {'1R%':>5} {'2R%':>5} {'inv1st%':>7} {'unres%':>6} {'medMFE':>6} {'medMAE':>6} {'medClose':>8} {'1R<=4b':>6} {'fs_n':>4} {'fs1R%':>5} {'base1R%':>7} {'diff':>5} {'fs_base':>7} {'fsdiff':>6} {'topSym%':>7} {'open%':>5} status")
    for d in S:
        if d.get("appearances", 0) == 0: print(f"{d['family']:32} DATA_UNSUPPORTED"); continue
        print(f"{d['family']:32} {d['appearances']:4} {d['independent_episodes']:4} {d['symbols']:3} {d['r1_rate']:5} {d['r2_rate']:5} {d['invalidation_first_rate']:7} {d['unresolved_rate']:6} {d['median_MFE_R']:6} {d['median_MAE_R']:6} {d['median_final_R']:8} {d['r1_within_4bars']:6} {d['first_sight_priced_n']:4} {str(d['first_sight_1R_rate']):5} {str(d['baseline_1R_rate']):7} {str(d['difference_vs_baseline_1R_pp']):5} {str(d['baseline_first_sight_1R_rate']):7} {str(d['difference_vs_baseline_first_sight_1R_pp']):6} {str(d['top_symbol_share_of_1R_hits']):7} {str(d['opening_bar_share']):5} {d['evidence_status']}")
allr = ROWS20; print(f"\n  ALL directional episodes (20): n={len(allr)} 1R {rate([r['m_1'] for r in allr])}% 2R {rate([r['m_2'] for r in allr])}% inval-first {rate([r['m_out']=='INVALIDATION_FIRST' for r in allr])}% medMFE {med([r['m_mfe'] for r in allr])} medClose {med([r['m_close'] for r in allr])} | first-sight priced {sum(r['f_priced'] for r in allr)} 1R {rate([r['f_1'] for r in allr if r['f_priced']])}%")
print("  outcome distribution (20, mechanical):", collections.Counter(r["m_out"] for r in ROWS20).most_common()); print("  first-sight outcome distribution (20):", collections.Counter(r["f_out"] for r in ROWS20).most_common())
print("  quality-flagged (rr) episodes (20):", sum(1 for e in EPS if e["symbol"] in U20 and e.get("rr_quality_flags")), "clean:", sum(1 for e in EPS if e["symbol"] in U20 and e.get("clean")))

# ---------- C. BASELINE_COMPARISON (strata-level) ----------
with open(f"{OUT}/BASELINE_COMPARISON.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["population", "family", "symbol", "direction", "clock_bucket", "family_n", "family_1R", "family_2R", "other_families_n", "other_1R", "other_2R"])
    for label, rows in (("PRIMARY_20", ROWS20), ("SECONDARY_148", ROWS148)):
        strata = collections.defaultdict(list)
        for r in rows: strata[(r["symbol"], r["dir"], r["clock"])].append(r)
        for (s, d, c), xs in sorted(strata.items()):
            for f in FAMILIES:
                a = [r for r in xs if r["family"] == f]; b = [r for r in xs if r["family"] != f]
                if a: w.writerow([label, f, s, d, c, len(a), sum(r["m_1"] for r in a), sum(r["m_2"] for r in a), len(b), sum(r["m_1"] for r in b), sum(r["m_2"] for r in b)])
# ---------- D. CONTEXT_STRATIFICATION (descriptive, 20) ----------
with open(f"{OUT}/CONTEXT_STRATIFICATION.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["family", "stratum", "value", "n", "1R_rate", "2R_rate", "inval_first_rate", "median_MFE_R", "first_sight_1R_rate"])
    print("\n=== CONTEXT STRATIFICATION (20, descriptive; cells n<10 omitted) ===")
    for f in FAMILIES:
        xs = FAM20.get(f, [])
        for stratum, keyf in (("SPY", lambda r: r["spy"]), ("QQQ", lambda r: r["qqq"]), ("daily", lambda r: r["daily"]), ("hourly", lambda r: r["hourly"]), ("clock", lambda r: r["clock"]), ("direction", lambda r: r["dir"])):
            for val in sorted(set(keyf(r) for r in xs), key=str):
                cell = [r for r in xs if keyf(r) == val]
                if len(cell) < 10: continue
                fp = [r for r in cell if r["f_priced"]]
                w.writerow([f, stratum, val, len(cell), rate([r["m_1"] for r in cell]), rate([r["m_2"] for r in cell]), rate([r["m_out"] == "INVALIDATION_FIRST" for r in cell]), med([r["m_mfe"] for r in cell]), rate([r["f_1"] for r in fp])])
                if f in ("STRAT_222_REVERSAL", "STRAT_212_REVERSAL", "STRAT_222_CONTINUATION", "OTHER:strat_22_continuation", "OTHER:strat_122", "OTHER:strat_outside_continuation"): print(f"  {f:30} {stratum:9} {str(val):22} n={len(cell):3} 1R={rate([r['m_1'] for r in cell])} 2R={rate([r['m_2'] for r in cell])} inv1st={rate([r['m_out']=='INVALIDATION_FIRST' for r in cell])} fs1R={rate([r['f_1'] for r in fp])}")
# ---------- E. CONCENTRATION (20) ----------
with open(f"{OUT}/SESSION_SYMBOL_CONCENTRATION.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["family", "dimension", "value", "episodes", "1R_hits", "share_of_family_1R_hits"])
    print("\n=== CONCENTRATION (20): share of a family's 1R hits from its top session / symbol / direction / clock ===")
    for f in FAMILIES:
        xs = FAM20.get(f, []); tot = sum(r["m_1"] for r in xs)
        if not xs or not tot: continue
        line = []
        for dim, keyf in (("session", lambda r: r["session"]), ("symbol", lambda r: r["symbol"]), ("direction", lambda r: r["dir"]), ("clock", lambda r: r["clock"])):
            c = collections.Counter(keyf(r) for r in xs if r["m_1"]); n = collections.Counter(keyf(r) for r in xs)
            for val, hits in c.most_common(): w.writerow([f, dim, val, n[val], hits, round(100 * hits / tot, 1)])
            top = c.most_common(1)[0]; line.append(f"{dim}={top[0]} {round(100*top[1]/tot)}%")
        print(f"  {f:32} 1R hits={tot:3} | " + " | ".join(line))
json.dump({"primary": S20, "secondary": S148}, open(f"{OUT}/summary.json", "w"), indent=1)
print("\nartifacts:", sorted(os.listdir(OUT)))
