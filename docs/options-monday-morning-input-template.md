# Options Monday Morning Input Template

A blank, reusable fill-in-the-blank template for the fields the existing
advisory pipeline actually needs, so a real Monday-morning session isn't
spent re-deriving what to write down or discovering a missing field
partway through. This is a template and a usage note only -- no code,
strategy, or execution change of any kind. Nothing here adds a new
field, module, or validator; every line below maps directly onto a
field already defined in `options_manager/validation/morning_scan_packet.py`,
`contract_quality_gate.py`, `proof_packet.py`, and `advisory_decision.py`.

## How to use this

1. Fill out all three sections below by hand, from the chart and option
   chain, before or at the moment a setup is being considered -- not
   reconstructed afterward. (Same discipline
   `docs/options-forward-proof-packet.md` describes for a single trade's
   proof packet.)
2. Feed the **Market context** + **Ticker** sections into
   `check_morning_scan_packet_intake()` as `{"market_context": {...},
   "candidates": [{...}]}`.
3. Feed the **Ticker** + **Contract** sections into
   `check_proof_packet_intake()` (entry plan + contract-quality-relevant
   facts) and `check_contract_quality_intake()` (contract fields only).
4. Combine the results with `check_advisory_decision_intake()` (or
   `evaluate_advisory_decision()` directly) to get one `TAKE` / `WAIT` /
   `AVOID` verdict.
5. If the position is later opened, the **Ticker**/**Contract** facts
   plus the position's current state feed
   `check_position_management_checklist_intake()` for the ongoing
   `HOLD` / `TRIM` / `EXIT` review.

This template does not run anything itself -- it is what a human fills
out by hand before those existing, already-merged functions are called.

## Market context

_(maps to `MarketContext` in `morning_scan_packet.py`)_

```text
GEX regime:
SPY flip:
QQQ flip:
SPY trend:
QQQ trend:
Gap %:
Location vs yesterday:
```

## Ticker

_(maps to `TickerCandidate` in `morning_scan_packet.py`, and the entry-plan
fields of `ProofPacket` in `proof_packet.py`)_

```text
Ticker:
Direction:
Setup type:
Timeframe:
Spot:
Flip:
Regime:
ORB high:
ORB low:
RES1:
RES2:
SUP1:
Distance to RES1:
Distance to SUP1:
Volume:
Signa grade:
Signa score:
Entry trigger:
Stop / invalidation:
Target 1:
Target 2:
R:R:
Current candle behavior:
Status:
```

## Contract

_(maps to `ContractQualityInput` in `contract_quality_gate.py`)_

```text
Expiration:
Strike:
Premium:
Bid:
Ask:
Spread %:
Volume:
Open interest:
Max contracts:
Max dollar risk:
IV / event risk:
Theta risk:
```

## Output expected

Running the filled-out template through the existing pipeline should
produce, at minimum:

- `TAKE` / `WAIT` / `AVOID` (from `AdvisoryDecisionResult.verdict`)
- missing proof fields, if any (`IntakeResult.missing_fields` /
  `blocking_reasons`)
- contract blocks, if any (`ContractQualityResult.blocking_reasons`)
- warnings, if any (`warnings` on any of the above)
- the next required action
  (`AdvisoryDecisionResult.next_required_action`)

A blank field left in this template is not filled in automatically by
anything downstream -- an incomplete template produces an honest
`missing <field>` result, the same fail-closed behavior every
`check_*_intake()` function in this package already guarantees.
