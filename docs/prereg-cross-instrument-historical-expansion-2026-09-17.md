# Prereg — Cross-Instrument Historical Expansion (M2K / MGC / MCL / MBT)

**Status:** SPEC ONLY / PAPER-RESEARCH ONLY / NO OUTCOMES AUTHORIZED

**Purpose:** extend the existing structural-level evidence framework beyond MNQ/MES without contaminating or retroactively changing the completed MNQ/MES P1/P2 prereg population. The existing `cross_instrument_observation_v1` forward campaign remains the live collection source for all six roots; this document governs only historical corpus, replay, and parity proof for M2K/MGC/MCL/MBT.

## 1. Hard boundaries

1. M2K/MGC/MCL/MBT remain observation/research only. Nothing here grants DecisionEngine, RiskEngine, PaperBroker, Tradovate, strategy, or execution eligibility.
2. Do not amend the MNQ/MES structural-level prereg population to pretend these four roots were included originally.
3. Do not pool instruments. Every gate is evaluated per instrument × family.
4. No candidate outcomes, expectancy, P&L, win rate, MAE/MFE performance conclusions, tuning, or promotion analysis are authorized by this prereg.
5. No runtime, deployment, `.env`, risk, strategy, broker, collector, or TradingView changes are authorized.
6. Missing or ambiguous source/roll/session data blocks that instrument from historical parity; forward observation may continue independently.
7. A field that is truly not defined for a product/session is recorded as `NOT_APPLICABLE`, never silently imputed from another instrument.

## 2. Roots and current readiness

| Root | Existing forward observation | Tick metadata | Historical continuous-roll readiness | Initial classification |
|---|---|---|---|---|
| M2K | yes, collection-only | present | existing quarterly equity-index roll machinery supports it | READY_FOR_SOURCE_PROBE |
| MGC | yes, collection-only | present | dated-contract selection / continuous roll not proven in current Polygon client | BLOCKED_ON_ROLL_PROOF |
| MCL | yes, collection-only | present | dated-contract selection / continuous roll not proven in current Polygon client | BLOCKED_ON_ROLL_PROOF |
| MBT | yes, collection-only | present | dated-contract selection / continuous roll not proven in current Polygon client | BLOCKED_ON_ROLL_PROOF |

The current `sources/polygon_client.py` roll scheduler is explicitly limited to quarterly equity-index symbols. M2K is included; MGC/MCL/MBT are not. Therefore the MNQ/MES corpus builder must not be reused unchanged for those three roots.

## 3. Phase X0 — source + contract-chain proof (must precede corpus build)

For each root independently, produce a machine-readable source/roll proof containing:

- provider and endpoint;
- exact dated contract tickers used;
- contract start/end coverage;
- roll rule and why that rule is causal/reproducible;
- exchange/session/calendar basis used only for continuity accounting;
- first/last available bar;
- missing-bar ledger on the 15m grid during expected product-open periods;
- duplicate/conflicting timestamp count;
- overlap checks around every proposed roll seam;
- provider retention limitation, if any;
- deterministic hash of the raw-input inventory/manifest.

### X0 stop conditions

Stop that root before corpus construction if any of the following is true:

- no reproducible dated-contract chain;
- overlapping contracts cannot be ordered causally without an outcome-informed choice;
- provider history cannot cover the proposed analysis window and no prereg amendment is made before downstream analysis;
- unexplained seam gaps/duplicates/conflicts remain unresolved;
- product session/calendar semantics needed for continuity cannot be established.

## 4. Historical window rule

Target the same confirmatory historical window used by the current MNQ/MES P-REPLAY study when the provider can support it cleanly:

- analysis window: **2024-10-01 through 2026-06-26**;
- warm-up: enough pre-window data to fully populate every required rolling feature before the first scored row;
- source vintage: one provider vintage per instrument whenever possible.

If a root cannot meet that window, do **not** silently shorten it. Record the available coverage and amend this prereg for that instrument before candidate parity or any future outcome work.

## 5. Phase X1 — corpus construction

After X0 passes for a root, construct a 15m replay corpus with:

- canonical root preserved exactly;
- raw OHLCV + timestamp + volume;
- product-aware session label;
- required structural-level fields where defined;
- reconstructed market-condition fields only when the same formula can be applied causally;
- prior-day / EMA / ORB / London-ORB fields only where semantically valid for the product; otherwise `NOT_APPLICABLE` or explicit unavailable state;
- manifest with git SHA, builder hashes, source segments, roll ledger, gap ledger, per-file hashes, row counts, coverage, and warm-up proof.

### X1 integrity gates

Per instrument:

- timestamps strictly increasing;
- zero conflicting duplicate timestamps;
- all files hash-match manifest;
- no stale files after rebuild;
- required fields either populated or explicitly classified `NOT_APPLICABLE` / `NOT_AVAILABLE`;
- first scored row has all required rolling warm-up state;
- every roll seam appears in the manifest;
- unexplained missing bars remain visible and contaminate affected detector windows rather than being fabricated/backfilled silently.

## 6. Phase X2 — live/replay parity

Use the same family-level parity philosophy as MNQ/MES, but classify product applicability before scoring.

For every root × family, report:

1. live firing count;
2. replay firing count;
3. both-evaluated bar count;
4. live-only / replay-only bar census;
5. firing overlap and Jaccard;
6. bracket parity by entry / stop / target within one tick;
7. input-source mismatch reason;
8. data-gap contamination count;
9. product applicability classification;
10. final parity classification.

### Frozen parity gates

For a family eligible to be compared on that product:

- firing Jaccard >= **0.90**;
- each bracket leg within one tick on >= **98%** of co-fired candidates;
- exact family/instrument population separation;
- no unexplained conflicting duplicates;
- no use of outcome fields.

Failure is reported, not tuned away. Do not widen tolerance, alter detector parameters, round differently, remove bad rows, or pool another instrument to rescue a failing cell.

## 7. Product-specific cautions

### M2K

M2K may use the existing quarterly equity-index roll machinery after a fresh source-coverage probe and manifest proof. Do not infer that MES/MNQ session/strategy behavior automatically transfers to M2K.

### MGC / MCL

Continuous historical replay is blocked until the dated-contract chain and roll rule are implemented and independently verified. Product-aware feed/session calendars are continuity tools only; they do not become strategy-session rules.

### MBT

Historical signal/geometry parity may be studied after source/roll proof, but terminal strategy outcomes remain **HOLD**. The project has not established a defensible hypothetical-position expiry horizon for this 24/7 product. Do not use an observation-day bookkeeping boundary as a trading outcome rule.

## 8. Allowed work under this prereg

Authorized now:

- X0 read-only data/source/roll probes;
- M2K historical corpus build only after its source probe passes;
- tooling changes required solely to represent proven MGC/MCL/MBT dated-contract schedules, with focused tests and no runtime imports;
- X1 integrity checks;
- X2 read-only firing/bracket parity after X1 passes;
- deterministic manifests/reports/tests.

Not authorized:

- outcomes or performance analysis;
- candidate regeneration for profit evaluation;
- strategy tuning;
- execution or broker work;
- deployment/restart;
- promotion/admission into a trading universe.

## 9. Required stop/report before outcomes

Return one row per instrument with:

- source status;
- historical window available;
- roll proof status;
- corpus integrity status;
- parity status by family;
- blockers;
- final classification: `PARITY_READY`, `PARTIAL`, `NOT_TESTABLE`, or `BLOCKED`.

Then stop. Any outcome phase requires a separate operator ruling.

## 10. Relationship to existing evidence

This expansion is additive. The existing six-root forward observation campaign continues under its own epoch and population identities. The completed MNQ/MES structural-level P1/P2 work remains a separate preregistered study and is not reopened by this document.
