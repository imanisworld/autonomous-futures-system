# 212R prospective collector RTH readiness audit — 2026-09-18

## Verdict

**HOLD / OBSERVATION CODE PROVEN / CURRENT REAL-TIME SIP SOURCE NOT ENTITLED.**

Do not deploy or schedule collector v0.3 yet.

The collector remains isolated from scanner/risk/broker/order state, exact-cross timing is now correct, and the current Public selector path is fast enough to measure. The configured Alpaca account cannot query consolidated SIP trades inside the latest 15 minutes, so it cannot supply the real-time first-break clock v0.3 requires.

## Follow-up status after #756

This readiness audit remains the authority for **exact-SIP collector v0.3**: with the current Alpaca entitlement, that exact-SIP lane is still not deployable.

A later preregistered source-policy study in #756 completed the alternative path contemplated below. It does **not** overturn the exact-SIP blocker. Instead it establishes a separate result:

- frozen population: all **183** structurally ARMED 212 windows, outcome-independent;
- IEX provisional reversals: **90**;
- delayed SIP confirmed: **89/90 (98.9%)**;
- SIP-authoritative reversals missed by IEX: **2/91 (2.2%)**;
- false IEX provisional: **1/90 (1.1%)**, rejected because delayed SIP proved continuation broke first;
- confirmed IEX-minus-SIP lag: median **3.501s**, p95 **156.273s**, max **569.811s**;
- verdict: `MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE`, **not SIP-equivalent and not deployed**.

Therefore the phrase “validate a different prospective observation source” below is now complete for the IEX-provisional research policy. The remaining IEX path is operational/policy proof, not another source-equivalence study: separately version the observer, pre-register lag/cadence, prove execution isolation, and collect a natural RTH provisional row with delayed SIP reconciliation.

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

There are now two distinct prospective source paths.

### Exact-SIP collector v0.3

1. Prove real-time consolidated SIP entitlement/access for the active watch window.
2. Re-run an RTH preflight proving a true `ARMED -> exact SIP first break -> selector evidence` row.
3. Choose and pre-register maximum exact-SIP-cross-to-selector-capture lag and collector cadence.
4. Build/prove the service-specific observation-only release.
5. Prove first natural RTH evidence on that exact release.

### IEX-provisional + delayed-SIP policy

The offline source-policy validation is complete in #756. Before any prospective deployment:

1. Explicitly authorize a separately versioned IEX-provisional observer/reconciliation release.
2. Pre-register IEX-trigger-to-selector-evidence lag policy and cadence.
3. Preserve IEX decision-time evidence immediately; never backfill a missed IEX event from delayed SIP.
4. Reconcile every provisional reversal later against consolidated SIP and exclude rejected rows from the confirmed cohort.
5. Prove execution isolation and the first natural RTH `ARMED -> IEX provisional break -> selector evidence -> delayed SIP reconciliation` row.

Historical Delta and contract-level open interest remain separate blockers for exact frozen historical selector replay.

## Classification

**212R: UNPROVEN / WAIT.**

PR #761 subsequently closed the separate pre-registered three-session **first-sight family-persistence** question: 47 prospective 212R episodes across 3 sessions produced an ex-opening matched-baseline excess of **-4.2 pp**, so that lane is `NO LONGER SHOWING EXCESS`. This does not answer the corrected exact-trigger collector lane in this document, but it removes the earlier retrospective first-sight excess as prospective support.

The operational verdict here is unchanged: **HOLD / do not deploy exact-SIP collector v0.3 under the current real-time SIP entitlement.**

No proof, no trade.
