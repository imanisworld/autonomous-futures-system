# SPX → SPXW Paper Lane Design

_Date: 2026-09-29. Status: AWAITING OPERATOR REVIEW. No deploy. Paper/advisory only._

## Intent

Add a **separate** scheduled paper lane that:

1. Scans **SPX** index bars for Strat setups (same setup authority / invalidation discipline as equity V1).
2. Routes valid SPX setups into **SPXW** weekly option contracts for paper accounting.
3. Allows **0DTE and 1+DTE** SPXW contracts in paper mode as **separate cohorts**.
4. Never mixes results with the equity/ETF 66-symbol universe or `OPTIONS_PAPER_V1` evidence.

## Non-goals

- Do **not** add SPX or SPXW to `alert_ranker.universe` / the 66-symbol equity watchlist.
- Do **not** change `OPTIONS_PAPER_V1` DTE bands, risk caps, liquidity thresholds, or alert rules.
- Do **not** extend MES/ES companion mapping to SPX in this change.
- Do **not** rely on webhook-only / manual injection as the primary signal path.
- Do **not** enable live broker/order submission.
- Do **not** deploy or mutate box `OPTIONS_SCANNER_WATCHLIST`.

## Architecture (Approach A)

```
┌─────────────────────────────────────────────────────────────┐
│ Equity / ETF scanner (unchanged)                            │
│  watchlist = alert_ranker.universe.DEFAULT_WATCHLIST (66)   │
│  policy    = OPTIONS_PAPER_V1  (MIN_DTE=14)                 │
│  journal   = options_shadow_journal (existing lane)         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ SPX → SPXW paper lane (NEW, isolated)                       │
│  signal underlying = SPX (index bars / quotes)              │
│  contract family   = SPXW                                   │
│  policy            = OPTIONS_PAPER_SPXW_V1 (new)            │
│  scheduler         = separate job id / interval             │
│  journal           = options_spxw_shadow_journal (new)      │
│  cohorts           = DTE_0  |  DTE_1_PLUS                   │
└─────────────────────────────────────────────────────────────┘
```

### Routing rules

| Input | Allowed path | Forbidden path |
|---|---|---|
| SPX index bars | SPX scheduled scanner → SPXW paper policy | Equity watchlist scan |
| SPXW symbol alone | Rejected / ignored by equity lane | Treated as equity ticker |
| Equity tickers (SPY, …) | Existing V1 only | SPXW policy |
| MES/ES companion | Unchanged SPY mapping | No SPX/SPXW emission here |

Hard invariant: `DEFAULT_WATCHLIST` / equity `scan_watchlist` never contains `SPX` or `SPXW`.

### Policy: `OPTIONS_PAPER_SPXW_V1`

New pure module (proposed: `alert_ranker/paper_spxw_v1.py`), sibling of `paper_v1.py`:

**Copied unchanged from V1 (not loosened):**

- max planned risk / trade: `$300`
- max aggregate open planned risk: `$1,000` (**lane-local** aggregate, not shared with equity V1)
- premium stop: 25% adverse from ask entry
- bid/ask validity, max spread %, min volume, min OI, delta quality band
- fail-closed on missing/stale quote or missing contract identity
- entry requires TRIGGERED setup + underlying invalidation + target
- late-entry / remaining-R guard (same numeric floor defaults)
- 1 contract, entry at ask / exit at bid, no commission
- contract multiplier **×100** (SPXW standard multiplier; assert in tests)
- no averaging down (resolver never adds size)

**SPXW-specific DTE policy (new cohort tags only):**

| Bucket | Rule | Cohort tag |
|---|---|---|
| `DTE_0` | expiration date == session date (America/New_York) | `0DTE` |
| `DTE_1_PLUS` | 1 ≤ DTE ≤ sanity max | `1_PLUS_DTE` |
| invalid | missing expiration / nonsense DTE | reject `DATA_INVALID` |

No preference / ranking bias that assumes 0DTE is better. Selection among liquid contracts in a chosen bucket uses the same delta/spread/strike ranking style as V1, but expiration choice may consider both buckets and **records which cohort was used**. Prefer documenting: try preferred liquid contract in either bucket that passes quality; tag by DTE. Do **not** change equity V1’s `<14 excluded` rule.

**Cash-settled / index handling:**

- Signal price source is SPX (index), not SPY.
- Contract discovery ticker for chains/expirations is **SPXW** (weekly), not SPX monthly, unless provider only lists under one root — adapter must normalize and record `signal_underlying=SPX`, `contract_root=SPXW`.
- Do not reuse equity assumptions that treat the option underlying as a stock borrow/shares deliverable; paper marks remain premium × 100.
- Quote freshness / stale checks apply to SPXW contract quotes the same way as equity V1.

### Scheduler

- New APScheduler (or equivalent) job, e.g. `options-spx-spxw-scan`, same 5-minute cadence **or** config `OPTIONS_SPXW_INTERVAL_MINUTES` defaulting to scanner interval.
- Market-hours gate: US equity RTH (same helper as equity scanner) unless index extended hours are explicitly configured later (out of scope; default RTH-only).
- Env enable flag: `OPTIONS_SPXW_PAPER_LANE_ENABLED` default **false** (fail-closed off until operator turns on).

### Journal / reporting

New SQLite table (or dedicated DB file under shared logs), e.g. `options_spxw_shadow_journal`, schema parallel to equity shadow journal plus:

- `signal_underlying` = `SPX`
- `contract_root` = `SPXW`
- `paper_policy_id` = `OPTIONS_PAPER_SPXW_V1`
- `dte_cohort` = `0DTE` | `1_PLUS_DTE`
- setup type, rejection reason, selected contract, entry/exit timestamps
- premium P&L, spread cost proxy `(ask-bid)*100` at entry

Daily/EOW rollup (read-only reporter) emits **SPXW-only** metrics never folded into equity cohort:

- 0DTE P&L, 1+DTE P&L
- win rate, avg winner, avg loser, expectancy
- max drawdown
- slippage/spread cost
- breakdown by setup type / rejection reason / DTE

### Provider validation (preflight, not deploy)

Isolated checks (same spirit as capacity preflight):

1. SPX underlying quote / bars available
2. SPXW expirations include a same-day expiry when market has 0DTE listed
3. SPXW chain fetch for a chosen expiry returns contracts
4. Multiplier accounting unit test (premium 1.50 → $150 planned risk at 25% stop = $37.50, etc.)

If credentials missing: fail closed; do not stub a PASS.

## File plan (implementation, after spec approval)

| File | Role |
|---|---|
| `alert_ranker/paper_spxw_v1.py` | Pure SPXW paper policy + cohort tagging |
| `alert_ranker/spxw_lane.py` | Scheduled SPX scan → SPXW contract apply; Discord optional/off by default |
| `alert_ranker/spxw_storage.py` | Isolated journal + rollup queries |
| `alert_ranker/app.py` | Register separate scheduler job when enabled |
| `alert_ranker/config.py` | Enable flag + interval; **no** SPX/SPXW in equity watchlist default |
| `ops/options_spxw_daily_pnl_report.py` | Separate Discord/JSON rollup (optional env webhook) |
| `tests/test_paper_spxw_v1.py` | Policy/cohort/risk/multiplier/fail-closed |
| `tests/test_spxw_lane_isolation.py` | Not in equity universe; no live order path |

Equity `paper_v1.py` / `universe.py` remain behavior-identical.

## Test matrix

1. SPX TRIGGERED setup → SPXW paper candidate with policy id `OPTIONS_PAPER_SPXW_V1`
2. SPXW never appears in equity `DEFAULT_WATCHLIST` / equity scan path
3. 0DTE expiration allowed under SPXW policy; tagged `0DTE`
4. Later expiration tagged `1_PLUS_DTE`
5. Invalid/missing/stale quotes → `DATA_INVALID` (fail closed)
6. Premium/risk uses ×100
7. Equity V1 constants (`MIN_DTE=14`, risk caps, spread/OI) unchanged
8. No broker submit / order ticket execution path referenced by the lane

## Safety

- Advisory/paper only; `PreparedOrderTicket` / live locks untouched
- Lane enable default off
- Separate aggregate risk pool from equity V1
- No production watchlist mutation; no deploy from this workstream

## Open items deferred (not blocking v1)

- Extended-hours SPX scanning
- SPX monthly (non-W) contracts
- Sharing aggregate risk with equity V1
- Live Webull/index trading hours proof

## Approval checkpoint

Operator: reply **approve spec** (or request edits) before implementation begins.
