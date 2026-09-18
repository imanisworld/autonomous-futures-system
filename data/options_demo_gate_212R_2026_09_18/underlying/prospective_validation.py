"""Prospective 30m family validation for 2-1-2 REVERSAL and 1-2-2. Reuses the observer/reducer/outcome artifacts unchanged.
Views: RETROSPECTIVE (sessions <= 2026-09-15, frozen), PROSPECTIVE (sessions > cutoff), COMBINED (supplemental). Read-only.
Inputs: <dir>/coverage_events.json (all sessions), <dir>/outcomes_ALL.json (aggregate outcomes covering all sessions), <dir>/ledger.jsonl."""
import json, sys, csv, statistics, collections, os
from datetime import datetime
D = sys.argv[1]; OUT = f"{D}/prospective"; os.makedirs(OUT, exist_ok=True)
CUTOFF = "2026-09-15"; FAMS = ["STRAT_212_REVERSAL", "OTHER:strat_122"]; PASSIVE = ["OTHER:strat_inside_break"]
U20 = set("AAPL,MSFT,NVDA,TSLA,SPY,QQQ,AMZN,GOOGL,PLTR,INTC,IWM,TLT,JPM,BAC,COIN,XOM,MRK,WMT,NFLX,GE".split(","))
# Collector commits allowed to have produced ledger lines. 2672153 (#606) changed ONLY the timer time,
# the completeness rule and repair provenance; observer/reducer/outcome scripts are byte-identical to 771b6cf.
ALLOWED_SHAS = {"771b6cfb9039f5e01d3d85a2d2bdbfaf124c95e5", "26721535ff721027951712d85c76597cde18668f", "58d6c5fa25c5dd3c8b8ed2cbeda36db74f6a5efd"}  # #606 timing/completeness, #607 allow-list/repair provenance; observer/reducer/outcome scripts byte-identical
EXPECT = {"sha": ALLOWED_SHAS, "observer_version": "cov-v0.1", "reducer_version": "ep-v0.1", "outcome_version": "out-v0.1"}
P = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))
ev = json.load(open(f"{D}/coverage_events.json")); oc = json.load(open(f"{D}/outcomes_ALL.json")); EPS = oc["episodes"]
# ---------- PHASE 0: methodology epoch check from the ledger ----------
problems = []; sessions_ok = collections.OrderedDict()
for line in open(f"{D}/ledger.jsonl"):
    r = json.loads(line)
    if r.get("status") != "DONE": continue
    src = r.get("source") or {}
    drift = [k for k, v in EXPECT.items() if ((src.get("sha") not in v) if k == "sha" else r.get(k) != v)]
    if drift: problems.append(f"{r['session_date']}: methodology drift {drift}")
    if (r.get("coverage") or {}).get("missing_rows"): problems.append(f"{r['session_date']}: missing_rows {r['coverage']['missing_rows']}")
    if (r.get("outcomes") or {}).get("provider_errors"): problems.append(f"{r['session_date']}: provider_errors")
    sessions_ok[r["session_date"]] = {"observer_run": (r.get("coverage") or {}).get("run_id"), "events": (r.get("coverage") or {}).get("events"), "episodes": (r.get("outcomes") or {}).get("episodes"), "sha": src.get("sha")}
ev_sessions = sorted(set(e["session_date"] for e in ev)); ep_sessions = sorted(set(e["session_date"] for e in EPS))
print("PHASE 0 — sessions in ledger:", list(sessions_ok)); print("  sessions in events:", ev_sessions, "| in outcomes:", ep_sessions)
if set(ev_sessions) != set(ep_sessions): problems.append(f"events/outcomes session mismatch {ev_sessions} vs {ep_sessions}")
fs_delay = sorted(set(round((P(e["first_sight_at"]) - P(e["first_bar_close"])).total_seconds() / 60, 1) for e in EPS)); print("  first-sight delays (min):", fs_delay[:5])
if any(d < 17 or d > 19 for d in fs_delay): problems.append(f"first-sight delay changed: {fs_delay}")
if problems: print("STOP — METHODOLOGY / DATA BLOCKED:", problems); json.dump({"status": "METHODOLOGY / DATA BLOCKED", "problems": problems}, open(f"{OUT}/status.json", "w"), indent=1); sys.exit(2)
print("  methodology consistent: collector sha in {771b6cf, 2672153, 58d6c5f (#606/#607: collector timing/completeness/provenance only)}, cov-v0.1 / ep-v0.1 / out-v0.1, no missing rows, no provider errors")
# ---------- episode rows (identical to family_validation) ----------
def view(e, kind):
    for v in e.get("views") or []:
        k = "first_sight" if v.get("entry_at") == e.get("first_sight_at") else "mechanical"
        if k == kind and v.get("geometry") == "nearest": return v
def clock(e): h = e["first_bar_close"][11:16]; return "opening_14:00" if h == "14:00" else ("early_14:30-16:00" if h <= "16:00" else "later")
def bars_to(v, key): return round((P(v[key]) - P(v["entry_at"])).total_seconds() / 1800 + 1 / 6, 2) if v and v.get(key) and v.get("entry_at") else None
rows = []
for e in EPS:
    m = view(e, "mechanical"); f = view(e, "first_sight")
    if not m: continue
    rows.append({"view_set": "RETROSPECTIVE" if e["session_date"] <= CUTOFF else "PROSPECTIVE", "family": e["family"], "symbol": e["symbol"], "session": e["session_date"], "direction": e["direction"], "bar_close": e["first_bar_close"], "trigger": e["entry_trigger"], "invalidation": e["invalidation"], "structural_risk": e["structural_risk"], "first_sight_at": e["first_sight_at"], "first_sight_delay_min": round((P(e["first_sight_at"]) - P(e["first_bar_close"])).total_seconds() / 60, 1), "opening_bar": clock(e) == "opening_14:00", "clock": clock(e), "n_events": e["n_events"], "in_20": e["symbol"] in U20,
                 "spy_aligned": e.get("spy_aligned"), "qqq_aligned": e.get("qqq_aligned"), "daily_aligned": e.get("daily_aligned"), "hourly_aligned": e.get("hourly_aligned"),
                 "m_mfe_r": m.get("mfe_r"), "m_mae_r": m.get("mae_r"), "m_05": bool(m.get("hit_0_5r_at")), "m_1": bool(m.get("hit_1_0r_at")), "m_15": bool(m.get("hit_1_5r_at")), "m_2": bool(m.get("hit_2_0r_at")), "m_inv": bool(m.get("invalidation_hit_at")), "m_target_first": m.get("outcome") == "TARGET_FIRST", "m_stop_first": m.get("outcome") == "INVALIDATION_FIRST", "m_close_r": m.get("close_r"), "m_outcome": m.get("outcome"),
                 "f_priced": bool(f and f.get("entry_reference") is not None and f.get("outcome") not in ("FIRST_SIGHT_AFTER_CLOSE", "DATA_INVALID")), "f_mfe_r": f.get("mfe_r") if f else None, "f_mae_r": f.get("mae_r") if f else None, "f_05": bool(f and f.get("hit_0_5r_at")), "f_1": bool(f and f.get("hit_1_0r_at")), "f_15": bool(f and f.get("hit_1_5r_at")), "f_2": bool(f and f.get("hit_2_0r_at")), "f_inv": bool(f and f.get("invalidation_hit_at")), "f_stop_first": bool(f and f.get("outcome") == "INVALIDATION_FIRST"), "f_close_r": f.get("close_r") if f else None, "f_outcome": f.get("outcome") if f else None})
rate = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
med = lambda xs: round(statistics.median([x for x in xs if x is not None]), 3) if any(x is not None for x in xs) else None
def stats(pop_rows, fam, ex_open=False):
    pool = collections.defaultdict(list)
    base_rows = [r for r in pop_rows if not (ex_open and r["opening_bar"])]
    for r in base_rows: pool[(r["symbol"], r["direction"], r["clock"])].append(r)
    xs = [r for r in base_rows if r["family"] == fam]
    if not xs: return {"family": fam, "episodes": 0}
    b1 = []; bf = []; b2 = []; bs = []
    for r in xs:
        o = [x for x in pool[(r["symbol"], r["direction"], r["clock"])] if x["family"] != fam]
        if o: b1.append(sum(x["m_1"] for x in o) / len(o)); b2.append(sum(x["m_2"] for x in o) / len(o)); bs.append(sum(x["m_stop_first"] for x in o) / len(o)); fo = [x for x in o if x["f_priced"]]; bf.append(sum(x["f_1"] for x in fo) / len(fo) if fo else None)
    fp = [r for r in xs if r["f_priced"]]; bfv = [b for b in bf if b is not None]
    d = {"family": fam, "raw_occurrences": sum(r["n_events"] for r in xs), "episodes": len(xs), "sessions": len(set(r["session"] for r in xs)), "symbols": len(set(r["symbol"] for r in xs)), "long": sum(r["direction"] == "LONG" for r in xs), "short": sum(r["direction"] == "SHORT" for r in xs), "opening_bar_n": sum(r["opening_bar"] for r in xs), "non_opening_n": sum(not r["opening_bar"] for r in xs),
         "fs_n": len(fp), "fs_1R": rate([r["f_1"] for r in fp]), "fs_baseline_1R": round(100 * statistics.mean(bfv), 1) if bfv else None, "fs_2R": rate([r["f_2"] for r in fp]), "fs_stop_first": rate([r["f_stop_first"] for r in fp]), "fs_median_MFE_R": med([r["f_mfe_r"] for r in fp]), "fs_median_MAE_R": med([r["f_mae_r"] for r in fp]), "fs_median_final_R": med([r["f_close_r"] for r in fp]),
         "m_1R": rate([r["m_1"] for r in xs]), "m_baseline_1R": round(100 * statistics.mean(b1), 1) if b1 else None, "m_2R": rate([r["m_2"] for r in xs]), "m_baseline_2R": round(100 * statistics.mean(b2), 1) if b2 else None, "m_stop_first": rate([r["m_stop_first"] for r in xs]), "m_baseline_stop_first": round(100 * statistics.mean(bs), 1) if bs else None, "m_median_MFE_R": med([r["m_mfe_r"] for r in xs]), "m_median_MAE_R": med([r["m_mae_r"] for r in xs]), "m_median_final_R": med([r["m_close_r"] for r in xs]), "baseline_matched_n": len(b1)}
    d["fs_diff"] = round(d["fs_1R"] - d["fs_baseline_1R"], 1) if d["fs_1R"] is not None and d["fs_baseline_1R"] is not None else None
    d["m_diff"] = round(d["m_1R"] - d["m_baseline_1R"], 1) if d["m_1R"] is not None and d["m_baseline_1R"] is not None else None
    return d
def status_of(d, dx):
    if not d or d.get("episodes", 0) == 0: return "INSUFFICIENT PROSPECTIVE SAMPLE"
    if d["episodes"] < 30 or d["sessions"] < 3 or min(d["long"], d["short"]) == 0: return "INSUFFICIENT PROSPECTIVE SAMPLE"
    ref = dx.get("fs_diff") if dx and dx.get("fs_diff") is not None else d.get("fs_diff")
    return "PERSISTING POSSIBLE SIGNAL" if ref is not None and ref >= 5.0 else "NO LONGER SHOWING EXCESS"
summary = []; conc = []
for pop_label, pop in (("PRIMARY_20", [r for r in rows if r["in_20"]]), ("SECONDARY_148", rows)):
    for vs in ("RETROSPECTIVE", "PROSPECTIVE", "COMBINED"):
        pr = [r for r in pop if vs == "COMBINED" or r["view_set"] == vs]
        for fam in FAMS + PASSIVE:
            d = stats(pr, fam); dx = stats(pr, fam, ex_open=True)
            row = {"population": pop_label, "view_set": vs, **d, "ex_opening_episodes": dx.get("episodes", 0), "ex_opening_fs_1R": dx.get("fs_1R"), "ex_opening_fs_baseline_1R": dx.get("fs_baseline_1R"), "ex_opening_fs_diff": dx.get("fs_diff"), "ex_opening_m_1R": dx.get("m_1R"), "ex_opening_m_diff": dx.get("m_diff")}
            row["status"] = ("PASSIVE_ONLY" if fam in PASSIVE else (status_of(d, dx) if vs == "PROSPECTIVE" else "context")); summary.append(row)
            if vs == "PROSPECTIVE" and d.get("episodes"):
                xs = [r for r in pr if r["family"] == fam]; tot = sum(r["f_1"] for r in xs if r["f_priced"]) or 0
                for dim, kf in (("direction", lambda r: r["direction"]), ("session", lambda r: r["session"]), ("symbol", lambda r: r["symbol"]), ("opening_bar", lambda r: str(r["opening_bar"])), ("spy_aligned", lambda r: str(r["spy_aligned"])), ("qqq_aligned", lambda r: str(r["qqq_aligned"])), ("daily_aligned", lambda r: str(r["daily_aligned"]))):
                    for val in sorted(set(kf(r) for r in xs)):
                        cell = [r for r in xs if kf(r) == val]; cf = [r for r in cell if r["f_priced"]]
                        conc.append({"population": pop_label, "family": fam, "dimension": dim, "value": val, "episodes": len(cell), "fs_1R_hits": sum(r["f_1"] for r in cf), "fs_1R_rate": rate([r["f_1"] for r in cf]), "share_of_family_fs_1R_hits": round(100 * sum(r["f_1"] for r in cf) / tot, 1) if tot else None})
with open(f"{OUT}/prospective_family_population.csv", "w", newline="") as fh:
    pr = [r for r in rows if r["view_set"] == "PROSPECTIVE" and r["family"] in FAMS + PASSIVE]
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); [w.writerow(r) for r in pr]
with open(f"{OUT}/prospective_family_summary.csv", "w", newline="") as fh:
    keys = sorted({k for d in summary for k in d}); w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); [w.writerow(d) for d in summary]
with open(f"{OUT}/prospective_baseline_comparison.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["population", "view_set", "family", "fs_1R", "fs_baseline_1R", "fs_diff", "ex_opening_fs_diff", "m_1R", "m_baseline_1R", "m_diff", "baseline_matched_n"])
    for d in summary: w.writerow([d["population"], d["view_set"], d["family"], d.get("fs_1R"), d.get("fs_baseline_1R"), d.get("fs_diff"), d.get("ex_opening_fs_diff"), d.get("m_1R"), d.get("m_baseline_1R"), d.get("m_diff"), d.get("baseline_matched_n")])
with open(f"{OUT}/prospective_concentration.csv", "w", newline="") as fh:
    if conc: w = csv.DictWriter(fh, fieldnames=list(conc[0].keys())); w.writeheader(); [w.writerow(c) for c in conc]
    else: fh.write("no prospective episodes yet\n")
print("\n=== REQUIRED TABLE (PRIMARY 20; first-sight primary; retrospective shown beside, never merged) ===")
print(f"{'view':13} {'family':22} {'ep':>4} {'sess':>4} {'L':>3} {'S':>3} {'fs1R':>5} {'base':>5} {'diff':>5} {'exOpen':>6} {'2R':>5} {'stop1st':>7} {'medMFE':>6} status")
for d in summary:
    if d["population"] != "PRIMARY_20" or d["family"] in PASSIVE: continue
    print(f"{d['view_set']:13} {d['family']:22} {d.get('episodes',0):4} {d.get('sessions',0):4} {d.get('long',0):3} {d.get('short',0):3} {str(d.get('fs_1R')):>5} {str(d.get('fs_baseline_1R')):>5} {str(d.get('fs_diff')):>5} {str(d.get('ex_opening_fs_diff')):>6} {str(d.get('fs_2R')):>5} {str(d.get('fs_stop_first')):>7} {str(d.get('fs_median_MFE_R')):>6} {d['status']}")
pro = [r for r in rows if r["view_set"] == "PROSPECTIVE"]
print("\nprospective sessions:", sorted(set(r["session"] for r in pro)), "| prospective episodes (all families, 20):", sum(1 for r in pro if r["in_20"]), "| passive inside-break prospective (20):", sum(1 for r in pro if r["in_20"] and r["family"] in PASSIVE))
json.dump({"status": "OK", "cutoff": CUTOFF, "sessions": list(sessions_ok), "prospective_sessions": sorted(set(r["session"] for r in pro))}, open(f"{OUT}/status.json", "w"), indent=1)
