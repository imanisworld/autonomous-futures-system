# AFS-0339 — options Daily late-entry source-only audit and held-out proof plan

Status: **QA / HOLD**, no deploy, no option submission, no timer/cadence change.
Source base: `94939d582fb376e94a055afdf0008dfcfe54cc9a` (2026-10-20).
Review: independent Grok exact-head; operator enablement required later.

## What GitHub already implements

The supplied AFS Options handoff cites deployed release `47ae01ac` and says the
Daily target floor defaults to `0.0`. **That statement conflicts with the
current GitHub main source**:

- `alert_ranker/config.py` already sets `paper_v1_daily_min_target_rr=1.0`,
  including a `1.0` environment fallback.
- `alert_ranker/multisetup_scanner.py` already passes that floor into
  `find_targets` using the canonical trigger and invalidation.
- `options_manager/levels/target_finder.py` already skips levels closer than
  the floor; `tests/test_options_daily_entry_timing_and_target_floor.py`
  covers 0R and 1R cases.

Therefore **this PR does not create or re-enable a second 1R rule**. Its
existing default/behavior must be reconciled against the actual deployed config
and AFS-0264 freeze before any installation. Turning that historical default
OFF is a separate behavioral change and is not silently applied here.

## New observation (default OFF)

`OPTIONS_PAPER_V1_TRIGGER_TIMING_OBSERVE_ENABLED=false` is the default. When
operator enabled later, triggered Daily candidates only append a first-scan
timestamp and signed first-sight underlying excursion to a separate shared
JSONL file, then copy those fields into each Daily candidate's `setup_inputs`.

- `trigger_known_at`: first **scanner observation**, **not** actual break time.
- `first_eval_price_vs_trigger_r`: signed (favorable + / adverse -) movement
  from canonical trigger to first evaluated underlying quote, divided by the
  trigger-to-invalidation risk, for CALL and PUT independently. Invalid/absent
  risk or price remains `null`; never invent a value.
- Same underlying Daily episode reuses the first durable observation across
  rescans or scanner process restarts.
- The timing observer uses its own append-only shared-log journal, no new
  SQLite write, no schema change, no DB-lock behavior change.
- With flag OFF, no fields, writes, alerts, signal/risk, or order changes.

The stored `trigger_known_at` is observational lag metadata. Measuring the
actual trigger crossing time requires separately approved intraday arming; phase
2 is **not approved**.

## Held-out proof required before any target-floor policy change

1. Pre-register a calendar-blocked period strictly after the investigation
   window, data sources, session filters, price/quote timestamp conventions,
   option chain quality filters, the current 1R rule, and a comparator using
   0R without changing the observed live classifier.
2. Freeze outcomes before inspection. Compare candidate counts, `target_missing`,
   `TARGET/STOP_CONSUMED_AT_ENTRY`, freshness and signed first-sight R with
   identical underlying bars and identical fill/exit assumptions. Preserve
   non-outcomes as non-outcomes in **both** arms.
3. Evaluate across multiple months and distinct market regimes; report all
   samples and confidence intervals. The observed 101 timely setups are
   insufficient for profitability validation.
4. Prohibit retroactive upgrading of censored rows, stop/target re-ordering,
   optimistic fills, or mixing hypothetical outcomes into ACTIVE P&L.
5. A gate-changing proposal needs an independent exact-head review, locked
   held-out evidence, and a new explicit operator enablement authorization.
   The current PR is **not** that authorization.

## Gates

No work in `storage.py`, no DB lock remediation, no pre-trigger arming, no
scanner cadence change, no API quota increase, no VPS mutation or release build.
AFS-0329 lock repair and weekend v9 immutable recut remain separate prerequisites
for any future runtime installation. Freeze AFS-0264 remains in force.
