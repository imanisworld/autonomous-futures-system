# Futures Research — Resume Checkpoint (2026-10-10)

**Purpose:** Persist the current investigation as a compact, source-only handoff. **No merge, deploy, trading activation, or strategy promotion.**

**Handling:** This public-repo checkpoint intentionally contains no per-strategy P&L, trade levels, or individual strategy-return tables. The operator's **private numeric handoff** is saved in the ChatGPT Library at `/Futures Research/Futures Research Handoff - 2026-10-10.md`. Do not reproduce that private report in this public repo.

## End goal and current verdict

The goal is a consistently positive-expectancy futures strategy under causal signals, executable prices, transaction costs, adverse slippage, valid market calendars, risk limits, and independent-period testing, followed by an **explicitly authorized DEMO-only** trial. An engineering regression suite passing is **not** a profitable system.

**Current strategy verdict: NOT PROVEN / NOT DEMO-READY.** Recent exploratory intraday momentum, reversal, divergence, gap, breakout, and fixed-time hypotheses did not survive extra periods, concentration checks, or proper higher-timeframe anchoring. Do not tune repeatedly on the same rejected data to produce a green backtest.

## Source-only corrective work (open draft PRs, verified 2026-10-10)

| PR | Scope | Exact-head CI at checkpoint | Remaining |
|---|---|---|---|
| [#1208](https://github.com/imanisworld/autonomous-futures-system/pull/1208) | Fail-closed next-executable-reference research fills, conservative ambiguous-bar ordering, isolated from hash-pinned canonical broker | 8,664 passed, 8 skipped | Independent review; operator approval before merge |
| [#1209](https://github.com/imanisworld/autonomous-futures-system/pull/1209) | Actual-bracket R:R, structural stop-direction validation incl. runner exemption | 8,665 passed, 8 skipped | Independent review; no risk-floor change |
| [#1210](https://github.com/imanisworld/autonomous-futures-system/pull/1210) | Combined regression of fill and risk correctness | 8,688 passed, 8 skipped | Independent review; QA-only |
| [#1211](https://github.com/imanisworld/autonomous-futures-system/pull/1211) | Isolated complete-bar, gap, DST and dated-contract provenance validation across 5m/15m/30m/60m/4H/12H | 8,677 passed, 8 skipped | Independent review; not wired to frozen trials |

These CI numbers refer to the **tested heads before this documentation-only checkpoint**. Recheck exact head after changes. Do not assume Grok/Cursor independent testing is complete merely because the operator said they were running it.

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
