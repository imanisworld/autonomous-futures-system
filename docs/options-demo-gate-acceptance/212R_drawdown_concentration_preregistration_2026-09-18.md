# 212R Option Validation Drawdown and Concentration Pre-registration

Date frozen: **2026-09-18**  
Strategy family: **2-1-2 reversal (212R), 30m**  
Status: **offline pre-registration only**  
Runtime/config impact: **none**

This document freezes the drawdown and concentration rules **before any 212R option-side validation population exists**. It does not change the current 212R classification (`WAIT`), does not create option fills, and does not retire any blocker in the preserved PR #653 package by itself.

The thresholds below are governance limits, not fitted parameters. The known 09-16/09-17 evidence is underlying-only and is not eligible to satisfy these option-side tests.

## 1. Population eligible for evaluation

These rules apply only to a later, untouched, chronological 212R **option** validation population that has already passed the shared infrastructure requirements for:

- mechanical contract selection;
- decision-time timestamped quotes;
- executable-side fill reconstruction;
- fees/slippage;
- premium-stop planned-risk calculation; and
- fail-closed missing/stale quote behavior.

Discovery-window data and the current underlying-only 212R evidence may not be used to satisfy this pre-registration.

No threshold in this document may be changed after inspecting the option validation result. A later change requires a new dated pre-registration and a fresh untouched validation window.

## 2. Normalized realized return

For each resolved option fill `i`:

```text
R_i = realized_net_pnl_dollars_i / planned_risk_dollars_at_entry_i
```

where:

- `realized_net_pnl_dollars_i` includes spread, modeled slippage, and fees;
- `planned_risk_dollars_at_entry_i` is frozen at entry from the canonical premium-stop risk formula;
- `planned_risk_dollars_at_entry_i` must be finite and > 0;
- no-fill rows are retained in chronology but contribute `0R`;
- unresolved/open rows are not treated as wins, losses, or zero-return resolved fills.

Any resolved fill without a valid entry-time planned-risk denominator fails the evaluation rather than being dropped.

## 3. Drawdown rule

### Measurement

Order all resolved fills chronologically by decision timestamp, with a deterministic tie-break by contract identity.

Define cumulative net R:

```text
E_0 = 0
E_n = sum(R_i for i <= n)
```

Define peak-to-trough drawdown:

```text
DD_n = max(E_0 ... E_n) - E_n
MAX_DD_R = max(DD_n)
```

### Pre-registered threshold

**Aggregate validation passes drawdown only when `MAX_DD_R <= 6.0R`.**

Each required validation cell must also satisfy:

**cell `MAX_DD_R <= 4.0R`.**

Both conditions are required.

### Additional fail-closed rules

Drawdown automatically fails if:

- chronology cannot be reconstructed;
- costs are missing from realized P&L;
- planned-risk denominator is missing/invalid;
- resolved losses are omitted;
- open trades are force-marked as winners or zero-loss trades;
- the population is filtered after outcomes are known.

No dollar drawdown threshold is pre-registered here because the aggregate open-risk dollar budget is not yet an approved configured value. Once such a budget is explicitly approved, it may add a stricter dollar limit, but it may not weaken the `6R / 4R` limits above.

### Rationale

The rule is expressed in R so it remains comparable across premiums and contract counts and cannot be relaxed by changing nominal position size. The aggregate limit allows normal losing sequences while rejecting evidence whose positive expectancy depends on a materially deep loss cycle. The tighter per-cell limit prevents one required validation cell from hiding unacceptable path risk inside a stronger aggregate result.

## 4. Concentration rule

Concentration is measured from the same untouched, after-cost option validation population.

Two denominators are used:

1. **resolved-fill share** — share of all resolved fills;
2. **gross-positive-R share** — for rows with `R_i > 0`, the share of `sum(R_i)`.

If total gross-positive R is not finite and > 0, concentration fails.

### Pre-registered limits

The population passes concentration only if **all** of these conditions hold:

| Dimension | Resolved-fill share limit | Gross-positive-R share limit |
|---|---:|---:|
| Single symbol | <= 25% | <= 25% |
| Single trading session/date | <= 30% | <= 30% |
| One direction (CALL or PUT) | <= 70% | <= 70% |
| One pre-registered clock bucket | <= 70% | <= 70% |

In addition:

- at least **3 distinct symbols** must contribute positive R;
- at least **3 distinct trading sessions/dates** must contribute positive R;
- both CALL and PUT directions must contain resolved fills;
- all concentration dimensions and bucket definitions must be frozen before the untouched validation window is evaluated.

### Failure rule

Exceeding **any** limit fails `concentration_pass`. There is no averaging of dimensions and no waiver because aggregate expectancy is positive.

Rows may not be removed to improve concentration after outcomes are known.

### Rationale

The purpose is not to force equal distribution. It is to prevent a validation pass from being driven primarily by one ticker, one day, one direction, or one time bucket. The limits are fixed before option-side results exist and therefore are not chosen to make the current 212R evidence pass.

## 5. Relationship to the qualification gate

Once a future evidence package can mechanically prove the rules above from an untouched option validation population:

- `validation.drawdown_limit_pre_registered` may be attested `true`;
- `validation.concentration_limit_pre_registered` may be attested `true`;
- each required validation cell's `drawdown_within_limit` and `concentration_pass` must still be computed from actual option fills.

This pre-registration **does not** make those current PR #653 fields true because PR #653 has zero option fills and its preserved evidence file remains intentionally unchanged.

It also does not affect:

- 212R classification = `WAIT`;
- minimum resolved fills per required cell;
- untouched-window requirement;
- multiple-month requirement;
- chronological walk-forward requirement;
- positive after-cost expectancy requirement;
- positive after-cost net P&L requirement.

## 6. Change control

This document is frozen once merged.

Any later proposal to change:

- `6R` aggregate maximum drawdown;
- `4R` per-cell maximum drawdown;
- any concentration percentage;
- the minimum number of positive symbols/sessions;
- the R formula; or
- concentration dimensions

must be recorded in a new dated pre-registration **before** collecting/evaluating the next untouched validation population.

No retroactive threshold change may be used to rescue a failed 212R validation run.
