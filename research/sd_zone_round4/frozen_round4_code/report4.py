import json, sys
R = json.load(open(sys.argv[1]))
def f(x, d=2):
    if x is None: return "—"
    if isinstance(x, float): return "inf" if x == float("inf") else f"{x:.{d}f}"
    return str(x)
print("## Funnel (all periods; IS/OOS split by zone creation day in parentheses for HOLD and H2 trades)\n")
print("| inst | def | zones | median width pts | died before leaving | never left | armed | no retest | retested | broken | no hold | HOLD (IS/OOS) | hold entry in RTH window | H2 trades after 1-pos (IS/OOS) | T2 fills | null mean holds | null mean H2 trades |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for k, v in R.items():
    i, n = k.split(":"); a = v["funnel_ALL"]; fi = v["funnel_IS"]; fo = v["funnel_OOS"]
    print(f"| {i} | {n} | {a['zones']} | {f(v['median_width_pts'])} | {a['dead_before_leaving']} | {a['never_left']} | {a['armed']} | {a['no_retest']} | {a['retested']} | {a['broken']} | {a['no_hold']} | {a['hold']} ({fi['hold']}/{fo['hold']}) | {a['hold_entry_in_window']} | {a['H2_trades_1pos']} ({fi['H2_trades_1pos']}/{fo['H2_trades_1pos']}) | {a['T2_fills']} | {f(v['null_mean_holds'],1)} | {f(v['null_mean_H2_trades'],1)} |")
T = {"H2": "HOLD entry, target 2R (PRIMARY)", "H15": "HOLD entry, target 1.5R (declared variant)", "T2": "TOUCH fill (limit at near edge, no hold), 2R — comparison"}
for m, title in T.items():
    print(f"\n## {m} — {title}\n")
    print("| inst | def | IS n | IS PF | IS net $ | IS maxDD $ | OOS n | OOS win% | OOS PF | OOS exp $ | OOS net $ | OOS maxDD $ | OOS H1/H2 net $ (n) | OOS ex-top3 $ | stop/target/close | median R pts | null PF med / p95 | OOS PF pct | verdict |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in R.items():
        i, n = k.split(":"); a = v[f"{m}_IS"]; o = v[f"{m}_OOS"]
        oth = o['trades'] - o['stopped'] - o['target']
        wr = f"{100*o['winrate']:.0f}" if o['winrate'] is not None else "—"
        print(f"| {i} | {n} | {a['trades']} | {f(a['pf'])} | {f(a['net'],0)} | {f(a['maxdd'],0)} | {o['trades']} | {wr} | {f(o['pf'])} | {f(o['exp'],1)} | {f(o['net'],0)} | {f(o['maxdd'],0)} | {f(o['half1_net'],0)} ({o['half1_n']}) / {f(o['half2_net'],0)} ({o['half2_n']}) | {f(o['net_ex_top3'],0)} | {o['stopped']}/{o['target']}/{oth} | {f(o['median_R_pts'])} | {f(o['null_pf_median'])} / {f(o['null_pf_p95'])} | {f(o['pctile_pf'],1)} | {o['verdict']} |")
print("\n## Value of waiting for the hold: H2 minus T2 on the same zones (OOS)\n")
print("| inst | def | H2 n / PF / net $ | T2 n / PF / net $ | net difference (H2 − T2) $ |")
print("|---|---|---|---|---|")
for k, v in R.items():
    i, n = k.split(":"); h = v["H2_OOS"]; t = v["T2_OOS"]
    print(f"| {i} | {n} | {h['trades']} / {f(h['pf'])} / {f(h['net'],0)} | {t['trades']} / {f(t['pf'])} / {f(t['net'],0)} | {f(h['net']-t['net'],0)} |")
