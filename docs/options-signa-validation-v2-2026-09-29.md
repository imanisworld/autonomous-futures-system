# Options Signa Validation v2 — 2026-09-29

Status: QA / advisory / observation-only / no trade authority.

This upgrades the existing Signa evidence lane. It does **not** create a second
Signa pipeline and does not grant Signa authority over setup status, contract
selection, risk permission, broker routing, orders, or execution.

## Operating rule

**Signa surfaces it. The options validator proves it. No proof, no trade.**

Signa candidates remain `SIGNA_CANDIDATE` / `SIGNA_CONTEXT` with
`observation_only=true` and `trade_authority=false`.

## Source-derived workflow

The current Signa training material establishes this order:

1. Read the market before looking at a stock.
2. Use Signa to surface a candidate.
3. Read score, grade, confidence, strength, and factor coverage.
4. Open the factor breakdown specifically to look for reasons **not** to trade.
5. Check GEX regime, gamma flip, and major walls.
6. Validate trend/context and the chart.
7. Read options flow as evidence, not as a directional verdict.
8. Require a defined entry, invalidation/stop, target, contract quality, and
   planned risk before the system may consider the setup actionable.

The canonical options validator still owns Strat proof, higher-timeframe
continuity, trigger/invalidation/targets, contract quality, and planned risk.

## Signa signal semantics

- Score: 0 = maximally bearish, 50 = neutral, 100 = maximally bullish.
- Strength: distance from 50 scaled to 0-100. Example: score 72 -> strength 44.
- Confidence: agreement among contributing Signa models.
- Grade: quality of the weighted consensus.
- Factor disagreement is a caution, not something to hide.
- Fewer independent factors means less evidence.
- Score >= 70 is useful for watch/alert prioritization only; it is not an entry rule.

The parser now records:

- `strength`
- `factor_count`
- `factor_conflicts`
- `observation_rating`
- `observation_rating_basis`

### Observation rating

This is an **AFS evidence-quality rating**, not a second Signa grade and not a trade score.
It exists only to make the observation lane easier to scan and audit:

- **A** — direction, score, confidence, and strength are present; at least one factor is present; no observed factor conflicts.
- **B** — the same core evidence is present, but one or more factor scores conflict with the published direction.
- **C** — the payload parsed, but core evidence or factor coverage is incomplete.
- **N/A** — the observation is unavailable or failed.

The stored `observation_rating_basis` explains the mechanical reason for the letter.
The rating cannot add to scanner score, change setup status, grant trade permission,
change risk, rank contracts, or affect broker/order/execution behavior.

### AFS trade grade

The user-facing card also carries a separate **AFS Trade Grade**. This is not
Signa's grade and it is not the post-trade journal `TradeGrader`. It is a
display-only summary of the existing options scanner/validator state:

- **A** — setup is mechanically triggered, scanner score is 9–10, the V1 contract/risk policy is valid, and `trade_proof_status=VALID`.
- **B** — setup is triggered and viable, but either the scanner score is 7–8 or trade proof remains `INCOMPLETE`.
- **C** — setup is triggered but the trade packet is incomplete or scanner score is below the normal alert threshold.
- **F** — an explicit negative trade-proof status exists or the V1 contract/risk policy is invalid.
- **N/A** — the setup has not mechanically triggered.

This grade is presentation-only. It cannot change alert eligibility, scanner score,
setup status, contract selection, risk permission, broker/order state, or execution.
It may be reconstructed from the underlying stored scanner/validator fields; it is
not a new source of truth.

These remain namespaced observational telemetry in the scanner path.

## GEX context

The Signa workflow reads:

**regime -> gamma flip -> call/put walls -> one-sentence playbook**

Operational interpretation:

- Positive gamma: dealer hedging can dampen moves and favor mean reversion.
- Negative gamma: dealer hedging can amplify directional moves.
- Gamma flip: regime boundary.
- Large gamma strikes/walls: reaction or profit-taking zones, not guaranteed
  reversal points.
- Thin areas between major gamma concentrations can permit faster price travel.
- Zero-DTE gamma is intraday-sensitive and disappears at the close, so levels
  must be refreshed each session and after major volatility/news changes.
- GEX is regime/context evidence, not a price prediction.

The current REST capability probe still has no proven standalone GEX endpoint.
Do not invent one. Manual/imported context may now preserve:

- `gex_regime`
- `net_gex`
- `gex_score`
- `gamma_flip`
- `call_wall`
- `put_wall`
- `zero_dte_gamma`

## Options-flow context

Before giving directional weight to a flow print, preserve evidence for four
questions:

1. Is it unusual relative to normal activity and open interest?
2. Is it urgent (for example, sweep/aggressive execution) or patient/block-like?
3. Is it plausibly new activity rather than closing/rolling?
4. Who is the aggressor?

A call is not automatically bullish and a put is not automatically bearish.
Raw flow remains evidence only. Spreads, rolls, fragmented fills, and sold puts
can reverse a naive directional read.

Manual/direct context can preserve fields such as:

- `flow_volume`
- `open_interest`
- `aggressor`
- `sweep`
- `block`
- `bid_ask_spread_pct`

## Trend context

Signa's trend workflow is used as secondary higher-timeframe evidence:

**Weinstein stage -> Wyckoff range/state -> Elliott wave/invalidation ->
momentum/volume/relative strength**

Rules retained from the training material:

- Prefer trend-aligned setups; Stage 2 supports long-side research.
- Stage 4 is evidence against longs.
- Do not chase extensions; favor validated breakouts/pullbacks.
- A Wyckoff structure that is not confirmed stays unconfirmed.
- Every Elliott count needs a price level that invalidates it.
- Missing confirmation is not a signal.

Manual/imported context can preserve:

- `weinstein_stage`
- `weinstein_confidence`
- `wyckoff_state`
- `elliott_wave`
- `elliott_invalidation`
- `relative_strength`
- `momentum`
- `volume_confirmation`

## Risk and contract boundary

Signa does not override the options risk system.

Before TAKE can exist elsewhere in the canonical validator, the plan still
needs:

- mechanical setup/trigger proof;
- underlying invalidation;
- premium stop/planned loss;
- realistic Target 1 / Target 2;
- expiration and strike;
- premium, bid/ask spread, volume, and open interest;
- IV/event/theta review;
- acceptable per-trade and aggregate planned risk.

Short-dated and 0DTE Signa signals remain research/observation by default.
They do not bypass the system's DTE and exceptional-override rules.

## Implementation boundary

This v2 upgrade enriches the existing observation/discovery lane only:

- richer Action Card telemetry;
- factor-coverage/conflict visibility;
- preservation of GEX/flow/trend context fields;
- human display of evidence quality.

It intentionally does **not**:

- change scanner scoring;
- change setup status;
- create a Signa hard gate;
- change contract ranking;
- change risk permission;
- create broker/order/execution behavior;
- wire live automation.
