"""Out-of-sample run of the UNCHANGED MES 1-2-2 test (frozen #373 control config
+ #337 isolated config) on the 2026-07-24..2026-09-08 Polygon window.
Evidence only. Imports the proof module so the config construction is byte-identical."""
import json, os, sys, tempfile, dataclasses
from pathlib import Path
REPO = Path(sys.argv[1]); OOS = Path(sys.argv[2]); OUT = Path(sys.argv[3])
os.environ["AFS_122_CORPUS"] = str(OOS)
sys.path.insert(0, str(REPO))
import scripts.mes_122_fallback_full_engine_proof as P
assert P.CORPUS == OOS, P.CORPUS

def refresh_outcomes(run, log_dir):
    """The proof's _run reads each day's journal right after that day is replayed,
    so an OUTCOME written on a later day (target/stop hit after the signal date)
    is missed and the trade shows as TRADE_UNRESOLVED. Re-read final journals."""
    for path in sorted(Path(log_dir).glob("journal_*.jsonl")):
        for entry in P._json_lines(path):
            if entry.get("type") == "OUTCOME":
                outcome = entry.get("outcome") or {}
                oid = outcome.get("paper_order_id")
                if oid:
                    run["outcomes"][str(oid)] = outcome
    return run

def strat122_rows(run):
    rows = []
    for bar_ts, entry in sorted(run["decisions"].items()):
        setup = entry.get("setup") or {}
        if setup.get("strategy") != P.STRATEGY:
            continue
        rows.append({"bar_ts": bar_ts, "date": bar_ts[:10], **P._classify(run, bar_ts)})
    return rows

def preempted_rows(iso, ctl):
    out = []
    for r in strat122_rows(iso):
        if r["classification"] != "TRADE_RESOLVED": continue
        c = P._classify(ctl, r["bar_ts"])
        if c["classification"] != "TRADE_RESOLVED":
            out.append({"bar_ts": r["bar_ts"], "isolated": r, "control": c})
    return out

def metrics(rows):
    return P._metrics([{"date": r["date"], "x": r} for r in rows], "x")

control_cfg = P._frozen_373_config()
isolated_cfg = dataclasses.replace(control_cfg, enabled_concepts=["strat_212", "strat_122"], disabled_concepts_per_instrument={})
with tempfile.TemporaryDirectory(prefix="mes122_oos_") as tmp:
    root = Path(tmp)
    print("[pass 1] isolated #337 config (OOS)", flush=True)
    iso = P._run(isolated_cfg, root / "isolated", treatment=False)
    print("[pass 2] frozen #373 production control (OOS)", flush=True)
    ctl = P._run(control_cfg, root / "control", treatment=False)
    iso = refresh_outcomes(iso, root / "isolated")
    ctl = refresh_outcomes(ctl, root / "control")
    # keep journals for audit
    import shutil; shutil.copytree(root, OUT.parent / "journals", dirs_exist_ok=True)

iso_rows = strat122_rows(iso); ctl_rows = strat122_rows(ctl)
def summarize(rows):
    from collections import Counter
    return dict(Counter(r["classification"] for r in rows))
all_trades_ctl = [(ts, (e.get("setup") or {}).get("strategy"), e.get("decision")) for ts, e in sorted(ctl["decisions"].items()) if e.get("decision") == "TRADE"]
report = {
    "window": {"start": "2026-07-24", "end": "2026-09-08", "days": len(sorted((OOS/"MES").glob("MES_*.jsonl")))},
    "corpus": str(OOS),
    "config_source": "scripts.mes_122_fallback_full_engine_proof._frozen_373_config (unchanged)",
    "isolated_classes": summarize(iso_rows),
    "control_classes": summarize(ctl_rows),
    "isolated_metrics": metrics(iso_rows),
    "control_metrics": metrics(ctl_rows),
    "isolated_rows": iso_rows,
    "control_rows": ctl_rows,
    "preempted_in_control": preempted_rows(iso, ctl),
    "control_all_TRADE_decisions": all_trades_ctl,
    "decision_bars_isolated": len(iso["decisions"]), "decision_bars_control": len(ctl["decisions"]),
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(report, indent=2, default=str) + "\n")
print(json.dumps({k: report[k] for k in ["window","isolated_classes","control_classes","isolated_metrics","control_metrics"]}, indent=2, default=str))
print("preempted_in_control:", len(report["preempted_in_control"]), "control TRADE decisions (all strategies):", len(all_trades_ctl))
