# Pre-registration — MNQ market-condition volume filter & Sunday-reopen window

**Registered 2026-09-21 04:50Z, before any further data is examined. Evidence
lanes only. No rule, gate, config, or runtime change is authorized by this
document. Any change is a separate post-2026-09-30 decision that must cite the
outcome recorded in §6.**

Companion evidence: `counterfactual-gates-off-2026-09-21.md`,
`counterfactual-sunday-reopen-2026-09-21.md` (+ addendum).

## 1. What we already know (frozen as of registration)

- The MNQ label is Pine-computed on the 15m chart: TRENDING requires the
  EMA9/21/55 stack ordered **and** `rel_vol ≥ 0.80` where
  `rel_vol = volume / sma(volume, 20)`. DEAD/CHOPPY are pure volume thresholds.
- On 2026-09-20 the EMA trend was UP on every bar 22:00–01:15Z; the label
  flipped away from TRENDING only because `rel_vol` was 0.29–0.62, since the
  20-bar SMA at a Sunday reopen is Friday RTH volume.
- On the 2026-09-16 → 09-21 tape (MNQ shadow OUTCOME rows): label TRENDING
  26/67 +$360; **EMA-aligned-but-volume-blocked 6/30 −$690**; EMA sideways
  24/69 +$59. Across 8 Sundays, every bucket lost inside Sun 22:00Z–Mon 01:00Z.
- Conclusion so far: the volume filter is the most valuable part of the label;
  loosening it is unsupported. The only surviving candidate is a tightening.

Both facts above are *in-sample for the purpose of this document*. Nothing
below may be scored on data observed before 2026-09-21 04:50Z.

## 2. Hypotheses (fixed)

**H1 — Sunday-reopen no-entry (tightening).**
Blocking all MNQ entries Sun 22:00Z – Mon 01:00Z improves net $ per week
versus the current rules, on forward data.

**H2 — Session-aware volume denominator (loosening, expected to FAIL).**
Replacing `sma(volume, 20)` with a same-session-of-week baseline (volume ÷
median volume of the same 15m slot over the prior 4 weeks) would relabel some
"aligned-but-thin" bars as TRENDING. The trades that *only* H2 admits are net
positive after costs.

**H3 — 1m volume acceleration inside the 15m bar (loosening, exploratory).**
Among aligned-but-thin 15m bars, the subset where the last 5 one-minute bars'
volume exceeds the first 10 one-minute bars' volume (of the same 15m bar) is net
positive after costs.

## 3. Data (forward only)

- Candidates: `cross_instrument_observation_v1.jsonl` CANDIDATE/OUTCOME rows,
  MNQ, `signal_timestamp > 2026-09-21T04:50:00Z`, all shadow families.
- Bars: `logs/bars_MNQ_*.jsonl` (15m) for EMA/`rel_vol` reconstruction;
  `logs/tf1m/bars_MNQ_*.jsonl` (1m, collected since 09-18) for H3.
- Costs: `execution/forward_evidence_campaign.py` constants
  (`SLIPPAGE_TICKS=1.0`, `COMMISSION_DOLLARS=1.48`), 1 contract.
- Resolution: the campaign's own OUTCOME resolver (stop-first ties). No new
  fill logic.

## 4. Minimum sample and decision rules (fixed)

| Hyp. | Population scored | Min n | PASS if | FAIL if |
|---|---|---|---|---|
| H1 | MNQ demo-eligible setups (label TRENDING, regime FULL) inside the window vs. outside, same weeks | ≥ 6 Sundays **and** ≥ 20 inside-window rows | inside-window net $ ≤ 0 **and** removing them raises weekly net $ in ≥ 4 of 6 weeks | otherwise |
| H2 | Rows admitted by H2 only (EMA aligned, `rel_vol < 0.80`, session-relative ≥ 0.80) | ≥ 30 rows | net $ > 0 after costs **and** win-rate ≥ TRENDING bucket's same-period win-rate | otherwise |
| H3 | Aligned-but-thin rows with 1m acceleration | ≥ 30 rows | net $ > 0 after costs **and** win-rate ≥ TRENDING bucket's | otherwise |

- One look, at the first weekly gate-report run after the minimum n is met.
  No interim peeks that inform a decision. The daily 22:20Z gate line may
  continue to print aggregate counts; it does not score H1–H3.
- Multiple-comparison note: three hypotheses, one look each. If exactly one of
  H2/H3 passes at the margin, treat as PROVISIONAL and require a second,
  non-overlapping sample of equal size before any proposal.
- A PASS on H2 or H3 does **not** authorize a change; it authorizes a proposal
  for the post-09-30 review with the numbers attached.

## 5. What is NOT allowed

- Re-slicing by family, direction, hour, or instrument after seeing forward
  results to rescue a FAIL. The populations above are the populations.
- Scoring on any row with `signal_timestamp` before registration time.
- Changing the cost constants, the resolver, or the label formula mid-study.
- Using the demo ledger (mixed-era, mixed-sizing) as the scoring series.

## 6. Outcome (to be filled once, at the look)

| Hyp. | Look date | n | Net $ | Win % | Result | Follow-up |
|---|---|---|---|---|---|---|
| H1 | | | | | | |
| H2 | | | | | | |
| H3 | | | | | | |

## 7. Ownership

Scoring is mechanical; whoever runs it appends §6 and links the run. Proposals,
if any, go to the operator with this document attached. The operator's standing
ruling (PAPER/OBSERVE only, freeze to 2026-09-30) is unchanged by anything here.
