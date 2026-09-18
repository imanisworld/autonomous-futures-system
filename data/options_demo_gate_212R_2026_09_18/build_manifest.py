import hashlib, json, sys, subprocess, csv
from pathlib import Path
E = Path(sys.argv[1]); U = E/"underlying"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
head = subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip()
rows = list(csv.DictReader(open(U/"FAMILY_POPULATION_MASTER.csv")))
r212 = [r for r in rows if r["family"]=="STRAT_212_REVERSAL" and r["in_20_universe"] in ("True","1","true")]
man = {
  "manifest_version": "underlying-v1", "strategy_family": "STRAT_212_REVERSAL",
  "source": "09-16 outcome-independent family validation (session 4c018889 scratchpad missed-signal-audit/family_validation) + prospective_validation 09-16/09-17",
  "underlying_bar_provider": "options coverage observer (alert_ranker/coverage_observer.py), 30m RTH bars, 20-symbol universe; collector pinned 771b6cf at collection time",
  "classifier_files_at_code_sha": {f: sha(f) for f in ["alert_ranker/coverage_observer.py","strategy/strat_classifier.py"]},
  "code_sha": head,
  "population": {"master_rows_all_families": len(rows), "STRAT_212_REVERSAL_primary20_rows": len(r212),
                 "sessions_retrospective": ["2026-09-09","2026-09-10","2026-09-11","2026-09-14","2026-09-15"],
                 "sessions_prospective": ["2026-09-16","2026-09-17"]},
  "option_quote_rows": 0,
  "note": "UNDERLYING ONLY. No option chain, quote, contract or premium data exists for STRAT_212_REVERSAL episodes.",
  "files": {p.name: {"sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(U.iterdir())},
}
(E/"underlying_manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True)+"\n")
print("manifest: 212R rows", len(r212), "| code_sha", head[:7])
