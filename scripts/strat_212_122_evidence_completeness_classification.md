# strat_212 / strat_122 — Final classification (evidence-completeness pass)

Companion to `strat_212_122_canonical_evidence_results.json` (FINAL, post-#338+#339)
and `strat_212_122_slippage_sensitivity_results.json` (1/2/3-tick adverse-slippage
sweep, real `PaperBroker` fill path). This file resolves the operator's four
outstanding evidence-completeness requirements on PR #337: per-cell classification,
considering sample size, H1/H2 behavior, direction dependence, concentration,
drawdown, and slippage sensitivity — not net/PF alone.

No strategy/replay-engine/risk/broker/config/deployment/corpus logic changed. No
enablement made or recommended anywhere in this file — `risk_rules.yaml` unchanged,
both strategies remain disabled.

## MNQ strat_212 — **PROMISING BUT UNPROVEN**

- Net +$540.50 raw / +$354.02 comm-adj (PF 1.19 / 1.12), 126 resolved.
- **Direction dependence is severe**: LONG (88 trades) nets +$3.50 — essentially a
  wash — while SHORT (38 trades) carries +$537.00, effectively the *entire* edge.
- **Concentration is severe**: top-5 winners account for 122% of net P&L
  (ex-top-5 net is **-$119.50** — without 5 of 126 trades, this cell is a loser).
  Top-3 alone is 80% of net.
- H1 raw +$112.50 but **comm-adjusted flips negative** (-$10.34) — H1 is a wash
  after cost; H2 (+$428.00 raw / +$364.36 comm-adj) carries the year.
- Slippage sensitivity: net stays positive at 1/2/3 ticks ($540.50 / $505.50 /
  $537.50 raw; $354.02 / $319.02 / $351.02 comm-adj) — **robust to slippage**,
  does not flip sign or collapse.
- Max drawdown $432.00, 10 consecutive losses (of 126) — unremarkable.

**Why not VALIDATED**: the edge is real and slippage-robust in aggregate, but it is
concentrated in a small number of trades and one direction — not yet evidence of a
repeatable, direction-agnostic edge. **Why not WAIT/BROKEN**: net and PF are
positive under every commission/slippage cut tested, and H2 alone is unambiguously
positive even after cost — there is a real signal here, just not yet proven robust
enough to trust without more out-of-sample SHORT-side data.

## MES strat_212 — **BROKEN**

- Net -$742.50 raw / -$1,075.50 comm-adj (PF 0.85 / 0.80), 225 resolved.
- **Negative in both directions**: LONG (135 trades) -$395.00, SHORT (90 trades)
  -$347.50 — no direction rescues it.
- H1 is thin-to-negative (+$188.75 raw, **-$3.65 comm-adj**); H2 is severely
  negative (-$931.25 raw / -$1,071.85 comm-adj) — the second half actively erodes
  the account.
- Slippage sensitivity: net stays negative at every level tested (raw: -$742.50 /
  -$711.25 / -$663.75; comm-adj: -$1,075.50 / -$1,038.33 / -$964.19 at 1/2/3
  ticks) — the *magnitude* of the loss shrinks slightly as slippage rises (fewer
  trades resolve identically — see the run's own trade-count shift, 225→221→203 —
  a `pessimistic_both_hit` bar-resolution-order effect, not noise), but it **never
  crosses into positive territory** at any tested slippage level.
- Max drawdown $1,326.25 (the largest of all four cells), 15 consecutive losses.

**Why BROKEN, not WAIT**: this is not a case of insufficient evidence — 225
resolved trades is the largest sample of the four cells, and every cut (both
halves, both directions, both commission states, all three slippage levels)
agrees: negative. That is a consistent, well-evidenced negative edge, not an
open question.

## MNQ strat_122 — **WAIT**

- Net -$180.00 raw / -$197.76 comm-adj (PF 0.53 / 0.51), but only **12 resolved
  trades** (3 wins, 9 losses) — well below this lane's own established sufficiency
  bar (~30, per MES strat_122 only being treated as SUFFICIENT once it crossed 31
  resolved earlier in this same evidence lane).
- 7 of the 12 trades were part of a single consecutive-loss streak — with a sample
  this small, one bad stretch dominates the entire read.
- Concentration stats (top-1/3/5 share) are **not informative** at N=12/3 wins —
  removing the single best winner makes the loss *worse* (top1_share = -44%),
  which is a small-sample artifact, not a meaningful concentration signal.
- Slippage sensitivity: stays negative and roughly stable across 1/2/3 ticks
  (-$180.00 / -$183.00 / -$186.00 raw) — consistent direction, but on a sample
  this thin that consistency doesn't yet mean much either way.

**Why WAIT, not BROKEN**: 12 resolved trades over a 313-day corpus is too thin to
distinguish a real negative edge from a small-sample artifact dominated by one
losing streak. The point estimate is negative, but the sample size itself is the
binding constraint — more data is needed before any classification stronger than
WAIT is defensible in either direction.

## MES strat_122 — **PROMISING BUT UNPROVEN**

- Net +$428.75 raw / +$379.91 comm-adj (PF 1.78 / 1.65), 33 resolved — the
  strongest PF of all four cells, and the first cell in this evidence lane to
  cross this lane's own ~30-trade sufficiency convention.
- **Positive in both halves**, both raw and comm-adjusted: H1 +$47.50 raw /
  +$22.34 comm-adj, H2 +$381.25 raw / +$357.57 comm-adj — unlike MNQ strat_212,
  H1 does *not* flip negative on cost.
- **Positive in both directions**: LONG (24 trades) +$168.75, SHORT (9 trades)
  +$260.00 — no single direction carries the entire edge, though the SHORT sample
  (9) is small on its own.
- Slippage sensitivity: net and PF decline steadily but stay solidly positive
  across the full 1/2/3-tick sweep (raw: $428.75 / $405.00 / $362.50, PF 1.78 /
  1.70 / 1.67; comm-adj: $379.91 / $356.16 / $318.10, PF 1.65 / 1.59 / 1.56) —
  robust within the tested range, though the monotonic decline is worth watching
  if slippage assumptions ever needed to be tested further out.
- **Same concentration risk as MNQ strat_212**: top-5 winners are 121% of net
  (ex-top-5 net is -$91.25) — without 5 of 33 trades this cell is also a loser.
- Max drawdown $141.25 — modest relative to net.

**Why not VALIDATED**: despite being the strongest cell on almost every other
axis (H1/H2 consistency, direction balance, slippage robustness), the same
severe top-5 concentration as MNQ strat_212 means the edge is not yet proven to
be a broad, repeatable pattern rather than a few large trades — and 33 resolved
trades, while past this lane's sufficiency convention, is still a modest sample
for a promotion decision. **Why not WAIT**: unlike MNQ strat_122, the sample is
past the sufficiency threshold and the signal is consistent across every cut
tested (both halves, both directions, three slippage levels) — this is real
evidence of an edge, just not yet enough to validate outright.

## Summary table

| Cell | Net (raw) | Net (comm-adj) | PF (raw/comm-adj) | N resolved | Classification |
|---|---:|---:|---:|---:|---|
| MNQ strat_212 | +$540.50 | +$354.02 | 1.19 / 1.12 | 126 | PROMISING BUT UNPROVEN |
| MES strat_212 | -$742.50 | -$1,075.50 | 0.85 / 0.80 | 225 | BROKEN |
| MNQ strat_122 | -$180.00 | -$197.76 | 0.53 / 0.51 | 12 | WAIT |
| MES strat_122 | +$428.75 | +$379.91 | 1.78 / 1.65 | 33 | PROMISING BUT UNPROVEN |

No enablement recommended or made for any cell. `risk_rules.yaml` unchanged.
