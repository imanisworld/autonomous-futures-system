# Options scanner timing replay — scoring result

trial_id: `T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01`
prereg: `docs/prereg-options-scanner-timing-2026-09-24.md` (#974) · Step 0 PASS: `docs/options-scanner-timing-step0-parity-2026-09-24.md` (#977)
result artifact: `docs/research-evidence/T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01/result.json`
(sha256 `c3a721360a77dd078f163aa81ee4048c4b2ebfd14c5b564f34e3b0c6cc9b4f26`)

**Verdict: NO IMPROVEMENT for all three variants → disposition RETIRE.** One read, run once,
with evaluator `scripts/research/options_timing_replay.py` frozen at `6fd9246` before any
scoring-window replay started. No evaluator, variant, threshold, universe, session or
exclusion change was made after replay began. No production, runtime, config, env, Discord,
watchlist, risk, alert, systemd or box change.

| Variant | Verdict |
|---|---|
| `v1_iex_feed_60s_close_confirm` | NO IMPROVEMENT |
| `v2_daily_at_open_live_cross` | NO IMPROVEMENT |
| `v3_30m_intrabar_live_cross` | NO IMPROVEMENT |

## What the numbers say

The detection delay is real, but none of the three timing changes fixes it. Every
variant, including the baseline, first detects with a median of **0.63–0.81R left** and
sees the target already hit before detection in **21–31 %** of episodes. No variant came
close to doubling v0's eligible episodes: the largest gain was v2 in H1, at +8 %.

- **v1 (IEX, 60 s buffer)** cuts median detection latency from 50.75 to 35.75 min.
  That does not lift R remaining or eligibility, because the 30-minute close
  confirmation still dominates.
- **v2 (daily at open, live cross)** adds a handful of open-gap daily detections: +11
  eligible in H1 and −1 in H2.
- **v3 (30m intrabar live cross)** fires earlier on forming bars. Its H2
  false-positive rate is 20 %, over the 15 % limit, and its median R remaining is lower
  than v0's.

## Per-half metrics

H1 = 2025-10-01 → 2026-03-17 (115 sessions); H2 = 2026-03-18 → 2026-08-31 (115 sessions).

| Metric | Half | v0 | v1 | v2 | v3 |
|---|---|---:|---:|---:|---:|
| Detected episodes | H1 | 378 | 392 | 400 | 397 |
| | H2 | 395 | 411 | 427 | 418 |
| Eligible episodes (past late gate) | H1 | 131 | 131 | 142 | 132 |
| | H2 | 161 | 156 | 160 | 158 |
| ENTRY_LATE rate | H1 | 65.3 % | 66.6 % | 64.5 % | 66.8 % |
| | H2 | 59.2 % | 62.0 % | 62.5 % | 62.2 % |
| Median R remaining at first detection | H1 | 0.70 | 0.68 | 0.80 | 0.63 |
| | H2 | 0.78 | 0.73 | 0.81 | 0.69 |
| Median detection latency (min) | H1 | 50.75 | 35.75 | 48.75 | 50.75 |
| | H2 | 50.75 | 35.75 | 50.75 | 50.75 |
| Target before detection | H1 | 28.6 % | 31.1 % | 25.0 % | 29.7 % |
| | H2 | 26.8 % | 25.8 % | 20.6 % | 29.2 % |
| False-positive rate (v1, v3) | H1 | — | 5.6 % (392) | — | 14.3 % (105) |
| | H2 | — | 5.4 % (411) | — | 20.0 % (95) |
| Underlying structural expectancy (R) | H1 | +0.051 | +0.038 | +0.113 | +0.035 |
| | H2 | +0.092 | +0.193 | +0.052 | +0.139 |
| Null p95 (R) | H1 | 0.182 | 0.171 | 0.208 | 0.148 |
| | H2 | 0.146 | 0.113 | 0.042 | 0.109 |

Late blocks split into past-target / past-stop / remaining-R below 1:

| Variant | H1 | H2 |
|---|---|---|
| v0 | 67 / 8 / 172 | 56 / 2 / 176 |
| v1 | 79 / 2 / 180 | 70 / 1 / 184 |
| v2 | 67 / 6 / 185 | 51 / 2 / 214 |
| v3 | 82 / 5 / 178 | 84 / 1 / 175 |

## Pass/fail checks (thresholds from the prereg; a variant must pass every check in both halves)

| Check | v1 H1 | v1 H2 | v2 H1 | v2 H2 | v3 H1 | v3 H2 |
|---|---|---|---|---|---|---|
| Eligible ≥ 2× v0 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Median R remaining ≥ 1.0 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Target before detection ≤ 10 % | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| False positives ≤ 15 % | ✓ | ✓ | n/a | n/a | ✓ | ✗ |
| Expectancy > 0R | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Expectancy ≥ v0 − 0.05R | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Expectancy > null p95 | ✗ | ✓ | ✗ | ✓ | ✗ | ✓ |

The eligible total is ≥ 60 for every variant (v1 287, v2 302, v3 290). No variant passes the
three detection-timing checks in either half, and no variant beats its null in H1.

## Frozen implementation choices (fixed in `6fd9246` before the read)

- Replay drives the real, unmodified production `OptionsScanner` wrapper chain; only inputs
  are substituted (as in Step 0). v2/v3 insert one layer after multisetup; v1 swaps the bar
  feed and buffer.
- Scan grid: session open + 45 s, every 5 min, all 230 NYSE sessions 2025-10-01 → 2026-08-31;
  halves 115/115.
- Episode identity: (ticker, timeframe, setup type, direction, trigger to 4 dp, NY date).
  Detection = first actionable row; eligible = at least one row past the late-entry gate
  (entry at the first such row).
- Outcome: first touch of target or stop on SIP regular-hours minutes; stop wins a shared
  minute; 14-day horizon (shortest DTE V1 may pick); unresolved = marked to close; truncated at
  data end 2026-09-01 (`data_end`, 7 episodes across all variants/halves).
- Null: random minute in the same session, same risk/reward distances, 500 draws, seed
  20260924, p95 of the draw means.
- False positives: SIP completed-bar 2-1-2 check for 30m triggers; SIP daily setup
  re-evaluation for v1 daily triggers.
- Undefined v0 expectancy would have been treated as 0 (not needed; both halves defined).
- v2 applies only to Daily 2-2 / 3-2-2; DAILY_212 stays on the v0 path.
- The IEX provider carries the `sip` label to pass the scanner's feed guard; substituting the
  feed *is* the v1/v3 variant.
- The data pull included extended-hours minutes on session days, used only for the 09:30:45
  quote proxy.

## Data and coverage limitations

- **Universe:** the current frozen 20-symbol watchlist (incl. SPY/QQQ) is applied across the
  whole window (operator decision: limitation, not amendment). The production watchlist
  differed earlier in the window, so this is not a reconstruction of what production saw.
- **SIP:** 3,427,834 one-minute bars, 100 % regular-hours coverage.
- **IEX (v1, v3):** 1,799,818 one-minute bars, **96.3 %** regular-hours minute coverage;
  **229 symbol-sessions below 80 %**, mostly TLT. Thin IEX sessions make v1/v3 bars sparser
  than a SIP bar would be. No sessions or tickers were excluded after seeing data.
- **Quote proxy:** the live quote is the last completed SIP 1-minute close (Step 0 caveat);
  it feeds R remaining identically across all variants.
- **Option P&L is not claimed.** Expectancy is on the underlying only. Option-chain and
  contract-quality gates cannot be replayed and are *not evaluated*.
- **Horizon truncation:** outcomes near 2026-08-31 are cut at the data end.
- **IEX freshness:** the only IEX entitlement probe ran after hours (Step 0). This trial says
  nothing about intraday IEX freshness; any production/observer IEX use would still need an
  in-hours probe.
- Nothing at or after 2026-09-23T14:07:08Z (#949 cohort) was read.

## Replay run record

| Variant | Sessions | Rows | Exit |
|---|---:|---:|---|
| v0 | 230 | 732,074 | 0 |
| v1 | 230 | 749,580 | 0 |
| v2 | 230 | 753,089 | 0 |
| v3 | 230 | 739,096 | 0 |

The raw replay records and minute data stay outside the repository. The first `score`
attempt exited 127 without running (`.venv/bin/python` is absent in this worktree; nothing
was computed or written). The single read then ran with the same `python3` interpreter the
replays used.

## What this means

This result authorizes nothing in production. Because it is RETIRE, none of these three
timing changes goes forward to a forward observer arm. The scanner's late-detection problem
(median ≈ 0.7R left, ~25–30 % of targets already hit) is confirmed on this data, but the
remedy is not the feed, the buffer, the open blind spot or intrabar confirmation as defined
here. Any new approach needs a fresh prereg and a new trial_id.
