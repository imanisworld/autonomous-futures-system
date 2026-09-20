# 4HR Pre-Armed 4H 2→2 Continuation Treatment — 2026-09-20

## Verdict

**PROMISING BUT UNPROVEN / OBSERVATION TREATMENT ONLY.**

Keep the broad MNQ 4HR Re-Trigger as the control. Prospectively tag the subset
whose latest completed ET-wall-clock 4H sequence is
`strat_22_continuation`. The tag has no signal, risk, paper-fill, DEMO, broker,
or live authority.

This is a one-variable treatment: trigger, causal stop anchor, target, costs,
slippage model, session and source strategy remain unchanged.

## Frozen sources

- corrected pre-armed timing artifact:
  `scripts/4hr_prearmed_touch_ab_2026-09-18.json`
  SHA-256 `e19671fc9c5402aa96ba20f2fd29e0c1fc6d129b1bca88aef2f8331b76da074e`;
- causal 4H attribution artifact:
  `scripts/4hr_compression_zone_attribution_2026-09-18.json`
  SHA-256 `282a9f9feb2185a97218be28a9fc49fc50ded7ae3479705e7776d24463356494`;
- combined audit:
  `scripts/4hr_prearmed_continuation_treatment_2026-09-20.json`.

The corrected pre-armed reconstruction reproduced the frozen broad-control
results exactly before the treatment was scored.
## Corrected pre-armed A/B

At 1 adverse tick:

| Population | n | W/L | Net | PF | H1 | H2 | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| broad control | 80 | 40/40 | +$1,414.60 | 1.299 | +$861.80 | +$552.80 | $863.78 |
| 4H 2→2 continuation | 29 | 16/13 | **+$1,444.58** | **2.032** | +$683.28 | +$761.30 | **$326.94** |
| non-continuation complement | 51 | 24/27 | -$29.98 | 0.991 | +$362.50 | -$392.48 | — |

At 3 adverse ticks the treatment remains positive:
**+$1,402.58 / PF 1.984 / H1 +$663.28 / H2 +$739.30**.
The complement falls to **-$107.98 / PF 0.968** with H2 **-$433.48**.

This supports prospective collection, not replacement of the control.

## Concentration blocker

The treatment remains historically concentrated. Its top three profitable
months contribute about **94% of 1-tick net** and **96% of 3-tick net**.
That is far above the existing wide-stop re-open concentration criterion.

Therefore the treatment is not validated and cannot replace the broad strategy
from this historical subset alone.
## Why no separate completed-5m paper ledger

The active `wide_stop_4k` contract is deliberately capped at 300 ticks with
R:R >= 1.0. Under the corrected pre-armed mechanics the continuation subset at
that contract is only 14 trades, +$58.78 / PF 1.068, with H2 -$60.86 at one
tick; at three ticks H2 is -$72.86.

The older 400-tick + R:R >= 1.0 research cell is stronger under pre-armed
mechanics (17 trades, +$962.34 / PF 2.121, both halves positive), but it does
not survive the current completed-5m IOC8 mechanism cleanly: only seven fills,
with H1 -$152.44 and H2 +$772.58.

Do not loosen the active 300-tick lane or create a 400-tick completed-5m paper
ledger from these results. The apparent improvement belongs to the pre-armed
mechanism and must be tested there.

## Exact forward tag definition

The historical `pre4_seq` labels were independently reconstructed from the
frozen 5m corpus. The canonical ET wall-clock 4H aggregation already used by
`strategy.four_hr_retrigger.aggregate_et_bars` reproduced **80/80** labels
and **29/29** continuation memberships.

For comparison, fixed UTC 4H buckets matched 62/80 and CME-session 18:00
anchored buckets matched 25/80. Neither is authorized for this treatment.
## Prospective collection contract

For every natural eligible 4HR 1m touch, retain the normal broad control event
and append read-only metadata:

- definition: `completed_et_wall_clock_4h_sequence_v1`;
- treatment: `4hr_prearmed_4h22_continuation_v1`;
- latest completed 4H sequence;
- treatment eligibility true/false/unknown;
- constituent completed 4H starts, counts and bar types.

Missing 4H context is **unknown**, never silently false.

The existing 1m execution-isolation contract is unchanged:
`decision=ONE_MIN_CONTEXT`, `fill=None`, `risk=None`,
`execution_reachable=false`, `trade_authorized=false`,
`external_broker=false`.

The existing preregistration
`docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md` remains
controlling. This tag cannot accelerate its 10-touch / 20-trading-day /
2-calendar-month mechanism gate and cannot itself authorize paper, DEMO or
live execution.

## Next decision

After the pre-armed mechanism itself clears its prospective gate, resolve the
same natural touch population offline under one frozen outcome contract and
compare broad control vs the preregistered continuation subset. A separate
explicit change request is required before any treatment receives paper-fill
authority.

No proof, no run.
