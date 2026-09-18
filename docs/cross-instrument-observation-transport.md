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
Configured price-structure populations may be resolved forward using gross
geometry only: fill touch, MAE/MFE, R, pessimistic same-bar handling,
WIN/LOSS/NO_FILL/EXPIRED. Commission and slippage assumptions remain `None`;
these rows are **observation evidence, not paper-execution validation**.

### Signal / metrics only
Occurrence, direction and raw geometry only; `bracket_authoritative: false` and
no simulated outcome. This includes ORB fade / EMA pullback broadly, and
VWAP/4HR observers only where already scoped.

### MBT caution
The transport currently records the configured structural geometry/outcomes for
MBT so we do not throw away exploratory information, but **MBT terminal outcomes
must not be used for strategy validation or promotion yet**. CME crypto is 24/7
in 2026 and this project has not established a defensible hypothetical-position
expiry horizon. The present observation-day boundary is a bookkeeping boundary,
not a validated trading rule. Gap-fill and "overnight" families remain excluded
from MBT because those session semantics are also unproven.

Before any MBT performance conclusion, either:

1. define and independently validate a causal MBT resolution horizon; or
2. formally downgrade MBT structural populations to signal/geometry-only in a
   separately reviewed policy change.

Until one of those is proven, MBT structural outcomes are **exploratory / HOLD**.

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

**Mitigation implemented:** `execution/cross_instrument_evidence_quality.py`
provides the authoritative read-only continuity gate. For terminal structural
outcomes it reconstructs the detector dependency window, extends through the
signal-to-resolution interval, compares observed 15m BarHistory against the
product-aware expected grid, tags any missing expected bar
`DATA_GAP_CONTAMINATED`, and excludes the row from quality-eligible readiness
counts. Do not backfill with invented bars. Maintenance and known exchange
closures must remain separately classified from unexplained gaps.

**Verified incident — M2K 2026-09-17:** the persisted 15m BarHistory is missing
11 M2K bars from **13:30Z through 16:00Z** (surrounding boundary 13:15Z →
16:15Z), while MNQ/MES/MGC/MCL/MBT are complete over the audited 13:00Z–16:30Z
window. Server logs split the incident into two phases: 13:45Z–14:15Z had only
11 normal successful webhook POSTs and no 500s, so one expected alert path was
absent/not arriving for a cause that remains unproven; 14:30Z–16:15Z had 11
normal successes plus four retries of one failing alert every 15 minutes. The
second phase is proven to be a re-created TradingView alert with a non-ASCII
webhook secret triggering the old string `hmac.compare_digest` TypeError.
Current/deployed authentication code fails such input closed as HTTP 401 instead.
The deployed `cross_instrument_evidence_quality_v1` gate was checked against
the real gap and classified **8 M2K terminal outcomes** whose dependency /
resolution windows crossed it as `DATA_GAP_CONTAMINATED`; all eight were
`eligible=false`. This proves the contaminated terminal outcomes are excluded
from readiness counts. Do not backfill or attribute the pre-14:30 phase to the
malformed-secret defect without new evidence. Full incident record:
[`m2k-feed-gap-incident-2026-09-17.md`](m2k-feed-gap-incident-2026-09-17.md).

### 2. MBT outcome horizon
There is no proven daily expiry boundary for a 24/7 product.

**Mitigation:** collect the raw exploratory evidence, but classify MBT structural
outcomes as HOLD and exclude them from any validation/promotion decision until a
causal horizon or a signal-only policy is separately proven.

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

### 8. Detector-input reproducibility
A candidate is useful only if the state that produced it can later be explained.
BarHistory preserves OHLC/volume/timeframe, but some detectors also consume
payload-derived Strat/session/context fields.

**Mitigation before using results for validation:** preserve or reconstruct a
sanitized per-bar detector-input snapshot (no secrets) so a sampled candidate can
be replayed from its inputs. Until then, use spot-replay audits of first-arm rows
as a required quality check.

## Deferred / separate proof work

- sanitized detector-input provenance / replay spot-check tooling;
- MBT structural outcome horizon or formal signal-only policy;
- MGC/MCL/MBT continuous historical roll schedules;
- per-instrument commissions/slippage;
- per-instrument trading rules;
- any broker or live eligibility expansion.

None of those are implied by collecting observations.

## Verification

```sh
python3 -m pytest -q tests/test_cross_instrument_observation_transport.py
python3 -m pytest -q tests/test_cross_instrument_observation_integrity.py
python3 -m pytest -q tests/test_cross_instrument_feed_health.py
python3 -m pytest -q tests/test_cross_instrument_evidence_quality.py
python3 -m pytest -q
git diff --exit-code b4cb614 -- risk_rules.yaml config/forward_evidence_campaign.json execution/tradovate_broker.py execution/paper_broker.py execution/forward_evidence_campaign.py execution/evidence_identity.py strategy/signal_engine.py risk/risk_engine.py context/mes_122_paper_lane.py context/wide_stop_execution.py tradingview/ deploy/
```
