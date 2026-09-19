# Forward 1m Trigger Evidence — Durable Response-Proof Amendment — 2026-09-18

## Status

**Additive evidence-safety amendment only.**

The frozen preregistration remains:
`docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md`.

This amendment does not change any trigger formula, session window, sample
threshold, IOC tolerance, strategy verdict, risk rule, or execution authority.

## Defect found

The observer event files persist trigger/arm evidence, but the actual runner
response was only available transiently plus in `latest_webhook*.json`.
Those files are rolling snapshots overwritten by later alerts and do not store
`execution_reachable`.

Therefore a natural event could not later prove all frozen isolation fields:
`decision`, null `fill`, null `risk`, and `execution_reachable=false`.

No natural 4HR or 3-2-2 observer event had occurred before this defect was
identified, so no prospective event needs invalidation.
## Additive proof artifact

For every runner result containing an emitted 4HR or 3-2-2 observer event,
append one row to:

`tf1m/observer_response_audit_<date>.jsonl`

Each row records:
- payload timestamp, ticker, timeframe, and event id;
- the emitted observer event snapshot;
- actual runner `decision`;
- whether `fill is None`;
- whether `risk is None`;
- actual `execution_reachable`;
- response resolution and observer errors.

The artifact is evidence-only and fail-soft. A write failure must never change
the webhook decision, ingestion, risk, or broker behavior.

## Review binding

A natural observer event cannot count toward the prospective mechanism sample
unless a matching durable response-audit row exists for the same payload/event.
Where an `arm_key` exists, it must also match.

Missing response proof = **UNKNOWN / HOLD**.
A recorded `TRADE` decision, non-null fill/risk, or
`execution_reachable=true` = **UNSAFE / STOP**.

The original event-class rules remain unchanged:
- 1m touch responses must remain `ONE_MIN_CONTEXT`;
- 3-2-2 5m arm/expiry responses must remain `FIVE_MIN_CONTEXT`;
- observer lanes retain no paper, DEMO, live, or broker authority.

## Read-only review

Use:

`python3 scripts/forward_one_min_trigger_review.py --log-dir <copied-log-dir>`

The reviewer checks persisted event/response pairing, duplicate accepted arms,
raw 1m trigger inequalities, 4HR completed-1H stop anchoring, 3-2-2 arm
reconstruction, and completed-5m timing/detachment metrics. It tracks the
3-touch early mechanism checkpoint and a conservative version of the later
10-touch / 20-full-window-day / 2-calendar-month / 3-LONG / 3-SHORT threshold.

The tool never calculates expectancy, changes strategy parameters, or grants
execution authority. Missing proof fails closed.

## Activation boundary

This proof exists prospectively only after the response-audit code is deployed.
Do not reconstruct old response rows from rolling latest-webhook snapshots.

Any natural event collected before activation may be retained as raw observer
evidence but cannot satisfy the execution-isolation requirement.

No proof, no run.
