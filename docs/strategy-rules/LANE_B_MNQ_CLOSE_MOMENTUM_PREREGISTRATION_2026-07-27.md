# Lane B MNQ Close-Momentum — Pre-registration

**Status:** FROZEN BEFORE RESULT INSPECTION  
**Scope:** research/replay only; no runtime, risk, execution, sizing, or deployment changes  
**Academic rule:** Baltussen, Da, Lammers, and Martens (2021), “Hedging Demand
and Market Intraday Momentum,” *Journal of Financial Economics* 142, 377–403,
DOI `10.1016/j.jfineco.2021.04.029`.

Primary-source links: [publisher-version PDF](https://pure.eur.nl/ws/portalfiles/portal/58145484/1_s2.0_S0304405X21001598_main.pdf)
and [Erasmus University record](https://repub.eur.nl/pub/131621). The separate
figures checked against the source were found in
[Quantitativo’s ES/NQ adaptation](https://www.quantitativo.com/p/intraday-momentum-for-es-and-nq).

## Source verification

| Claim | Primary source | Verified? | Exact definition |
|---|---|---:|---|
| Market/session | Baltussen et al. (2021), Sections 1–2 and Table A1 | Yes | The paper uses liquid futures but selects the common hours of the underlying cash market. NQ uses 09:30–16:00 U.S. Eastern time. |
| Rest-of-day return | Baltussen et al. (2021), Section 1 | Yes | A trading day runs from the previous market close to the current close. `ROD = ON + FH + M + SLH`: the return from the previous market close through 30 minutes before the current close. For NQ this is previous 16:00 to current 15:30 ET. |
| Signal cutoff | Baltussen et al. (2021), Sections 1 and 3.5 | Yes | 30 minutes before the underlying market close: 15:30 ET for NQ. |
| Entry/exit | Baltussen et al. (2021), Sections 1 and 3.5, Eq. (12) | Yes | Hold the sign-directed position only for the last half-hour return `rLH`, i.e. 15:30–16:00 ET for NQ. |
| Direction/threshold | Baltussen et al. (2021), Eq. (12), Table 6 note | Yes | LONG when `rROD > 0`; SHORT otherwise. No magnitude threshold is used. |
| Zero/near-zero | Baltussen et al. (2021), Eq. (12) | Partly | The literal equation assigns the `otherwise` branch, including exact zero, to SHORT. The paper specifies no near-zero tolerance. |
| Short sessions | Baltussen et al. (2021), Section 2 | Yes | Days on which the exchange closed early are removed. |
| Overnight | Baltussen et al. (2021), Sections 1 and 3.5 | Yes | Overnight return is included in the predictor because ROD begins at the previous close. The trading position itself is intraday and is closed at 16:00 ET. |
| Missing/illiquid days | Baltussen et al. (2021), Section 2 | Yes | The paper removes non-business days, early-close days, non-positive prices, and days with total volume below 100 contracts. Our local aggregate corpus cannot reproduce the paper's contract-volume filter reliably, so it will fail closed only on missing required timestamp bars and disclose that implementation difference. |
| Transaction costs | Baltussen et al. (2021), Section 3.5 | Yes | Main Table 6 results exclude costs. The authors state that a one-tick-cost S&P 500 futures implementation remains positive; they do not publish NQ cost-adjusted results there. |
| NQ source evidence | Baltussen et al. (2021), Tables A1 and B1 | Yes | NQ is included from 1996-04-12 to 2020-05-01, 6,017 observations, 09:30–16:00 ET. Table B1 reports a positive and statistically significant NQ `rROD` slope; it does not publish NQ strategy P&L metrics. |
| 24.3% return, 1.67 Sharpe, +6 bps/trade, 38% win rate, 2.25 payoff | Baltussen et al. (2021) | **No** | These figures are absent. They are reported by Quantitativo for a later NQ adaptation of Zarattini, Aziz, and Barbon's SPY “Noise Area” strategy after changing its lookback and leverage. They are not a replication of the Baltussen literal close-momentum rule. |

## Frozen MNQ implementation contract

- **Instrument:** one MNQ contract.
- **Calendar/session:** America/New_York; only full Nasdaq cash-market sessions
  with a regular 09:30–16:00 ET close.
- **Data:** local `data/replay_polygon_5m/MNQ/MNQ_YYYY-MM-DD.jsonl`.
- **Required observations:** prior full-session 15:55–16:00 bar, current
  15:25–15:30 bar, current 15:30–15:35 bar, and current 15:55–16:00 bar.
  A missing required observation makes the session ineligible and is reported,
  never silently dropped.
- **Signal:** `r_rod = current_15:25_bar.close / prior_full_session_15:55_bar.close - 1`.
  The current 15:25 bar closes at 15:30 ET, so this is the last causally known
  price at the academic cutoff.
- **LONG:** `r_rod > 0`.
- **SHORT:** `r_rod <= 0`, including exact zero, following Eq. (12)'s literal
  “otherwise” branch.
- **No-trade:** only an ineligible/early-close/missing-required-bar session.
  No magnitude threshold or filter.
- **Entry:** market at the current 15:30 bar open, after the 15:25 bar has
  closed. Apply adverse slippage to entry: add ticks for LONG, subtract ticks
  for SHORT.
- **Exit:** market at the current 15:55 bar close (the 16:00 ET cash close).
  Apply adverse slippage to exit: subtract ticks for LONG, add ticks for SHORT.
- **Stops/targets:** none.
- **Overnight carry:** none.
- **Same-bar assumptions:** none are needed; there is no intrabar stop or
  target. Entry and exit use distinct timestamp boundaries.
- **Point/tick economics:** MNQ tick size 0.25, $0.50/tick, $2.00/point.
- **Commission:** $1.48 per completed round trip, matching the repository's
  existing futures research convention.
- **Slippage:** baseline one adverse tick on entry and one adverse tick on
  exit. Sensitivities use 2, 3, and 4 adverse ticks **per side**. This explicit
  per-side interpretation is conservative and frozen before results.
- **Fills/resolution:** every eligible signal is treated as filled and resolved
  because both orders are market-at-observed-boundary conventions. Missing
  boundary data makes the session ineligible rather than unresolved.
- **No parameter sweep:** chronology, direction, calendar block, and cost
  results are descriptive slices of this single frozen rule.

## Holdout

Before result inspection, sort all eligible sessions chronologically and reserve
the final 25% as an untouched holdout. The first 75% is the development/reproduction
portion. No rule change is allowed after either portion is calculated. Overall
results may be reported after the holdout is opened, but the development and
holdout results remain separately identified.

## Promotion criteria

Use the operator-supplied criteria verbatim: useful cadence; positive after
commission and adverse slippage; positive H1 and H2; multi-period and
directional stability; no month or top-winner dependence; positive untouched
holdout; and meaningful sample size. Historical evidence alone cannot produce
`VALIDATED`.
