# Prereg — Structural-Level Tranche 2: MGC / MCL / MBT (definitions + X0 source/roll proof)

**Status:** SPEC ONLY / PAPER-RESEARCH ONLY / **NO OUTCOMES, NO CORPUS BUILD, NO R5**.
**Parent:** `docs/prereg-cross-instrument-historical-expansion-2026-09-17.md` (#622) + amendment 1
(#625). **Sibling:** `docs/prereg-dynamic-structural-level-attribution-2026-09-16.md` v1.5 (M2K).
**Scope (operator instruction 2026-09-17):** definitions + X0 source/contract-chain probes only.
Nothing here authorises a confirmatory corpus; that needs a separate go after this document is
reviewed. No runtime, `.env`, risk, strategy, broker, collector, TradingView, epoch, threshold
or calendar change. Box only read (six bar files copied 2026-09-17T03:05Z).

Reuse, not rebuild (§B1): tick/value metadata `config/futures_contracts.contract_economics`
(MGC 0.10/$1, MCL 0.01/$1, MBT 5.0/$0.50); observation-only routing for all three (#585/#588);
product-aware **feed-health** calendar `context/futures_session.product_session_active`
(feed expectation only — **not** structural authority; unchanged here, no defect found in it).

---

## 1. What the research layer assumes today (proven incompatibility, §B2)

`research/structural_level_features.py` (P1, frozen v1.3) hard-codes the CME equity-index model;
every item below is an equity-index constant, not a parameter:

| Construct | Code | Equity-index assumption |
|---|---|---|
| Trading day | `_trading_day` → `(ET + 6h).date()` | 18:00 ET roll |
| Session labels | `detect_session` | asian 18:00–03:00, london 03:00–09:30, new_york 09:30–17:00 ET |
| Open-slot / gap accounting | `_slot_open`, `cme_open_hours` | 17:00–18:00 ET halt, Fri 17:00 → Sun 18:00 closed |
| NY ORB | `_orb("NY_ORB", 09:30, 09:45, …)` | 09:30 ET canonical bar |
| London ORB | `_orb("LDN_ORB", 03:00, 03:15, …)` | 03:00 ET |
| ONH / ONL | bars in [18:00 ET prev day, 09:30 ET) | frozen at 09:30 ET |
| PMH / PML | [04:00 ET, 09:30 ET) | equity pre-market |
| PDH / PDL / PDC_BAR | previous 18:00-ET trading day | — |
| PWH / PWL | Mon–Fri trading week, Sun 18:00 → Fri 17:00 ET | — |
| HOD / LOD | current 18:00-ET trading day, running | — |
| VWAP (diagnostic, never admitted) | anchored at the 18:00 ET day roll | C14 open: even for MNQ/MES the anchor is wrong on some holiday transitions |
| LC_ZONE 1H/4H | `aggregate` of 15m bars on clock hours | timeframe-only; no session dependence |
| Roll / seams | `polygon_client.contract_schedule` | quarterly equity-index cycle only |

The live side is the same: the Pine indicator that feeds the observation lane computes
`orb_*`, EMAs and `session` with the equity map, `webhook/state_builder.detect_session` labels
every root with the equity sessions (MBT bars at 17:00–18:00 ET are labelled `off_hours`
although MBT trades then — seen in the box's records), and
`execution/cross_instrument_observation.observation_day` uses the 18:00 ET boundary for
MGC/MCL and the **ET calendar date for MBT** (bookkeeping only; it is not a structural
trading day and per #586 it must never become an outcome-expiry rule).

Consequence: nothing in that list may be inherited by MGC, MCL or MBT without an explicit
per-root classification (§3). "Applicable with product definition" means a *new* definition
has to be frozen for that root — no other product's value is substituted to avoid N/A.

## 2. Exchange definitions verified for this document

Sources checked 2026-09-17 (CME pages time out from this host; CME education / FAQ / SER
notices and secondary summaries were used, cited per item; anything not verified from an
exchange document is marked **UNVERIFIED** and stays `UNPROVEN` in §3):

| Item | MGC (COMEX Micro Gold) | MCL (NYMEX Micro WTI) | MBT (CME Micro Bitcoin) |
|---|---|---|---|
| Globex hours | Sun–Fri 18:00 → 17:00 ET, 60-min break from 17:00 ET (CME Micro Gold overview) | Sun–Fri 17:00 → 16:00 CT (= 18:00 → 17:00 ET), 60-min halt (CME Micro WTI overview) | **24/7 since 2026-05-29** (CME press release 2026-06-01); maintenance **Mon–Fri 16:00–16:02 CT and Sat 02:00–04:00 CT** (CME crypto FAQ) — matches `context/futures_session._crypto_active` |
| Exchange-defined trading-day boundary | 18:00 ET Globex reopen (trade date advances at the reopen) | same | **UNVERIFIED** for the 24/7 regime (trade-date roll presumably at the 16:00 CT maintenance; not confirmed from an exchange document) |
| Daily settlement window (exchange-defined intraday anchor) | **13:29:00–13:30:00 ET** (CME gold settlement procedure, SER 9637) | **14:28:00–14:30:00 ET** (NYMEX energy settlement procedure) | final settlement to the BRR at **16:00 London** on the last Friday of the contract month (CME FAQ); the *daily* settlement window under 24/7 is **UNVERIFIED** |
| "Pit" / primary-session open | 08:20 ET is the **legacy COMEX open-outcry open** (pits closed 2016); not an exchange definition today, a vendor "RTH" convention | 09:00 ET is the **legacy NYMEX pit open** (same status) | none — there is no session |
| Contract months | education page lists Apr/Jun/Aug/Dec for the micro; the provider inventory (§4) is the ground truth used here | monthly (current year + 10 years listed) | six consecutive months + two Decembers |
| Termination | third-last business day of the delivery month (**UNVERIFIED** from an exchange document; §4 uses the provider's `last_trade_date`) | one business day before the corresponding CL termination (secondary sources; §4 uses the provider's `last_trade_date`) | last Friday of the contract month, 16:00 London (CME FAQ / MarketsWiki) |
| Tick / unit | 0.10 = $1 (10 oz) | 0.01 = $1 (100 bbl) | 5.0 = $0.50 (0.1 BTC) |

**Finding for §B3:** neither 08:20 ET (gold) nor 09:00 ET (crude) is an exchange-defined open.
The only exchange-defined intraday anchors for MGC/MCL are the 18:00 ET Globex reopen and the
settlement window close (13:30 ET / 14:30 ET). A "primary session" for these products is
therefore a *convention* that this prereg must freeze explicitly and label as such — it cannot
be verified as a fact. The candidate conventions are recorded in §3 with status `UNPROVEN`
(needs a ruling), not adopted.

## 3. Per-root structural-concept classification (frozen for review)

Legend: `APPLICABLE_AS_IS` = the equity-index definition is already the correct product
definition; `APPLICABLE_WITH_PRODUCT_DEFINITION` = the concept applies but needs a new frozen
definition (candidate given, **not adopted**); `NOT_APPLICABLE` = the concept has no meaning
for the product; `UNPROVEN` = the concept may apply but no defensible definition can be
written from verified facts yet.

### 3.1 MGC

| Concept | Status | Definition / reason |
|---|---|---|
| Trading day | `APPLICABLE_WITH_PRODUCT_DEFINITION` | 18:00 ET Globex reopen — numerically the same boundary as equity (`_trading_day`), but frozen here on its own authority (COMEX Globex schedule), not inherited |
| Maintenance halt / open slots | `APPLICABLE_AS_IS` for the 17:00–18:00 ET halt and the weekend; **the 16:15–16:30 ET equity halt does not exist** — `_slot_open` / `cme_open_hours` are correct for MGC only because they do not model that halt either |
| Primary / RTH open | `UNPROVEN` | candidate A = 08:20 ET (legacy pit open, vendor RTH); candidate B = no primary session, use the 18:00 ET reopen only; candidate C = settlement-anchored session ending 13:30 ET. 08:20 is **not** exchange-defined (§2). Needs a ruling |
| ORB | `APPLICABLE_WITH_PRODUCT_DEFINITION` | first 15m bar of the chosen primary open (A → 08:20–08:35 ET); validity until the settlement close 13:30 ET. **Blocked on the RTH ruling.** The live Pine `orb_*` fields for MGC are the equity 09:30 ORB → live SIGNAL rows of `orb_false_break_fade` for MGC are equity-convention rows, not product ORB evidence |
| London ORB / London session | `APPLICABLE_WITH_PRODUCT_DEFINITION` | 03:00 ET is the London Metal Exchange / LBMA morning and is economically meaningful for gold; the 03:00–03:15 ET bar definition can be reused **as a product definition** (same clock, different justification); PM fix 10:00 ET London is a further candidate anchor, not proposed |
| Overnight (ONH/ONL) | `APPLICABLE_WITH_PRODUCT_DEFINITION` | [18:00 ET, primary open) — undefined until the primary open is ruled |
| PDH / PDL / PDC | `APPLICABLE_WITH_PRODUCT_DEFINITION` | previous 18:00-ET trading day H/L; **PDC should be the settlement-window close (13:30 ET) or the 16:59 ET last trade — two different closes; ruling needed** (equity uses the last bar before 17:00 ET) |
| PWH / PWL | `APPLICABLE_AS_IS` | Sun 18:00 → Fri 17:00 ET week; identical schedule |
| HOD / LOD | `APPLICABLE_AS_IS` | running high/low of the 18:00-ET trading day |
| Zones (LC_ZONE 1H/4H) | `APPLICABLE_AS_IS` | timeframe aggregation only; no session dependence |
| VWAP anchor | `UNPROVEN` (diagnostic only, never admitted — C14 open for MNQ/MES too) | candidate: 18:00 ET day roll; the settlement close is another candidate. Not proposed until C14's generalised session rule exists |
| Session labels (asian/london/new_york) | `NOT_APPLICABLE` as strategy sessions; **informational only** in lane records | the equity map has no product meaning for gold; kept as bookkeeping labels |

### 3.2 MCL

| Concept | Status | Definition / reason |
|---|---|---|
| Trading day | `APPLICABLE_WITH_PRODUCT_DEFINITION` | 18:00 ET Globex reopen (NYMEX schedule), frozen on its own authority |
| Maintenance halt / open slots | as MGC (`APPLICABLE_AS_IS`, no 16:15 halt) |
| Primary / RTH open | `UNPROVEN` | candidate A = 09:00 ET (legacy NYMEX pit open, vendor RTH); B = none; C = settlement-anchored session ending 14:30 ET. **09:00 ET verified only as the legacy pit open**, not as a current exchange definition. Separate ruling from MGC — not borrowed |
| ORB | `APPLICABLE_WITH_PRODUCT_DEFINITION` | first 15m bar of the ruled primary open (A → 09:00–09:15 ET), valid until 14:30 ET. Blocked on the ruling. Live Pine `orb_*` = equity 09:30 ORB (same caveat as MGC) |
| London ORB / London session | `UNPROVEN` | 03:00 ET has no product justification for WTI (ICE Brent opens 20:00 ET prev day; the London energy morning is not a known anchor). Not proposed |
| Overnight | `APPLICABLE_WITH_PRODUCT_DEFINITION` | [18:00 ET, primary open) once ruled |
| PDH / PDL / PDC | `APPLICABLE_WITH_PRODUCT_DEFINITION` | previous trading day H/L; PDC = settlement close 14:30 ET vs last bar before 17:00 ET — ruling needed. **Weekly EIA inventory (Wed 10:30 ET) and expiry weeks are known regime breaks — flag, do not model** |
| PWH / PWL | `APPLICABLE_AS_IS` |
| HOD / LOD | `APPLICABLE_AS_IS` |
| Zones | `APPLICABLE_AS_IS` |
| VWAP anchor | `UNPROVEN` (diagnostic) | as MGC |
| Session labels | `NOT_APPLICABLE` as strategy sessions; informational |
| Monthly expiry inside the analysis window | additional product caution: a monthly roll means ~12 seams per year; per prereg §9.5 item 5 every seam contaminates the roll trading day + next (PD levels), the roll week + next (PW levels), 10 trading days (zones) → a large share of MCL rows will be roll-contaminated for weekly/zone features. Reported, not tuned |

### 3.3 MBT (terminal outcomes remain **HOLD** — `MBT_OUTCOME_HORIZON_UNPROVEN`)

| Concept | Status | Definition / reason |
|---|---|---|
| Trading day / bookkeeping boundary | `UNPROVEN` | 24/7 since 2026-05-29: no Globex reopen. Candidates: (a) the 16:00–16:02 CT daily maintenance (exchange-defined, 2 min); (b) 00:00 UTC (crypto-market convention, not exchange); (c) ET calendar date (the lane's current bookkeeping, `observation_day`). A feature-construction boundary may be frozen after a ruling; **none of them is an outcome-expiry rule** (#586) |
| Maintenance windows | `APPLICABLE_WITH_PRODUCT_DEFINITION` | Mon–Fri 16:00–16:02 CT, Sat 02:00–04:00 CT (CME FAQ); `_slot_open` (equity) is **wrong** for MBT: it would mark Fri 17:00 ET → Sun 18:00 ET and every 17:00 ET hour as closed. A product `_slot_open` is required before any gap accounting |
| Pre-2026-05-29 history | additional | before the 24/7 launch MBT traded the Globex week (Sun 18:00 → Fri 17:00 ET, daily halt); a historical corpus spanning the launch needs a **dated** calendar. `product_session_active` is undated (correct for now, wrong for history) — finding, not a change |
| Primary / RTH open | `NOT_APPLICABLE` | no session exists |
| ORB | `NOT_APPLICABLE` as a product concept (no open); any "ORB" would be an arbitrary clock anchor → not proposed. Live Pine `orb_*` for MBT = equity 09:30 ORB (informational only) |
| London / NY session labels | `NOT_APPLICABLE` structurally; informational only |
| Overnight | `NOT_APPLICABLE` (no RTH to be "overnight" to) |
| PDH / PDL / PDC | `UNPROVEN` | meaningful only relative to a ruled bookkeeping boundary; PDC = last bar before that boundary |
| PWH / PWL | `UNPROVEN` | a "week" needs the same boundary; Mon–Fri has no meaning for a 24/7 product (weekend bars exist) |
| HOD / LOD | `UNPROVEN` | running extremes relative to the ruled boundary |
| Zones (LC_ZONE) | `APPLICABLE_AS_IS` | timeframe aggregation only |
| VWAP anchor | `UNPROVEN` (diagnostic) | no exchange daily anchor verified under 24/7 |
| Tick geometry | note | tick 5.0 on a ~76,000 price: one tick = 0.0066 % — the one-tick parity tolerance is far tighter relative to range than on the index micros; feed-vs-provider revision noise will dominate bracket parity. Report, do not widen |

## 4. X0 — source, contract chain and roll evidence (read-only probes)

Tool: `scripts/structural_level_tranche2_source_probe.py` (provider inventory endpoint
`/futures/v1/contracts?product_code=<ROOT>`, de-duplicated by ticker; 15m bars per dated
contract; per-UTC-day volume; volume-front chain and crossovers; retention edge; continuity
accounting against the feed-health calendar). Live-side identity for the six box bar files
(2026-09-16 12:15Z → 09-17 ~01:45Z, `source_ticker` = `<ROOT>1!`) is established with the same
per-bar OHLC identification used for M2K (`scripts/structural_level_x0_roll_proof.py`
`live_identity`, tool `slx0-roll-proof-v1.5.1` from #623: nearest candidate within 4 ticks, runner-up
≥ 20 ticks away; the two nearest dated contracts by delivery month are the candidates).
Artifacts: `docs/structural-level-tranche2-2026-09-17-probe-{MGC,MCL,MBT}.json` and
`…-live-identity-{MGC,MCL,MBT}.json`. Probe tool versions: MCL and MBT ran on `v1.1`
(spread tickers such as `MBTF5-MBTG5` excluded from the inventory — the first MBT run on `v1`
had let 32 spreads consume the 60-contract cap; its chain and crossovers were identical), MGC on
`v1.2` (adds retry on transient provider 5xx after a 503 aborted the first MGC listing walk; no
other change). Every run is read-only (provider GETs at 13 s pacing), one at a time. Bar
counts for the live contracts (`MGCV6`/`MGCZ6`, `MCLV6`/`MCLX6`/`MCLZ6`, `MBTU6`/`MBTV6`/`MBTX6`/`MBTZ6`)
end at the fetch time, not at expiry.

### 4.1 MGC

**Inventory (/futures/v1/contracts?product_code=MGC, fetched 2026-09-17T06:18:52Z, tool `slt2-source-probe-v1.2`):** 243,864 rows → 60 unique dated outrights (spreads dropped), venues ['XCEC'], listed life 2015-03-30 → 2028-12-27; month codes listed: G=10, J=10, M=10, Q=10, V=10, Z=10. Contracts intersecting the probe window 2024-09-01 → 2026-09-17 (delivery ≤ window end + 3 months): 14.

| dated contract | listed first → last trade | bars served (15m) | first bar → last bar (UTC) | volume | days with bars |
|---|---|---|---|---|---|
| `MGCV4` | 2022-11-29 → 2024-10-29 | 860 | 2024-09-17T00:00 → 2024-10-28T01:30 | 30,239 | 28 |
| `MGCZ4` | 2022-12-29 → 2024-12-27 | 4,939 | 2024-09-17T00:00 → 2024-12-26T16:00 | 5,908,186 | 82 |
| `MGCG5` | 2023-03-30 → 2025-02-26 | 8,763 | 2024-09-17T00:15 → 2025-02-25T21:30 | 4,813,050 | 135 |
| `MGCJ5` | 2023-05-30 → 2025-04-28 | 10,233 | 2024-09-17T01:00 → 2025-04-28T12:30 | 5,429,196 | 188 |
| `MGCM5` | 2023-06-29 → 2025-06-26 | 11,484 | 2024-09-17T01:45 → 2025-06-25T20:30 | 12,865,028 | 238 |
| `MGCQ5` | 2023-09-28 → 2025-08-27 | 12,136 | 2024-09-17T15:30 → 2025-08-26T14:15 | 9,756,431 | 279 |
| `MGCV5` | 2023-11-29 → 2025-10-29 | 12,739 | 2024-09-17T04:30 → 2025-10-28T15:45 | 804,645 | 323 |
| `MGCZ5` | 2023-12-28 → 2025-12-29 | 15,573 | 2024-09-17T15:30 → 2025-12-28T23:30 | 34,056,525 | 378 |
| `MGCG6` | 2024-03-27 → 2026-02-25 | 14,899 | 2024-09-17T02:15 → 2026-02-24T16:00 | 19,067,568 | 379 |
| `MGCJ6` | 2024-05-30 → 2026-04-28 | 15,482 | 2024-09-26T14:00 → 2026-04-27T20:30 | 25,114,476 | 371 |
| `MGCM6` | 2024-06-27 → 2026-06-26 | 15,722 | 2024-09-17T10:30 → 2026-06-26T17:15 | 15,245,974 | 361 |
| `MGCQ6` | 2024-09-27 → 2026-08-27 | 16,264 | 2025-03-27T15:30 → 2026-08-27T13:15 | 13,644,962 | 360 |
| `MGCV6` | 2024-11-27 → 2026-10-28 | 15,485 | 2025-04-30T11:00 → 2026-09-16T22:15 | 1,614,989 | 333 |
| `MGCZ6` | 2024-12-30 → 2026-12-29 | 15,703 | 2025-02-06T08:15 → 2026-09-16T22:15 | 11,102,967 | 360 |

**Retention / coverage:** earliest bar served 2024-09-17; 624 UTC days with any bar in the window.

**Volume-front chain (provider evidence only — not a roll rule, not feed provenance):** `MGCZ4` 2024-09-17…2024-11-26 (61 d) → `MGCG5` 2024-11-27…2025-01-29 (55 d) → `MGCJ5` 2025-01-30…2025-03-27 (49 d) → `MGCM5` 2025-03-28…2025-05-27 (51 d) → `MGCQ5` 2025-05-28…2025-07-28 (53 d) → `MGCZ5` 2025-07-29…2025-11-24 (102 d) → `MGCG6` 2025-11-25…2026-01-27 (55 d) → `MGCJ6` 2026-01-28…2026-03-26 (50 d) → `MGCM6` 2026-03-27…2026-05-26 (51 d) → `MGCQ6` 2026-05-27…2026-07-28 (54 d) → `MGCZ6` 2026-07-29…2026-09-16 (43 d). Causally ordered by expiry: True; flip-flops: 0.

| crossover | UTC day | from-vol prev day → on day | to-vol prev day → on day | both trading on day | from-bars after | to-bars before |
|---|---|---|---|---|---|---|
| `MGCZ4`→`MGCG5` | 2024-11-27 | 80,210 → 4,418 | 72,235 → 113,689 | True | 251 | 4544 |
| `MGCG5`→`MGCJ5` | 2025-01-30 | 42,962 → 3,658 | 41,896 → 115,890 | True | 226 | 6174 |
| `MGCJ5`→`MGCM5` | 2025-03-28 | 74,940 → 6,702 | 67,137 → 156,482 | True | 301 | 7245 |
| `MGCM5`→`MGCQ5` | 2025-05-28 | 234,660 → 113,704 | 53,354 → 168,317 | True | 385 | 7807 |
| `MGCQ5`→`MGCZ5` | 2025-07-29 | 161,589 → 45,733 | 9,534 → 112,033 | True | 307 | 7284 |
| `MGCZ5`→`MGCG6` | 2025-11-25 | 327,853 → 78,573 | 54,868 → 401,877 | True | 483 | 10585 |
| `MGCG6`→`MGCJ6` | 2026-01-28 | 566,136 → 121,186 | 157,750 → 948,767 | True | 354 | 11358 |
| `MGCJ6`→`MGCM6` | 2026-03-27 | 509,296 → 52,706 | 102,698 → 369,283 | True | 273 | 11512 |
| `MGCM6`→`MGCQ6` | 2026-05-27 | 248,384 → 73,129 | 77,231 → 283,893 | True | 356 | 11821 |
| `MGCQ6`→`MGCZ6` | 2026-07-29 | 190,182 → 84,327 | 32,174 → 251,863 | True | 335 | 12407 |

**Continuity of the volume-front contract vs the repo feed-health calendar (`context/futures_session.product_session_active`), 15m slots per month:** 2024-09 0/920 missing (0.0%); 2024-10 0/2116 missing (0.0%); 2024-11 23/1924 missing (1.2%); 2024-12 109/2028 missing (5.37%); 2025-01 110/2112 missing (5.21%); 2025-02 15/1840 missing (0.82%); 2025-03 0/1940 missing (0.0%); 2025-04 8/1940 missing (0.41%); 2025-05 10/2016 missing (0.5%); 2025-06 10/1940 missing (0.52%); 2025-07 16/2116 missing (0.76%); 2025-08 0/1932 missing (0.0%); 2025-09 10/2024 missing (0.49%); 2025-10 0/2108 missing (0.0%); 2025-11 62/1844 missing (3.36%); 2025-12 112/2116 missing (5.29%); 2026-01 98/2020 missing (4.85%); 2026-02 16/1840 missing (0.87%); 2026-03 1/2032 missing (0.05%); 2026-04 8/1940 missing (0.41%); 2026-05 10/1932 missing (0.52%); 2026-06 16/2024 missing (0.79%); 2026-07 16/2108 missing (0.76%); 2026-08 0/1940 missing (0.0%); 2026-09 16/1104 missing (1.45%).

**Live-side identity (box `bars_MGC` snapshot 2026-09-16/17, `source_ticker` ['MGC1!']; X0 `live_identity`, tol 4 ticks, separation ≥ 20 ticks, tick 0.1):** 55 live bars 2026-09-16T12:15 → 2026-09-17T02:45; identified per contract {'MGCZ6': 37, 'NOT_SERVED_BY_PROVIDER': 18}; within one tick {'MGCZ6': 35}; switch observed in span: False; candidates ['MGCV6', 'MGCZ6'].

**Reading (MGC):**

- *Dated-contract source:* `SOURCE_PROVEN` — the provider lists the even-month outrights only (G, J, M, Q, V, Z; venue `XCEC`) and serves 15m bars for every one of the 14 contracts in the window under its own dated ticker, from the retention edge 2024-09-17 (C17) to 2026-09-16 with a volume-front contract on 624/624 UTC days.
- *Historical coverage:* ~24 months of 15m history on dated tickers. Intraday fill on days with data ≥ 94.6 % every month; the Dec/Jan/Nov dips (3.4–5.4 %) coincide with the holiday early closes the feed calendar does not model (same signature as MCL).
- *Volume-front chain:* `Z4 → G5 → J5 → M5 → Q5 → Z5 → G6 → J6 → M6 → Q6 → Z6` — 10 crossovers, all causally ordered, 0 flip-flops; **the October contracts (`MGCV5`, `MGCV6`) are listed and traded but never became the volume front** (0.8 M / 1.6 M lots vs 34 M for `MGCZ5`), so the active chain is the five-contract cycle G/J/M/Q/Z, not "every even month". Every crossover lands **28–34 calendar days before the expiring contract's last-trade date**, on the last or second-to-last business day of the month before the delivery month (2024-11-27, 2025-01-30, 03-28, 05-28, 07-29, 11-25, 2026-01-28, 03-27, 05-27, 07-29) — the shape of a roll ahead of first notice; the exchange first-notice calendar was **not** verified here and is not cited as the rule. Candidate rule (**"roll to the next active month on the second-to-last business day of the month preceding delivery; skip V"**) recorded as provider evidence, not adopted; `sources/polygon_client.py` has no such schedule (a corpus build fails closed today). The next crossover, `MGCZ6 → MGCG7`, is expected in the last week of November 2026.
- *Continuous-roll proof:* **`ROLL_PROVENANCE_UNKNOWN`** — no MGC live feed before 2026-09-16, so none of the 10 historical seams can be reconciled against the box's `MGC1!` stream; the first observable roll is late November 2026, ~10 weeks away (MCL's is within days, MBT's on 09-25).
- *Feed calendar:* `product_session_active("MGC")` (same Sun 18:00 → Fri 17:00 ET map as MCL) is supported by the provider data — 0 % missing in 6 of 25 months, ≤ 1.5 % in 14 more, holiday months up to 5.4 %. No defect proven; nothing changed.

### 4.2 MCL

**Inventory (/futures/v1/contracts?product_code=MCL, fetched 2026-09-17T04:43:29Z, tool `slt2-source-probe-v1.1`):** 223,211 rows → 101 unique dated outrights (spreads dropped), venues ['XNYM'], listed life 2021-07-09 → 2029-11-16; month codes listed: F=8, G=8, H=8, J=8, K=8, M=8, N=8, Q=9, U=9, V=9, X=9, Z=9. Contracts intersecting the probe window 2024-09-01 → 2026-09-17 (delivery ≤ window end + 3 months): 27.

| dated contract | listed first → last trade | bars served (15m) | first bar → last bar (UTC) | volume | days with bars |
|---|---|---|---|---|---|
| `MCLV4` | 2023-09-19 → 2024-09-19 | 258 | 2024-09-17T00:00 → 2024-09-19T18:15 | 65,150 | 3 |
| `MCLX4` | 2023-10-19 → 2024-10-21 | 2,282 | 2024-09-17T00:00 → 2024-10-21T18:15 | 2,094,828 | 30 |
| `MCLZ4` | 2023-07-28 → 2024-11-19 | 4,203 | 2024-09-17T00:00 → 2024-11-19T19:15 | 1,741,580 | 55 |
| `MCLF5` | 2023-10-20 → 2024-12-18 | 5,701 | 2024-09-17T00:45 → 2024-12-18T19:15 | 1,356,543 | 80 |
| `MCLG5` | 2023-10-20 → 2025-01-17 | 6,281 | 2024-09-17T06:00 → 2025-01-17T19:15 | 1,109,708 | 105 |
| `MCLH5` | 2023-10-20 → 2025-02-19 | 7,081 | 2024-09-17T07:00 → 2025-02-19T19:15 | 1,568,671 | 132 |
| `MCLJ5` | 2023-10-20 → 2025-03-19 | 6,687 | 2024-09-17T08:45 → 2025-03-19T18:15 | 1,110,446 | 148 |
| `MCLK5` | 2023-10-20 → 2025-04-21 | 6,752 | 2024-09-17T03:45 → 2025-04-21T18:15 | 1,527,854 | 167 |
| `MCLM5` | 2023-07-28 → 2025-05-19 | 7,393 | 2024-09-17T08:45 → 2025-05-19T18:15 | 1,316,373 | 195 |
| `MCLN5` | 2023-10-20 → 2025-06-18 | 6,438 | 2024-09-18T17:30 → 2025-06-18T18:15 | 1,690,005 | 194 |
| `MCLQ5` | 2023-10-20 → 2025-07-21 | 7,107 | 2024-09-17T10:00 → 2025-07-21T18:15 | 2,301,813 | 214 |
| `MCLU5` | 2023-10-20 → 2025-08-19 | 6,993 | 2024-09-17T13:30 → 2025-08-19T18:15 | 1,313,287 | 221 |
| `MCLV5` | 2023-10-20 → 2025-09-19 | 6,866 | 2024-09-25T16:15 → 2025-09-19T18:15 | 1,037,647 | 228 |
| `MCLX5` | 2023-10-20 → 2025-10-20 | 6,514 | 2024-09-30T15:15 → 2025-10-20T18:15 | 1,098,083 | 239 |
| `MCLZ5` | 2023-10-20 → 2025-11-19 | 8,738 | 2024-09-19T13:45 → 2025-11-19T19:15 | 1,209,362 | 334 |
| `MCLF6` | 2023-10-20 → 2025-12-18 | 6,612 | 2024-10-09T10:15 → 2025-12-18T19:15 | 890,805 | 254 |
| `MCLG6` | 2023-10-20 → 2026-01-16 | 6,280 | 2024-10-22T13:45 → 2026-01-16T19:15 | 891,694 | 220 |
| `MCLH6` | 2023-10-20 → 2026-02-19 | 6,793 | 2024-10-27T23:15 → 2026-02-19T19:15 | 1,632,822 | 233 |
| `MCLJ6` | 2023-10-20 → 2026-03-19 | 6,725 | 2024-10-11T14:15 → 2026-03-19T18:15 | 8,980,995 | 226 |
| `MCLK6` | 2023-10-20 → 2026-04-20 | 6,990 | 2024-10-01T15:15 → 2026-04-20T18:15 | 10,502,824 | 227 |
| `MCLM6` | 2023-10-20 → 2026-05-18 | 7,834 | 2024-10-03T16:30 → 2026-05-18T18:15 | 6,228,207 | 290 |
| `MCLN6` | 2023-10-20 → 2026-06-18 | 8,067 | 2025-01-29T09:15 → 2026-06-18T18:15 | 4,761,566 | 252 |
| `MCLQ6` | 2023-10-20 → 2026-07-20 | 8,413 | 2025-02-23T23:00 → 2026-07-20T18:15 | 2,855,228 | 239 |
| `MCLU6` | 2023-10-20 → 2026-08-19 | 8,917 | 2025-02-26T00:45 → 2026-08-19T18:15 | 4,023,352 | 277 |
| `MCLV6` | 2023-10-20 → 2026-09-21 | 7,995 | 2025-02-26T00:45 → 2026-09-16T20:30 | 3,736,699 | 233 |
| `MCLX6` | 2023-10-20 → 2026-10-19 | 5,993 | 2025-04-23T18:00 → 2026-09-16T20:30 | 335,651 | 205 |
| `MCLZ6` | 2023-10-20 → 2026-11-19 | 9,187 | 2024-10-27T23:00 → 2026-09-16T20:30 | 92,717 | 342 |

**Retention / coverage:** earliest bar served 2024-09-17; 624 UTC days with any bar in the window.

**Volume-front chain (provider evidence only — not a roll rule, not feed provenance):** `MCLV4` 2024-09-17…2024-09-17 (1 d) → `MCLX4` 2024-09-18…2024-10-17 (26 d) → `MCLZ4` 2024-10-18…2024-11-15 (25 d) → `MCLF5` 2024-11-17…2024-12-16 (26 d) → `MCLG5` 2024-12-17…2025-01-15 (26 d) → `MCLH5` 2025-01-16…2025-02-16 (27 d) → `MCLJ5` 2025-02-17…2025-03-17 (25 d) → `MCLK5` 2025-03-18…2025-04-16 (26 d) → `MCLM5` 2025-04-17…2025-05-15 (24 d) → `MCLN5` 2025-05-16…2025-06-16 (27 d) → `MCLQ5` 2025-06-17…2025-07-17 (27 d) → `MCLU5` 2025-07-18…2025-08-15 (25 d) → `MCLV5` 2025-08-17…2025-09-17 (28 d) → `MCLX5` 2025-09-18…2025-10-16 (25 d) → `MCLZ5` 2025-10-17…2025-11-17 (27 d) → `MCLF6` 2025-11-18…2025-12-16 (25 d) → `MCLG6` 2025-12-17…2026-01-14 (25 d) → `MCLH6` 2026-01-15…2026-02-17 (29 d) → `MCLJ6` 2026-02-18…2026-03-17 (24 d) → `MCLK6` 2026-03-18…2026-04-16 (25 d) → `MCLM6` 2026-04-17…2026-05-14 (24 d) → `MCLN6` 2026-05-15…2026-06-16 (28 d) → `MCLQ6` 2026-06-17…2026-07-16 (26 d) → `MCLU6` 2026-07-17…2026-08-17 (27 d) → `MCLV6` 2026-08-18…2026-09-16 (26 d). Causally ordered by expiry: True; flip-flops: 0.

| crossover | UTC day | from-vol prev day → on day | to-vol prev day → on day | both trading on day | from-bars after | to-bars before |
|---|---|---|---|---|---|---|
| `MCLV4`→`MCLX4` | 2024-09-18 | 47,033 → 15,542 | 25,561 → 62,889 | True | 166 | 92 |
| `MCLX4`→`MCLZ4` | 2024-10-18 | 56,222 → 20,102 | 22,629 → 62,322 | True | 166 | 2109 |
| `MCLZ4`→`MCLF5` | 2024-11-17 | 49,199 → 1,319 | 22,033 → 1,612 | True | 174 | 3618 |
| `MCLF5`→`MCLG5` | 2024-12-17 | 31,776 → 12,900 | 12,369 → 37,843 | True | 170 | 4288 |
| `MCLG5`→`MCLH5` | 2025-01-16 | 66,315 → 20,815 | 24,330 → 54,825 | True | 166 | 4832 |
| `MCLH5`→`MCLJ5` | 2025-02-17 | 2,265 → 16,785 | 1,527 → 18,922 | True | 252 | 4595 |
| `MCLJ5`→`MCLK5` | 2025-03-18 | 31,717 → 15,343 | 17,431 → 37,680 | True | 166 | 4562 |
| `MCLK5`→`MCLM5` | 2025-04-17 | 43,080 → 20,302 | 14,714 → 29,933 | True | 166 | 5387 |
| `MCLM5`→`MCLN5` | 2025-05-16 | 50,582 → 15,656 | 18,888 → 33,592 | True | 166 | 4258 |
| `MCLN5`→`MCLQ5` | 2025-06-17 | 137,704 → 47,308 | 62,399 → 133,621 | True | 166 | 4851 |
| `MCLQ5`→`MCLU5` | 2025-07-18 | 38,230 → 20,718 | 16,435 → 49,571 | True | 166 | 4895 |
| `MCLU5`→`MCLV5` | 2025-08-17 | 33,918 → 1,590 | 11,477 → 2,179 | True | 174 | 4586 |
| `MCLV5`→`MCLX5` | 2025-09-18 | 28,457 → 15,102 | 11,451 → 37,981 | True | 166 | 4416 |
| `MCLX5`→`MCLZ5` | 2025-10-17 | 38,747 → 16,649 | 18,136 → 36,855 | True | 166 | 6549 |
| `MCLZ5`→`MCLF6` | 2025-11-18 | 23,935 → 8,834 | 13,349 → 34,907 | True | 169 | 4574 |
| `MCLF6`→`MCLG6` | 2025-12-17 | 29,331 → 14,474 | 17,166 → 39,729 | True | 170 | 4375 |
| `MCLG6`→`MCLH6` | 2026-01-15 | 61,007 → 13,627 | 38,934 → 52,307 | True | 170 | 4435 |
| `MCLH6`→`MCLJ6` | 2026-02-18 | 45,691 → 16,705 | 24,111 → 50,889 | True | 170 | 4717 |
| `MCLJ6`→`MCLK6` | 2026-03-18 | 280,531 → 92,417 | 144,995 → 450,933 | True | 166 | 4892 |
| `MCLK6`→`MCLM6` | 2026-04-17 | 161,574 → 97,594 | 57,287 → 313,407 | True | 166 | 5828 |
| `MCLM6`→`MCLN6` | 2026-05-15 | 142,491 → 48,036 | 31,638 → 97,197 | True | 166 | 5795 |
| `MCLN6`→`MCLQ6` | 2026-06-17 | 103,229 → 30,801 | 40,744 → 115,360 | True | 166 | 6255 |
| `MCLQ6`→`MCLU6` | 2026-07-17 | 94,738 → 41,186 | 28,890 → 102,450 | True | 166 | 6727 |
| `MCLU6`→`MCLV6` | 2026-08-18 | 101,478 → 27,191 | 47,209 → 95,677 | True | 166 | 5990 |

**Continuity of the volume-front contract vs the repo feed-health calendar (`context/futures_session.product_session_active`), 15m slots per month:** 2024-09 0/920 missing (0.0%); 2024-10 0/2116 missing (0.0%); 2024-11 27/1924 missing (1.4%); 2024-12 109/2028 missing (5.37%); 2025-01 110/2112 missing (5.21%); 2025-02 15/1840 missing (0.82%); 2025-03 0/1940 missing (0.0%); 2025-04 8/1940 missing (0.41%); 2025-05 10/2016 missing (0.5%); 2025-06 10/1940 missing (0.52%); 2025-07 16/2116 missing (0.76%); 2025-08 0/1932 missing (0.0%); 2025-09 10/2024 missing (0.49%); 2025-10 0/2108 missing (0.0%); 2025-11 61/1844 missing (3.31%); 2025-12 112/2116 missing (5.29%); 2026-01 98/2020 missing (4.85%); 2026-02 10/1840 missing (0.54%); 2026-03 2/2032 missing (0.1%); 2026-04 8/1940 missing (0.41%); 2026-05 10/1932 missing (0.52%); 2026-06 16/2024 missing (0.79%); 2026-07 16/2108 missing (0.76%); 2026-08 0/1940 missing (0.0%); 2026-09 19/1104 missing (1.72%).

**Live-side identity (box `bars_MCL` snapshot 2026-09-16/17, `source_ticker` ['MCL1!']; X0 `live_identity`, tol 4 ticks, separation ≥ 20 ticks, tick 0.01):** 55 live bars 2026-09-16T12:15 → 2026-09-17T02:45; identified per contract {'MCLV6': 37, 'NOT_SERVED_BY_PROVIDER': 18}; within one tick {'MCLV6': 37}; switch observed in span: False; candidates ['MCLV6', 'MCLX6'].

**Reading (MCL):**

- *Dated-contract source:* `SOURCE_PROVEN` — every monthly outright from `MCLV4` to `MCLZ6` is served under its own dated ticker on venue `XNYM`; the window is fully covered from the provider's retention edge (2024-09-17, the same ~2-year edge seen for MNQ/MES/M2K — C17) to 2026-09-16 with no month lacking a volume-front contract (624/624 UTC days with bars in the window).
- *Historical coverage:* ~24 months of 15m history, **not** the 2024-10-01 → 2026-06-26 P-REPLAY window plus warm-up as a proven-roll population — see the roll item. Intraday fill on days with data ≥ 94.6 % in every month (Dec/Jan dips coincide with the holiday early closes the feed calendar does not model).
- *Volume-front chain:* 25 contracts, 24 crossovers, all causally ordered by expiry, 0 flip-flops. The provider volume crossover falls on the **last trading day before the expiring contract's last-trade date** in 21 of 24 cases (including 2025-04-17, where Good Friday made Thursday that day); the three exceptions (2024-11-17, 2025-02-17 Presidents' Day, 2025-08-17) are Sunday-evening / holiday sessions with fewer than 2.3 k lots on both sides, where the UTC-day comparison resolves one session early. `MCLV6` has last trade 2026-09-21 (Mon), so the next crossover is expected on **2026-09-18**. This is a candidate rule (**"roll = last trading day before the last-trade date"**), recorded as provider evidence — it is **not** adopted, and it is not the `roll_days=N` quarterly scheduler in `sources/polygon_client.py`, which has no monthly schedule for MCL at all (a corpus build would fail closed today).
- *Continuous-roll proof:* **`ROLL_PROVENANCE_UNKNOWN`.** No MCL live feed existed before 2026-09-16, so none of the 24 historical seams can be reconciled against the box's `MCL1!` stream (#625: a scheduler or a volume rule is a candidate chain, not proof). The live-side identity below shows which contract the feed carries *today*; the first roll the feed will exhibit is `MCLV6 → MCLX6` around 2026-09-18 → 09-21 — observable on `bars_MCL_*.jsonl` within days, and that observation is what would upgrade the seam rule to `FEED_CONFIRMED` for one seam (one seam does not prove 24).
- *Feed calendar:* `product_session_active("MCL")` (Sun 18:00 → Fri 17:00 ET, 17:00–18:00 halt) is supported by the provider data — 0 % missing in 7 of 25 months, ≤ 1.7 % in 15 more; the residual sits in holiday months. No calendar defect is proven; nothing changed.

### 4.3 MBT

**Inventory (/futures/v1/contracts?product_code=MBT, fetched 2026-09-17T05:21:59Z, tool `slt2-source-probe-v1.1`):** 107,688 rows → 75 unique dated outrights (spreads dropped), venues ['XCME'], listed life 2021-04-30 → 2028-03-31; month codes listed: F=6, G=6, H=7, J=5, K=6, M=7, N=6, Q=6, U=7, V=6, X=6, Z=7. Contracts intersecting the probe window 2024-09-01 → 2026-09-17 (delivery ≤ window end + 3 months): 28.

| dated contract | listed first → last trade | bars served (15m) | first bar → last bar (UTC) | volume | days with bars |
|---|---|---|---|---|---|
| `MBTU4` | 2023-03-31 → 2024-09-27 | 789 | 2024-09-17T00:00 → 2024-09-27T14:45 | 310,445 | 10 |
| `MBTV4` | 2024-04-26 → 2024-10-25 | 2,492 | 2024-09-17T00:00 → 2024-10-25T14:45 | 925,970 | 34 |
| `MBTX4` | 2024-05-31 → 2024-11-29 | 3,534 | 2024-09-17T15:45 → 2024-11-29T15:45 | 2,311,623 | 64 |
| `MBTZ4` | 2022-12-30 → 2024-12-27 | 4,607 | 2024-09-17T00:30 → 2024-12-27T15:45 | 1,695,260 | 88 |
| `MBTF5` | 2024-07-26 → 2025-01-31 | 2,774 | 2024-09-18T12:30 → 2024-12-31T21:45 | 237,041 | 84 |
| `MBTG5` | 2024-08-30 → 2025-02-28 | 779 | 2024-10-01T13:30 → 2024-12-31T21:45 | 6,595 | 57 |
| `MBTH5` | 2023-09-29 → 2025-03-28 | 424 | 2024-09-30T23:30 → 2024-12-31T21:30 | 1,480 | 46 |
| `MBTJ5` | 2024-10-25 → 2025-04-25 | 20 | 2024-12-02T21:00 → 2024-12-30T18:00 | 25 | 12 |
| `MBTK5` | 2024-11-29 → 2025-05-30 | 0 | — → — | 0 | 0 |
| `MBTM5` | 2023-12-29 → 2025-06-27 | 0 | — → — | 0 | 0 |
| `MBTN5` | 2025-01-31 → 2025-07-25 | 0 | — → — | 0 | 0 |
| `MBTQ5` | 2025-02-28 → 2025-08-29 | 0 | — → — | 0 | 0 |
| `MBTU5` | 2024-03-28 → 2025-09-26 | 0 | — → — | 0 | 0 |
| `MBTV5` | 2025-04-28 → 2025-10-31 | 0 | — → — | 0 | 0 |
| `MBTX5` | 2025-06-02 → 2025-11-28 | 0 | — → — | 0 | 0 |
| `MBTZ5` | 2023-12-29 → 2025-12-26 | 0 | — → — | 0 | 0 |
| `MBTF6` | 2025-07-28 → 2026-01-30 | 0 | — → — | 0 | 0 |
| `MBTG6` | 2025-09-02 → 2026-02-27 | 0 | — → — | 0 | 0 |
| `MBTH6` | 2024-09-27 → 2026-03-27 | 0 | — → — | 0 | 0 |
| `MBTJ6` | 2025-11-03 → 2026-04-24 | 1,619 | 2026-03-31T18:30 → 2026-04-24T14:45 | 961,079 | 22 |
| `MBTK6` | 2025-12-01 → 2026-05-29 | 3,706 | 2026-03-31T18:45 → 2026-05-29T14:45 | 1,362,116 | 52 |
| `MBTM6` | 2024-12-27 → 2026-06-26 | 5,099 | 2026-03-31T20:15 → 2026-06-26T14:45 | 1,436,262 | 80 |
| `MBTN6` | 2026-02-02 → 2026-07-31 | 5,317 | 2026-04-12T23:45 → 2026-07-31T14:45 | 1,273,678 | 102 |
| `MBTQ6` | 2026-03-02 → 2026-08-28 | 4,765 | 2026-04-17T14:00 → 2026-08-28T14:45 | 1,220,305 | 101 |
| `MBTU6` | 2025-03-31 → 2026-09-25 | 3,905 | 2026-04-07T18:15 → 2026-09-16T21:15 | 995,994 | 112 |
| `MBTV6` | 2026-04-27 → 2026-10-30 | 1,310 | 2026-06-01T12:45 → 2026-09-16T20:45 | 11,540 | 69 |
| `MBTX6` | 2026-06-01 → 2026-11-27 | 125 | 2026-07-28T18:30 → 2026-09-16T20:30 | 451 | 24 |
| `MBTZ6` | 2024-12-27 → 2026-12-24 | 238 | 2026-03-31T20:00 → 2026-09-16T19:15 | 867 | 74 |

**Retention / coverage:** earliest bar served 2024-09-17; 253 UTC days with any bar in the window.

**Volume-front chain (provider evidence only — not a roll rule, not feed provenance):** `MBTU4` 2024-09-17…2024-09-26 (9 d) → `MBTV4` 2024-09-27…2024-10-24 (24 d) → `MBTX4` 2024-10-25…2024-11-28 (30 d) → `MBTZ4` 2024-11-29…2024-12-26 (24 d) → `MBTF5` 2024-12-27…2024-12-31 (4 d) → `MBTJ6` 2026-03-31…2026-04-23 (21 d) → `MBTK6` 2026-04-24…2026-05-28 (30 d) → `MBTM6` 2026-05-29…2026-06-25 (28 d) → `MBTN6` 2026-06-26…2026-07-30 (35 d) → `MBTQ6` 2026-07-31…2026-08-27 (28 d) → `MBTU6` 2026-08-28…2026-09-16 (20 d). Causally ordered by expiry: True; flip-flops: 0.

| crossover | UTC day | from-vol prev day → on day | to-vol prev day → on day | both trading on day | from-bars after | to-bars before |
|---|---|---|---|---|---|---|
| `MBTU4`→`MBTV4` | 2024-09-27 | 40,858 → 1,176 | 13,414 → 48,029 | True | 55 | 596 |
| `MBTV4`→`MBTX4` | 2024-10-25 | 30,025 → 2,262 | 8,979 → 56,357 | True | 56 | 1176 |
| `MBTX4`→`MBTZ4` | 2024-11-29 | 10,486 → 4,179 | 9,801 → 45,430 | True | 63 | 2817 |
| `MBTZ4`→`MBTF5` | 2024-12-27 | 62,469 → 2,836 | 14,378 → 58,648 | True | 64 | 2502 |
| `MBTF5`→`MBTJ6` | 2026-03-31 | 34,727 → — | — → 7,280 | False | 0 | 0 |
| `MBTJ6`→`MBTK6` | 2026-04-24 | 45,762 → 3,543 | 28,289 → 56,058 | True | 60 | 1346 |
| `MBTK6`→`MBTM6` | 2026-05-29 | 46,412 → 4,164 | 17,408 → 50,620 | True | 60 | 2481 |
| `MBTM6`→`MBTN6` | 2026-06-26 | 50,623 → 5,081 | 29,536 → 66,533 | True | 59 | 1993 |
| `MBTN6`→`MBTQ6` | 2026-07-31 | 31,965 → 3,720 | 13,604 → 56,417 | True | 60 | 2143 |
| `MBTQ6`→`MBTU6` | 2026-08-28 | 50,243 → 4,227 | 35,296 → 77,429 | True | 60 | 2067 |

**Continuity of the volume-front contract vs the repo feed-health calendar (`context/futures_session.product_session_active`), 15m slots per month:** 2024-09 224/1142 missing (19.61%); 2024-10 453/2569 missing (17.63%); 2024-11 465/2379 missing (19.55%); 2024-12 651/2570 missing (25.33%); 2026-03 77/95 missing (81.05%); 2026-04 473/2474 missing (19.12%); 2026-05 523/2563 missing (20.41%); 2026-06 17/2826 missing (0.6%); 2026-07 53/2921 missing (1.81%); 2026-08 110/2915 missing (3.77%); 2026-09 36/1508 missing (2.39%).

**Live-side identity (box `bars_MBT` snapshot 2026-09-16/17, `source_ticker` ['MBT1!']; X0 `live_identity`, tol 4 ticks, separation ≥ 20 ticks, tick 5.0):** 59 live bars 2026-09-16T12:15 → 2026-09-17T02:45; identified per contract {'MBTU6': 41, 'NOT_SERVED_BY_PROVIDER': 18}; within one tick {'MBTU6': 41}; switch observed in span: False; candidates ['MBTU6', 'MBTV6'].

**Reading (MBT):**

- *Dated-contract source:* `SOURCE_PARTIAL` — the provider lists every monthly MBT outright (venue `XCME`, life 2021-04-30 → 2028-03-31) and serves 15m bars under the dated tickers **only for 2024-09-17 → 2024-12-31 and 2026-03-31 → now**. Every contract whose life falls in 2025-01-01 → 2026-03-30 (`MBTK5` … `MBTH6`, eleven contracts) returns **0 bars**, and the contracts bracketing the hole stop/start exactly at its edges (`MBTF5`/`MBTG5`/`MBTH5` last bar 2024-12-31T21:45Z; `MBTJ6` first bar 2026-03-31). This is a provider coverage hole of **15 months**, not an exchange fact (MBT traded throughout). It also explains the earlier `MBTZ5` 0-bar result.
- *Historical coverage:* **`INSUFFICIENT_HISTORY`** for any corpus that needs a contiguous multi-month window: ~3.5 months in late 2024 (pre-24/7 regime) + ~5.5 months in 2026 (of which the 24/7 regime starts 2026-05-29). No window on either side of the hole supports 3 chronological folds, and the two sides are different products in session terms (§3.3).
- *Volume-front chain:* causally ordered, 0 flip-flops; the provider volume crossover falls **on the expiring contract's last trade date** (the last Friday of the month; last bars at 14:45Z/15:45Z = 16:00 London BRR settlement, consistent with the CME final-settlement rule in §2) in every crossover where both contracts trade (`MBTU4→V4` 2024-09-27, `V4→X4` 10-25, `X4→Z4` 11-29, `Z4→F5` 12-27, `J6→K6` 2026-04-24, `K6→M6` 05-29, `M6→N6` 06-26, `N6→Q6` 07-31, `Q6→U6` 08-28). The `F5→J6` "crossover" is the coverage hole, not a roll. Candidate rule (**"roll on the last-trade date, at the BRR settlement"**) recorded as evidence, not adopted.
- *Continuous-roll proof:* **`ROLL_PROVENANCE_UNKNOWN`** — no MBT live feed before 2026-09-16; the next roll `MBTU6 → MBTV6` is on **2026-09-25** and will be the first observable one on `bars_MBT_*.jsonl`.
- *Feed calendar:* `product_session_active("MBT")` (24/7 less the two maintenance windows) matches the provider data for 2026-06 → 09 (0.6 %, 1.8 %, 3.8 %, 2.4 % missing) and **does not** match 2024-09 → 12 (17.6–25.3 % missing on days with data, plus whole weekend days absent) — the expected signature of the pre-2026-05-29 Globex-week regime. 2026-04/05 (19–20 % missing) are still pre-launch. This is the dated-calendar finding of §3.3 confirmed from data; no change is made.

## 5. Required deliverable — one matrix per root

Legend for the two evidence rows: **dated-contract source** ∈ `SOURCE_PROVEN` (every contract in the window served under its dated ticker, no hole) / `SOURCE_PARTIAL` (served, with a coverage hole) / `SOURCE_MISSING`; **continuous-roll proof** ∈ `ROLL_PROVEN` / `ROLL_PROVENANCE_UNKNOWN` / `INSUFFICIENT_HISTORY` / `BLOCKED` (operator vocabulary, §B4). Everything above the evidence rows is the §3 classification; the final status is per the operator's set `READY_FOR_CORPUS_PREREG` / `PARTIAL` / `NOT_TESTABLE` / `BLOCKED`.

| Field | MGC | MCL | MBT |
|---|---|---|---|
| Feed calendar supported (`product_session_active`) | **YES** — Sun 18:00 → Fri 17:00 ET map matches provider fill (0–1.5 % missing outside holiday months) | **YES** — same map, same fill profile | **YES only from 2026-05-29** (24/7 map matches Jun–Sep 2026 fill 0.6–3.8 %); **NO for history** — undated calendar contradicts the 2024 data (17.6–25.3 % missing + closed weekend days) |
| Structural trading-day definition | `APPLICABLE_WITH_PRODUCT_DEFINITION` — 18:00 ET Globex reopen, frozen on COMEX authority (numerically = equity `_trading_day`, not inherited) | `APPLICABLE_WITH_PRODUCT_DEFINITION` — 18:00 ET Globex reopen (NYMEX) | `UNPROVEN` — candidates (a) 16:00–16:02 CT maintenance, (b) 00:00 UTC, (c) ET calendar date (current bookkeeping only); ruling needed; **never an outcome-expiry rule** |
| Primary / RTH open | `UNPROVEN` — 08:20 ET is a legacy pit / vendor convention, not exchange-defined; candidates A 08:20 ET / B none / C settlement-anchored 13:30 ET close; **ruling needed** | `UNPROVEN` — 09:00 ET legacy pit open only; candidates A 09:00 ET / B none / C settlement-anchored 14:30 ET; **separate ruling, not borrowed from MGC** | `NOT_APPLICABLE` — no session exists |
| ORB definition / status | `APPLICABLE_WITH_PRODUCT_DEFINITION` — first 15m bar of the ruled open, valid to 13:30 ET; **blocked on the RTH ruling**; live Pine `orb_*` = equity 09:30 (informational only) | `APPLICABLE_WITH_PRODUCT_DEFINITION` — first 15m bar of the ruled open, valid to 14:30 ET; **blocked on the ruling**; live Pine `orb_*` = equity 09:30 | `NOT_APPLICABLE` (any clock anchor would be arbitrary); live Pine `orb_*` informational only |
| London / overnight applicability | London ORB `APPLICABLE_WITH_PRODUCT_DEFINITION` (03:00 ET LBMA/LME morning — same clock, product justification); overnight `APPLICABLE_WITH_PRODUCT_DEFINITION` = [18:00 ET, ruled open) | London `UNPROVEN` (no product anchor at 03:00 ET); overnight `APPLICABLE_WITH_PRODUCT_DEFINITION` once the open is ruled | `NOT_APPLICABLE` structurally; session labels informational only |
| PDH / PDL / PDC | `APPLICABLE_WITH_PRODUCT_DEFINITION` — prior 18:00-ET day H/L; **PDC ruling: settlement-window close 13:30 ET vs last trade 16:59 ET** | `APPLICABLE_WITH_PRODUCT_DEFINITION` — prior day H/L; **PDC ruling: 14:30 ET settlement vs last trade**; EIA Wednesdays and expiry weeks flagged | `UNPROVEN` — only relative to a ruled boundary |
| PWH / PWL | `APPLICABLE_AS_IS` (Sun 18:00 → Fri 17:00 ET week) | `APPLICABLE_AS_IS` | `UNPROVEN` — no product week under 24/7 |
| HOD / LOD | `APPLICABLE_AS_IS` | `APPLICABLE_AS_IS` | `UNPROVEN` — relative to a ruled boundary |
| VWAP anchor status | `UNPROVEN`, diagnostic only (never admitted; C14 open) | `UNPROVEN`, diagnostic only | `UNPROVEN`, diagnostic only |
| Zone applicability (LC_ZONE 1H/4H) | `APPLICABLE_AS_IS` | `APPLICABLE_AS_IS` | `APPLICABLE_AS_IS` |
| Dated-contract source status | **`SOURCE_PROVEN`** — 14/14 window contracts served (`XCEC`); live feed identified as `MGCZ6` 37/37 served bars (35 within one tick, 2 within 4 ticks — feed-revision class), 0 other, 0 ambiguous | **`SOURCE_PROVEN`** — 27/27 served (`XNYM`); live feed = `MCLV6` 37/37 (37 within one tick) | **`SOURCE_PARTIAL`** — listed 75 outrights (`XCME`) but bars served only 2024-09-17 → 2024-12-31 and 2026-03-31 → now; 11 contracts return 0 bars (**15-month hole**); live feed = `MBTU6` 41/41 (41 within one tick) |
| Continuous-roll proof | **`ROLL_PROVENANCE_UNKNOWN`** — 10 scheduler-free volume crossovers recorded (G/J/M/Q/Z cycle, V skipped, ~1 month before last trade); no live feed at any historical seam; first observable roll `Z6 → G7` late Nov 2026 | **`ROLL_PROVENANCE_UNKNOWN`** — 24 crossovers, 21/24 on the last trading day before the last-trade date; no live feed at any historical seam; first observable roll `V6 → X6` on/about **2026-09-18 → 09-21** | **`ROLL_PROVENANCE_UNKNOWN`** for the two covered stretches (crossover on the last-trade Friday at the 16:00-London BRR settlement, 9/9 covered seams); **`INSUFFICIENT_HISTORY`** across the hole; first observable roll `U6 → V6` on **2026-09-25** |
| Historical coverage | ~24 months (2024-09-17 → now) of dated 15m bars; the 2024-10-01 → 2026-06-26 P-REPLAY window is coverable **only** as a stitched corpus whose seams are unproven, or as seam-free per-contract windows (~2 months each) | ~24 months, complete; P-REPLAY window coverable only stitched-unproven (monthly seams → ~12/yr, heavy §9.5 contamination of PW/zone features) or as ~1-month seam-free windows | ~3.5 months (late 2024, pre-24/7 regime) + ~5.5 months (2026, 24/7 from 05-29); **no contiguous window supports 3 folds** |
| Blocker(s) | (1) primary-open ruling; (2) PDC ruling; (3) roll rule unproven — confirm at the Nov roll or admit only seam-free per-contract windows; (4) no monthly/bimonthly schedule in the builder (fails closed) | (1) primary-open ruling; (2) PDC ruling; (3) roll rule unproven — first confirmation possible within days; (4) no monthly schedule in the builder (fails closed); (5) monthly-seam contamination share to be quantified before any corpus prereg | (1) provider coverage hole 2025-01 → 2026-03 (**BLOCKED at the source**); (2) bookkeeping boundary unruled; (3) dated pre/post-2026-05-29 calendar required for any history; (4) `MBT_OUTCOME_HORIZON_UNPROVEN` — terminal outcomes HOLD; (5) tick geometry makes one-tick bracket parity revision-dominated |
| **Final status** | **`PARTIAL`** — source proven, definitions need two rulings, roll unproven | **`PARTIAL`** — source proven, definitions need two rulings, roll unproven (soonest to resolve) | **`BLOCKED`** — source hole + no structural boundary; not testable as a corpus in any current window |

## 6. What is authorised next (and what is not)

- **Authorised by this document:** nothing beyond further read-only probes.
- **Needs a ruling before any corpus:** MGC/MCL primary-session open (§3, candidates A/B/C),
  PDC definition (settlement close vs last trade), MBT bookkeeping boundary and product
  `_slot_open`, and — for every root — a roll rule proposed from §4 evidence and to be
  **confirmed against the live feed at its first observed roll** (MCL `V6 → X6` on/about
  2026-09-18 → 09-21, MBT `U6 → V6` on 2026-09-25, MGC `Z6 → G7` in the last week of November
  2026 — all observable on the box's `bars_<ROOT>_*.jsonl`; one confirmed seam upgrades that
  seam only, not the historical chain).
- **MBT is `BLOCKED` at the source** (§4.3): no corpus prereg can be written until the provider
  serves 2025-01 → 2026-03 or another dated-contract source is proven; the boundary ruling and
  the dated calendar are additionally required. `MBT_OUTCOME_HORIZON_UNPROVEN` stands.
- **Not authorised:** confirmatory corpora, candidate regeneration, outcomes, R5, any
  strategy-policy inheritance (stops, ORB offsets, commissions, contracts, session permissions,
  routes — §B5), any runtime/collector/calendar change.
- Forward observation of all four roots continues under its own epoch regardless (#622 §10).

---

**Verdict (2026-09-17, base `8b20b9b` = main with #623/#629/#630/#633; probes ran on the pre-rebase tree at `bbe4c51`, which differs from `8b20b9b` in docs only): MGC `PARTIAL`, MCL `PARTIAL`,
MBT `BLOCKED`.** Dated-contract sources are proven for MGC (14/14) and MCL (27/27) and the box
feeds identify as `MGCZ6` / `MCLV6` / `MBTU6` on every served bar; every historical roll seam of
all three is `ROLL_PROVENANCE_UNKNOWN` (no live feed existed; provider volume chains are
candidate evidence only, not adopted); MBT additionally has a 15-month provider coverage hole
and no structural day boundary. **Nothing is ready for a corpus prereg**: MGC and MCL each need
the primary-open and PDC rulings plus either a feed-confirmed roll rule or a seam-free
per-contract design before a corpus prereg can be authorised; MBT is blocked at the source.
No outcome read, no corpus built, no runtime or calendar change. **Stop here.**
