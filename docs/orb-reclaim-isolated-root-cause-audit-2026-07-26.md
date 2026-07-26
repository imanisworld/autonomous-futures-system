# ORB Reclaim — isolated honest-fill root-cause audit

**MNQ verdict: WAIT** — sample below 30-trade minimum (n=21); insufficient data for walk-forward (one half has zero resolved trades); fails 1-4 tick slippage sensitivity
**MES verdict: WAIT** — insufficient data for walk-forward (one half has zero resolved trades); fails 1-4 tick slippage sensitivity

Pinned code: `63f6bc9ed66824905caa13e24869d42a26595a40`
Corpus: `data/replay_corpus_v1_market_condition_fixed` (post-#338 corrected market_condition, post-#339/#342 ReplayEngine)
Range: 2025-07-24 → 2026-07-23

## Method

- **Isolated** single-strategy replay (`enabled_concepts=["orb_reclaim"]` only) — MNQ and MES run as two SEPARATE fresh accounts, never combined. A breaker trip on one reflects only that instrument's own P&L.
- `entry_fill_model="ioc_limit"` canonical, MNQ tolerance 32t / MES tolerance 16t, asserted not overridden.
- 1/2/3/4-tick adverse slippage sweep on the canonical config, each instrument.
- A diagnostic `entry_fill_model="market"` run (1-tick) joined to the canonical ioc_limit run by (date, bar_ts) candidate identity, to classify market-vs-IOC outcome transitions. Context only, not a canonical result.
- $1.48 round-trip commission at the analysis layer only.
- `risk_rules.yaml` verified byte-identical before/after (`56677a0ab37bbf62…`).

## MNQ — overall (1-tick canonical)

| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 46 | 21 | 25 | 21 | 23.8% | $-306.25 | $-337.33 | $-7.33 | $-16.06 | 0.546 | $388.35 | 6 | 100.0% | ❌ |

## MES — overall (1-tick canonical)

| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MES | 104 | 75 | 29 | 75 | 29.3% | $-330.00 | $-441.00 | $-4.24 | $-5.88 | 0.831 | $848.15 | 9 | 27.9% | ✅ |

## Combined reporting aggregate (post-hoc sum of the two isolated runs, 1-tick)

| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| COMBINED | 150 | 96 | 54 | 96 | 28.1% | $-636.25 | $-778.33 | $-5.19 | $-8.11 | 0.768 | $1,215.62 | 9 | 23.5% | ✅ |

## By session, all-session-account view (1-tick canonical) — CONTEXT ONLY, NOT independently isolated

⚠️ These rows are a post-hoc filter of the single all-session account above — a London loss in this same account CAN still consume the breaker budget that would otherwise be available to a later New York bar. See the session-isolated lanes section below for the clean, independently-breakered test.

### MNQ
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 0 | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | — | $0.00 | 0 | — | ❌ |
| london | 26 | 18 | 8 | 18 | 27.8% | $-146.25 | $-172.89 | $-6.65 | $-9.61 | 0.701 | $311.99 | 6 | 100.0% | ❌ |
| new_york | 20 | 3 | 17 | 3 | 0.0% | $-160.00 | $-164.44 | $-8.22 | $-54.81 | 0.000 | $164.44 | 3 | — | ❌ |

### MES
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 3 | 3 | 0 | 3 | 33.3% | $-20.00 | $-24.44 | $-8.15 | $-8.15 | 0.776 | $109.21 | 2 | 100.0% | ❌ |
| london | 52 | 43 | 9 | 43 | 23.3% | $-420.00 | $-483.64 | $-9.30 | $-11.25 | 0.644 | $787.96 | 11 | 59.0% | ✅ |
| new_york | 49 | 29 | 20 | 29 | 37.9% | $110.00 | $67.08 | $1.37 | $2.31 | 1.059 | $264.67 | 4 | 49.9% | ❌ |

### Combined
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 3 | 3 | 0 | 3 | 33.3% | $-20.00 | $-24.44 | $-8.15 | $-8.15 | 0.776 | $109.21 | 2 | 100.0% | ❌ |
| london | 78 | 61 | 17 | 61 | 24.6% | $-566.25 | $-656.53 | $-8.42 | $-10.76 | 0.661 | $1,099.95 | 13 | 40.6% | ✅ |
| new_york | 69 | 32 | 37 | 32 | 34.4% | $-50.00 | $-97.36 | $-1.41 | $-3.04 | 0.925 | $361.97 | 5 | 49.9% | ✅ |

## Session-isolated canonical lanes — AUTHORITATIVE London vs New York test (operator HOLD amendment)

AUTHORITATIVE clean London-vs-New-York test (operator HOLD amendment). Each lane is an INDEPENDENT account with allowed_sessions=[session] -- off-session bars never generate a candidate (signal_engine.py:294 returns NO_TRADE before _try_orb_reclaim is ever called), so one session cannot censor another's evidence. Breaker ON (canonical, matches production). Supersedes the earlier by-session breakdown of the all-session run below, which was a post-hoc filter of a single shared-breaker account and could not rule out cross-session censorship.

### MNQ london
**Verdict: WAIT** — fails both-halves-positive walk-forward under honest fills; fails 1-4 tick slippage sensitivity
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ london | 165 | 75 | 90 | 75 | 32.0% | $-230.50 | $-341.50 | $-2.07 | $-4.55 | 0.859 | $655.56 | 11 | 23.5% | ✅ |
Own breaker: did NOT trip.
Both halves positive: **False**. Survives 1-4 tick slippage: **False**.

### MNQ new_york
**Verdict: WAIT** — sample below 30-trade minimum (n=6); insufficient data for walk-forward (one half has zero resolved trades); fails 1-4 tick slippage sensitivity
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ new_york | 33 | 6 | 27 | 6 | 0.0% | $-313.00 | $-321.88 | $-9.75 | $-53.65 | 0.000 | $321.88 | 6 | — | ❌ |
Own breaker: tripped 2025-11-10 (Account drawdown 20.9% exceeds max 20.0% from peak $1,500.00.).
Both halves positive: **None**. Survives 1-4 tick slippage: **False**.

### MES london
**Verdict: WAIT** — insufficient data for walk-forward (one half has zero resolved trades); fails 1-4 tick slippage sensitivity
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MES london | 49 | 40 | 9 | 40 | 25.0% | $-306.25 | $-365.45 | $-7.46 | $-9.14 | 0.705 | $644.54 | 11 | 59.0% | ✅ |
Own breaker: tripped 2025-12-03 (Account drawdown 20.4% exceeds max 20.0% from peak $1,500.00.).
Both halves positive: **None**. Survives 1-4 tick slippage: **False**.

### MES new_york
**Verdict: WAIT** — fails 1-4 tick slippage sensitivity
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MES new_york | 128 | 79 | 49 | 79 | 38.0% | $248.75 | $131.83 | $1.03 | $1.67 | 1.041 | $474.11 | 7 | 18.4% | ✅ |
Own breaker: did NOT trip.
Both halves positive: **True**. Survives 1-4 tick slippage: **False**.

## Walk-forward H1/H2, all-session accounts (1-tick canonical)

### MNQ
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 46 | 21 | 25 | 21 | 23.8% | $-306.25 | $-337.33 | $-7.33 | $-16.06 | 0.546 | $388.35 | 6 | 100.0% | ❌ |
| H2 | 0 | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | — | $0.00 | 0 | — | ❌ |
Both halves positive: **None**

### MES
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 104 | 75 | 29 | 75 | 29.3% | $-330.00 | $-441.00 | $-4.24 | $-5.88 | 0.831 | $848.15 | 9 | 27.9% | ✅ |
| H2 | 0 | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | — | $0.00 | 0 | — | ❌ |
Both halves positive: **None**

## Drawdown breaker — all-session isolated accounts (canonical, used for classification)

- **MNQ**: isolated account's OWN 20% breaker tripped on its own P&L: 2025-09-29 (Account drawdown 20.4% exceeds max 20.0% from peak $1,500.00.). New order admission stopped from that date on the canonical run.
- **MES**: isolated account's OWN 20% breaker tripped on its own P&L: 2025-12-11 (Account drawdown 22.0% exceeds max 20.0% from peak $1,500.00.). New order admission stopped from that date on the canonical run.

### Breaker-off diagnostic (NON-CANONICAL, OUT OF SCOPE — provenance only, not used for anything above)

NON-CANONICAL. Ruled outside the authorized validation path by the operator's HOLD amendment. Preserved ONLY as diagnostic provenance (what evidence the 20% breaker censored) for auditability -- MUST NOT be used to determine classification, Master Table fields, or any 'genuinely negative' / 'near-flat' style conclusion. Not rerun in this amendment; figures are exactly as first produced.

- **MNQ** (non-canonical, breaker disabled): n=107 resolved, 29.9% WR, $-826.86 net, PF 0.772. Provenance only — not a classification input.
- **MES** (non-canonical, breaker disabled): n=181 resolved, 34.3% WR, $-171.62 net, PF 0.973. Provenance only — not a classification input.

## Slippage sensitivity 1/2/3/4-tick (overall, canonical)

### MNQ
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 46 | 21 | 25 | 21 | 23.8% | $-306.25 | $-337.33 | $-7.33 | $-16.06 | 0.546 | $388.35 | 6 | 100.0% | ❌ |
| 2tick | 46 | 21 | 25 | 21 | 23.8% | $-324.25 | $-355.33 | $-7.72 | $-16.92 | 0.532 | $405.85 | 6 | 100.0% | ❌ |
| 3tick | 46 | 21 | 25 | 21 | 23.8% | $-342.25 | $-373.33 | $-8.12 | $-17.78 | 0.518 | $423.35 | 6 | 100.0% | ❌ |
| 4tick | 45 | 20 | 25 | 20 | 25.0% | $-302.75 | $-332.35 | $-7.39 | $-16.62 | 0.546 | $381.87 | 6 | 100.0% | ❌ |
Survives 1-4 tick: **False**

### MES
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 104 | 75 | 29 | 75 | 29.3% | $-330.00 | $-441.00 | $-4.24 | $-5.88 | 0.831 | $848.15 | 9 | 27.9% | ✅ |
| 2tick | 100 | 72 | 28 | 72 | 30.6% | $-311.25 | $-417.81 | $-4.18 | $-5.80 | 0.837 | $814.94 | 9 | 28.0% | ✅ |
| 3tick | 54 | 43 | 11 | 43 | 30.2% | $-314.37 | $-378.01 | $-7.00 | $-8.79 | 0.761 | $765.16 | 8 | 47.8% | ✅ |
| 4tick | 51 | 41 | 10 | 41 | 31.7% | $-334.38 | $-395.06 | $-7.75 | $-9.64 | 0.751 | $772.19 | 6 | 47.9% | ✅ |
Survives 1-4 tick: **False**

## Slippage sensitivity 1/2/3/4-tick — session-isolated lanes

### MNQ london
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 165 | 75 | 90 | 75 | 32.0% | $-230.50 | $-341.50 | $-2.07 | $-4.55 | 0.859 | $655.56 | 11 | 23.5% | ✅ |
| 2tick | 165 | 75 | 90 | 75 | 32.0% | $-292.50 | $-403.50 | $-2.45 | $-5.38 | 0.836 | $696.06 | 11 | 23.5% | ✅ |
| 3tick | 163 | 74 | 89 | 74 | 32.4% | $-303.00 | $-412.52 | $-2.53 | $-5.57 | 0.832 | $683.58 | 11 | 23.5% | ✅ |
| 4tick | 90 | 50 | 40 | 50 | 32.0% | $-309.00 | $-383.00 | $-4.26 | $-7.66 | 0.776 | $632.56 | 11 | 36.0% | ✅ |
Survives 1-4 tick: **False**

### MNQ new_york
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 33 | 6 | 27 | 6 | 0.0% | $-313.00 | $-321.88 | $-9.75 | $-53.65 | 0.000 | $321.88 | 6 | — | ❌ |
| 2tick | 33 | 6 | 27 | 6 | 0.0% | $-319.00 | $-327.88 | $-9.94 | $-54.65 | 0.000 | $327.88 | 6 | — | ❌ |
| 3tick | 33 | 6 | 27 | 6 | 0.0% | $-325.00 | $-333.88 | $-10.12 | $-55.65 | 0.000 | $333.88 | 6 | — | ❌ |
| 4tick | 33 | 6 | 27 | 6 | 0.0% | $-331.00 | $-339.88 | $-10.30 | $-56.65 | 0.000 | $339.88 | 6 | — | ❌ |
Survives 1-4 tick: **False**

### MES london
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 49 | 40 | 9 | 40 | 25.0% | $-306.25 | $-365.45 | $-7.46 | $-9.14 | 0.705 | $644.54 | 11 | 59.0% | ✅ |
| 2tick | 44 | 39 | 5 | 39 | 25.6% | $-322.50 | $-380.22 | $-8.64 | $-9.75 | 0.694 | $654.29 | 11 | 59.2% | ✅ |
| 3tick | 33 | 28 | 5 | 28 | 25.0% | $-351.87 | $-393.31 | $-11.92 | $-14.05 | 0.590 | $662.40 | 11 | 77.5% | ❌ |
| 4tick | 32 | 27 | 5 | 27 | 25.9% | $-354.38 | $-394.34 | $-12.32 | $-14.61 | 0.586 | $658.41 | 10 | 77.6% | ❌ |
Survives 1-4 tick: **False**

### MES new_york
| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 128 | 79 | 49 | 79 | 38.0% | $248.75 | $131.83 | $1.03 | $1.67 | 1.041 | $474.11 | 7 | 18.4% | ✅ |
| 2tick | 128 | 79 | 49 | 79 | 38.0% | $206.25 | $89.33 | $0.70 | $1.13 | 1.028 | $489.11 | 7 | 18.4% | ✅ |
| 3tick | 128 | 79 | 49 | 79 | 38.0% | $111.25 | $-5.67 | $-0.04 | $-0.07 | 0.998 | $504.11 | 7 | 18.4% | ✅ |
| 4tick | 128 | 79 | 49 | 79 | 38.0% | $-30.00 | $-146.92 | $-1.15 | $-1.86 | 0.956 | $516.61 | 7 | 18.4% | ✅ |
Survives 1-4 tick: **False**

## IOC vs market-fill comparison (1-tick, joined by date+bar_ts candidate identity)

### MNQ
- `market_loss__ioc_loss`: 16
- `market_win__ioc_no_fill`: 15
- `market_loss__ioc_no_fill`: 8
- `market_win__ioc_win`: 5

### MES
- `market_loss__ioc_loss`: 52
- `market_win__ioc_win`: 22
- `market_win__ioc_no_fill`: 16
- `market_loss__ioc_no_fill`: 11

## Root-cause questions (operator's list, answered explicitly)

1. Is ORB Reclaim genuinely negative in isolation? MNQ: **True** ($-337.33). MES: **True** ($-441.00).
2. Is MES the dominant problem? Combined net $-778.33 = MNQ $-337.33 + MES $-441.00.
3. Is London the dominant problem? See the session-isolated canonical lanes above — the authoritative answer (all-session by-session tables are context only, see caveat above them).
4. Is NY materially better? See the session-isolated canonical lanes above.
5. Is the combined-book -$588.28 representative or misleading? See combined reporting aggregate above vs PR #346's historical comparator — isolated numbers are NOT breaker-truncated to H1-only the same way (see walk-forward section) unless the isolated account tripped its own breaker (see drawdown breaker section). Non-canonical breaker-off provenance is preserved below for auditability only and does not answer this question on its own.
6. Does the isolated strategy itself hit max drawdown? MNQ: **True**. MES: **True**. See breaker section.
7. Does H2 recover or remain weak? See walk-forward H1/H2 tables above.
8. Does the result survive 1-4 tick slippage? MNQ: **False**. MES: **False**.
9. Is the problem signal quality, fill behavior, session mix, instrument mix, or a combination? See IOC-vs-market comparison, by-session, and by-instrument breakdowns above.
10. Is any current runtime/config/Pine mismatch proven? See parity findings below — one material trend-gate replay/live population gap, one material MES-promotion evidence-basis tension. Neither fixed here.
11. Does anything justify changing rules? No — evidence-only lane, no tuning performed regardless of result.
12. Final classification: MNQ **WAIT**, MES **WAIT**.

## Parity findings

- **Predicate/direction/stop/target**: LONG only -- no SHORT branch exists in Python (_try_orb_reclaim, signal_engine.py:1963-1995) or Pine (risksentinel_context.pine:433-439). Entry/stop/target formula is an EXACT structural match between Python and Pine (entry=orb_h+2t, stop=max(orb_l-4t, entry-MAX_STOP*t) with MNQ=80t/MES=40t on both sides, target=entry+2.5xrisk). No Pine staleness found for this strategy (contrast with orb_breakout's stale-8-tick finding, PR #349 -- does NOT apply here).
- **Trend-gate replay-vs-live population gap (MATERIAL)**: Pine's orb_reclaim branch requires trend_dir=='UP' (its own EMA-stack recompute) before ever alerting; Python's _try_orb_reclaim has NO trend check. The 2026-07-24 Pine Parity Audit's Finding 3 hard-gate list (6 strategies) does not include orb_reclaim. Consequence: replay's orb_reclaim candidate population is a strict SUPERSET of what live could ever produce -- this evidence may include bars a live alert would never have fired on. Not fixed (shared-code change out of scope); reported per instruction.
- **MES sole-proof-lane evidence-basis tension (MATERIAL)**: risk_rules.yaml disables every other MES strategy on the basis that MES orb_reclaim was 'the ONLY strategy with validated positive expectancy under honest fills' (622-day study, PR #150, pre-#338/#339/#342 engine). PR #346's newer corrected combined-book run shows MES orb_reclaim net -$441.00 on this dataset's window -- in tension with that promotion's stated basis. This isolated run is the first honest-fill MES orb_reclaim test under the POST-correction engine. Not fixed (risk_rules.yaml untouched); reported per instruction, addressed empirically below.
- **Sessions**: risk_rules.yaml `sessions.allowed` = [asian, london, new_york], no per-strategy restriction. The `allowed_sessions: [new_york]` at line 505 belongs to the unrelated `options_trading` block -- checked directly, ruled out as a false lead before this write-up.
- **GEX gate**: state.gex.gex_regime is None throughout the corpus (confirmed in the ORB Breakout PR #349 audit, same corpus) -- _gex_allows_orb() is a no-op in replay. Consistent with GEX being observe-only/inert in production too.

## Historical comparators (context only)

- **MNQ+MES combined-book, ioc_limit, PR #346 (post-#338/#339/#342), full H1, breaker-truncated** (docs/corrected-ioc-corpus-evidence-2026-07-26.md, docs/corpus-v1-loss-attribution-2026-07-26.md): combined-book attribution, valid for what it is, but the SHARED account's own 20% breaker (mostly tripped by OTHER strategies) halted new orders before H2 -- every one of the 86 resolved trades is H1-only. Not a standalone orb_reclaim verdict; exactly the contamination this isolated run corrects for.
- **MES orb_reclaim, ioc_limit, 622-day Polygon set, runner exit, pre-#338/#339/#342 engine** (docs/ioc-faithful-baseline-622d-2026-07-06.md, docs/mes-orb-reclaim-deepdive-2026-07-06.md): the ONLY basis cited in risk_rules.yaml for promoting MES orb_reclaim to sole active MES proof lane. Positive both halves under 95% CI [+0.20,+25.13] at the time -- but ran on the engine BEFORE #338 (market_condition parity fix) and #339/#342 (replay cross-day carry-forward fixes). This isolated run is the first honest-fill re-test of MES orb_reclaim under the corrected engine/corpus.

## Reproduction

```bash
python scripts/orb_reclaim_isolated_root_cause_audit.py \
  --logs logs/replay_orb_reclaim_isolated \
  --out scripts/orb_reclaim_isolated_root_cause_audit_results.json \
  --raw scripts/orb_reclaim_isolated_root_cause_audit_raw_trades.jsonl \
  --report docs/orb-reclaim-isolated-root-cause-audit-2026-07-26.md
```
