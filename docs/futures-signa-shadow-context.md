# Futures Signa shadow context

## Verdict

**AUDIT ONLY / PAPER CONTEXT ONLY.**

Signa may be measured beside MNQ/MES decisions. It is not a futures signal,
filter, confirmation gate, stop/target source, risk input, or execution input.

## Mapping

- MNQ -> QQQ
- MES -> SPY

Phase 1 uses only the current Signa Action Card because its normalized client is
already present on `main`. No GEX, options-flow, dark-pool, or market-tide path
is guessed. Those surfaces remain blocked until the Founding-key weekday probe
proves endpoint availability, payload shape, timestamp behavior, and useful
freshness.

## Collector contract

`context/futures_signa_shadow.py` writes `futures_signa_shadow.jsonl` only when
`FUTURES_SIGNA_SHADOW_ENABLED=true`.

Even when enabled, there is deliberately **no default timeframe**. The caller
must also set a proven `FUTURES_SIGNA_SHADOW_TIMEFRAME` (`1h` or `1d` in the
current probe scope). A missing/unproven timeframe causes no provider request and
records an explicit config-blocked observation.

Every row states:

- `observation_only=true`
- `gate_authoritative=false`
- `risk_authoritative=false`
- `broker_authoritative=false`
- `execution_authoritative=false`

Provider failure is fail-soft and cannot alter the futures decision.

## Why this is a separate evidence file

The authoritative futures journal drives daily state, open-position recovery,
trade counts, and outcome accounting. Signa is not allowed to participate in
those semantics. A separate file keyed by decision timestamp/instrument lets us
join the context later without introducing a new row type into daily-state or
position reconstruction.

The repo already has `strategy_context_observations.jsonl` for descriptive
futures context. Once the Signa contract is proven, the smallest runtime hook is
to call this collector next to that existing observer, after the system has made
its decision and before no downstream code consumes the returned row.

## Evidence questions

After collection exists, evaluate Signa only as an explanatory variable:

- setup family
- MNQ vs MES
- LONG vs SHORT
- Signa direction relation: aligned / opposed / neutral
- grade
- confidence band
- provider freshness / cached state
- Action Card timeframe
- individual Action Card component scores

Do not promote a field because a small cell looks good. Require the project's
pre-registered sample/freshness/OOS standards and preserve live/replay decision
invariance.

## Still needed before runtime wiring

1. Monday weekday Founding-key re-probe must establish whether `1h` is genuinely
   intraday and choose the observation timeframe/cache policy.
2. Confirm request budget after combining options and futures observation loads.
3. Prove the runner hook is default-OFF, fail-soft, and downstream-inert.
4. Add exact timestamp/instrument join tests against the existing strategy
   context and futures journal evidence.
5. Only after live payload contracts are proven, separately consider QQQ/SPY
   options flow, dark pool, market tide, and GEX. Missing GEX must remain visible
   as unavailable, never fabricated or required.

**No proof, no promotion.**
