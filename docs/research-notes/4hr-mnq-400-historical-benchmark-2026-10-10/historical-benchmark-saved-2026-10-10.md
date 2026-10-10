# Saved benchmark — what worked (4HR MNQ)

**Status:** Historical benchmark only. Not forward P&L. Do not rescore these dates as a new trial.

## The cell that paid on honest bracket fills

| Field | Value |
|---|---|
| Strategy | `strat_4hr_retrigger` |
| Instrument | MNQ |
| Stop cap | ≤ 400 ticks ($200 per contract) |
| Minimum R:R | ≥ 1.0 |
| Contracts | 1 |
| Entry model | `ioc_limit` at decision-bar close, 8 marketable ticks, 1 adverse tick |
| Exit | Static documented bracket (no runner) |
| Isolated ledger | `wide_stop_4k`, starting balance $4,000, daily-loss floor $400 |

## Reproduced from committed artifact (2026-10-09 / 2026-10-10)

**Source:** `scripts/edge_decomposition_audit_results_candidates.jsonl.gz`  
**Lane filter:** `4hr_mnq`, stop ≤ 400 ticks, R:R ≥ 1.0  
**Window (consumed — never rescored for this trial):** 2024-07-09 through 2026-06-03  

| Metric | Value |
|---|---|
| Resolved trades | 36 |
| Wins / losses | 20 / 16 |
| Net | +$3,076.72 |
| Profit factor | 3.125 |
| H1 net | +$1,433.86 |
| H2 net | +$1,642.86 |
| Top-3-month share of net | 66.8% |
| Losses over $150 | 2 |

Prior 300-tick isolated contract (superseded in source): 32 trades, +$1,901.14, PF 2.313 (top-3 months > 100% of net).

## Source wiring that implements this cell (not deployed)

| Item | Location |
|---|---|
| Branch | `research/4hr-mnq-400-forward-20261009` |
| Head (2026-10-10) | `0c5db3d` — 4HR-only fills; 3-2-2 shadow |
| Approved forward spec | `docs/research-experiment-specs/E-2026-10-09-4hr-mnq-400-forward-01.json` |
| Prereg | `docs/prereg-4hr-mnq-400-forward-2026-10-09.md` |
| Ledger caps | `context/wide_stop_ledger_paper.py` → `wide_stop_4k` |
| Offline pins | `scripts/wide_stop_ledger_offline_expectation.py` → `BRACKET_CELLS["wide_stop_4k"]` |

**Forward collection:** eligible sessions start **2026-10-12**. Sessions through **2026-10-09** inclusive are ineligible.

## What stays off (losers on the same artifact)

Do not re-enable fills for: inverse ORB (50-tick stop), VWAP hold (~30-tick stop), ORB breakout/reclaim written brackets, failed-breakdown upside-down R:R, ORB reclaim MES wrong-way. The $1,500 book’s 120-tick cap and 2.0 R:R floor still exclude this cell on the real book.

## Related fixes (verified, not merged)

- #1208 / #1209 / #1210 — honest reference fills and actual bracket R:R; Ubuntu CI green on listed heads. No merge or deploy from this save.
