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
per-bar OHLC identification used for M2K (`scripts/structural_level_x0_roll_proof.py` logic).
Artifacts: `docs/structural-level-tranche2-2026-09-17-probe-{MGC,MCL,MBT}.json`.

{{PROBE_SECTION}}

## 5. Required deliverable — one matrix per root

{{MATRIX}}

## 6. What is authorised next (and what is not)

- **Authorised by this document:** nothing beyond further read-only probes.
- **Needs a ruling before any corpus:** MGC/MCL primary-session open (§3, candidates A/B/C),
  PDC definition (settlement close vs last trade), MBT bookkeeping boundary and product
  `_slot_open`, and — for every root — a roll rule proposed from §4 evidence and to be
  **confirmed against the live feed at its first observed roll** (MCL's next roll is in
  October 2026, MBT's on 2026-09-25, MGC's in late November — all observable within weeks on
  the box's `bars_<ROOT>_*.jsonl`).
- **Not authorised:** confirmatory corpora, candidate regeneration, outcomes, R5, any
  strategy-policy inheritance (stops, ORB offsets, commissions, contracts, session permissions,
  routes — §B5), any runtime/collector/calendar change.
- Forward observation of all four roots continues under its own epoch regardless (#622 §10).
