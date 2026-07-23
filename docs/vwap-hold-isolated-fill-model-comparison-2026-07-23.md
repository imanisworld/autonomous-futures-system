# VWAP-hold isolated fill-model comparison (2026-07-23)

Research only. No production edits, rule-document rewrites, configuration
changes, broker actions, deployments, or automatic strategy reclassification.

Operator-directed isolation test, run against five locked preconditions
(verbatim below) so the "isolated" comparison cannot be contaminated by an
unfrozen population, an underspecified fill model, a mixed proximity-gate
state, or a metric that structurally favors the model with the higher
rejection rate.

## The five locks

1. **Freeze the exact 348-signal population.** Verified: reconstructing the
   arms from `logs/retest_baseline_off/MNQ/journal_*.jsonl` reproduces
   sha256 `18cbbc8427b8afc462b1145347125ae45bb2b6af97f4ef9f374a10565a96d880`
   exactly — byte-identical to the PR #283 fingerprint. The loader only
   reads persisted historical journal rows; it never invokes
   `strategy/signal_engine.py`, so there is no regeneration through current
   strategy code. A manifest recording every row plus the hash is committed:
   `scripts/vwap_hold_isolated_fill_model_manifest.json`.
2. **IOC defined precisely** — extracted from `execution/paper_broker.py`
   source, not paraphrased: limit price = `entry ± 32 ticks` (the live
   `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ` pin); marketability is a single
   snapshot check against the arrival bar's **open** (the code comment says
   "close" — that mismatch is inherited from the verified PR #283 script and
   stated here, not silently changed); no persistence across bars, no
   later-bar fill, no partial fills; fill price = the better of (market,
   limit), capped at the limit.
3. **Market fill defined precisely** — decision timestamp = signal
   `bar_ts + 15min`; first executable price = the next 5m bar's open;
   gap-through fills immediately at open + 1 adverse tick; otherwise a
   touch-based fill (bar high/low, never intrabar sequencing) within a
   20-minute window at level + 1 adverse tick; no partial fills; a uniform
   $2.24/contract round-turn cost overlay applied identically to both legs
   at settlement (not additional entry slippage — does not double-count the
   embedded 1-tick fill slippage).
4. **Proximity gate frozen disabled.** `config/settings.py:233
   vwap_entry_max_distance_ticks` defaults to `0.0` ("never gates"). The
   field was introduced default-off in PR #92 (2026-06-26); the only attempt
   to enable it, PR #95, was closed unmerged. It has never been ON for any
   day in this population's 2024-07-02..2026-06-25 span, so the primary
   comparison already carries the production-default state with no filter
   applied by this script. No sensitivity variant was run — out of scope
   here per instruction.
5. **Primary metric = net expectancy per armed signal** (net $ ÷ n=348,
   unfilled arms contribute $0). Expectancy-per-fill is reported but is not
   the verdict metric.

## Results

| Leg | Filled | Net after cost | **PRIMARY: exp/armed signal (n=348)** | Secondary: exp/fill | WR | PF |
|---|---|---|---|---|---|---|
| old anchored IOC (32t) | 105/348 (30.2%) | $936.54 | **$2.69** | $8.92 | 0.495 | 1.72 |
| new market entry | 343/348 (98.6%) | $3,583.08 | **$10.30** | $10.51 | 0.501 | 1.52 |

Raw artifacts: `scripts/vwap_hold_isolated_fill_model_manifest.json` (frozen
population + hash), `scripts/vwap_hold_isolated_fill_model_comparison_results.json`
(full per-leg summary).

## Why the primary metric changes the picture

Per-fill, the two legs looked close ($8.92 vs $10.51 — an 18% gap). That
similarity was an artifact of IOC's own rejection rate: expectancy-per-fill
only measures the trades that got through, and IOC let through the
minority. Once every armed signal counts in the denominator — including the
70% IOC self-cancelled — the picture is starkly different: **the market
leg's per-armed-signal expectancy is roughly 3.8x the IOC leg's ($10.30 vs
$2.69)**. This is exactly the distortion lock #5 was written to prevent: a
fill model can look nearly as good as another on a per-fill basis while
producing a fraction of the total value across the actual population of
opportunities the strategy generates.

This reproduces and sharpens the PR #283 finding rather than contradicting
it — the market leg's numbers here ($3,583.08 net, 343/348 filled) match
that PR's independently-reproduced headline. What's new is the isolated,
locked comparison confirming the gap holds, and is materially larger, on
the metric that actually answers the strategy question: does the model
that's actually deployable turn the strategy's real opportunity set into
positive value.

## What this does NOT decide

Per operator instruction, this test does not conclude, and this document
does not recommend, that `vwap_hold` or `vwap_rejection` be retired,
merged, or redesigned. It also does not itself constitute proof that either
represents a genuinely separate market state versus a duplicate
implementation. That determination is explicitly deferred to the follow-on
**overlap audit** — the next task, not detector construction — which must
first establish whether the two are (a) genuinely separate conditions
needing mutually exclusive gating, (b) the same setup under two historical
names, or (c) one valid setup plus one redundant implementation. The
previous VWAP narrative ("fillable vwap trades lose") is superseded by this
and the PR #283 finding and should be considered withdrawn, per operator
verdict.

## Verification

- Population hash independently reconstructed and asserted equal to the
  PR #283 frozen fingerprint (hard-fails the script otherwise).
- Both legs reuse the exact PaperBroker call paths already verified in PR
  #283 (`scripts/vwap_hold_paired_fill_comparison.py`); no fill-model code
  was changed, only precisely documented, manifested, and re-summarized
  under the operator-mandated primary metric.
- No file in this PR is imported by the runtime; deploy state unaffected.
