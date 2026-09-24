# Options scanner detection-timing replay — preregistration

**RESEARCH / OFFLINE REPLAY ONLY. NO EXECUTION CHANGE. NO PRODUCTION CHANGE.**

trial_id: `T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01`

This plan is frozen before any replay result is scored. Ledger entry:
`docs/research-trial-ledger.jsonl`; frozen variant manifest:
`docs/research-trial-manifests/T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01.json`.

## What this PR may and may not change

This preregistration PR is **docs and ledger only**. It may not change production code,
environment files, systemd units, scanner config, Discord routing, the watchlist, risk
rules or alert rules. It does not deploy, restart or edit anything on the box.

Not authorized by this document (each needs its own explicit operator GO):

- the offline evaluator code;
- any data pull beyond the Step 0 read-only feasibility checks below;
- any scanner timing, alert-rule, risk-rule or Discord-routing change.

## Background

A 2026-09-24 read-only why-no-alert audit found that the options scanner is safe
(advisory-only; no reachable order path) and that Discord delivery works. No alert has
been sent since the 2026-09-07 smoke post because every row is suppressed upstream with a
recorded reason. A large share of those reasons are timing-driven:

- the scanner produces no setup rows for roughly the first 50 minutes of each regular
  session (13:30–14:20Z during EDT);
- 30m setups are recognised only after the breakout bar has closed **and** the 960 s data
  buffer has elapsed, so the first scan that sees a trigger is 16–46 minutes after the
  underlying crossed it;
- the first late observation blocks the rest of that episode.

## Hypothesis

- **H1.** Detection timing, not setup quality, is the main reason otherwise-valid setups
  are classified `ENTRY_LATE`. Earlier detection using only data that would have been
  available in real time yields materially more on-time alert-eligible rows with at least
  1R remaining, without worse underlying structural expectancy in R and without a high
  false-trigger rate.
- **H0.** Earlier detection mainly adds false triggers or already-exhausted setups; on-time
  eligible rows and underlying structural expectancy in R do not improve.

## Current behaviour (verified at `main@45d1a95`, deployed scanner release `5b7be0f7`)

- Bars: Alpaca SIP (`OPTIONS_BAR_CONTEXT_FEED=sip`). SIP is 15-minute delayed on the
  current entitlement and a request inside that window fails the whole call, hence
  `sip_delay_buffer_seconds=960` (`alert_ranker/config.py:137-140, 273`).
- Information cutoff `now − 960 s` (`alert_ranker/bar_context.py:259`); only bars closed at
  or before the cutoff are used (`alert_ranker/causal_bars.py:116`).
- Open blind spot: `session_not_started` while the cutoff precedes 09:30 ET
  (`alert_ranker/multisetup_scanner.py:168`); then `no_session_bars` until the first 30m
  bar (09:30–10:00 ET) is visible at about 10:16 ET.
- 30m 2-1-2 is `TRIGGERED` only after the breakout bar closes.
- Daily 2-2 / 3-2-2 triggers derive from the prior completed daily bar but are evaluated
  only once the 30m session context exists; market alignment needs session bars.
- Late entry: `ENTRY_LATE` when remaining reward from the live price is below 1.0R, price is
  past target, or past stop (`alert_ranker/paper_v1.py:378-404`); the first late
  observation writes an episode block.
- Scan cadence 5 minutes.

## Variants (frozen; exactly four)

Each variant changes **only when data becomes visible**. Setup definitions, alignment
definition, late-entry rule, risk and contract rules are identical across variants. Scan
cadence stays 5 minutes in every variant.

| ID | Definition | Isolates |
|---|---|---|
| `v0_baseline_sip_960_close_confirm` | Current production: SIP bars, 960 s buffer, trigger after bar close | reference |
| `v1_iex_feed_60s_close_confirm` | Same logic; bars from Alpaca IEX with a 60 s buffer | cost of the 16-minute delay |
| `v2_daily_at_open_live_cross` | Daily 2-2 / 3-2-2 only: evaluate from 09:30 ET; trigger at the first scan where the live price has crossed the daily trigger; alignment uses the **same function** on the latest bars causally available at that scan (including the prior session); a missing input fails closed | 50-minute open blind spot for Daily setups |
| `v3_30m_intrabar_live_cross` | 30m 2-1-2 only: inside bar must be complete (IEX, 60 s buffer); trigger at the first scan where the live price crosses the inside-bar high/low, without waiting for the breakout bar to close | close-confirmation wait |

In replay, the live price at a scan time is the close of the last completed SIP 1-minute
bar at or before that scan time. No other variants, buffers, cadences or thresholds may
be added after this document is merged.

## Data

- Source: Alpaca historical 1-minute bars, SIP and IEX, for the 20 tickers on the current
  scanner watchlist plus SPY and QQQ. SIP history older than 15 minutes is fully
  entitled and is the ground truth.
- **Step 0 — feasibility and parity (required before any scoring):**
  1. Replay `v0_baseline_sip_960_close_confirm` over **2026-09-15T16:50:00Z →
     2026-09-23T14:07:08Z** and reproduce the recorded scanner rows (ticker, 30m bucket,
     pattern, reason class) at **≥ 95 %** agreement.
  2. One read-only probe confirming the current Alpaca entitlement serves real-time IEX
     bars.
  3. Either check failing ⇒ trial status **BLOCKED**; no scoring.
  Step 0 reads production rows only for parity (reason classes), never outcomes.
- **Scoring window:** **2025-10-01 → 2026-08-31** regular sessions, split chronologically
  into two halves (H1 = first half of sessions, H2 = second half).
- **Excluded completely** from replay and scoring:
  - everything at or after **2026-09-23T14:07:08Z** (start of the #949 forward cohort),
    and in particular everything from 2026-09-24 onward, so this trial cannot contaminate
    #949 or any forward observation;
  - the 2026-09-16 → 2026-09-18 postmortem rows.

## Metrics (per variant, per half)

1. **Eligible rows** — rows passing every gate that can be replayed, through the
   late-entry gate. Option-chain and contract-quality gates cannot be replayed without
   historical option quotes; they are reported as *not evaluated*, never assumed to pass.
2. **Late blocks** — `ENTRY_LATE` count and rate, split into past-target, past-stop and
   remaining-R-below-1.
3. **Target before detection** — target touched between the true trigger (first SIP
   1-minute cross) and detection.
4. **False positives** (`v1`, `v3`) — a variant trigger that is not a trigger under SIP
   completed-bar classification of the same bar, or whose triggering bar completes as an
   outside (3) bar.
5. **R remaining at first detection** — median and quartiles; plus detection latency in
   minutes from true cross to detection.
6. **Underlying structural expectancy in R** from detection — first touch of target or
   stop on the underlying, in R. **No option P&L is claimed by this trial.**

## Pass / fail (single read, thresholds fixed here)

A variant is classified **SUPPORTS** only if, in **both** halves:

- eligible rows ≥ **2× `v0`**, and ≥ **60** in total across the scoring window;
- median R remaining at first detection ≥ **1.0**;
- target-before-detection rate ≤ **10 %**;
- false-positive rate ≤ **15 %** (`v1`, `v3` only);
- underlying structural expectancy in R is **> 0R** and not more than **0.05R** below
  `v0`;
- underlying structural expectancy in R exceeds the **95th percentile** of a matched
  random-time null (same ticker, session and direction; detection time drawn uniformly
  from that session's regular hours; 500 draws).

Otherwise **NO IMPROVEMENT**. **BLOCKED** if Step 0 fails or lineage cannot be established.
Results are read once; no parameter adjustment or second attempt from this trial.

A **SUPPORTS** result authorizes nothing in production. It permits only proposing a next
stage (a forward counts-only observer arm), which would need its own preregistration and
operator GO; any production timing change after that is a separate PR with its own GO.

## Safety rules that remain unchanged

- `ENTRY_LATE` minimum remaining R 1.0 and the episode block.
- $300 per-trade and $1,000 aggregate risk caps, DTE rules, contract-quality filters.
- RTH delivery-time send check, 30-minute duplicate suppression, score threshold 7.
- Market-alignment definition (only its input timing differs, in `v2`).
- Advisory-only scanner; no execution; no Webull wiring; futures runtime untouched.
- Production scanner behaviour is unchanged for the duration of the trial.

## Why this is not an execution change

The trial is an offline replay of historical bars that writes nothing to the box. It
changes no scanner, alert, risk or contract-selection rule, touches no broker path (the
2026-09-24 audit found no reachable options order path), and does not touch futures or
the #949 cohort.

## Files (only after separate approval)

- Evaluator: `scripts/research/options_timing_replay.py`,
  `tests/test_options_timing_replay.py` — offline, read-only; imports existing
  `causal_bars` / `bar_context` / `setup_authority` / `paper_v1` logic without modifying it.
- Result artifact: `docs/research-evidence/T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01/`.

**No proof, no run.**
