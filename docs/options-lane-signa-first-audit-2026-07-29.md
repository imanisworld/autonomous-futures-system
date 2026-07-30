# Options Lane — Signa-First Redesign Audit (2026-07-29)

Audit-first record for the Signa-first, price-action-triggered options workflow.
**Advisory only. No broker submission, no execution, no deployment, no auto-entry.**
Nothing in this document was assumed from prior descriptions; every claim below is
either verified against the repo at an exact line, or verified against the live
Signa API, or explicitly marked UNPROVEN.

---

## 1. Current-state audit (file paths + line references)

### 1.1 Signa ingestion

| Location | Finding |
|---|---|
| `sources/signa_client.py:86` | Sends `params={"sym": symbol, "timeframe": timeframe}`. **The API ignores `timeframe`.** Verified live: the correct parameter is `tf`. Every Signa call this system has ever made returned daily data regardless of the argument. |
| `sources/signa_client.py:71` | `fetch_signal(symbol, timeframe="1d")` — the argument exists but has never had an effect (see above). |
| `sources/signa_client.py:139` | `enrich_payload_with_signa` calls `fetch_signal(symbol)` with no timeframe at all. |
| `sources/signa_client.py:170-176` | `_normalize_grade` returns `raw[0]` — **`"A+"` is silently truncated to `"A"`.** Confirmed defect. |
| `sources/signa_client.py:105` | `score = engine.score or signa.conviction or data.confidence` — three semantically different fields collapsed into one `score`. |
| `sources/signa_client.py:119` | `confidence = engine.confidence or signa.conviction` — `signa.conviction` can populate **both** `score` and `confidence` in the same response. |
| `sources/signa_client.py:105,119` | `or`-chaining is falsy-unsafe: a legitimate `score` of `0` falls through to a different field. |
| `sources/signa_client.py:112` | `weekly_direction` reads `data.weekly_direction` / `signa.weeklyDirection`. **Neither field exists in the live response.** `weekly_direction` is therefore always `None`. |
| `sources/signa_client.py:114` | `ok=bool(payload.get("ok", True))` — defaults to True when absent. |
| `sources/signa_client.py:18-30` | `SignaSignal` has no `signal_timestamp`, no `tier`, no `flowScore`, no `stage`, no `entry/stop/target/rr`, no `triggers`, no per-timeframe fields. **No staleness capability exists anywhere.** |

### 1.2 Signa gating — three mutually inconsistent gates

| Location | Keys on | Missing Signa | Threshold |
|---|---|---|---|
| `strategy/signa_gate.py:22-35` | **weekly** direction | NEUTRAL (**fails open**) | grade A/B pass, C/D/F fail |
| `options_companion/signa_gate.py:38-60` | **daily** direction | REJECT (fails closed) | grade A/B required |
| `alert_ranker/rh_options.py:756-761` | daily + weekly conflict | n/a | **`signa_score < 70`** |
| `options_manager/risk_gate.py:120-128` | none (score/grade only) | n/a | **`risk_min_signa_score = 30`** |
| `options_manager/packet_builder.py:21-23` | none | n/a | **`MIN_SIGNA_SCORE = 30`** |

**`strategy/signa_gate.py:31` is dead code.** It gates on `weekly_direction`, which
the parser can never populate (§1.1). `SIGNA_WEEKLY_OPPOSES` can never fire.

**Three distinct score thresholds coexist: 30, 30, 70.** They are applied to a
`score` field whose provenance is itself ambiguous (§1.1).

**`config/settings.py:996`** accepts `"A+"` as a valid `min_confluence_grade` — so
`A+` is a real value in this system's vocabulary, while `signa_client` destroys it
before anything can see it.

### 1.3 Fabricated GEX — confirmed, not assumed

| Location | Finding |
|---|---|
| `alert_ranker/rh_options.py:214` | `extracted["gex_regime"] = "LOW_PINNING"` from a **regex match on alert text**. |
| `alert_ranker/rh_options.py:271-278` | *"Infer LOW_PINNING when GEX says 'near support' + bullish"* — invents a gamma regime from a support/resistance phrase and a direction. |
| `alert_ranker/rh_options.py:252-260` | `Target 1` is written into **`gex_resistance_wall`**. An ordinary price target is renamed a gamma wall. |
| `alert_ranker/rh_options.py:219-222` | Plain `SUPPORT` / `RESISTANCE` text is written into `gex_support_wall` / `gex_resistance_wall`. |
| `alert_ranker/rh_options.py:762` | `if inputs.gex_regime != "LOW_PINNING": failures.append(...)` — **hard-gates on the fabricated value.** |

This is circular: the system invents `LOW_PINNING` from text, then requires
`LOW_PINNING` to approve. Ordinary support/resistance is laundered into gamma
vocabulary at four separate sites.

---

## 2. Verified Signa API contract

Captured live, read-only, `GET /api/v1/signal`, HTTP 200, `engine_version v3.1`,
`api_version v1`.

### 2.1 Request

| Param | Status |
|---|---|
| `sym` | **Correct.** Echoed back as `symbol`. |
| `tf` | **Correct timeframe parameter.** |
| `timeframe`, `interval`, `resolution`, `symbol` | **Silently ignored** — server falls back to `1d`. |

Verified `tf` values: `1d`, `4h`, `1h`, `30m`, `15m`, `5m`, `1w`, `1m` (one minute),
`1mo` (one month, echoes `1M`). `daily` and `D` alias to `1d`.

### 2.2 Response — three independent surfaces, not one

Top-level keys: `ok, symbol, timeframe, timeframe_input, cached, engine,
engine_coverage, signa, data, options_flow, confidence_pillars, trade_plan,
engine_version, signal_timestamp, meta, crossSurfaceConflict`.

| Block | Source (per `meta.signal_sources`) | Timeframe-varying? |
|---|---|---|
| `engine` | "Nightly 30+ model pipeline consensus (matches in-app Action Card)" | **NO — invariant across all `tf`** |
| `data` | "Live single-pass technical analysis (real-time)" | YES |
| `signa` | (undocumented) | YES |

Observed for SPY at capture time, across `tf = 1d / 4h / 1h / 30m / 15m`:

```
engine.direction  BULLISH  BULLISH  BULLISH  BULLISH  BULLISH   <- invariant
engine.grade      B        B        B        B        B         <- invariant
engine.score      81       81       81       81       81        <- invariant
signa.grade       C        C        C        D        D         <- varies
data.direction    WAIT     WAIT     WAIT     SHORT    WAIT      <- varies
data.tier         NEUTRAL  NEUTRAL  WATCH    WATCH    WATCH     <- varies
```

### 2.3 Schema defects identified

**D-1 — `tf` parameter wrong (`sources/signa_client.py:86`).** Multi-timeframe
Signa is currently impossible; every call returns daily.

**D-2 — `engine` is timeframe-invariant.** The parser prefers `engine` for
grade/score/direction. **Fixing D-1 alone would produce fake MTF**: identical
grade/score/direction at every timeframe. D-1 and the block-preference must be
fixed together or the MTF feature is a lie.

**D-3 — two conflicting grades in one response.** `engine.grade = "B"` while
`signa.grade = "C"`. The parser takes `engine` and silently discards `signa`.

**D-4 — three conflicting directional reads in one response.**
`engine.direction = BULLISH`, `data.direction = WAIT`, `signa.action = HOLD`,
`data.bias = "neutral"`, `data.tier = "NEUTRAL"`.

**D-5 — currently exploitable.** For SPY at capture, the parser yields
`grade="B"`, `score=81`. That **passes** `risk_gate` (min 30, grades A/B) and
`packet_builder`, while Signa's own read is `HOLD / C / neutral / tier NEUTRAL /
overallScore 27.5 / direction WAIT`. The system can today report a healthy B/81
Signa read on a ticker Signa itself says WAIT on.

**D-6 — `crossSurfaceConflict` exists and is never read.** The API ships an
explicit conflict field. Present as `None` at capture; semantics UNPROVEN.

**D-7 — `signal_timestamp` exists and is never read.** It is identical across all
`tf` values, so it timestamps the **engine** (nightly), not the live `data` block.
Staleness semantics therefore differ per block.

**D-8 — unparsed fields the brief requires:** `tier`, `stage`, `stageDescription`,
`flowScore`, `stageStrength`, `volumeGrade`, `regimeClass`, `alphaEvent`,
`riskScore`, `overallScore`, `entry`, `stop`, `target`, `rr`, `triggers`,
`patterns`, `pivotPoints`, `options_flow.*`, `confidence_pillars.*`, `trade_plan`.

**D-9 — `data.pivotPoints` (`pivot/r1-r3/s1-s3`) are classical pivots.** These are
the values at risk of being laundered into "gamma walls" (§1.3). They are
support/resistance pivots and must be named as such.

### 2.4 BLOCKER — unresolved field precedence

`engine.grade` and `signa.grade` disagree, and the API documents no precedence.
Per the brief ("do not change gating thresholds until the field meanings are
proven"), **eligibility thresholds cannot be promoted until the operator or the
vendor resolves which grade is authoritative.** This does not block preserving both
fields distinctly — which is the correct interim design.

---

## 3. TradingView indicator classification

Verified from each public script page. **`plot()` exposure could not be verified for
any script from its public page** — TradingView does not publish that in the
description. It is a 2-minute manual check: add the indicator, open another script's
Inputs, and see whether the series appears in an `input.source()` dropdown.

| # | Name | Publisher | License | Calculates | Drawing style (stated) | Alerts | Class |
|---|---|---|---|---|---|---|---|
| 1 | 50%er (PreMarket & ORB) | BigNateVibes | Protected, free use | 50% midpoints D/W/M/Q/Y, PDH/PDL, premarket, ORB, killzones | lines, labels, bg gradient | not stated | **REJECTED** (ORB duplicate) |
| 2 | Candle Type (The Strat) | Crinklebine | Protected, free use | Strat candle types: inside, up/down, engulfing | plots (stated) | not stated | **CORE** |
| 3 | ORB 30 Alerts (ATH) | cautiousRice_nolo | **Open-source** | ORB Tokyo/London/NY, 30m ranges | plots hi/lo/mid, labels, boxes, debug table | **yes** | **CORE** (single ORB) |
| 4 | ORB with Price Targets | getthatcashmoney | **Open-source** | ORB + 50–500% targets | lines, labels | yes | **REJECTED** (ORB duplicate) |
| 5 | Price Action SMC | BigBeluga | **Open-source** | structure, order blocks, FVG, SFP, liquidity, sweeps | zones, blocks, lines, colored candles | not stated | **OPTIONAL** (most features off) |
| 6 | Ultimate ORB | LuxAlgo | **Open-source** | ORB + expansion targets, volume profile/POC, ATR trail | labels, **statistical table** | not stated | **REJECTED** (ORB duplicate) |
| 7 | Support and Resistance Signals MTF | LuxAlgo | **Open-source** | swing S/R + breakout/test/retest/rejection | signal marks, sentiment profiles | **yes (confirmed)** | **CORE** (single auditable S/R) |
| 8 | MY FREE GAMMA LEVELS | TheRealDrip2Rip | **Protected**, free use | GEX flip, call wall, put wall, HVL, vol trigger, max pain from CBOE data | dashboard, **DOM ladder**, **strike table**, status bar, bands, beam, bar tint | not stated | **SHADOW-ONLY** |
| 9 | FTFC (The Strat) | rwestbrookjr | **Open-source** | Full Timeframe Continuity across TFs | TF status cues | not stated | **CORE** |
| 10 | Magnetic Zones v1.1 Beta | Arun_K_Bhaskar | **Protected** | pivot S/R zones R1–R3/S1–S3, minor zones, PDH/PDL | zones, levels | not stated | **REJECTED** (S/R duplicate; closed-source, unauditable) |

**#8 is the notable one.** It is a *free* gamma-levels indicator computing exactly
the six levels GEX Sniper provided. It is the plausible chart-side replacement.
But it is **closed-source and heavily table/dashboard/ladder-driven**, which is
precisely the drawing style Pine cannot read. Classification is **SHADOW-ONLY**:
eligible for eyes-on chart use and for a documented shadow scorecard, **not** for
trade approval and **not** assumed backend-ingestible.

Backend ingestion: **none of the 10 is backend-ingestible.** All are chart-display
Pine. The only machine paths are (a) `input.source()` on a `plot()` series into
another Pine script — unverified per script; (b) TradingView alerts → webhook, which
exists for #3 and #7; (c) the TradingView MCP's `data_get_pine_lines` /
`data_get_pine_labels`, which can read line/label drawings Pine itself cannot.

Resulting chart architecture (matches the expected one, with #8 assigned):
- **Core:** #2 Candle Type + #9 FTFC (Strat), #3 ORB 30 Alerts (one ORB only),
  #7 LuxAlgo S/R MTF (one auditable S/R only), existing Strat/ICC Pine.
- **Optional:** #5 BigBeluga SMC, most features disabled.
- **Shadow-only:** #8 MY FREE GAMMA LEVELS.
- **Rejected:** #1, #4, #6 (ORB duplicates), #10 (S/R duplicate, closed-source).

---

## 4. GEX dependency map and blast radius

PR #379 (`claude/options-gex-optional`, open, unmerged) removed three hard
dependencies in `options_manager`. **It did not touch `alert_ranker`,** which
contains the fabricated-GEX behavior in §1.3.

| Site | Status |
|---|---|
| `options_manager/context/market_validator.py:59` | **FIXED in #379** — was `INVALID/missing_gex_context` |
| `options_manager/risk_gate.py:142` | **FIXED in #379** — default flipped to warn |
| `options_manager/validation/morning_scan_packet.py:58` | **FIXED in #379** — was blocking whole packet |
| `alert_ranker/rh_options.py:762` | **OPEN — hard-requires fabricated `LOW_PINNING`** |
| `alert_ranker/rh_options.py:214,271-278` | **OPEN — fabricates `gex_regime`** |
| `alert_ranker/rh_options.py:219-222,252-260` | **OPEN — renames S/R and targets as gamma walls** |
| `strategy/gex_gate.py:25-27` | Already fail-soft (returns NEUTRAL when absent). No change needed. |
| `context/wall_context.py:374-388` | Reads real `state.gex` only; correctly sourced. No change needed. |
| `sources/gex_observer.py` | Observe-only, correct. **Stays shadow-only.** |

**Blast radius of the remaining work: `alert_ranker/` only.** No futures strategy,
no execution, no broker path.

---

## 5. Proposed data model

Preserve every surface distinctly. Never collapse, never fabricate.

```
SignaSurface           # one per API block, per timeframe
  block: "engine" | "signa" | "data"
  timeframe: str                    # the tf actually echoed back, not requested
  grade: str | None                 # RAW — "A+" preserved verbatim
  grade_letter: str | None          # derived convenience, never overwrites grade
  score: float | None               # engine.score ONLY
  confidence: float | None          # engine.confidence / data.confidence, per block
  conviction: float | None          # signa.conviction — NOT score, NOT confidence
  direction: str | None             # raw
  direction_normalized: Direction   # UP | DOWN | NEUTRAL | WAIT | UNKNOWN
  action / bias / tier / stage / risk_rating / flow_score / regime_class
  entry / stop / target / rr
  triggers: tuple[Trigger, ...]

SignaReading
  symbol / requested_tf / echoed_tf
  ok: bool
  surfaces: Mapping[str, SignaSurface]
  signal_timestamp: datetime | None      # engine run time
  generated_at: datetime | None          # meta.generated_at
  age_seconds: float | None              # computed, drives staleness
  cross_surface_conflict: Any | None     # raw passthrough, semantics unproven
  surfaces_disagree: bool                # DERIVED, always computed
  options_flow / confidence_pillars      # preserved raw
  fetch_error: str | None

SignaMultiTimeframe
  daily / four_hour / one_hour: SignaReading | None
  # engine block is invariant — recorded ONCE, not repeated per timeframe
```

Direction mapping (brief §2): `LONG/UP/BULLISH` → CALL-eligible;
`SHORT/DOWN/BEARISH` → PUT-eligible; `WAIT/NEUTRAL/UNKNOWN/missing` → **no trade**.
`WAIT` becomes a first-class value, distinct from `NEUTRAL` and from `UNKNOWN`.

Renames removing gamma laundering:
`gex_support_wall` → `signa_support_pivot`; `gex_resistance_wall` →
`signa_resistance_pivot`. Real GEX fields stay, optional, populated only by a
validated GEX source.

## 6. Proposed gate order and decision matrix

Order — cheapest and most-fail-closed first:

1. **Data integrity** → `DATA_BLOCKED` on missing/stale Signa, fetch error, or
   unresolvable surface disagreement.
2. **Market regime** (SPY/QQQ + HTF). Broad-market opposition → `REJECTED`.
3. **Signa veto** — grade / direction / staleness. Never a trigger.
4. **Price-action trigger** (Strat / reclaim / retest). **No setup → `WATCHING`,
   regardless of Signa strength.**
5. **Location context** (ORB, S/R, PDH/PDL, sweeps, OB, FVG) — upgrade/downgrade only.
6. **Contract quality** — DTE, spread, volume, OI, Greeks, event risk.
7. **Risk** — $300 max loss, 5 open positions, ~$1,000 total open risk.

| Signa | Chart trigger | Result |
|---|---|---|
| missing / stale / fetch error | any | `DATA_BLOCKED` |
| opposes direction | any | `REJECTED` |
| C/D/F, or WAIT/NEUTRAL | any | `REJECTED` |
| A+/A, conf 80+, daily+4H aligned | present | `TRIGGERED` (priority review) |
| B, conf 75+, daily aligned, 4H not opposing | present | `TRIGGERED` (standard review) |
| strong grade | **absent** | `WATCHING` |
| strong grade, mixed/neutral LTF | any | `WATCHLIST` |

States: `REJECTED, WATCHLIST, WATCHING, TRIGGERED, DATA_BLOCKED, INVALIDATED,
ACTIVE, EXITED, EXPIRED`. Existing today: `WATCHING/TRIGGERED/INVALIDATED/ACTIVE/
EXITED/EXPIRED` (`proof_packet.py:60`), `SKIPPED` (`morning_scan_packet.py:92`),
`PENDING/QUEUED/REJECTED` (`models.py:33`), `DATA_BLOCKED` (`risk_gate.py:27`).
**Missing: `WATCHLIST`.** `SKIPPED` is an extra to reconcile.

**All thresholds in the matrix above remain configurable and are NOT promoted in
this pass** — §2.4 blocks that until grade precedence is resolved.

## 7. Implementation plan — safe increments

| # | Increment | Risk | Depends on |
|---|---|---|---|
| A | Sanitized Signa fixtures from the real capture + parser contract tests | none (tests only) | — |
| B | Fix `tf` param (D-1) + record echoed timeframe | low | A |
| C | Preserve distinct fields; stop collapsing score/confidence/conviction; preserve `A+` | low | A |
| D | Parse `signal_timestamp`, add staleness; `WAIT`/`UNKNOWN` handling | low | C |
| E | Remove fabricated GEX + gamma renaming in `alert_ranker/rh_options.py` | medium | — |
| F | Add `surfaces_disagree`; surface `crossSurfaceConflict` | low | C |
| G | Add `WATCHLIST` state; reconcile `SKIPPED` | low | — |
| H | MTF capability (1d/4h/1h) — **capability only, not wired into gating** | medium | B, C |
| I | Docs + Discord/scanner output | low | all |

**Not in this pass:** eligibility-threshold promotion, `engine`-vs-`signa`
precedence, any GEX promotion.

## 8. Test plan and acceptance criteria

Regression coverage required: missing Signa, stale Signa, neutral Signa, `WAIT`,
directional opposition, `A+` preservation, score≠confidence≠conviction, missing GEX,
unavailable gamma targets, surface disagreement, `tf` echo verification.

Acceptance:
1. `A+` survives parse→packet→output unmodified.
2. `signa.conviction` never populates `score`.
3. A `score` of `0` never falls through to another field.
4. `tf=4h` returns an echoed `4h`; a mismatch is recorded, not silently accepted.
5. `data.direction = "WAIT"` never yields a CALL/PUT-eligible packet.
6. Missing/stale Signa → `DATA_BLOCKED`, never a silent pass.
7. No code path writes a `gex_*` field from S/R, pivots, or targets.
8. `rh_options` approves without any `gex_regime`.
9. Signa alone never produces `TRIGGERED` without a chart trigger.
10. Full suite green; no futures/execution/broker test changes.

## 9. Assumptions that remain UNPROVEN

1. **Which grade is authoritative — `engine.grade` or `signa.grade`.** Blocks
   eligibility promotion (§2.4).
2. **`crossSurfaceConflict` semantics.** Observed `None` only.
3. **`plot()` exposure for all 10 indicators.** Not published; needs the manual
   `input.source()` dropdown check.
4. **Whether #8 MY FREE GAMMA LEVELS is accurate.** Free CBOE-derived gamma levels
   are unvalidated. SHADOW-ONLY until scored.
5. **Staleness thresholds.** `signal_timestamp` tracks the nightly engine; the live
   `data` block has no timestamp of its own. What counts as "stale" per block is
   undecided.
6. **`tf` value coverage beyond those tested**, and whether `1m` is minute or month
   on every deployment (`1mo` echoes `1M`).
7. **Signa universe/ranking endpoint.** The brief calls for Signa to rank a ticker
   universe; only a single-symbol `/api/v1/signal` endpoint has been verified. No
   ranking or scanning endpoint has been located or tested.
8. **The `data` block's real-time claim** is from `meta`, not independently verified.
