# ORB Breakout — canonical evidence, isolated honest-fill, static vs runner

**Static verdict: WAIT** — sample below 30-trade minimum (n=25); fails both-halves-positive walk-forward under honest fills; fails 1-4 tick slippage sensitivity
**Runner verdict: WAIT** — sample below 30-trade minimum (n=25); fails both-halves-positive walk-forward under honest fills; fails 1-4 tick slippage sensitivity

Pinned code: `a25f4f09f47c133a9f08ea2913aaee05ff86a2fa`
Corpus: `data/replay_corpus_v1_market_condition_fixed` (post-#338 corrected market_condition, post-#339/#342 ReplayEngine)
Instrument: MNQ only — see parity findings for why
Range: 2025-07-24 → 2026-07-23

## Method

- **Isolated** single-strategy replay (`enabled_concepts=["orb_breakout"]` only) — own fresh account, so the frozen 20% drawdown breaker (if it trips) reflects only this strategy's own P&L.
- `entry_fill_model="ioc_limit"` in memory (PR #346's corrected posture), canonical MNQ IOC tolerance (32 ticks) and `orb_stop_ticks` (48 ticks) asserted, not overridden.
- Both exit modes on the SAME candidate population: static (fixed 2.2R target) and runner (activation=1.0R, trail=0.5R) — `config.runner_mode`, the actual bool replay reads (verified `config.exit_mode` is a live-webhook-only concept, not consumed by replay/replay_engine.py or execution/paper_broker.py).
- 1/2/3/4-tick adverse slippage sensitivity, each exit mode, same isolation/corpus.
- $1.48 round-trip commission at the analysis layer only.
- `risk_rules.yaml` verified byte-identical before/after (`56677a0ab37bbf62…`).

## Static exit — overall (1-tick)

| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| STATIC | 106 | 25 | 81 | 25 | 28.0% | $-306.50 | $-343.50 | $-3.24 | $-13.74 | 0.463 | $410.18 | 8 | 74.3% | ❌ |

## Runner exit — overall (1-tick)

| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| RUNNER | 106 | 25 | 81 | 25 | 32.0% | $-335.75 | $-372.75 | $-3.52 | $-14.91 | 0.381 | $397.43 | 5 | 77.8% | ❌ |

## By direction (1-tick)

### Static
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 66 | 17 | 49 | 17 | 35.3% | $-119.00 | $-144.16 | $-2.18 | $-8.48 | 0.642 | $206.34 | 6 | 85.1% | ❌ |
| SHORT | 40 | 8 | 32 | 8 | 12.5% | $-187.50 | $-199.34 | $-4.98 | $-24.92 | 0.158 | $208.88 | 6 | 100.0% | ❌ |

### Runner
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 66 | 17 | 49 | 17 | 41.2% | $-148.25 | $-173.41 | $-2.63 | $-10.20 | 0.526 | $188.55 | 3 | 83.9% | ❌ |
| SHORT | 40 | 8 | 32 | 8 | 12.5% | $-187.50 | $-199.34 | $-4.98 | $-24.92 | 0.158 | $208.88 | 6 | 100.0% | ❌ |

## By session (1-tick)

### Static
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 10 | 2 | 8 | 2 | 50.0% | $6.50 | $3.54 | $0.35 | $1.77 | 1.104 | $33.98 | 1 | 100.0% | ❌ |
| london | 69 | 16 | 53 | 16 | 25.0% | $-240.50 | $-264.18 | $-3.83 | $-16.51 | 0.399 | $275.76 | 5 | 100.0% | ❌ |
| new_york | 27 | 7 | 20 | 7 | 28.6% | $-72.50 | $-82.86 | $-3.07 | $-11.84 | 0.502 | $100.44 | 3 | 100.0% | ❌ |

### Runner
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 10 | 2 | 8 | 2 | 50.0% | $6.50 | $3.54 | $0.35 | $1.77 | 1.104 | $33.98 | 1 | 100.0% | ❌ |
| london | 69 | 16 | 53 | 16 | 31.2% | $-273.00 | $-296.68 | $-4.30 | $-18.54 | 0.263 | $296.68 | 4 | 100.0% | ❌ |
| new_york | 27 | 7 | 20 | 7 | 28.6% | $-69.25 | $-79.61 | $-2.95 | $-11.37 | 0.522 | $100.44 | 3 | 100.0% | ❌ |

## Walk-forward H1/H2 (1-tick)

### Static
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 86 | 20 | 66 | 20 | 35.0% | $-145.50 | $-175.10 | $-2.04 | $-8.76 | 0.628 | $284.30 | 8 | 74.3% | ❌ |
| H2 | 20 | 5 | 15 | 5 | 0.0% | $-161.00 | $-168.40 | $-8.42 | $-33.68 | 0.000 | $168.40 | 5 | — | ❌ |
Both halves positive: **False (H2) / overall walk-forward FAIL**
⚠️ **This isolated account's OWN 20% drawdown breaker tripped on its own P&L**: 2026-03-16 (Account drawdown 20.4% exceeds max 20.0% from peak $1,500.00.). New order admission stopped from that date — H2/Q3/Q4's thin counts are not just "not enough sample happened to exist," they are orb_breakout's own honest performance halting its own isolated account, well before quarter-end.

### Runner
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 86 | 20 | 66 | 20 | 40.0% | $-174.75 | $-204.35 | $-2.38 | $-10.22 | 0.529 | $245.55 | 5 | 77.8% | ❌ |
| H2 | 20 | 5 | 15 | 5 | 0.0% | $-161.00 | $-168.40 | $-8.42 | $-33.68 | 0.000 | $168.40 | 5 | — | ❌ |
Both halves positive: **False (H2) / overall walk-forward FAIL**
⚠️ **This isolated account's OWN 20% drawdown breaker tripped on its own P&L**: 2026-03-16 (Account drawdown 22.4% exceeds max 20.0% from peak $1,500.00.).

## Quarter (1-tick)

### Static
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 59 | 16 | 43 | 16 | 37.5% | $-90.00 | $-113.68 | $-1.93 | $-7.11 | 0.690 | $180.36 | 5 | 85.2% | ❌ |
| Q2 | 27 | 4 | 23 | 4 | 25.0% | $-55.50 | $-61.42 | $-2.27 | $-15.36 | 0.409 | $103.94 | 3 | 100.0% | ❌ |
| Q3 | 20 | 5 | 15 | 5 | 0.0% | $-161.00 | $-168.40 | $-8.42 | $-33.68 | 0.000 | $168.40 | 5 | — | ❌ |
| Q4 | 0 | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | — | $0.00 | 0 | — | ❌ |

### Runner
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 59 | 16 | 43 | 16 | 37.5% | $-151.25 | $-174.93 | $-2.96 | $-10.93 | 0.524 | $199.61 | 5 | 92.5% | ❌ |
| Q2 | 27 | 4 | 23 | 4 | 50.0% | $-23.50 | $-29.42 | $-1.09 | $-7.36 | 0.561 | $66.96 | 2 | 100.0% | ❌ |
| Q3 | 20 | 5 | 15 | 5 | 0.0% | $-161.00 | $-168.40 | $-8.42 | $-33.68 | 0.000 | $168.40 | 5 | — | ❌ |
| Q4 | 0 | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | — | $0.00 | 0 | — | ❌ |

## Slippage sensitivity 1/2/3/4-tick (overall)

### Static
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 106 | 25 | 81 | 25 | 28.0% | $-306.50 | $-343.50 | $-3.24 | $-13.74 | 0.463 | $410.18 | 8 | 74.3% | ❌ |
| 2tick | 106 | 25 | 81 | 25 | 28.0% | $-327.00 | $-364.00 | $-3.43 | $-14.56 | 0.446 | $424.68 | 8 | 74.2% | ❌ |
| 3tick | 105 | 24 | 81 | 24 | 29.2% | $-306.50 | $-342.02 | $-3.26 | $-14.25 | 0.459 | $396.70 | 8 | 74.1% | ❌ |
| 4tick | 105 | 24 | 81 | 24 | 29.2% | $-325.00 | $-360.52 | $-3.43 | $-15.02 | 0.444 | $409.70 | 8 | 73.9% | ❌ |
Survives 1-4 tick: **False**

### Runner
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 106 | 25 | 81 | 25 | 32.0% | $-335.75 | $-372.75 | $-3.52 | $-14.91 | 0.381 | $397.43 | 5 | 77.8% | ❌ |
| 2tick | 105 | 24 | 81 | 24 | 33.3% | $-321.00 | $-356.52 | $-3.40 | $-14.86 | 0.383 | $371.70 | 5 | 78.2% | ❌ |
| 3tick | 140 | 36 | 104 | 36 | 33.3% | $-167.75 | $-221.03 | $-1.58 | $-6.14 | 0.752 | $449.00 | 5 | 73.9% | ✅ |
| 4tick | 112 | 29 | 83 | 29 | 31.0% | $-326.25 | $-369.17 | $-3.30 | $-12.73 | 0.513 | $465.10 | 5 | 79.3% | ❌ |
Survives 1-4 tick: **False**

## Robustness questions (operator's list, answered explicitly)

1. Positive under honest fills? Static: **False**. Runner: **False**.
2. H2 positive? Static: **False**. Runner: **False**.
3. Survives 1-4 tick slippage? Static: **False**. Runner: **False**.
4. Concentrated in a few trades? Top-5 winner share — static: **74.3%**, runner: **77.8%**.
5. Concentrated in one period? See quarter tables above — check for any single quarter carrying the whole result.
6. Does runner materially improve edge, or just increase tail dependence? PF delta (runner − static) = **-0.082**; 'materially improves, not just tail dependence' flag: **False** (requires PF delta > 0.1 AND runner's top-5 concentration not meaningfully worse than static's).
7. Does static fail while runner passes? **False**.
8. If runner passes, is it already canonical/executable? **NO — production exit_mode default is static (config.runner_mode default False); runner_mode exists and is exercised identically to live by replay, but is not the deployed default**
9. Does the result depend on stale combined-book assumptions? No — this run is isolated single-strategy, own account; see historical comparators below for what the combined-book (#346) and pre-correction (2026-07-11/07-24) studies showed instead.
10. Does MNQ alone support the strategy? Static: **False**. Runner: **False**.
11. Any live/replay formula differences? See parity findings below — one material Pine/backend stop-offset mismatch found (does not affect replay).
12. Is "WAIT — gated on runner exit" still accurate? See classification above — this pass tests runner directly for the first time under honest fills rather than gating on its promotion; the wording should be replaced with whatever this pass's classification is.

## Parity findings

- **Pine stop-offset staleness (MATERIAL)**: MATERIAL: tradingview/risksentinel_context.pine:419,427 hardcodes the ORB stop offset at `tick * 8` for orb_breakout. The Python backend (strategy/signal_engine.py:1899) reads risk_rules.yaml's `orb_stop_ticks: {MNQ: 48, MES: 16}` instead -- a deliberate, risk_rules.yaml-documented widening from the same legacy 8-tick default, 'validated on replay'. strategy/signal_engine.py:1036-1112 (_apply_advisory_bracket) will accept Pine's complete bracket and OVERRIDE the backend's own computed stop whenever Pine agrees on direction+strategy -- there is no minimum-stop-distance floor, only structural checks (stop<entry<target, positive values, RR>0). If Pine ever sends a live orb_breakout alert with a complete bracket, the stale narrower 8-tick stop would silently replace the wider, risk-validated 48-tick one. Confirmed this does NOT affect replay evidence (replay/replay_engine.py:1172 sets state.raw=candle.source, which is None/absent in this corpus -- verified by sampling -- so _apply_advisory_bracket's pine_has_bracket check is always False in replay). This is a live-path-only risk. NOT FIXED in this lane per instruction -- reported only.
- **`orb_stop_ticks=48` provenance**: risk_rules.yaml's own comment on `orb_stop_ticks: {MNQ: 48}` states the 622-day sweep that chose 48 (over 8/16/32) assumed RUNNER exit ON and 'Replay = fills assumed -> live-shadow before trusting' -- i.e. that tuning was done under an OPTIMISTIC fill assumption, not honest IOC. This isolated run is the first honest-fill test of this exact stop width, for both exit modes.
- **Cross-instrument contamination**: Checked: DailyState.orb_break_long_played/short_played (which gate one-fire-per-direction-per-day for orb_breakout) were confirmed Dict[str,bool] keyed by instrument in current code -- PR #324 fixed the prior cross-instrument leak 2026-07-24, 3 hours after it was recorded. Not a live blocker; moot anyway since this run is MNQ-only.
- **GEX gate**: state.gex.gex_regime is None in every sampled corpus bar (checked MNQ_2025-08-15.jsonl, 0/84 bars non-null) -- _gex_allows_orb() always returns True (no-op) in this replay corpus. Consistent with GEX being observe-only/inert in production too (memory: GEX_OBSERVE_ENABLED, analysis toggle OFF) -- not a live/replay parity contradiction.
- **MES scope**: orb_breakout is explicitly disabled for MES in production (risk_rules.yaml: 'never the validated cell in the #236/#237/#238 evidence chain'). No rule support, no evidence reason to test it here -- MNQ only, per operator instruction not to expand instruments casually.

## Historical comparators (context only — NOT walk-forward-valid, NOT honest-fill except PR #346)

- **MNQ, market-fill, runner exit, unbounded entry, 622d retest_baseline_off arms** (docs/orb-breakout-entry-study-2026-07-11.md:27): n=60, 58.3% WR, PF 1.770, $1,043.75 net, exp $17.40. provenance/context only -- market-fill (not ioc_limit), predates #338/#339/#342 corpus corrections, inputs explicitly documented as gitignored/unreproducible (scripts/ORB_BREAKOUT_ENTRY_STUDY_EVIDENCE_NOTE.md), and superseded by the 2026-07-24 validation pass finding this SAME cited edge is carried almost entirely by LONG+london (SHORT/NY separately near-breakeven-or-negative)
- **MNQ, market-fill, static exit, same 622d arm population as the runner figure above** (docs/orb-breakout-entry-study-2026-07-11.md:27): n=63, 71.4% WR, PF 1.060, $56.50 net, exp $0.90. provenance/context only -- same caveats as the runner figure above
- **MNQ, market-fill (legacy), Corpus v1, 2026-07-24 validation pass** (docs/strategy-validation-pass-2026-07-24.md:273-286): n=68, 54.4% WR, PF 2.245, $3,656.00 net, exp $54.00. provenance/context only -- market-fill, predates #338/#339/#342. Materially concentrated: LONG n=43 PF 3.144 exp $80 vs SHORT n=25 PF 1.151 exp $8; london n=43 PF 3.227 exp $85 vs new_york n=22 PF 0.953 exp -$2 (net NEGATIVE)
- **MNQ+MES combined-book, ioc_limit, PR #346, post-#338/#339/#342** (docs/corrected-ioc-corpus-evidence-2026-07-26.md): n=3, 33.3% WR, PF 0.723, $-18.44 net, exp $-6.15. the only POST-correction figure before this pass, but combined-book: the account-level 20% breaker was halted mostly by OTHER strategies' losses (2025-09-08 MNQ / 2025-12-11 MES), leaving only n=3 resolved and zero H2 data for orb_breakout specifically -- not this strategy's own evidence, exactly the contamination this isolated run corrects for

## Reproduction

```bash
python scripts/orb_breakout_canonical_evidence.py \
  --logs logs/replay_orb_breakout_canonical \
  --out scripts/orb_breakout_canonical_evidence_results.json \
  --raw scripts/orb_breakout_canonical_evidence_raw_trades.jsonl \
  --report docs/orb-breakout-canonical-evidence-2026-07-26.md
```
