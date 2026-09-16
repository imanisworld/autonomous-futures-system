# Cross-instrument observation transport (`cross_instrument_observation_v1`)

Observation-only evidence across **MNQ, MES, M2K, MGC, MCL, MBT**. Nothing in
this design grants ingestion, strategy, risk, broker or execution eligibility.
Base: `b4cb614` (#584). Rule: **No proof, no run.** Default: **OFF**.

## Hard boundaries

| Boundary | Mechanism |
|---|---|
| Separate campaign | `execution/cross_instrument_observation.py`, own evidence file `cross_instrument_observation_v1.jsonl` + own state file. Never reads or writes `forward_ab_2026_08_v1`. |
| M2K/MGC/MCL/MBT never reach DecisionEngine / RiskEngine / PaperBroker / Tradovate | `webhook/app.py::_route_for_ticker` sends collection-only roots to `webhook/observation_transport.py`, never to `process_alert`; `process_alert` also has a first-line `OBSERVATION_ONLY` backstop. |
| Observation before trade-capacity gates | MNQ/MES leg runs before max-trades / loss-lockout / open-position early returns. Collection-only roots never read daily trading state. |
| 15m campaign only | The campaign is pinned to 15m. 5m uses its separate `tf5m/` lane. Other timeframes fail closed. Collection-only detector history is explicitly filtered to 15m. |
| M2K 5m normalization | `context/five_min_feed.py::_root` uses the canonical contract parser so `M2K1!` stays `M2K`. |
| Pine advisory ignored | Collection-only entry/stop/target/signal fields are stripped before market state is built. |
| M2K/MBT ingestion only into observation | Accepted only while the campaign is armed and only on the observation route. The trading allowlist is unchanged. MGC/MCL are also diverted to observation. |
| Product-aware sessions | Feed-health calendars are product-aware. This is feed expectation only, not strategy authority. |
| Evidence identity | `campaign × strategy × instrument × variant × evidence_epoch`; population gates never pool. |
| Crash idempotence | Evidence append is idempotent by `(record_type, candidate_id)`, and reporting defensively dedupes old duplicate rows. |
| Epoch isolation | Seen-bar identity and canonical Strat state are epoch-scoped; an arm from one epoch cannot fire in another. |
| Authoritative feed proof | `ops/cross_instrument_feed_health.py` requires an active-epoch 15m campaign seen-bar **and** the matching 15m BarHistory row. Generic webhook receipt and 5m freshness are non-authoritative. Collection-only roots also persist the latest 15m transport attempt and last error. |
| Zero-count visibility | Every configured population remains visible at zero; no silent lane disappearance. |
| Not activated | Campaign/env vars remain unset until a separate operator order. |

## Collection modes

### Structural outcome
For **MNQ, MES, M2K, MGC, MCL**, selected price-structure populations may be
resolved forward using gross geometry only: fill touch, MAE/MFE, R,
pessimistic same-bar handling, WIN/LOSS/NO_FILL/EXPIRED. Commission and
slippage assumptions remain `None`; these rows are not execution validation.

### Signal / metrics only
Occurrence, direction and raw geometry only; `bracket_authoritative: false` and
no simulated outcome. This includes ORB fade / EMA pullback broadly, VWAP/4HR
observers only where already scoped, and **all MBT structural families**.

MBT is deliberately downgraded to signal/geometry for structural families.
CME crypto is 24/7 in 2026 and this project has not established a defensible
position-expiry horizon for a hypothetical structural trade. An arbitrary ET or
UTC midnight must not manufacture MBT WIN/LOSS statistics. Gap-fill and
"overnight" families remain excluded from MBT because their session semantics
are also unproven.

## Authoritative feed-health gate

Use:

```sh
python3 ops/cross_instrument_feed_health.py --log-dir <logs> --json
```

A root is not proven merely because a webhook arrived. `proven_15m` requires:

1. the campaign persisted the exact bar under the active evidence epoch; and
2. that exact 15m timestamp exists in the instrument BarHistory.

For M2K/MGC/MCL/MBT, a newer failed 15m transport attempt produces
`TRANSPORT_ERROR` immediately and exposes `last_error`. A 5m alert never
updates the 15m transport marker. `ready_to_trust_collection_feed` is false
until all six roots have independent proof and every active root is healthy.

The ordinary `latest_webhook_<ROOT>.json` files remain useful receipt telemetry,
but **receipt freshness is not the campaign activation/readiness authority**.

## Activation sequence — provisional first, trusted only after proof

M2K/MBT are intentionally rejected while the campaign is OFF, so their real
bar arrival cannot be proven before the observation route is armed. Therefore
the safe sequence is:

1. Merge and deploy with both campaign env vars **unset**. Prove behavior-neutral
   operation of the existing system first.
2. Create/review TradingView alerts for all six roots. Do not infer feed support
   from parser support.
3. Arm a fresh epoch with the campaign still **observation-only**. This epoch is
   initially **PROVISIONAL / UNTRUSTED**.
4. Require `cross_instrument_feed_health.py` to prove every root independently.
   Inspect first real 15m payloads for ticker, timeframe, timestamp cadence and
   OHLC/context fields.
5. Only after all-six feed proof may the epoch be treated as trustworthy
   collection evidence. This does not authorize trading or strategy promotion.

## Known uncertainties and mitigations

### 1. Missing-bar contamination — highest remaining evidence risk
Freshness does not prove completeness. One missing 15m bar can change a Strat
sequence, a multi-bar detector, or a hypothetical resolution path.

**Mitigation before strategy validation:** add product-aware continuity coverage
for the campaign. Candidate detector windows and forward-resolution windows that
cross an unexplained missing bar should be tagged `DATA_GAP_CONTAMINATED` and
excluded from readiness gates. Do not backfill with invented bars. Maintenance
and known exchange closures must be classified separately from unexplained gaps.

### 2. MBT outcome horizon
There is no proven daily expiry boundary for a 24/7 product.

**Mitigation now:** MBT structural populations are signal/metrics only. No MBT
terminal outcome can satisfy the 30-outcome gate until a causal horizon is
explicitly designed and independently validated.

### 3. TradingView payload equivalence
Parser acceptance does not prove that all six alerts send the same useful bar
semantics or survive contract changes.

**Mitigation:** first-arm payload audit per root. Verify confirmed 15m cadence,
contract/root, timestamps, OHLC, volume and required optional fields. A root
with incomplete payload semantics remains collection-limited.

### 4. Historical continuity / rolls
M2K can use the existing quarterly-equity roll machinery. Continuous historical
roll support for MGC/MCL/MBT is not proven.

**Mitigation:** collect them forward. Do not claim continuous historical replay
for MGC/MCL/MBT until dated-contract selection and roll provenance are proven.

### 5. Session/calendar exceptions
The product calendar is used for feed-health expectations, but holidays,
special maintenance and exchange rule changes can create legitimate gaps.

**Mitigation:** calendar exceptions must not silently become missing-data faults
or strategy rules. Record them as known/unknown closure classifications and keep
strategy/session semantics separate from feed-health semantics.

### 6. Correlated instruments
MNQ, MES and M2K are correlated equity-index products. Micros and their larger
counterparts would also duplicate underlying moves.

**Mitigation:** never pool instruments to satisfy sample gates. The campaign's
population identity already enforces this; any future cross-market summary must
remain informational only.

### 7. Costs and execution realism
New-instrument commission/slippage and broker liquidity behavior are not proven.

**Mitigation:** signal collection may proceed, but no gross geometry row becomes
paper-execution validation until instrument-specific costs/fills are separately
proven.

## Deferred

- product-aware continuity / data-gap contamination gate;
- MGC/MCL/MBT continuous historical roll schedules;
- per-instrument commissions/slippage;
- per-instrument trading rules;
- MBT structural outcome horizon;
- any broker or live eligibility expansion.

None of those are implied by collecting observations.

## Verification

```sh
python3 -m pytest -q tests/test_cross_instrument_observation_transport.py
python3 -m pytest -q tests/test_cross_instrument_observation_integrity.py
python3 -m pytest -q tests/test_cross_instrument_feed_health.py
python3 -m pytest -q
git diff --exit-code b4cb614 -- risk_rules.yaml config/forward_evidence_campaign.json execution/tradovate_broker.py execution/paper_broker.py execution/forward_evidence_campaign.py execution/evidence_identity.py strategy/signal_engine.py risk/risk_engine.py context/mes_122_paper_lane.py context/wide_stop_execution.py tradingview/ deploy/
```
