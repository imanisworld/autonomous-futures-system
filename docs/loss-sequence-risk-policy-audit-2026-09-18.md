# Loss-sequence / "revenge" risk-policy audit — 2026-09-18

## Question

Should the autonomous futures system add a fixed post-loss cooldown or stop trading after one/two consecutive losses simply to enforce a human-style "no revenge trading" rule? Should the current three-trades/day cap be changed?

## Ruling

**No separate post-loss/revenge throttle is justified by the available evidence.**

"No revenge trades" is defined mechanically as:
- no manual or unsignaled re-entry;
- no averaging down;
- every new entry must be a fresh, reproducible strategy signal;
- every new entry must pass the ordinary risk/execution gates.

A loss by itself does not invalidate the next independently valid signal. A consecutive-loss stop, cooldown, or early-session loss floor should be added only if combined-account evidence shows that post-loss trades have worse expectancy or materially improve drawdown when removed.

The current three-trades/day limit remains a **provisional account-safety cap**, not an evidence-derived revenge throttle. The available sealed strategy studies do not provide a valid combined-account counterfactual for changing that cap.

## Evidence sources

### Historical full-engine throttle test

Commit `4e137051064edae059dd87800e28cf22c2ef2665` / PR #57 recorded a 556-day honest-fill replay with strategy posture held constant, comparing the then-live psychology/throttle gates against those gates removed while survival controls remained.

Recorded result, gates-off versus gates-on:
- MES P&L: $198,061 -> $228,706 (+15.5%);
- MNQ P&L: $92,848 -> $126,968 (+37%);
- win rate improved by 0.8 percentage points MES / 1.2 points MNQ;
- expectancy per trade improved by about $8 on each instrument.

That test removed several throttles together, so it does not isolate a specific 1-loss or 2-loss threshold. It does show that the old bundle of human-style throttles was not protective in the tested system.

### Current sealed active/relevant family rows

A read-only sequence audit used retained canonical trade rows; no strategy, replay, journal, or runtime state was changed.

**MES 1-2-2**
- 31 resolved trades over 30 trading dates;
- only one date had a second trade;
- that second trade followed a loss and won **+$71.02 net after the repo-standard $1.48 round-trip commission adjustment**;
- a one-loss stop would therefore have removed that winner;
- a two-loss or three-loss stop never triggered.

**MNQ 4HR Re-Trigger**
- 80 resolved baseline trades over 80 distinct dates;
- maximum one trade per date;
- no same-day post-loss trade exists to support or reject a cooldown.

**MNQ 3-2-2**
- 20 resolved honest-fill trades over 20 distinct dates;
- maximum one trade per date;
- no same-day post-loss trade exists to support or reject a cooldown.

These active/relevant rows therefore contain **one** observed same-day post-loss follow-up trade total, and it was profitable. That is nowhere near enough evidence to install a post-loss lock.

## Inactive 2-1-2 sensitivity

The retired/unproven 2-1-2 population is not used to validate an active strategy, but its larger sample is useful as a sensitivity check on the loss-throttle mechanism.

Commission-adjusted canonical rows:

### MNQ 2-1-2
- baseline: 126 resolved, +$354.02;
- stop after two same-day consecutive losses: 123 resolved, +$129.46;
- removed post-two-loss trades: 3/3 winners, **+$224.56**;
- H1 baseline -$205.26 vs two-loss stop -$306.78;
- H2 baseline +$559.28 vs two-loss stop +$436.24.

### MES 2-1-2
- baseline: 217 resolved, -$1,041.16;
- stop after two same-day consecutive losses: 209 resolved, -$1,388.07;
- removed trades: 8 trades, 5 wins / 3 losses, **+$346.91**;
- H1 baseline +$9.24 vs two-loss stop -$272.34;
- H2 baseline -$1,050.40 vs two-loss stop -$1,115.73.

The two-loss stop reduced net P&L in **both halves of both instruments**. This does not validate 2-1-2; it only demonstrates that "two losses means the next valid signal should be blocked" is not supported as a general risk rule.

## Three-trades/day cap

The cap was restored by commit `220d7c8bccdd68309cd424119ad65ecc34167373` / PR #376 specifically to isolate the MNQ ORB Breakout inverse forward-paper lane. The commit explicitly states that the move from 9999 back to 3 was **not** a reversal of the June throttle-removal evidence.

Current sealed active-family studies cannot validly answer whether a combined multi-strategy account should use 2, 3, 4, 5, or unlimited trades/day:
- 4HR and 3-2-2 are one-trade-per-day populations;
- MES 1-2-2 had only one two-trade day;
- simply concatenating independent studies would create impossible overlapping MNQ trades and would not reproduce the real account/open-position rules.

Therefore:
- keep 3/day for current safety/isolation;
- do not claim 3/day is optimized;
- re-evaluate only from a compatible combined-account replay or sufficient forward multi-strategy evidence.

## Policy after this audit

- `max_consecutive_losses=9999`: remain effectively disabled.
- `circuit_breaker_losses=0`: remain disabled.
- `early_session_loss_floor=0`: remain disabled.
- `max_trades_per_day=3`: remain as the current provisional safety/isolation cap.
- global `max_drawdown_percent`: 30% prospective hard account survival floor.
- frozen lane-specific drawdown contracts remain unchanged for their active epochs.

## Re-open trigger

Revisit post-loss throttles only when a compatible combined-account population has enough same-day second/third trades to evaluate:
- expectancy after one loss;
- expectancy after two losses;
- drawdown impact of blocking those trades;
- H1/H2 or equivalent temporal replication.

Until then, an automatic post-loss cooldown would be an invented rule rather than an evidence-based control.
