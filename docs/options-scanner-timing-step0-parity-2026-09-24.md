# Options scanner timing replay — Step 0 parity result

trial_id: `T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01`
prereg: `docs/prereg-options-scanner-timing-2026-09-24.md` (merged `b5f4b7f`, #974)

**Step 0 verdict: PASS.** Feasibility/parity check only. This is not a scored
result and not ledger-governed evidence; no outcome, P&L or underlying structural
expectancy in R was computed. Scoring (v1/v2/v3, scoring-window data) is **not**
approved and has not been started.

## What was run

- Evaluator: `scripts/research/options_timing_replay.py` (Step 0 only; refuses any
  variant other than `v0_baseline_sip_960_close_confirm`; refuses any bar request
  or scan time at/after `2026-09-23T14:07:08Z`).
- The replay drives the real, unmodified `alert_ranker.scanner.OptionsScanner`
  (all wrappers) with the production scanner's non-secret config. Only inputs are
  substituted:
  - bars: Alpaca SIP 1-minute bars aggregated to clock-aligned 30Min, served only
    once fully closed by each scan's information cutoff (now − 960 s);
  - calendar: local `nyse_session_for` (no calendar API call);
  - quote: close of the last completed SIP 1-minute bar at the scan time;
  - option chains: unavailable → only rows already past the late-entry gate are
    affected, and those are compared as one class;
  - Discord: no webhook and an HTTP transport that refuses every request.
- Scan times and per-cycle tickers: exactly the 409 production scheduled cycles
  in the window (6 tickers in V1-EPOCH-1, 20 after), with the 2 episode blocks
  that pre-date the window seeded from production.

## Data (Step 0 approval scope only)

- Alpaca historical SIP 1-minute bars, 20 symbols (watchlist incl. SPY/QQQ),
  `2026-09-05T17:00:00Z` → `2026-09-23T14:07:07Z` — the first 30Min bucket inside
  the scanner's 10-day lookback for the first scan, through the exclusion boundary.
  164,806 bars; kept outside the repository.
- Production scanner rows 2026-09-15T16:50Z → 2026-09-23T14:07:08Z, read-only
  (`mode=ro`), reason fields only: 15,039 rows.
- One IEX entitlement probe (below).

## Parity

Comparison class per row (frozen in the evaluator before the first run):
SESSION_NOT_STARTED, NO_SESSION_BARS, BAR_CONTEXT_OTHER, NO_SETUP, SETUP_FORMING,
SETUP_PROOF_INCOMPLETE, OBSERVER, LEVELS_INVALID, ENTRY_LATE_FIRST,
ENTRY_LATE_BLOCKED, PAST_LATE_GATE.

| Measure | Rows | Agree | Agreement |
|---|---:|---:|---:|
| **Verdict metric** — full window, key (scan time, ticker, source, pattern, direction) | 15,337 | 14,736 | **96.08 %** |
| Same key, code-equivalent segment (from 2026-09-21T04:21:21Z) | 6,211 | 6,063 | 97.62 % |
| Same key, earlier segment | 9,126 | 8,673 | 95.04 % |
| Prereg key (no direction), full window | 15,039 | 15,031 | 99.95 % |
| Prereg key (no direction), code-equivalent segment | 6,137 | 6,137 | 100.00 % |

Threshold ≥ 95 % → **PASS** on the stricter verdict metric.

### Where the disagreements are

- **287 direction-only pairs** across the window (74 of them in the
  code-equivalent segment, where they are the only disagreements): same ticker, source, pattern and class; only the scorer's
  VWAP/EMA20 direction label differs (almost all `UNKNOWN` ↔ `LONG`/`SHORT`). That
  label comes from the live price, and the replay's SIP-minute proxy sometimes
  sits on the other side of the band from the production Public quote. It does
  not change any reason class.
- **8 class differences**, all before 2026-09-21T04:21Z: ENTRY_LATE_BLOCKED ↔
  ENTRY_LATE_FIRST (3), ENTRY_LATE ↔ LEVELS_INVALID (3), ENTRY_LATE_FIRST →
  PAST_LATE_GATE (2). These sit in the segment produced by code that pre-dates
  #869; the replay deliberately runs the current production code (v0).

### Code identity across the window

Every scanner release from `dd3aa9d` (restart 2026-09-21T04:21:21Z) through the
deployed `5b7be0f7` has byte-identical decision modules except `discord.py`
(message wording only) and a `session_calendar.py` Easter-date fix (irrelevant in
September). Releases before that differ in `scorer.py`, `paper_v1.py`,
`scanner_legacy.py` and others (#866, #869). The prereg-key agreement on the
code-equivalent segment is 100 %.

## IEX entitlement probe (single request)

`GET /v2/stocks/bars` SPY 1Min `feed=iex`, window ending at the request time
(2026-09-24T02:37:44Z): **HTTP 200, 398 bars, newest bar 2026-09-23T20:11Z.** The
production account is entitled to query IEX up to "now". The probe ran outside
market hours, so intraday IEX freshness is **not** demonstrated; that matters only
for v1/v3 and is not needed for Step 0.

## Caveats

- The quote proxy is a 1-minute close, not the live Public quote; in the
  code-equivalent segment the direction label is the only place it showed up; in
  the earlier segment the 8 class differences cannot be split between the proxy
  and the older code. In the scoring window it also
  feeds remaining-R at detection, identically across all variants.
- Rows past the late-entry gate (65 prod / 67 replay) cannot be checked beyond
  "reached the contract stage".
- Production restarts inside the window are reproduced only through the recorded
  scan times; nothing else about process state is modeled except the episode
  blocks.

## Not done (needs separate GO)

v1/v2/v3 implementation · scoring-window data pull (2025-10-01 → 2026-08-31) ·
any outcome or underlying structural expectancy in R · anything at/after
2026-09-23T14:07:08Z · any scanner, config, env, Discord, watchlist, risk, alert,
systemd or box change.
