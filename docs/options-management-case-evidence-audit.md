# Options Management-Case Evidence Audit (Increment 25A/25C)

Increment 23 shipped four hand-authored `ManagementCase` records
(`options_manager/validation/management_cases.py`): NOK, EBAY, ADP, ARM.
None of the four had been checked against an independent record at the
time. This audit reconciles all four against the actual Robinhood
account order history and candle data, and records the resulting
corrections/deprecations made in Increment 25C.

A new `evidence_status` field (`EvidenceStatus`) was added to
`ManagementCase` to carry the result of this reconciliation going
forward, independent of `classification` (which describes the
management *lesson*, not whether the numbers were checked):

- `broker_verified` — not yet used by any existing case; reserved for a
  case authored directly from broker records from the start.
- `corrected_from_broker_records` — the case had one or more factual
  errors, but the underlying lesson survived the correction.
- `contradicted_as_described` — the real trade found in broker records
  does not support the case's original narrative/lesson at all.

`build_active_management_case_dataset()` was added alongside the
existing `build_management_case_dataset()`: the former excludes any
`contradicted_as_described` case, for use wherever a case is meant to be
treated as a verified lesson rather than a historical record of a claim.

## NOK — `corrected_from_broker_records`

| Field | Original (Increment 23) | Verified (broker order history) |
|---|---|---|
| Exit premium | $1.02, single exit | ~$0.93 blended, **3 separate exits** over 5 weeks (2026-05-18 ×2 @ $0.95, 2026-06-12 @ $0.90) |
| Realized P&L | -$75 | **-$101** |

The management lesson (emotional cut, no stop defined, thesis not
actually broken at the time) survives the correction. Fields corrected
in place; a new `exit_tranche_count=3` field records the scaled-exit
structure.

## EBAY — `corrected_from_broker_records`

| Field | Original (Increment 23) | Verified (broker order history) |
|---|---|---|
| Entry premium | $1.06 | **$1.18** |
| Position size | 4 contracts | **5 contracts** |
| Narrative | "scaled out across two pre-defined GEX-wall targets ($106, $108)" | Target 1 (~$106) confirmed **during RTH** before the exits; target 2 ($108) did not print until **post-market, after the position was already fully closed** — not a target the trade captured |

Realized P&L ($1,884, the sum of the four exit fills) was already
correct and is unchanged. The management lesson (rule-based scale-out
against a pre-defined target) survives, narrowed to the one target that
actually confirmed live.

## ADP — `contradicted_as_described`

| Field | Original (Increment 23) | Verified (broker order history) |
|---|---|---|
| Position size | 8 contracts | **2 contracts** |
| P&L shape | "+116% peak reversing to -93% by expiry" | Plain straight-line decline, entry $0.60 → exit $0.22 over 8 days, **~-63%**, no evidence of any intraday peak |

This is not a units/precision error — the size is off by 4x and the
described peak-then-crash shape isn't in the record at all. The
original claim is **preserved unchanged** in the case's fields (as the
historical record of what was claimed) but the case is flagged
`contradicted_as_described` and excluded from
`build_active_management_case_dataset()`. A corrected case built from
the real 2-contract trade may be added in a future increment; it does
not exist yet.

## ARM — `contradicted_as_described`

| Field | Original (Increment 23) | Verified (broker order history) |
|---|---|---|
| Trade shape | Multi-day hold, exited early on external recommendation, thesis never broke | Same-day, **33-minute**, 1-DTE trade (bought and sold 2026-04-30, expiring 2026-05-01) |
| Realized P&L | +$624 | **-$380** ($624 was the exit credit, not net P&L — cost basis was $1,004) |

This is a contradiction of the trade's direction (gain vs. loss) and
shape (multi-day swing vs. same-day scalp), not a rounding difference.
The original claim is **preserved unchanged** in the case's fields but
the case is flagged `contradicted_as_described` and excluded from
`build_active_management_case_dataset()`. A corrected case built from
the real same-day scalp may be added in a future increment; it does not
exist yet.

## Net effect

- `build_management_case_dataset()` — unchanged shape, still returns all
  4 cases keyed the same way; two are now factually corrected, two carry
  an explicit contradiction flag rather than silently standing in as
  verified truth.
- `build_active_management_case_dataset()` (new) — returns only NOK and
  EBAY. Anything that wants "verified management lessons" rather than
  "the full historical record including disputed claims" should call
  this instead.
- No scanner, broker, or execution behavior changed. This increment
  touched only `options_manager/validation/management_cases.py`,
  `options_manager/validation/__init__.py` (exports), and
  `tests/test_options_management_cases.py`.
