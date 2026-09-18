# Options shared-infrastructure handoff — 2026-09-18 morning

## Current ruling

- `strat_212_reversal_30m_options`: **BLOCKED / WAIT**.
- No evidence blocker is retired merely because code/tests exist.
- No deployment, restart, `.env` change, aggregate-risk budget setting, DEMO/paper activation, broker submission, or LIVE trading is authorized by the infrastructure work below.
- The futures bot is outside this options change set and must not be restarted for it.

## Merged foundation

- #656 / `d87d858`: deterministic advisory-only contract-selector foundation.
- #661 / `a9fee8c`: timestamped quote-retention foundation.
- #664 / `70fa9c4`: retained quote evidence required on canonical advisory intake.
- #667 / `e98eeb3`: Public bid/ask timestamp provenance, canonical quote manifests and exact dataset-byte verification, retained-quote replay adapter, replay/forward parity through the actual contract selector and existing ASK-entry/BID-exit paper fill consumer.

#667 proves executable quote freshness from the older of Public `bidTimestamp` / `askTimestamp` when both are present and timezone-aware. It never substitutes `lastTimestamp` and never synthesizes a missing side timestamp.

## Work prepared after #667

### PR #676 — selector serialized replay/forward parity

Status: open, ready for review; latest head `0072b8d52f333f8633c28a4c1b4d536ff12d24cc`.

Adds a canonical byte-stable selector-input envelope, strict schema parsing, one select-from-serialized-input path, replay/forward golden parity on identical bytes, no-hindsight through that path, and fail-closed malformed identity/schema cases.

This advances Item 1 but **does not yet prove the real forward producer emits these exact serialized bytes**.

### PR #678 — premium-stop risk + no-averaging core

Status: open, ready for review; latest reviewed head `b96c2a64f3c47611579c7a846f2a1338d4b7139d` had green CI before the latest documentation/comment-only clarification cycle.

Adds a named planned-risk formula from planned entry premium and premium stop, rejects invalid/non-finite stop/risk inputs, removes clamp-to-zero behavior, and rejects same-underlying + same-direction canonical open positions or supplied open orders.

The aggregate-risk budget remains **unset by default**. No `$1,000` runtime default was added. Budget provenance remains incomplete.

### PR #679 — fill event realism

Status: open, ready for review; latest head `f8b8d10f86529b476efefb8f205feb59ab8c5133`.

Adds pessimistic same-bar stop-first resolution, CALL/PUT gap-through-stop classification, first-available executable retained-quote selection, explicit `NO_FILL`, malformed-retained-data blocking, and malformed bar/stop-target geometry checks.

This advances Item 2B but does **not** choose unapproved fee/slippage policy values and does not complete the stress requirement.

### PR #682 — market-hours Public timestamp probe

Status: open draft; latest head `5b1b1b3bf5e4ff3e91fee39bcbb46cfe1e39e864`.

Adds a read-only probe using the existing Public market-data client. It reports actual bid/ask executable timestamp coverage and freshness for the configured scanner watchlist, uses the frozen quote-age rule and selector DTE bands, requires the frozen Public source identity, prints no credentials/account ID, writes nothing, and calls no trading/account endpoints.

Its strongest possible result is **PROVEN_FOR_CAPTURE** for that capture only. It explicitly does not claim historical coverage, future provider guarantees, DEMO eligibility, or strategy validity.

## Still not proven

1. A real frozen historical option-quote dataset covering the claimed backtest decision population.
2. A checked-in/materialized historical `option_quotes_manifest.json` tied to those real quote bytes.
3. Real market-hours Public evidence that usable returned contracts consistently carry both executable side timestamps.
4. Actual forward producer wiring to the exact serialized selector-input contract in #676.
5. Complete Item 2B frozen fee/slippage policy + stress proof.
6. Complete event-to-fill/gate/runtime parity.
7. Item 3 aggregate-budget value/provenance matching between evidence and runtime config.
8. Full infrastructure regression leaving exactly the expected 23 212R-specific blockers.

Therefore 212R remains **BLOCKED / WAIT**.

## Market-open proof boundary

The next live-data action is read-only: run the #682 Public timestamp probe during market hours after that code is merged/available in the authorized runtime environment. A successful capture only resolves the current-provider timestamp question for that capture.

Do **not** deploy or restart just to manufacture proof. If later options scanner runtime changes are merged and we want the running scanner to use them, deployment/restart must be separately approved and followed by post-restart journal/runtime proof.

## Weekend restart point

Do not reconstruct prior work. Start by checking PRs #676, #678, #679, #682 and current main. Then:

1. finish/merge reviewed infrastructure slices;
2. run the read-only Public timestamp capture during market hours;
3. obtain/freeze real historical quote bytes and manifest;
4. finish Item 2B fee/slippage/stress policy only with explicitly approved values;
5. finish aggregate-budget provenance without inventing a default;
6. rerun the preserved #653 gate package;
7. require the result to remain `BLOCKED / WAIT` with exactly the expected 23 strategy-specific blockers and both activation flags false.
