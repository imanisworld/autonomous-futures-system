# Raw evidence for docs/options-etf-expression-research-2026-10-10.md

- `scratch-scripts/`: the session research scripts as run. They read a Polygon key from `$TMPDIR/pk`; the key is not stored here.
  - `run.py`: first QQQ 4HR test (0–1DTE and ~7DTE).
  - `opt_bt.py`: variant engine. Args: lane, ETF, ratio, min days, spread width, tag, OTM %.
  - `scan_resim.py` + `polylib.py`: scanner time-exit re-sim.
- `variant-results/`: per-trade JSON for each variant.
  - `res_mnq_qqq_7d`, `res_mes_spy_7d`, `res_mnq_qqq_7d_v5`, `res_322_qqq_7d`, `res_*_otm1`.
  - `results.json`: the first run.
  - `o_*.txt` / `out.txt`: summaries. FAIL lines are transient download errors.
- `scanner-resim/`: per-setup rows and summaries for the scanner re-sim (`same` = scanner's own contract, `wk` = ~7DTE ATM).
- `forward-tracker/`: `scripts/options_4hr_etf_forward.py` outputs.
  - Parity run Jan–Jun 2026 (41/41 signals match the audit).
  - Out-of-sample run Jul 24 – Oct 9 2026: 9 trades, −$384.
