# Inverted Lane B paper-candidate validation — preregistration

Frozen: 2026-07-27

Status at freeze: **PROMISING BUT UNPROVEN**

This is an isolated research lane. It must not contact or modify the deployed
box, the #359 forward epoch, MNQ 60M 3-2-2, MES `strat_122`, sizing, execution
mode, runtime configuration, or strategy permissions.

## Frozen identity

The candidate is the exact direction inverse of the rule preregistered in
`LANE_B_MNQ_CLOSE_MOMENTUM_PREREGISTRATION_2026-07-27.md`.

- Instrument: one MNQ contract.
- Session timezone: America/New_York.
- Eligible session: a weekday with complete 15:25, 15:30, and 15:55 ET
  five-minute bars. Shortened/incomplete sessions are excluded.
- Signal observed at 15:30 ET: return from the previous eligible full
  session's 15:55 bar close (the 16:00 close) through the current session's
  15:25 bar close.
- Original direction: LONG when that return is positive and SHORT otherwise.
- Candidate direction: the exact opposite—SHORT when positive and LONG
  otherwise. Exact zero therefore maps to LONG.
- Entry: current 15:30 five-minute bar open.
- Exit: current 15:55 five-minute bar close (16:00 ET).
- No threshold, filter, stop, target, trend/VWAP/ORB/volatility condition, or
  time optimization.
- Baseline costs: $1.48 round-trip commission and one adverse tick per side.
- Stress costs: two, three, and four adverse ticks per side; one modest
  commission increase specified below.
- Missing-data handling and prior-full-session state are unchanged.

Any implementation or result that differs from those statements is a HOLD.

## Frozen reconciliation target

The committed audit at `7348096` must reproduce:

- 490 trades; gross +$4,858.50; net +$3,643.30.
- Expectancy +$7.4353; PF 1.1941; win rate 51.4286%; max drawdown $1,320.96.
- H1 +$3,474.90; H2 +$168.40.
- Inverse LONG +$2,838.44; inverse SHORT +$804.86.
- Four chronological periods +$402.94, +$3,071.96, +$120.44, +$47.96.
- Untouched final 25% +$47.96.
- One/two/three/four-tick net +$3,643.30 / +$3,153.30 /
  +$2,663.30 / +$2,173.30.
- Top-one winner $546.52; top-five winners $2,194.10; net after removing
  top five +$1,449.20.

## Untouched extension protocol

The viewed sample is every eligible session available in the committed local
MNQ five-minute cache through its frozen final date. Additional data qualifies
as untouched only if it is outside that date range and was not present when the
rule or inversion was evaluated.

Search order:

1. Later vendor bars after the frozen cache endpoint.
2. Earlier five-minute MNQ history before the cache start, if available from a
   credible source with continuous-contract construction compatible enough to
   disclose.
3. Existing local but genuinely out-of-range files, verified by path, date,
   and content hashes.

The old and new samples are evaluated separately before a combined summary.
No observation from the extension may change the rule. If no credible data is
obtainable, the report must say so and cannot claim OOS validation.

## Temporal and tail analyses

Without changing the rule, report calendar years, calendar quarters, rolling
three-month and six-month windows, H1/H2, and earliest/latest periods.

Report top one/five/ten winners, net with each removed, largest loss, longest
losing streak, maximum drawdown, and maximum recovery duration measured in
both trading observations and calendar days. An unrecovered terminal
drawdown must be labeled.

## Cost protocol

The exact raw entry and exit remain fixed. Apply adverse slippage symmetrically
on both sides. Baseline commission is $1.48. The sole commission stress is
$2.00 round trip, chosen before results as a modest 35.1% increase. There is no
same-bar stop/target ambiguity because the rule has neither; entry and exit are
fixed boundary prices.

## Runtime-parity audit only

No runtime implementation is authorized. The audit must trace:

- five-minute feed completeness and timestamp semantics;
- causal availability of the 15:25 close and 15:30 open;
- persistence of the previous eligible 16:00 close;
- exchange holiday and shortened-session treatment;
- deterministic day-only close behavior;
- required journal identity and fields;
- global gates that would change eligibility;
- collision/open-position/max-trades/day behavior;
- instrument rooting and contract-roll implications.

The future paper lane must not silently inherit `TRENDING`, ranked selection,
strategy permission, max-trades/day, or another gate that was absent from the
research contract. Any unavoidable mismatch is a promotion blocker.

## Classification and promotion gates

Historical evidence alone cannot produce a conclusion stronger than
**PROMISING BUT UNPROVEN**.

Future paper candidacy requires exact reproduction, frozen rules, positive
realistic-cost results, positive H1/H2 and recent period, positive untouched
extension when available, acceptable concentration, both directions positive,
clean causal implementation, no material replay/runtime mismatch, and
preservation of explicit risk controls without changing signal eligibility.

Possible classifications: VALIDATED, PROMISING BUT UNPROVEN, BROKEN, OVERFIT,
UNSAFE, WAIT.

Possible recommendations: PROMOTE TO PAPER CANDIDATE, KEEP RESEARCHING, REJECT.

ORB Breakout inversion is excluded from this pass.
