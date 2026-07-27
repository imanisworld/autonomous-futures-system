# Causal inverted Lane B executable candidate — preregistration

Frozen: 2026-07-27, before any P&L for this candidate was calculated.

Status at freeze: **NEW CANDIDATE — UNTESTED**

This is not the 509-trade inverted Lane B candidate. The earlier result may be
used only as a disclosed comparison. It does not validate this rule.

This pass is research only. It must not modify the deployed box, #359, runtime,
risk configuration, strategy permissions, or another strategy. There is one
test and no post-result tuning or rescue variant.

## Economic identity retained

- Instrument: MNQ only.
- Data: five-minute OHLC bars, timestamped at bar open, converted to
  `America/New_York`.
- Position: exactly one MNQ contract.
- Point value: $2.00 per point; tick size: 0.25 point.
- Idea: invert the sign of the return from the prior U.S. cash-session close
  through the last close-momentum observation available before entry.
- No threshold, stop, target, trailing logic, `TRENDING`, `market_condition`,
  HTF direction, ranking, confluence, VWAP, ORB, volatility, advisory,
  permission, or variable-sizing gate.

## Authoritative prospective session calendar

The reference session is the NYSE core cash session, 09:30–16:00 ET. NYSE
publishes that core session and its holiday/early-close schedule at:

`https://www.nyse.com/trade/hours-calendars`

A date is prospectively eligible only when that published schedule says the
core cash session is open through 16:00 ET. Weekends, full holidays, special
closures, and scheduled 13:00 early closes are excluded before entry. The
calendar decision never uses the observed presence of a later bar.

For the frozen replay range, 2024-07-01 through 2026-07-24, the excluded
weekday dates are:

- 2024 full closures: 07-04, 09-02, 11-28, 12-25.
- 2024 scheduled early closes: 07-03, 11-29, 12-24.
- 2025 full closures: 01-01, 01-09 (National Day of Mourning), 01-20,
  02-17, 04-18, 05-26, 06-19, 07-04, 09-01, 11-27, 12-25.
- 2025 scheduled early closes: 07-03, 11-28, 12-24.
- 2026 full closures through 07-24: 01-01, 01-19, 02-16, 04-03, 05-25,
  06-19, 07-03.
- 2026 scheduled early closes through 07-24: none.

These dates are frozen inputs, not inferred from bar presence.

## Exact causal rule

### Previous-session source

For an eligible date `D`, `previous_close` is the close of the 15:55–16:00 bar
from the immediately preceding prospectively eligible NYSE session.

The previous close is usable only if that exact bar was observed and persisted
before `D`. If it was missing, `D` produces no signal; the algorithm does not
skip backward to an older close. An observed exact 15:55 close on `D` becomes
the source for the next prospectively eligible session even if another
required bar on `D` was missing.

### Signal

- Decision time: 15:30 ET.
- Required current observation: the completed 15:25–15:30 bar.
- Formula:
  `signal_return = current_15_25_close / previous_close - 1`.
- Direction: SHORT when `signal_return > 0`; LONG otherwise.
- Exact zero therefore remains LONG.

Only the persisted prior close, the published calendar, and bars completed by
15:30 may influence the decision.

### Entry

- Entry intent exists at 15:30 after the signal bar completes.
- The frozen replay fill is the **15:35 bar open**, the first five-minute
  boundary separated from signal availability by one complete bar.
- This deliberately does not use the simultaneous 15:30 open.
- Apply adverse slippage at entry: add slippage for LONG and subtract it for
  SHORT.
- If the exact 15:35 bar is absent, the candidate is `ENTRY_UNRESOLVED`; no
  later bar substitutes for it.

### Exit

- A market exit is scheduled before the 15:55 boundary and does not depend on
  information from the 15:55 bar.
- The frozen replay fill is the **15:55 bar open**.
- Apply adverse slippage at exit: subtract slippage for LONG and add it for
  SHORT.
- If the exact 15:55 bar is absent after entry, the candidate is
  `EXIT_UNRESOLVED`; it remains in the candidate ledger and no later price is
  substituted.

The modeled holding interval is therefore 15:35–15:55 ET.

## Costs

- Baseline commission: $1.48 round trip.
- Baseline slippage: one adverse MNQ tick per side.
- Frozen stress tiers: two, three, and four adverse ticks per side with the
  same commission.
- There are no favorable same-bar assumptions and no intrabar stop/target
  ordering because neither a stop nor a target exists.

## Missing-data and duplicate policy

- A prospectively eligible day with no completed 15:25 bar produces
  `SIGNAL_DATA_MISSING`, not a retrospectively excluded session.
- Missing prior-session close produces `PRIOR_CLOSE_MISSING`.
- Missing 15:35 entry produces `ENTRY_UNRESOLVED`.
- Missing 15:55 after entry produces `EXIT_UNRESOLVED`.
- Unresolved candidates are reported and never assigned a synthetic price.
- Exact duplicate timestamps with identical OHLCV are deduplicated.
- Conflicting duplicates fail the replay; no result may be published.
- Metrics use resolved trades, while candidate and unresolved counts are
  reported separately. Any unresolved entered trade blocks promotion.

## Instrument and roll handling

The replay uses the same local Polygon MNQ continuous front-contract corpus as
the prior study. Its frozen repository convention rolls eight calendar days
before the third Friday of the quarterly expiry month. Files are keyed by MNQ
root and exact timestamp. No price adjustment, alternate vendor stitching, or
post-result roll change is allowed.

This corpus cannot prove live source/broker contract identity. A promotion
decision must therefore retain exact source-symbol and dated-contract
journaling as an implementation gate; a mismatched or fallback contract fails
closed.

## Replay population and partitions

- Viewed historical root:
  `data/replay_polygon_5m/MNQ`, ending 2026-06-26.
- Prior extension root:
  `data/research_oos/inverted_lane_b_2026_07/MNQ`, after 2026-06-26.
- Combined population: all available unique bars across both roots through
  2026-07-24.

The extension is reported separately as the **previous untouched OOS period**.
It was untouched for the old frozen candidate but is not claimed as untouched
for this newly defined candidate because its data existed before this
preregistration.

H1/H2 are the first and second chronological halves of resolved combined
trades. Four chronological periods are equal-count chronological quarters of
the resolved combined sample. “Recent period” is the latest 126 resolved
trades, or the entire sample if smaller.

## Required analyses

Without changing the rule, report:

- candidate, resolved-trade, and unresolved counts;
- gross and net P&L, expectancy, profit factor, win rate, maximum drawdown,
  longest losing streak, and recovery duration;
- H1/H2, LONG/SHORT, calendar year, calendar quarter, equal-count
  chronological quarters, rolling three-month, rolling six-month, and recent
  period;
- the previous untouched OOS period separately;
- top-one, top-five, and top-ten winner contributions and net P&L after
  removing each set;
- one-, two-, three-, and four-tick cost tiers.

## Frozen old-versus-new attribution

Performance change from the old non-causal 509-trade result is decomposed in
this fixed order, with one-tick slippage and $1.48 commission throughout:

1. `OLD`: old retrospective completeness, 15:30 open entry, 15:55 close exit.
2. `CALENDAR`: replace only eligibility/state with the prospective calendar
   and immediately preceding scheduled-session close policy.
3. `ENTRY`: additionally replace entry with the 15:35 open.
4. `EXIT / FINAL`: additionally replace exit with the 15:55 open.

Each incremental P&L delta is reported. The decomposition is explicitly
order-dependent and is not a tuning exercise.

## Decision contract

Classification must be exactly one of:

`VALIDATED`, `PROMISING BUT UNPROVEN`, `BROKEN`, `OVERFIT`, `UNSAFE`, `WAIT`.

Final decision must be exactly one of:

`PROMOTE TO PAPER-BUILD CANDIDATE`, `KEEP RESEARCHING`, `REJECT`.

Promotion requires all of:

- positive net after baseline realistic costs;
- positive H1 and H2;
- no catastrophic recent decay;
- acceptable drawdown and recovery;
- no severe winner concentration;
- survival through a reasonable slippage tier;
- no lookahead or retrospective eligibility;
- zero unresolved entered trades;
- direct runtime implementability through an isolated lane without semantic
  transformation.

If the candidate fails, it is rejected. No alternative entry time, exit time,
filter, threshold, calendar rule, or rescue variant may be tested in this pass.

Only if it passes may the report provide a minimal paper-build plan. No
implementation is authorized by this preregistration.
