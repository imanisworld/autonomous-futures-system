# ORB Reclaim strategy-logic rework — Pass 1: rule anatomy diagnosis

**Verdict: NO VARIANT PASSES ON THE CANONICAL SUBSTRATE (breaker censoring makes both-halves unreachable there); DIAGNOSTIC-SUBSTRATE CANDIDATE(S): V4_ny_and_true_reclaim — candidate-flag only, requires an isolated filtered replay for any claim**

Pinned code: `74b14071822be46de46be3c2db0eff7c95b8fced`; input: PR #352 raw trades (branch @ `3d0220a970d1`), pinned verbatim in-repo.

## The frozen rule, as PROVEN (not as documented)

- `reclaimed_high` is ANY 15m close-cross up through the session ORB
  high — the docstring's "rejected above, pulled back, now
  reclaiming" pattern is NOT required by the state machine
  (csv_to_replay.derive_orb_status; proven exact by #356's 131/131
  bracket reconstruction). The `true_reclaim` feature below measures
  the documented pattern explicitly.
- Gates: VWAP-above, TRENDING, GEX-not-positive-gamma; LONG-only.
- Bracket: entry ORBhigh+2t; stop max(ORBlow−4t, entry−80t/40t);
  target entry+max(2.5R, 15pt).

## Anatomy — DIAGNOSTIC substrate (breaker-off, uncensored; never a claim)

### True reclaim (documented pattern) vs first cross (implemented pattern)

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| first_cross | 364 | 209 | 209 | 32.1% | $-1,056.30 | $-5.05 | 0.859 | $-1,132.55 | $76.25 |
| true_reclaim | 167 | 79 | 79 | 34.2% | $57.82 | $0.73 | 1.023 | $263.03 | $-205.21 |

### Prior explicit rejected_high earlier same day

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| False | 366 | 209 | 209 | 31.6% | $-1,201.93 | $-5.75 | 0.840 | $-1,228.80 | $26.87 |
| True | 165 | 79 | 79 | 35.4% | $203.45 | $2.58 | 1.082 | $359.28 | $-155.83 |

### Attempt index within day (3 = 3rd or later)

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 301 | 182 | 182 | 35.7% | $-130.84 | $-0.72 | 0.978 | $-159.72 | $28.88 |
| 2 | 141 | 66 | 66 | 21.2% | $-1,120.44 | $-16.98 | 0.573 | $-829.10 | $-291.34 |
| 3 | 89 | 40 | 40 | 37.5% | $252.80 | $6.32 | 1.189 | $119.30 | $133.50 |

### Session

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 23 | 15 | 15 | 26.7% | $-205.70 | $-13.71 | 0.637 | $-79.36 | $-126.34 |
| london | 290 | 175 | 175 | 32.0% | $-605.74 | $-3.46 | 0.892 | $-454.97 | $-150.77 |
| new_york | 218 | 98 | 98 | 34.7% | $-187.04 | $-1.91 | 0.951 | $-335.19 | $148.15 |

### Instrument

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MES | 254 | 181 | 181 | 34.2% | $-171.62 | $-0.95 | 0.973 | $-174.74 | $3.12 |
| MNQ | 277 | 107 | 107 | 29.9% | $-826.86 | $-7.73 | 0.772 | $-694.78 | $-132.08 |

### Chase distance at decision (quartiles, ticks past plan)

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1(<= 5t) | 135 | 135 | 135 | 30.4% | $-215.54 | $-1.60 | 0.950 | $118.99 | $-334.53 |
| Q2(<= 19t) | 132 | 122 | 122 | 35.2% | $-425.56 | $-3.49 | 0.906 | $-503.87 | $78.31 |
| Q3(<= 53t) | 133 | 31 | 31 | 32.3% | $-357.38 | $-11.53 | 0.692 | $-484.64 | $127.26 |
| Q4(> 53t) | 131 | 0 | 0 | — | $0.00 | — | — | $0.00 | $0.00 |

### ORB width (stop-geometry driver, terciles)

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mid(<= 146t) | 180 | 100 | 100 | 36.0% | $83.75 | $0.84 | 1.024 | $357.62 | $-273.87 |
| narrow(<= 54t) | 178 | 134 | 134 | 33.6% | $-226.81 | $-1.69 | 0.948 | $-359.10 | $132.29 |
| wide(> 146t) | 173 | 54 | 54 | 24.1% | $-855.42 | $-15.84 | 0.581 | $-868.04 | $12.62 |

### Trend strength on trigger bar

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| STRONG | 531 | 288 | 288 | 32.6% | $-998.48 | $-3.47 | 0.900 | $-869.52 | $-128.96 |

### Hour of day (ET bands)

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 03-08 | 272 | 169 | 169 | 31.9% | $-659.86 | $-3.90 | 0.879 | $-451.80 | $-208.06 |
| 09-11 | 208 | 92 | 92 | 32.6% | $-393.91 | $-4.28 | 0.891 | $-571.75 | $177.84 |
| 12-15 | 27 | 11 | 11 | 45.5% | $153.72 | $13.97 | 1.422 | $233.39 | $-79.67 |
| other | 24 | 16 | 16 | 31.2% | $-98.43 | $-6.15 | 0.826 | $-79.36 | $-19.07 |

## Loser MFE anatomy (canonical 1t fills, 5m approximation)

- Losers measured: 69
- Median MFE before stop: 0.336R
- Losers reaching ≥0.5R favorable first: 0.391
- Losers reaching ≥1.0R favorable first: 0.232
- 5m-granularity approximation (entry=IOC fill est., exit=first 5m bar through the stop); diagnostic for target-geometry only.

## Pre-registered variants — CANONICAL substrate (breaker-on)

| Variant | Resolved | WR | Net after RT | PF | Both halves + | 2t net | 3t net | 4t net | Verdict |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| V1_new_york_only | 32 | 34.4% | $-97.36 | 0.925 | False | $-8.65 | $-187.87 | $-222.12 | FAILS |
| V2_true_reclaim_only | 18 | 38.9% | $324.98 | 1.730 | False | $294.99 | $67.68 | $89.16 | FAILS |
| V3_first_attempt_only | 65 | 29.2% | $-504.82 | 0.768 | False | $-442.39 | $-255.70 | $-233.50 | FAILS |
| V4_ny_and_true_reclaim | 6 | 83.3% | $481.12 | 7.615 | False | $474.87 | $225.79 | $223.29 | FAILS |
| V5_ny_and_first_attempt | 13 | 38.5% | $16.26 | 1.032 | False | $138.47 | $145.91 | $133.91 | FAILS |

Diagnostic (uncensored) views of the same filters are in the results
JSON (`variants_canonical_substrate.*.diagnostic_view`).

## #352 session-isolated lanes (authoritative, restated)

### Session lanes (independent accounts, breaker on)

| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ_london_ioc_1tick | 165 | 75 | 75 | 32.0% | $-341.50 | $-4.55 | 0.859 | $-201.14 | $-140.36 |
| MNQ_new_york_ioc_1tick | 33 | 6 | 6 | 0.0% | $-321.88 | $-53.65 | 0.000 | $-321.88 | $0.00 |
| MES_london_ioc_1tick | 49 | 40 | 40 | 25.0% | $-365.45 | $-9.14 | 0.705 | $-365.45 | $0.00 |
| MES_new_york_ioc_1tick | 128 | 79 | 79 | 38.0% | $131.83 | $1.67 | 1.041 | $95.34 | $36.49 |

## Caveats

- Variant scores are post-hoc filters of replayed accounts: they
  inherit the unfiltered account's one-position blocking and breaker
  path. A passing variant is a CANDIDATE for its own isolated
  filtered replay — never directly promotable from this pass.
- Diagnostic substrate is breaker-off by construction and is used
  only for feature separation, per the operator's #352 ruling.
- Loser MFE is a 5m-granularity approximation.
- No engine, config, or Pine change is made or recommended here.

## Reproduction

```bash
python scripts/orb_reclaim_rule_anatomy.py \
  --corpus data/replay_corpus_v1_market_condition_fixed \
  --m5 data/replay_polygon_5m \
  --raw-input scripts/orb_reclaim_pr352_raw_trades_input.jsonl \
  --out scripts/orb_reclaim_rule_anatomy_results.json \
  --raw scripts/orb_reclaim_rule_anatomy_raw.jsonl \
  --report docs/orb-reclaim-strategy-rework-diagnosis-2026-07-27.md
```
