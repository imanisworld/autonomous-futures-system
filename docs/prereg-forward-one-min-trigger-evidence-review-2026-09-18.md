# Preregistration — Forward 1m Trigger Evidence Review — 2026-09-18

## Scope

This preregistration governs prospective review of the two active MNQ
lower-latency trigger observers:

- 4HR Re-Trigger 1m observer;
- 60M 3-2-2 First Live 1m observer.

Purpose: determine whether the deployed observation lanes reproduce their
documented trigger semantics safely and causally on natural market events.

This review does **not** validate strategy profitability and does not authorize:
- paper fills;
- DEMO orders;
- live orders;
- broker routing;
- risk-policy changes;
- new setup discovery;
- LC_ZONE use;
- any strategy promotion.

Active futures release at preregistration:
`6d5b224aa5c208cad0f1d39c09eda13c6171b98b`.
## Fixed runtime safety requirements

At every review checkpoint, require:
- `LIVE_TRADING_ENABLED=false`;
- `TRADOVATE_ENV=demo`;
- `SCHEDULE_MODE=always_on_shadow`;
- `EXIT_MODE=static`;
- `MAX_CONTRACTS_HARD_CAP=1`;
- `ONE_MIN_TRIGGER_ENABLED=true`;
- `ONE_MIN_322_OBSERVER_ENABLED=true`;
- matching proof-critical pins;
- live-box drift guard `ok=true`;
- no missing pins, unpinned overrides, or mismatches.

Any failure is a **STOP / HOLD** condition. Do not interpret trigger evidence
collected while the runtime proof is invalid.

## Evidence sources

4HR:
- `tf1m/4hr_trigger_evidence_<date>.jsonl`;
- authoritative journaled `four_hr_retrigger_state`;
- MNQ `tf1m` and `tf5m` bars.

3-2-2:
- `tf1m/322_first_live/state_<date>.json`;
- `tf1m/322_first_live/evidence_<date>.jsonl`;
- MNQ `tf1m` and `tf5m` bars.
## Review unit

The independent unit is one distinct armed setup / `arm_key`.

Do not count repeated webhook deliveries, duplicate evidence responses, or
multiple 1m bars after the first valid break as additional samples.

A natural trigger-touch sample is eligible only when:
- the observer was already armed before the touch;
- the touch occurred in the documented session window;
- required reference data was complete;
- the bracket geometry was valid at the touch;
- the event was produced by natural authenticated feed data;
- no manual/synthetic request created the evidence record.

Synthetic tests remain software verification only and never count toward the
prospective sample.

## 4HR arm correctness

For every eligible 4HR touch verify:
- source state status was `ARMED`;
- source state trading date matched the event date;
- direction was LONG or SHORT;
- trigger and target equal the authoritative persisted state;
- touch occurred from 09:30 through before 11:00 ET;
- LONG touch satisfies 1m high >= trigger;
- SHORT touch satisfies 1m low <= trigger;
- gap-through reference uses the 1m open when beyond the trigger.
## 4HR stop-anchor correctness

For every 4HR touch:
- `stop_bar_ts` must identify the last genuinely completed 1H reference
  available before the 1m trigger bar opened;
- that 1H bar must contain all 12 constituent 5m bars;
- its completion time must be <= the 1m trigger-bar open;
- LONG stop = that completed 1H low;
- SHORT stop = that completed 1H high.

`COMPLETED_1H_STOP_MISSING` is a valid fail-closed block, not a touch sample.

Any event that uses an incomplete/newly-completing 1H candle is a **causal
parity failure** and immediately blocks promotion discussion.

## 3-2-2 arm correctness

For every 3-2-2 trading day verify:
- setup classification occurs only at the 10:00 ET boundary;
- all 36 expected 5m bars from 07:00 through 09:55 ET are present;
- canonical 7AM / 8AM / 9AM state-machine output matches the observer state;
- `ARM_BLOCKED / REFERENCE_DATA_INCOMPLETE` occurs if any required bar is
  missing;
- an `ARMED` event records the canonical direction, trigger, stop, target,
  setup timestamp, and expiry;
- 1m itself never creates or modifies the setup.
## 3-2-2 First Live correctness

For every eligible 3-2-2 touch:
- event occurs from 10:00 through before 11:00 ET;
- LONG requires 1m high > trigger;
- SHORT requires 1m low < trigger;
- equality alone is not a break;
- first valid break only is counted;
- gap-through uses 1m open as `trigger_reference`;
- `trigger_reference` must remain strictly inside the stored stop/target
  bracket;
- invalid gap geometry must produce
  `TRIGGER_BLOCKED / ENTRY_BRACKET_INVALID_AT_TOUCH`;
- untouched arms expire at the 11:00 boundary.

Any 3-2-2 `TRIGGER_TOUCH` without a preceding valid same-day `ARMED` state
is an immediate parity failure.

## 1m versus completed-5m timing

For each eligible natural touch reconstruct the 5m bar containing that 1m
trigger and calculate:
- 1m trigger-bar open timestamp;
- 1m observer decision time;
- equivalent completed-5m decision time;
- timing advantage in seconds;
- completed-5m close;
- absolute trigger-to-5m-close distance in ticks;
- adverse detachment in ticks.
Adverse detachment definition:
- LONG = max(0, five_min_close - trigger) / tick_size;
- SHORT = max(0, trigger - five_min_close) / tick_size.

Report, per strategy:
- n;
- median adverse detachment;
- p90 adverse detachment;
- maximum adverse detachment;
- share beyond the historical IOC tolerance
  (4HR: 8 ticks; 3-2-2: 32 ticks);
- median and p90 timing advantage.

Do not use these forward observations to retune either IOC tolerance.

## Dedupe requirements

For each `arm_key`:
- exactly one accepted `TRIGGER_TOUCH` evidence event may exist;
- exactly one claim file may own the touch;
- repeated webhooks may be ignored or reported as duplicate, but must not append
  another accepted touch;
- no later 1m bar may become a second sample for the same arm.

Any duplicate accepted touch is a **safety blocker**.

## Execution-isolation requirements

For every eligible 1m touch response require:
- decision remains `ONE_MIN_CONTEXT`;
- `fill is None`;
- `risk is None`;
- `execution_reachable=false`.
4HR evidence additionally requires:
- `mode=paper_evidence_only`;
- `trade_authorized=false`;
- `external_broker=false`.

3-2-2 evidence additionally requires:
- `mode=observation_only`;
- `trade_authorized=false`;
- `paper_fill_authorized=false`;
- `external_broker=false`.

For 3-2-2 5m arm/expiry responses require:
- decision remains `FIVE_MIN_CONTEXT`;
- no fill;
- no risk;
- `execution_reachable=false`.

Any correlated broker submission, PaperBroker position, non-null fill/risk, or
reachable execution path is an immediate **UNSAFE / STOP** result.

## Blocked and expired events

Report separately; do not silently discard:
- missing completed 1H stop;
- invalid bracket at touch;
- missing 3-2-2 reference data;
- canonical setup not armed;
- 3-2-2 expiry without touch;
- duplicate deliveries.
These events do not count as successful touch samples, but their counts and
reasons remain part of the forward evidence.

A high block rate may indicate feed/state quality problems even when safety
behavior is correct.

## Checkpoints

### Immediate safety review

Review the first natural event of every event class:
- first 4HR touch;
- first 4HR blocked touch;
- first 3-2-2 ARMED event;
- first 3-2-2 touch;
- first 3-2-2 blocked touch;
- first 3-2-2 expiry.

Purpose: catch implementation defects early. This checkpoint cannot authorize
promotion.

### Early mechanism checkpoint

After **3 distinct natural trigger touches per strategy**, verify all arm,
timing, dedupe, geometry, and isolation rules.

Classification at this checkpoint is only:
- MECHANISM CLEAN SO FAR; or
- HOLD / DEFECT FOUND.
## Minimum sample before any promotion discussion

Evaluate each strategy independently.

No discussion of granting paper-fill authority is allowed until that strategy
has:
- at least **10 distinct natural TRIGGER_TOUCH arm_keys**;
- evidence spanning at least **20 trading days** from first eligible arm to
  checkpoint;
- evidence spanning at least **2 calendar months**;
- zero arm/parity failures;
- zero causal-data failures;
- zero duplicate accepted touches;
- zero execution-isolation violations;
- zero unauthorized broker/PaperBroker activity.

For a direction-general conclusion, the 10 touches must include at least:
- 3 LONG;
- 3 SHORT.

If direction balance is not met, the mechanism may only be described for the
observed direction; executable authority remains unchanged.

These are minimum mechanism-proof thresholds, not profitability-validation
thresholds.

## Promotion discussion gate

At the minimum sample, classify only the trigger mechanism:
- **PROSPECTIVE TIMING PARITY SUPPORTED**;
- **PROMISING BUT INSUFFICIENT**;
- **HOLD / DEFECT FOUND**;
- **UNSAFE**.
PROSPECTIVE TIMING PARITY SUPPORTED requires:
- every eligible arm/touch matches the documented formula;
- every required fail-closed case blocks correctly;
- all dedupe/isolation requirements pass;
- no unexplained source-state divergence;
- no outcome-based exclusions.

Even that classification does **not** authorize paper or live execution.
A separate change request, risk review, replay/live parity review, and explicit
authorization are required.

## Prohibited analysis choices

Do not:
- combine 4HR and 3-2-2 samples to reach n=10;
- count synthetic smoke tests;
- count duplicate webhook deliveries;
- remove blocked or unfavorable events from reporting;
- alter trigger inequalities after seeing evidence;
- retune IOC tolerances from forward data;
- add LC_ZONE filtering or target clipping;
- add regime, Signa, VWAP, direction, month, or outcome filters;
- infer strategy expectancy from this observer-only evidence.

## LC_ZONE ruling

LC_ZONE v1 remains **HOLD**.

No 4HR target-vs-zone A/B, zone clipping, zone gate, or replacement zone rule
is part of this forward observer review. Any new zone construct requires its
own preregistration.

No proof, no run.
