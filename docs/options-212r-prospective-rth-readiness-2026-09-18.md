# 212R prospective collector RTH readiness audit — 2026-09-18

## Verdict

**HOLD / OBSERVATION CODE PROVEN / CURRENT REAL-TIME SIP SOURCE NOT ENTITLED.**

Do not deploy or schedule collector v0.3 yet.

The collector remains isolated from scanner/risk/broker/order state, exact-cross timing is now correct, and the current Public selector path is fast enough to measure. The configured Alpaca account cannot query consolidated SIP trades inside the latest 15 minutes, so it cannot supply the real-time first-break clock v0.3 requires.

## What was verified

### Execution isolation

Focused collector / trigger / selector regressions passed after the entitlement-specific fail-closed reason was added.

No files under scanner execution, strategy, risk, broker, webhook, or order-routing paths are changed by this audit.

The collector remains an observation-only script with isolated JSONL/SIP evidence storage.

### Isolated primary-20 RTH cycle

A manual isolated cycle ran against current collector code with:

- temp journal and temp SIP directory under the VPS research area;
- no production scanner DB;
- no service/timer;
- no alert;
- no ACTIVE risk reservation;
- no broker/order path;
- mechanics-only `max_capture_lag_seconds=9999`.

That numeric value was used only to avoid accidentally turning the timing gate into the limiting variable during the readiness probe. It is **not policy** and is not approved for deployment.

Observed cycle:

- wall time: **27.951439869 seconds**;
- tickers: **20**;
- `ARMED` records written: **3**;
- terminal resolutions written: **26**;
- reversal triggers reconstructed: **17**;
- option evidence captured: **0**;
- option evidence blocked: **17**.

The 17 reversal rows failed safely:

- **16**: `no_proven_pretrigger_arm`;
- **1**: recent SIP query unavailable on the configured Alpaca entitlement.

The three current-session arms were MSFT, AMZN, and NFLX for the 19:30Z–20:00Z watch window.

This proves the first-run no-hindsight guard works: already-past reversals do not receive current option chains merely because they can be reconstructed later.

### Current selector-capture mechanics benchmark

A separate read-only primary-20 benchmark called the current Public underlying/expiry/chain + `OPTIONS_PAPER_V1` retained-input replay path directly. It did not write evidence or count as 212R observations.

Results:

- symbols tested: **20**;
- `CAPTURED`: **19**;
- fail-closed: **1** (GE: no liquid contract);
- production replay parity: true on every captured row;
- selector-capture wall time:
  - minimum: **0.283299s**;
  - median: **0.3438015s**;
  - p90: **0.4855518s**;
  - p95: **0.52491945s**;
  - maximum: **0.605355s**.

This is mechanics evidence only. It does not choose a capture-lag threshold or collector cadence.

## Real-time SIP entitlement blocker

During the active 19:30Z SPY watch window, the collector's Alpaca consolidated-SIP trade request failed with HTTP 403:

`subscription does not permit querying recent SIP data`

Older same-day SIP windows succeeded after they aged beyond the recent-data restriction. This matches Alpaca's documented Basic-plan behavior: real-time equities coverage is IEX, while SIP historical queries are restricted for the latest 15 minutes unless the account has the required paid market-data entitlement.

Therefore:

- delayed/historical SIP remains useful for source reconciliation;
- current credentials cannot power collector v0.3's real-time consolidated-SIP first-break detector;
- IEX must not be silently substituted and labeled SIP-equivalent;
- no capture-lag or cadence policy can make the current v0.3 architecture deployable until the real-time first-break source is resolved.

## Required before any collector deployment

1. Resolve the prospective first-break source:
   - prove real-time consolidated SIP entitlement, **or**
   - explicitly design and validate a different prospective observation source with later consolidated-SIP reconciliation.
2. Re-run an RTH preflight proving a true `ARMED -> first break -> selector evidence` row.
3. Only then choose and pre-register:
   - maximum trigger-to-selector-capture lag;
   - collector cadence.
4. Build a service-specific observation-only release that cannot mutate scanner/risk/broker state.
5. Prove first natural RTH evidence on that exact release.

Historical Delta and contract-level open interest remain separate blockers for exact frozen historical selector replay.

## Classification

**212R: PROMISING BUT UNPROVEN / WAIT.**

No proof, no trade.
