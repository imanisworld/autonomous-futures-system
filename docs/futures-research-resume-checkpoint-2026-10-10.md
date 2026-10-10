# Futures Research — Resume Checkpoint (2026-10-10)

**Purpose:** Persist the current investigation as a compact, source-only handoff. **No merge, deploy, trading activation, or strategy promotion.**

**Handling:** This public-repo checkpoint intentionally contains no per-strategy P&L, trade levels, or individual strategy-return tables. The operator's **private numeric handoff** is saved in the ChatGPT Library at `/Futures Research/Futures Research Handoff - 2026-10-10.md`. Do not reproduce that private report in this public repo.

## Current PR and evidence reconciliation — 2026-10-10 (supersedes historical table below)

**VERIFIED on GitHub:** #1208 (executable research-reference fills), #1209 (actual R:R and stop geometry), #1210 (combined integration/1m route isolation), and #1211 (strict research-only timeframe helper) have **MERGED into main**. Their historical PR head CI numbers below are preserved for provenance; do **not** interpret the old “open draft” column or pre-merge review instruction as current. The isolated timeframe helper is **not automatically wired into the sealed MGC scorer**. A source merge is not a VPS release.

**ACTIVE, NOT MERGED:** #1212 optional prospective 5m full-arm provenance sidecar (default **OFF**, latest inspected head `9df1a09`, **CI SUCCESS** on this head; no independent review recorded); #1213 1m↔5m pure offline identity reconciliation (latest inspected head `730ef532`, **CI SUCCESS** on that head; the **8,777 passed / 8 skipped** count belongs to earlier head `2e5c2fa`, no independent review recorded). **#1213's diff includes #1212's source**, so determine merge order and reconcile overlapping changes before either merge. A synthetic match, even through the actual observer-event schema, is **not** live/prospective fill or performance parity.

**ACTIVE RESEARCH TRACKS:** MNQ broad 4HR pre-armed remains the best existing historical lead, not a proven DEMO model. Track A observes 1m natural touches without orders; Track B's **separate, registered** 5m IOC8/400-tick future candidate is on `research/4hr-mnq-400-forward-20261009` at last documented `2f4f776`, pending fresh head, safety, deployment and authorization proof. No forward collection, VPS state, or profitability asserted by this documentation update.

**NEXT:** independently review #1212/#1213; explicitly decide whether to permit future-only identity-sidecar collection; run prospective *unsealed* identity/timing/parity study only after its pre-registered source/eligibility rules. Natural 1m observer's 10 eligible arms/20 trading days/2 months calendar gate and separate profitability/DEMO GO remain. See `docs/agent-work-state.md` START HERE and `docs/repo-hygiene-deferred-removal-tracker-2026-10-10.md`. **Do not re-run closed historical sweeps, backfill a fictional arm key, enable flags, deploy or place orders.**

---

## End goal and current verdict

The goal is a consistently positive-expectancy futures strategy under causal signals, executable prices, transaction costs, adverse slippage, valid market calendars, risk limits, and independent-period testing, followed by an **explicitly authorized DEMO-only** trial. An engineering regression suite passing is **not** a profitable system.

**Current strategy verdict: NOT PROVEN / NOT DEMO-READY.** Recent exploratory intraday momentum, reversal, divergence, gap, breakout, and fixed-time hypotheses did not survive extra periods, concentration checks, or proper higher-timeframe anchoring. Do not tune repeatedly on the same rejected data to produce a green backtest.

## Historical source-correction PR snapshots (all #1208–#1211 now merged)

| PR | Scope | Exact-head CI at checkpoint | Remaining |
|---|---|---|---|
| [#1208](https://github.com/imanisworld/autonomous-futures-system/pull/1208) | Fail-closed next-executable-reference research fills, conservative ambiguous-bar ordering, isolated from hash-pinned canonical broker | 8,664 passed, 8 skipped | Independent review; operator approval before merge |
| [#1209](https://github.com/imanisworld/autonomous-futures-system/pull/1209) | Actual-bracket R:R, structural stop-direction validation incl. runner exemption | 8,665 passed, 8 skipped | Independent review; no risk-floor change |
| [#1210](https://github.com/imanisworld/autonomous-futures-system/pull/1210) | Combined regression of fill and risk correctness | 8,688 passed, 8 skipped | Independent review; QA-only |
| [#1211](https://github.com/imanisworld/autonomous-futures-system/pull/1211) | Isolated complete-bar, gap, DST and dated-contract provenance validation across 5m/15m/30m/60m/4H/12H | 8,677 passed, 8 skipped | Independent review; not wired to frozen trials |

Historical table only: these CI numbers refer to the **tested heads before the later merges and reviews**. Recheck exact head after changes. Do not assume Grok/Cursor independent testing is complete merely because the operator said they were running it.

## Root causes now evidenced

1. **False historical fills:** Some legacy studies used a plan level after signal confirmation rather than an executable entry. #1208 isolates an honest research path; canonical runtime defaults remain unchanged.
2. **Risk metadata drift and structural guards:** Trade-provided R:R did not establish actual bracket R:R; wrong-side runner stops needed an independent invariant. #1209 tightens checks without loosening trading policy.
3. **Optimistic same-bar execution:** A stop and target both touched within one bar cannot be ordered from OHLC alone. The strict research path resolves ambiguity pessimistically.
4. **Higher-timeframe identity:** UTC-fixed 4H bars are not equivalent to ET-anchored session or strategy-specific bars. The frozen MGC-style resampler can emit incomplete buckets and does not preserve dated-contract provenance; #1211 adds an isolated new-study validator. **Do not silently switch frozen prospective scorers to it.**
5. **No single universal 4H/12H clock:** MGC wide research uses a 6 p.m. ET CME-session anchor, canonical 4HR Re-Trigger uses fixed Eastern wall-clock blocks, and Miyagi uses 4 a.m./4 p.m. ET 12H blocks. They must be tested under their own identity.
6. **Session-day definition:** Holiday-adjusted futures sessions are not safely approximated by 1,440-minute UTC bars; exchange calendars and provider session metadata matter.
7. **Fragile profitability:** Several exploratory signals changed sign across years or became losses without a single outsized event. Such candidates were rejected, not promoted.

Connected Massive/Polygon price bars were inspected; cross-resolution consistency checks on complete reconstructed 15m intervals found exact OHLCV parity. **Raw historical data and the temporary research workspace are not preserved in the public repo.** Preserve reproducible hashes/SQL separately before treating any experiment as independent proof.

## Preserve the prospective study boundary

The pre-registered MGC 4H wide forward observation is controlled by `docs/prereg-mgc-4h-wide-forward-2026-09-23.md`. It remains **blinded and separate** from exploratory tests. Do not peek at embargoed outcomes, change its frozen aggregation/criteria in place, or assume its preliminary historical performance validates DEMO trading. A research-method repair that changes its identity requires separate independent review and separately registered prospective evidence.

## Exact next work (avoid audit loops)

1. Fetch current `main` and exact PR heads; reconcile independent Grok/Cursor reviews and any concurrent changes.
2. Evaluate the research-only correctness improvements in isolation. Do not merge for the purpose of producing a strategy-profitability claim.
3. On **unsealed** data, run a reproducible per-strategy timeframe-parity matrix: actual 5m base bars; explicit strategy clock; signal-decision availability; next executable entry or genuinely pre-armed trigger; post-fill risk; gap/stop-first execution; costs/slippage; no-fill/invalid counts.
4. Separate **signal predictive strength** from **entry/fill realism** and **risk/exit geometry**. Test policy alternatives only as isolated research arms; keep live hard caps, broker protections and live enable flags unchanged. Evaluate multi-period expectancy, drawdown, concentration, and independent controls.
5. A candidate can approach DEMO only after independent evidence of positive net expectancy, executable parity, acceptable drawdown/margin, working fail-safe and broker-demo route, and an explicit operator GO. **No candidate currently meets all gates.**

**Explicit non-actions:** No merges, VPS/box changes, builds, redeploys, restarts, broker orders, live or demo activation, capital changes, permission expansions, loosening of risk caps, or access to sealed forward results were made as part of this checkpoint.

## Appendix — Grok research addendum (Oct 9–10, 2026)

Append-only. No account data, journals, or paper-trade P&L. Backtest figures are on public market bars (Polygon, Oct 2024 – Jun 26 2026; no bars from Jun 29 2026 onward, per the MNQ seal). Costs $1.48/side + 2–3 ticks; honest fills (next-bar open or stop entry); 1 micro contract; holdout Jan–Jun 26 2026 run once per rule; every rule frozen (timestamp + sha256) before testing. Ledger references are in the private AFS ledger.

### Verdicts
| Study | Verdict | Ledger |
|---|---|---|
| Options V1 (current formulation) | REJECT tested formulation | AFS-0201 |
| 4HR Re-Trigger / 60M 3-2-2 | INSUFFICIENT EVIDENCE (0 qualifying forward fills; not risk-eligible at the current account size) | AFS-0202 – AFS-0204 |
| Existing inventory vs the current account | None qualifies | AFS-0205 |
| Capital-unconstrained ranking | No proven edge at any size | AFS-0206 |
| Overnight long + 20-day filter | Dead on clean data (no better than random) | AFS-0209 / 0210 |
| Intraday MNQ rules (OR breakout, stretch fade, gap fade, last-hour) | All fail | AFS-0211 |
| Daily trend, 5 micros | Parked candidate: holdout positive but concentrated (gold, June); design weak | AFS-0214 |
| Full sweep: Strat setups / ORB / VWAP / 4HR / trend / failed breakdown × 5m–daily × 5 micros, 358 frozen fixes | Nothing robust; 1 survivor consistent with chance | AFS-0215 |
| Events (CPI, FOMC, NFP, EIA) | 1 lead: FOMC first-15m follow (thin, 14 FOMC days); the rest fail | AFS-0216 |

Total evaluations: 384. Nothing passes a Bonferroni correction.

### Why setups fail
- Stops sit inside normal noise: the 120-tick cap is smaller than the median 5m bar at 9–11 ET, and about 32% of stopped trades later reached their target.
- Strat reversals lose even before costs (pre-cost PF 0.70–0.80).
- Typical favorable excursion is 0.33–0.68R against 2R targets.
- ORB and VWAP pre-cost PF is 1.09–1.14, so costs erase it.
- At 1–4 hour holds, intraday MNQ behaves like a coin flip. 5m is the worst timeframe and daily the least bad.

### Do not redo
Intraday pattern rules on MNQ/MES at 5m–60m; Strat reversal variants; ORB/VWAP variants; overnight drift; CPI/NFP/EIA fades; re-scoring options V1.

### Active
Log-only shadow tracking of the daily trend rule and the FOMC follow rule (no orders).

### Untested
Options volatility selling (needs more than a month of option quotes); carry/roll yield; longer-history multi-market trend.

## Appendix — Cursor research addendum (2026-10-10)

Append-only. Complements the Grok appendix and the operator’s **private** ChatGPT Library handoff (`/Futures Research/Futures Research Handoff - 2026-10-10.md`). This section does **not** duplicate private journals, account P&L, or per-trade tables. Numeric detail for repo-local artifacts lives only under `docs/research-evidence/` paths cited below.

### What was verified (this lane, source-only)

- **Fill / R:R correctness drafts:** [#1208](https://github.com/imanisworld/autonomous-futures-system/pull/1208), [#1209](https://github.com/imanisworld/autonomous-futures-system/pull/1209), [#1210](https://github.com/imanisworld/autonomous-futures-system/pull/1210) — exact-head CI green at the heads recorded in the table above; **not merged, not deployed**.
- **`market_condition` / TRENDING:** Pine bucket logic reconstructed in `scripts/pine_market_condition.py`; runtime trusts Pine `TRENDING` / `RANGE_BOUND` verbatim in `strategy/signal_engine.py` (`_score_market_condition`); production `require_trending_condition` blocks non-`TRENDING` before setups (322 exempt, 4HR not). Replay labels carry `RECONSTRUCTED_UNVALIDATED_INIT` for ATR-dependent buckets — parity spot-check still open.
- **ORB / VWAP in inventory corpus:** Under **IOC @ decision close**, ORB reclaim/breakout and VWAP hold remain negative in both `TRENDING` and `RANGE_BOUND` on the committed candidate artifact; losses align with **written bracket geometry**, not a missing trending gate alone.
- **Supply / demand:** No runtime detector — `research/sd_zone_round4/` is research-only; validation prereg exists, blind window not scored.

### Saved public artifacts (do not re-run unless paths change)

| Artifact | Branch / note |
|---|---|
| MNQ 4HR 400-tick historical benchmark + forward prereg wiring | `research/4hr-mnq-400-forward-20261009` (not merged); `docs/research-evidence/T-2026-10-09-prereg-4hr-mnq-400-forward-2026-10-09-01/` |
| ORB/VWAP 10m·15m·30m × NY windows 09:40–11:00 & 13:30–16:00, IOC | `docs/research-evidence/orb-vwap-tf-window-sweep-2026-10-10/` (`README.md`, `results.json`) |
| Same sweep + **TRENDING** filter; + **TRENDING** and MNQ ORB stop 48t | `FOLLOWUP-trending.md`, `results-trending.json`, `results-trending-orb48.json` in that folder |
| Runner | `scripts/orb_vwap_tf_window_sweep.py` (`--require-trending`, `--mnq-orb-stop-ticks`) |

**ORB/VWAP sweep verdict (IOC, NY-only):** No demo-ready lane from timeframe + clock window alone. A weak **10m ORB reclaim** slice without trending does **not** survive the trending filter; wider ORB stops do not flip requested windows positive. **15m** (audit-aligned) stays negative in those windows. **VWAP** negative all cells with very low fill rate.

**4HR cell (separate from ORB/VWAP):** Historical benchmark on consumed artifact dates is saved in the evidence folder above; forward collection spec is **APPROVED** for sessions from **2026-10-12** — still **not collected**, **not deployed**. Demo wiring on the research branch shadows 3-2-2 fills.

### Unfinished before DEMO (Cursor scope)

1. Independent review / operator GO on #1208–#1210 (and #1211 on its own merits) — no merge for profitability claims. **#1208 / #1209:** Grok review fixes (operator: AFS-0212 / AFS-0213) still pending before fill-sensitive replays.
2. **Step 4 parity (done 2026-10-10):** `docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md` — guarded DEMO ≠ 1m pre-armed lead; forward/demo must label **5m IOC8 wide-stop** or complete 1m observer prereg first.
3. **Track B readiness (done 2026-10-10):** `docs/futures-track-b-forward-readiness-2026-10-10.md` — deploy **research** SHA for **400-tick** forward; do not score E-2026-10-09 on 300-tick `main`.
4. **MNQ 4HR parity** — research branch wiring matches Track B prereg; collection still blocked until gates in readiness doc.
5. Push reviewed SHA and **explicit** controlled deploy if operator wants Track B on box — checklist in Track B readiness doc; not done in this workstream.
6. Do **not** re-open gzip census, ORB/VWAP sweeps, or #1208/#1209 CI reads unless heads or corpora drift (see `docs/agent-work-state.md` on branch `research/4hr-mnq-400-forward-20261009`).

## Appendix — Claude options ETF expression (2026-10-10)

Append-only. **Separate lane** from futures DEMO work above; does **not** change Grok’s futures verdict or Strategy Inventory. Full narrative, tables, and pass/fail gates for paper: branch `claude/options-time-exit-research-20261010`, file [`docs/options-etf-expression-research-2026-10-10.md`](https://github.com/imanisworld/autonomous-futures-system/blob/claude/options-time-exit-research-20261010/docs/options-etf-expression-research-2026-10-10.md) (HEAD **`a30f0a6`** at Cursor verify). **Not merged** into `main` or #1211.

**Verdict (options as profit system): NOT PROVEN / NOT DEMO-READY.** Aligns with Grok **Options V1 REJECT** on scanner geometry; adds a **hypothesis** that 4HR direction-to-close may be expressible as defined-risk QQQ/SPY ~7DTE held to session close — **not** validated for live/demo.

**Reconciliation with futures lane (conceptual, not a combined strategy):**

- Same underlying idea as gzip 4HR “hold to EOD without stop”: direction often works by the close while account-sized stops fail intraday noise (Claude §3a; Cursor 4HR cell on research branch).
- **Futures forward path** on box remains **5m IOC wide-stop collector** / prereg on `research/4hr-mnq-400-forward-20261009` — **not** ETF options and **not** 1m pre-armed DEMO wiring.
- **Options expression** is paper-only tracker `scripts/options_4hr_etf_forward.py` on the Claude branch (Polygon bars + canonical `advance_4hr_retrigger`); box has **no** enabled MNQ 4HR strategy in main engine per handoff §5.6.

**Cursor verified this session (remote branch only):** handoff doc exists at cited SHA; tracker imports same state machine as `edge_decomposition_audit`; Claude-reported **parity Jan–Jun 2026: 41/41** and **OOS Jul 24–Oct 9 2026: 9 trades, −$384** are **not independently re-run here** — treat numbers as Claude evidence until a breaker re-executes.

**Pending on Claude branch (§6):** 1% OTM ~7DTE QQQ/SPY; scanner 413-setup time-exit re-sim — update the linked doc when complete; do not promote from partial OOS.

**Operator hygiene:** rotate **Polygon** API key if it was exposed in chat (Claude note); no broker action from this research.

### Explicit non-actions (Cursor)

No merge, deploy, VPS change, broker order, demo/live activation, strategy-status edit, or sealed MGC forward peek.
