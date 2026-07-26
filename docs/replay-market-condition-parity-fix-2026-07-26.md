# Replay market-condition parity fix — 2026-07-26

## Verdict

The engine-facing replay `market_condition` now comes from the same
EMA-stack + relative-volume + Wilder ATR14 cascade used by Pine/runtime.
The former replay heuristic is retained only as `legacy_market_condition`
provenance and cannot control ReplayEngine.

Existing regime-gated results produced from the old corpus are
**SUPERSEDED / parity-invalid**. They were preserved, not deleted.

## Formula

The shared reconstruction in `scripts/pine_market_condition.py` implements:

1. `rel_vol = volume / SMA20(volume)`, current bar included;
2. `range_ratio = (high - low) / Wilder-RMA ATR14`;
3. `rel_vol < 0.40` → `DEAD`;
4. `range_ratio < 0.40` or `rel_vol < 0.60` → `CHOPPY`;
5. full directional `close/EMA9/EMA21/EMA55` stack and
   `rel_vol >= 0.80` → `TRENDING`;
6. otherwise → `RANGE_BOUND`.

No strategy, runtime, risk, broker, configuration, or trend-classification
code changed.

## Missing data and initialization

- Exact SMA20 volume is unavailable until 20 causal bars exist.
- Wilder ATR14 is unavailable until 14 causal true-range values exist.
- Missing/synthetic volume or incomplete EMA/ATR warm-up writes
  `market_condition: null`; the former heuristic is never substituted.
- Polygon generation fetches the existing 10-day pre-roll before the requested
  evidence range and discards pre-range candles after all rolling calculations.
  The measured corpus therefore had zero warm-up exclusions.
- Polygon EMA/ATR initialization remains honestly labeled
  `RECONSTRUCTED_UNVALIDATED_INIT`: the calculation is causal and has the
  configured pre-roll, but no overlapping Pine export exists to claim
  bit-for-bit initialization identity. This caveat is preserved in every row
  and is not concealed.

## Corpus rematerialization

The original downloaded Polygon response is no longer retained. The existing
derived corpus does retain every OHLCV/EMA input and the already-reviewed
canonical reconstruction. `scripts/rematerialize_market_condition_corpus.py`
therefore produced a new corpus from those canonical fields without mutating
the source corpus:

```text
python3 scripts/rematerialize_market_condition_corpus.py \
  --input data/replay_corpus_v1 \
  --output /private/tmp/replay_corpus_v1_market_condition_parity \
  --report /private/tmp/replay_corpus_v1_market_condition_parity_report.json
```

| Metric | Result |
|---|---:|
| Files | 626 |
| Bars compared | 47,066 |
| Comparable bars | 47,066 |
| Warm-up/missing-data exclusions | 0 |
| Mismatches before | 33,635 |
| Mismatches after | **0** |
| Legacy TRENDING removed | 27,967 |
| TRENDING added | 0 |

### Label distribution

| Label | Before | After |
|---|---:|---:|
| TRENDING | 41,144 | 13,177 |
| RANGE_BOUND | 0 | 18,531 |
| CHOPPY | 1,994 | 7,567 |
| DEAD | 0 | 7,791 |
| CONSOLIDATING | 3,928 | 0 |

The source corpus and its old results remain intact for provenance. Strategy
evidence must be rerun against the rematerialized corpus after this code lands.

## Regression proof

Tests prove:

- exact Pine thresholds and bucket order;
- exact current-inclusive SMA20;
- Wilder ATR14 seed and recurrence;
- synthetic-volume and warm-up failure behavior;
- legacy `TRENDING` cannot leak when canonical output is `DEAD`, `CHOPPY`, or
  `RANGE_BOUND`;
- ReplayEngine and DecisionEngine consume the canonical field;
- converter calculations are causal and introduce no lookahead.

Targeted validation: `112 passed`.

Full repository validation: `3,738 passed, 4 skipped`.
