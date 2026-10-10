# Options research handoff — futures signals expressed as ETF options (2026-10-10)

Author: Claude (auditor/breaker role), working the **options** side at operator request.
Status: **RESEARCH ONLY / NOT PREREGISTERED / NOT A SYSTEM.** No merge, deploy, runtime change, broker action or real money. Every result below is post-hoc on already-exposed signals unless marked OUT-OF-SAMPLE.

Companion: the ChatGPT futures checkpoint `docs/futures-research-resume-checkpoint-2026-10-10.md` (PR #1211 branch). This file adds to it; it does not replace it.

---

## 1. Bottom line

1. **What's wrong with the current options lane (OPTIONS_PAPER_V1 / Epoch-3): the exits and the contracts, not mainly the direction.** Targets sit 0.1–0.3× the stop distance on the main intraday setups. That needs an 80–90% win rate to break even; observed is about 58%. Contracts are 45–75 DTE, so small underlying moves barely move the option while the full spread is paid.
2. **What's wrong with 4HR Re-Trigger futures: the account can't absorb the intraday noise.** The direction is right by the close, but 20–40 point adverse swings stop out most trades at an account-sized stop.
3. **A lead that fixes both: take the 4HR signal (MNQ→QQQ, MES→SPY), buy an ATM ~7DTE call or put, and hold to the close.** Defined risk (the premium), no stop. On 151 historical signals it made +$5.3k to +$5.9k, t ≈ 2.0–2.2, both halves positive. It is concentrated, and each trade costs about $450–520.
4. **First out-of-sample look (Jul 24 – Oct 9 2026): 9 trades, −$384.** Too small to decide, and not supportive. See §6.
5. **A tracker exists and passes parity.** `scripts/options_4hr_etf_forward.py` regenerates the 4HR signals from Polygon futures bars with the canonical state machine. It reproduces the audit population exactly: **41/41 signals, Jan–Jun 2026**. It prices each one as a QQQ/SPY option from Polygon minute bars.

---

## 2. Diagnosis of the current options lane (Epoch-3 / scanner journal)

Source: Oct 9 copy of `options_scanner.sqlite` (886 MB; `private/trading-evidence-2026-10-09/`, not committed). Population: `options_shadow_journal` rows with status WIN/LOSS = 523, deduped to the **first sighting of each unique setup** (ticker, setup_type, direction, trigger, stop) = **413**. Of these, 395 are COUNTERFACTUAL and 18 ACTIVE. Option P&L is the scanner's own ask-entry / bid-exit outcome.

| Setup family | n | Win % | Median target/stop (R) | Win % needed to break even | Option P&L |
|---|---:|---:|---:|---:|---:|
| H1 2-2-2 continuation | 92 | 58 | 0.13 | 89 | −$2,156 |
| H1 2-2-2 reversal | 74 | 58 | 0.11 | 90 | −$1,652 |
| 30m 2-1-2 continuation | 66 | 53 | 0.34 | 74 | −$1,154 |
| Daily 2-2-2 reversal | 41 | 46 | 1.14 | 47 | −$1,071 |
| H4 2-2-2 reversal | 33 | 67 | 0.16 | 86 | −$576 |
| H4 2-2-2 continuation | 21 | 100 | 0.15 | 87 | +$442 |

| Exit reason | n | Option P&L | Per trade |
|---|---:|---:|---:|
| target_hit | 247 | +$3,784 | +$15 |
| stop_hit | 144 | −$8,804 | −$61 |
| premium_stop_hit | 22 | −$3,687 | −$168 |

By DTE: every contract was 45–75 DTE. Bucket 45–59: n=275, −$7,013. Bucket 60–74: n=138, −$1,694.

**Read:** wins are tiny and losses are large by construction. Target_1 is the nearest Strat level, usually much closer than the invalidation. Long-dated contracts make it worse. Daily setups are the only family with sane geometry (R≈1.1–1.2), and they still lose: about 46–53% wins, with premium stops costing the most.

Re-simulation of these 413 setups with time exits (EOD / next day / two days), on the scanner's own contract and on a ~7DTE ATM contract: **running at time of writing; results go in §6.**

---

## 3. 4HR → ETF options: experiments and results

Signal source: `scripts/edge_decomposition_audit_results_candidates.jsonl.gz` (lanes `4hr_mnq` n=79/81, `4hr_mes` n=74/76; 2024-07 → 2026-06). Entry = first option minute bar at/after the signal bar close (≤10 min lag). Exit = last minute bar ≤ 15:59 ET the same day. Price = minute VWAP (Polygon `/v2/aggs` options). Costs = half-spread haircut per side ($0.01/$0.03/$0.05) plus $0.65/side commission. One contract. Halves split at the median date.

### 3a. Why options (futures-side diagnosis, from the same file)
- 4HR MNQ, enter at signal and hold to EOD with no stop: +$7,600, t=2.42, both halves positive (n=79).
- The same trades with a 20-pt fixed stop (fits the 120-tick cap): 67/79 stopped. +$1.9k at 3 ticks of slippage, but 2026 is negative and the top-3-month share is 128%. Fragile.
- So the signal carries direction to the close; the account can't hold the noise. Defined-risk options remove the stop.

### 3b. Results (all post-hoc)
| # | Variant | n | Total P&L | t | H1 / H2 | By year | Max DD | Cost/trade | Verdict |
|---|---|---:|---:|---:|---|---|---:|---:|---|
| 1 | MNQ→QQQ ATM 0–1DTE | 75 | +$2.4k…+$3.0k | 0.8–1.0 | + / + | — | ~$2.5k | $191 | weak |
| 2 | **MNQ→QQQ ATM ~7DTE** | 78 | +$3.35k…+$3.66k | 1.55–1.69 | +$2.2–2.4k / +$1.1–1.3k | 2024 +$0.2–0.3k, **2025 +$3.6–3.7k**, 2026 −$0.3–0.4k | ~$1.9k | $522 | lead (2025-heavy) |
| 3 | MNQ→QQQ $5 debit call/put spread ~7DTE | 71 | +$0.25k…+$0.8k | 0.46–1.52 | + / + | 2026 ≈ 0 to −$0.2k | ~$0.4k | $209 | **dead** (two-leg spread cost) |
| 4 | **MES→SPY ATM ~7DTE** | 73 | +$2.0k…+$2.3k | 1.21–1.39 | +$0.9–1.0k / +$1.1–1.2k | 2024 −$0.5k, 2025 +$0.8–1.0k, **2026 +$1.75–1.8k** | ~$0.9k | $456 | lead |
| 5 | 3-2-2 MNQ→QQQ ATM ~7DTE | 30 | +$1.4k…+$1.5k | 0.8–0.9 | **−$0.03…−$0.09k** / +$1.5k | — | ~$0.7k | $520 | weak (H1 negative) |
| 6 | **COMBINED #2 + #4 (QQQ+SPY)** | 151 | **+$5.3k…+$5.9k** | **1.97–2.20** | +$3.1–3.4k / +$2.3–2.6k | 2024 −$0.2…−$0.4k, 2025 +$4.4–4.7k, 2026 +$1.3–1.5k | ~$1.8–1.9k | ~$490 | **best lead** |
| 7 | MNQ→QQQ 1% OTM ~7DTE | — | — | — | — | — | — | — | running (§6) |
| 8 | MES→SPY 1% OTM ~7DTE | — | — | — | — | — | — | — | running (§6) |

Ranges are across spread haircuts of $0.01→$0.03 per side (and $0.05 for #1). Combined top-3-month share is 87–95%, and 13 of 24 months are positive.

### 3c. Ideas tested and killed the same day (futures-side, kept for the record)
- Fade MES ORB Reclaim (30m t=−2.83, the most extreme of 52 lane×horizon cells): +$2.7k at 0 slippage, about half at 1 tick, negative at 2 ticks, ≈0 when deduped to the first signal per campaign. **Dead.**
- Transition-reclaim time exit with account-sized stops: halves flip sign. **Dead.**
- "ORB/VWAP fail because they wait for the 5m close": already tested (resting order at the level, PF 0.78). **Dead.**
- The 3 wide-stop paper setups the demo refused (Sep 11/15/30): they would have lost about −$45 and −$60, and the third was degenerate. The filters cost nothing.

---

## 4. Tooling built

| File | What |
|---|---|
| `scripts/options_4hr_etf_forward.py` (this branch) | Polygon futures 5m bars → canonical `advance_4hr_retrigger` walk (09:30–11:00 ET, as in `edge_decomposition_audit.extract_state_machine`) → QQQ/SPY ATM ~7DTE option priced from Polygon minute bars, held to 15:59 ET. Paper only. Needs `POLYGON_API_KEY`. |
| Parity check | Jan 2 – Jun 26 2026: **41 tracker signals = 41 audit signals**, exact (instrument, date, direction). 39 priced, +$1,495. |
| `private/qqq-4hr-options-2026-10-10/` (gitignored) | First QQQ run: `run.py`, `results.json`, `summary.txt`, `FINDINGS.md`. |
| Session scratch scripts (not committed) | `opt_bt.py` (variant engine), `scan_resim.py` (scanner time-exit re-sim), `polylib.py`. Can be committed on request. |

Polygon notes: `/v3/reference/options/contracts` with `as_of` + `expired` returned empty for QQQ history, so the scripts build OCC tickers directly ($1 strikes, probing expiries by whether minute bars exist). Large minute-bar responses sometimes fail with `IncompleteRead` through the proxy; the scripts retry and skip, and the counts above report NO_PRICE rows.

---

## 5. Caveats a breaker should press on

1. **Multiple testing.** About 8 option variants were tried on the same 4HR signals, plus 52 futures horizon cells and two dead ideas. A combined t≈2.0–2.2 after that much searching is suggestive, not proof.
2. **Concentration.** 2025 carries most of the profit; the top 3 months are 87–95% of net. That fails the inventory's <60% rule.
3. **Fill realism.** Minute VWAP with a fixed half-spread is not an executable quote. Polygon `/v3/quotes` (historical NBBO) would be stricter; `scripts/options_polygon_historical_quote_probe.py` exists for that.
4. **Size.** About $490 per trade (one ATM ~7DTE contract) exceeds OPTIONS_PAPER_V1's $300 per-trade cap. The 1% OTM tests (§6) check whether a cheaper strike keeps the edge.
5. **Signal rate.** About 3.3 signals/month per instrument historically, about 6–7/month for QQQ+SPY combined. Forward evidence accrues slowly.
6. **Live signal source.** On the box, the main engine has *no* MNQ strategies enabled (journal: `NO_ENABLED_STRATEGY`). The only live 4HR detector is the MNQ wide-stop collector, and there is no MES 4HR live detector. The forward tracker regenerates signals from Polygon bars instead, so it doesn't depend on the box.

---

## 6. Pending at time of writing (filled in as runs finish)
- **OUT-OF-SAMPLE (done): tracker on 2026-07-24 → 2026-10-09** (after the audit corpus ended; never examined by any study): **9 signals, 9 priced, 4 wins, −$384.46.**

  | Date | Inst | Dir | Option | P&L |
  |---|---|---|---|---:|
  | 07-27 | MES | SHORT | SPY 260803 P746 | +395.13 |
  | 07-28 | MES | LONG | SPY 260803 C739 | +71.70 |
  | 07-31 | MES | SHORT | SPY 260807 P744 | −236.27 |
  | 07-31 | MNQ | SHORT | QQQ 260806 P688 | −275.30 |
  | 08-14 | MES | SHORT | SPY 260820 P778 | +43.70 |
  | 08-28 | MNQ | LONG | QQQ 260903 C721 | −302.97 |
  | 09-15 | MNQ | LONG | QQQ 260921 C708 | −212.30 |
  | 09-24 | MES | LONG | SPY 260930 C765 | +142.15 |
  | 09-30 | MNQ | LONG | QQQ 261006 C742 | −10.30 |

  Read: n=9 cannot confirm or reject. The in-sample mean of about +$35/trade implies about +$315 expected here, against a standard error of about ±$700, so the miss is within noise. **It does not support the lead.** MES→SPY is +$416 and MNQ→QQQ is −$801, the same split as in-sample 2026 (SPY carried, QQQ lagged). The signal rate was about 3.6/month combined, lower than the historical ~6–7/month. Sep 15 and Sep 30 MNQ match the box wide-stop collector's live 4HR candidates (same dates and directions), an independent sanity check that the tracker sees what the box sees.
- 1% OTM ~7DTE QQQ/SPY: _pending_.
- **Scanner 413-setup time-exit re-sim (done).** Window: only **2026-09-09 → 2026-10-07**, the span where WIN/LOSS rows carry a selected contract. Entry = option minute VWAP at first sighting (≤10 min lag). Exits = 15:59 ET same day (eod), next session close (d1), second session close (d2). Costs: own contract uses half its recorded entry spread per side; ~7DTE ATM uses max($0.02, 1.5%) per side; plus $0.65/side commission.

  | Family | Contract | eod | d1 | d2 |
  |---|---|---|---|---|
  | ALL | own (45–75 DTE), n≈290 | −$7,299 (t −7.3) | +$256 | +$4,443 (t 1.1; H2 −$825) |
  | ALL | ~7DTE ATM, n≈340 | −$6,805 (t −5.3) | +$2,775 | +$8,659 (t 1.3; H2 −$3,105) |
  | H1 2-2-2/2-1-2/3-2-2 | ~7DTE | −$3,385 | −$2,143 | −$3,322 |
  | 30m 2-1-2 | ~7DTE | −$708 | +$240 | +$1,545 (t 0.8) |
  | **H4 2-2-2/3-2-2/2-1-2** | **~7DTE, n≈68** | −$930 | **+$6,097 (t 2.01; H1 +$3.8k / H2 +$2.3k)** | **+$12,589 (t 2.62; H1 +$10.9k / H2 +$1.7k)** |
  | H4 | own contract, n≈55 | −$1,014 | +$2,742 (t 1.5; H2 −$578) | +$6,507 (t 2.4; H2 −$120) |
  | DAILY | ~7DTE | −$1,782 | −$1,418 | −$2,154 |

  Read:
  1. **Same-day exits lose for every family** (t −2 to −7). The scanner's intraday-target model can't be rescued by a same-day time exit.
  2. **The H4 family held 1–2 sessions on ~7DTE ATM is the only positive cell with both halves positive** (d1). It echoes the 4HR futures finding: 4-hour-timeframe signals carry direction over the following session(s); 1H and Daily don't.
  3. **Weak evidence:** one month, one regime, setups clustered on the same days (not independent), best of 24 family×exit×contract cells (max |t| ≈ 2.5 expected by chance). The scanner's realised 45–75 DTE choice also costs about half the H4 edge versus ~7DTE.
  4. Per-trade cost: ~7DTE ATM on single names had a median premium of about $290–300, which fits the $300 OPTIONS_PAPER_V1 per-trade cap. The scanner's own contracts were about $750–850.

  Hypothesis worth a frozen forward test, not a rule change: **H4 Strat setups → ~7DTE ATM, exit at the next session's close.**

## 7. Proposed next step (needs operator GO; nothing is armed)
Freeze the rule exactly as in `scripts/options_4hr_etf_forward.py` (signal, ETF map, ATM by parity, first expiry ≥6 days, entry ≤10 min lag, exit 15:59 ET, one contract). Register it as a prospective paper trial in `docs/research-trial-ledger.jsonl` with a fixed one-look:
- n ≥ 30 forward priced trades;
- net > 0 after the $0.03/side haircut;
- both halves > 0;
- no single month > 50% of net.

Run the tracker after each session (paper only). DEMO or real options only after a pass, and through the release gates.
