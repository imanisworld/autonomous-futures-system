# Edge decomposition audit — where does each strategy's edge disappear?

_Generated 2026-09-07T19:22:41+00:00 at `431154042`. Evidence only; no strategy, risk, replay, broker, config, or deployment change._

Every lane is pushed through one standardized waterfall. Each stage answers one question:

| Stage | Question | Mechanism |
|---|---|---|
| A raw signal | how often does the strategy's own detector fire, with no gates? | canonical state machine / `_try_*` predicate / research detector / saved shadow population |
| B time-exit control | does the signal carry direction at all? | next-bar-open entry, exit at 30/60/120 min and EOD close, no stop, 1-tick adverse each side |
| C documented bracket | does the strategy's own stop/target keep that direction? | real `PaperBroker`, legacy market fill at the plan price, pessimistic same-bar, day-only flatten where documented |
| D1 structural gates | which candidates does the risk architecture admit, path-independently? | the real `RiskEngine` structural checks (session, bracket, R:R, confluence, min target, max stop) on every raw candidate |
| D2 / D3 full engine | what does the executable system actually do, floors off / frozen? | isolated `ReplayEngine` (config-only isolation, permission gate off, 2026-07-27 evidence posture), each raw candidate anchored on its own journal bar |
| E IOC / costs | what survives production-matching fills? | real `PaperBroker` `ioc_limit` at the decision-bar close, 1/2/3-tick adverse |

Costs: $1.48 round-turn commission at the analysis layer plus PaperBroker's own adverse slippage on entry and stop exit. One contract. Walk-forward halves split at each lane's median candidate date.

## Summary — primary stage where the edge disappears

| Lane | Raw n | B: 60m mean $ (t) | B: EOD mean $ (t) | C (primary fill): net / PF | C: plan − resting Δ | D1: survivors | D1 survivors: net / PF | D2 floors-off: approved → filled, net | E: IOC 1-tick fill / net / PF | Primary failure stage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 4HR Re-Trigger MNQ | 81 | $27.40 (1.164) | $97.44 (2.456) | $3,069.60 / 1.77 | $1,203.54 | 4 (5%) | $-6.42 / 0.94 | 1 → 0, $0.00 | 50% / $-37.46 / 0.00 | **RISK_GATES_REMOVE_EDGE** |
| 4HR Re-Trigger MES | 76 | $2.98 (0.248) | $37.86 (1.968) | $167.98 / 1.07 | $-9.04 | 11 (14%) | $183.95 / 1.52 | 7 → 4, $-224.67 | 64% / $-277.63 / 0.00 | **RISK_GATES_REMOVE_EDGE** |
| 60M 3-2-2 First Live MNQ | 34 | $67.74 (1.777) | $-9.65 (-0.108) | $2,532.66 / 13.57 | $646.26 | 0 (0%) | $0.00 / — | 0 → 0, $0.00 | — / $0.00 / — | **RISK_GATES_REMOVE_EDGE** |
| 12HR Miyagi MNQ (causal stop) | 8 | $43.65 (1.336) | $228.08 (1.495) | $525.91 / 2.85 | $-174.48 | 0 (0%) | $0.00 / — | n/a (no engine concept) | — / $0.00 / — | **RISK_GATES_REMOVE_EDGE** |
| 12HR Miyagi MES (causal stop) | 10 | $-11.61 (-0.502) | $26.64 (0.623) | $33.97 / 1.09 | $-162.16 | 0 (0%) | $0.00 / — | n/a (no engine concept) | — / $0.00 / — | **SIGNAL_NOT_DIRECTIONAL** |
| ORB Reclaim MNQ | 611 | $-6.38 (-1.026) | $21.43 (1.112) | $-3,607.57 / 0.78 | $20,680.20 | 549 (90%) | $-2,898.37 / 0.80 | 260 → 97, $-758.56 | 33% / $-1,451.58 / 0.75 | **SIGNAL_NOT_DIRECTIONAL** |
| ORB Reclaim MES | 626 | $-8.21 (-2.812) | $-4.73 (-0.558) | $-1,840.62 / 0.89 | $9,839.16 | 492 (79%) | $-1,122.66 / 0.92 | 249 → 177, $89.30 | 62% / $-1,525.76 / 0.84 | **SIGNAL_NOT_DIRECTIONAL** |
| ORB Breakout MNQ (source) | 710 | $-12.11 (-1.668) | $-12.15 (-0.694) | $-2,427.48 / 0.50 | $22,953.64 | 680 (96%) | $-2,302.22 / 0.49 | 111 → 34, $-393.32 | 10% / $-667.12 / 0.60 | **SIGNAL_NOT_DIRECTIONAL** |
| ORB Breakout MNQ (inverted lane) | 710 | $7.15 (0.985) | $7.19 (0.411) | $-3,635.30 / 0.21 | $-11,154.44 | 680 (96%) | $-3,439.06 / 0.20 | 111 → 34, $292.68 | 16% / $-176.98 / 0.86 | **BRACKET_DESTROYS_EDGE** |
| VWAP Hold MNQ | 4579 | $-1.03 (-0.449) | $8.23 (0.998) | $-3,942.70 / 0.67 | $102,861.76 | 4025 (88%) | $-2,895.50 / 0.65 | 122 → 28, $-118.92 | 6% / $-594.22 / 0.82 | **SIGNAL_NOT_DIRECTIONAL** |
| Transition failed-breakdown reclaim MNQ (full corpus) | 3292 | $2.69 (1.203) | $6.88 (0.703) | $-10,768.34 / 0.80 | $-209.39 | 11 (0%) | $-166.03 / 0.23 | n/a (no engine concept) | 100% / $-150.03 / 0.25 | **BRACKET_DESTROYS_EDGE** |
| Transition failed-breakdown reclaim MNQ (May-Jul 2026 audit set, re-anchored) | 299 | $-1.67 (-0.156) | $3.08 (0.084) | $-1,306.12 / 0.82 | $23.60 | 1 (0%) | $-25.98 / 0.00 | n/a (no engine concept) | 100% / $-24.48 / 0.00 | **BRACKET_DESTROYS_EDGE** |
| Transition failed-breakdown reclaim MES (Apr-Jul 2026 set, re-anchored) | 405 | $1.81 (0.485) | $22.56 (2.213) | $-1,327.32 / 0.72 | $66.21 | 0 (0%) | $0.00 / — | n/a (no engine concept) | — / $0.00 / — | **BRACKET_DESTROYS_EDGE** |

Primary-stage rule (mechanical, first failing stage wins): mean net $ not positive at a majority of the available control horizons → `SIGNAL_NOT_DIRECTIONAL`; documented bracket (primary fill model) net ≤ 0 or PF ≤ 1 → `BRACKET_DESTROYS_EDGE`; structural survivors < 50% of raw or survivors' bracket P&L not positive → `RISK_GATES_REMOVE_EDGE`; IOC 1-tick on survivors not positive or fill rate < 50% → `FILL_MODEL_REMOVES_EDGE`; IOC 3-tick not positive → `SURVIVES_BUT_COST_FRAGILE`; else `SURVIVES_PIPELINE`.

## Reading the waterfall

The question this audit was asked to settle: **are the signals bad, or is the execution / risk / stop architecture systematically removing edge from otherwise useful signals?** The answer is not one system-wide failure. The lanes split into three families with different first failing stages, and the family boundary is the strategy's *entry style*, not its instrument or its author.

### Binding validations (read before any interpretation)

- 4HR MNQ stage C (plan fill, day-only exit, 1 tick, $1.48) reproduces #334's standalone result to the cent: **+$3,069.60, PF 1.774, H1 +$1,794.80 / H2 +$1,274.80, 80 resolved**.
- 4HR MNQ stage D2 (isolated engine) reproduces #372's disposition vector exactly: **38** `MARKET_CONDITION_NOT_TRENDING` / **23** signal-layer (19 `RR_BELOW_MINIMUM` + 4 `WEAK_BAR_CLOSE`) / **11** `stop_too_wide` / **8** `ENTRY_DETACHED_FROM_PRICE` / **1** approved (2025-09-24). Under the production `ioc_limit` leg that single admitted trade is `ENTRY_NOT_FILLED`. 4HR MES: 7 approved attempts, matching #372's 7-fill baseline.
- 3-2-2 stage E (32-tick IOC, 1 tick) reproduces #340's honest-fill population: **20-21 fills of 34, 19W/2L**.
- Miyagi MNQ: **0 of 8** causal-stop triggers inside the 120-tick cap, matching #366.
- The re-anchored May-Jul 2026 transition set reproduces the 2026-07-08 expectancy audit: **−$1,306 / PF 0.82 / WR 58%** here vs −$1,640 / PF 0.80 / WR 56% there (12 bar-data outliers dropped, cost convention differs).

### Family 1 — close-confirmed level predicates: the signals never carried direction

`orb_reclaim` (MNQ, MES), `orb_breakout` (MNQ, and its inverted lane), `vwap_hold` (MNQ). These predicates confirm at bar close against a level (ORB high, VWAP) that the bar has already traded through, and their documented entry is *at that level*.

- **Stage B is flat or negative at every horizon.** Next-bar-open entries with no stop return ≈ $0/trade for ORB Reclaim MNQ and ORB Breakout MNQ, and are significantly negative for ORB Reclaim MES (t = −2.8 at 30 minutes). VWAP Hold: 1 of 4 horizons positive, t ≤ 1.2 on n = 4,579.
- **Every historical positive result for this family was a fill-price artifact.** Filling at the plan level (the legacy "fills assumed" model every early study used) versus a resting order at that same level on the next bar changes net P&L by **+$20,680 (ORB Reclaim MNQ), +$9,839 (ORB Reclaim MES), +$22,954 (ORB Breakout MNQ), +$102,862 (VWAP Hold MNQ)**. With honest resting fills all four are net negative with PF 0.50–0.89, before any gate is applied.
- The risk gates admit 79–96% of these candidates (stage D1) and the admitted set is still negative. The IOC leg then fills 6–33% of them (ORB Breakout 11%, VWAP Hold 6%) and the filled set is still negative. **Gates and fills are not removing edge from this family; there was no edge to remove**, which is why every honest-fill closure (#346, #349, #368, the VWAP audits) kept landing negative regardless of how the account was isolated.

### Family 2 — armed-trigger day strategies: real signal, real bracket, removed by the stop architecture

`strat_4hr_retrigger` (MNQ), `strat_322_first_live` (MNQ), `strat_12hr_miyagi` (MNQ, causal stop). These arm a level before the open and enter on the bar that trades through it.

- **Stage B is positive at every horizon, with t ≈ 2.0–2.7 at the strategy's own holding horizon** (4HR EOD t = 2.49; 3-2-2 120-minute t = 2.06; Miyagi 120-minute t = 2.67). This is the only family with directional information in the signal bar.
- **The documented bracket keeps it**: plan-fill PF 1.77 / 13.5 / 2.85, and 4HR and 3-2-2 are positive in both chronological halves. The resting-order model (pessimistic for this family, since the level was already crossed on the signal bar) is also positive.
- **Production-matching IOC fills are fine**: 54–63% fill rates, filled sets at PF 2.0 (4HR MNQ, +$1,732), 12.2 (3-2-2, +$1,859), 2.4 (Miyagi MNQ).
- **Structural risk gates then remove 95–100% of candidates**: `stop_too_wide` (median documented stop 254 / 486 / 564 ticks against the 120-tick cap) and `rr_below_minimum` (median R:R 0.94 / 0.27 / 0.50 against 2.0). Structural survivors: 4 / 0 / 0. The bracket P&L of the candidates the gates *rejected* is the whole edge: +$3,076 (PF 1.80), +$2,531 (PF 13.5), +$526 (PF 2.85).
- The isolated engine confirms the same mechanism with the real gate ordering: 4HR MNQ 81 → 1 approved → 0 filled.

This family is the one the operator's hypothesis was about, and for it the hypothesis is correct: the risk architecture, not the signal, is the binding constraint. It does not follow that the cap should move. #372 already showed that lifting the 4HR cap admits 12 trades whose profit is 99.8% concentrated in two months at 2.7× the policy stop width; 3-2-2's 34 candidates need stops of 486 ticks median ($243/contract, 16% of the account). The decision these lanes need is a **risk-policy decision about wide-stop, low-R:R day strategies** (a separate sizing/stop budget, or an explicit "not compatible with this account" verdict), not another detector or fill study.

4HR MES and Miyagi MES are weaker versions of the same shape: weak or mixed signal (best t ≈ 2.0 / 0.8), marginal documented bracket (PF 1.07 / 1.09), same gate removal.

### Family 3 — transition failed-breakdown reclaim: the fixed bracket destroys a weak signal

On the full-corpus population (3,292 MNQ candidates, Polygon price basis) the long signal has a small positive forward drift at every horizon (mean +0.7 to +6.9 pts, best t = 1.86 at 60 minutes), but the fixed stop/target converts it into **PF 0.80, −$10,768** with resting fills; the re-anchored May-Jul MNQ and Apr-Jul MES audit sets show the same shape (PF 0.82 / 0.72). It is also structurally inadmissible as documented: 91% fail `rr_below_minimum`, 83% are graded WEAK/C on confluence, and it is RANGE/CHOP-conditioned by construction so it always fails `require_trending_condition`. The bracket is the first failing stage, but the signal (+2.7 pts mean at 60 minutes) is not strong enough to carry a 2R bracket even if the geometry were re-derived from its own volatility.

### What this means for the pipeline question

1. **Stop using plan-price fills as evidence for close-confirmed predicates.** The fill-assumption inflation ($10k–$100k per lane) is the single largest systematic distortion found; every stage-A "edge" in Family 1 disappears the moment a fill has to be earned. Any future study of a close-confirmed setup should start from the resting-order or IOC model.
2. **The stop-width cap and R:R floor are the binding constraint for the armed-trigger day strategies, and only for them.** That is a policy question with the concentration and account-risk caveats #372 documented, not an engine defect.
3. **Nothing in this audit indicates the IOC leg is destroying edge.** Where a signal survives to it, fill rates are 54–90% and the filled set keeps the sign of the bracket P&L.
4. **The isolated engine runs agree with the closures and add the ordering.** 3-2-2 is rejected at `TREND_STRENGTH_BELOW_REQUIRED` (26/34) and `ENTRY_DETACHED_FROM_PRICE` before the stop cap is ever reached — the cap (34/34 over 120 ticks) sits behind it, as #367's ceiling pass found. The frozen-floor runs trip their own 20% breaker on the same dates the closures recorded (ORB Reclaim MES 2025-12-11 per #368; ORB Breakout MNQ 2026-03-16 per #349). The inverted ORB lane is the one place where the gated engine population and the raw population disagree in sign: sign-flipping the engine's 23–34 gated fills gives +$271 / +$293 (PF 1.6–2.0), consistent with its PROMISING BUT UNPROVEN standing, while the 101 IOC fills across all 680 structurally admissible source candidates are −$177 (PF 0.86). The gates are doing real selection work for that lane; n is too small to say more, which is exactly the inventory's current verdict.
5. VWAP Hold's engine funnel (122 approved, 28 filled, −$119) is the all-session isolated population on the 313-day corrected corpus, not the NY-only 348-arm population the 2026-07-26 re-scoring used; it is reported for the pipeline picture, not as a re-verdict of that cell.

## 4HR Re-Trigger MNQ

`strat_4hr_retrigger` · MNQ · corpus `replay_corpus_v1_5m_4hr_audit` (2024-07-02 → 2026-06-26, 5m) · source: state_machine_4hr · day-only: True · walk-forward boundary 2025-06-30

### A. Raw signal

- candidates: **81** (81 campaigns); direction {'SHORT': 47, 'LONG': 34}; sessions {'new_york': 81}; 2024-07-09 → 2026-06-19
- documented stop width (ticks): median 254.0, p90 610.0, max 1229.0; policy cap 120 → **93% of raw candidates exceed the cap**; median R:R 0.937

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 81 | 11.95 | -0.75 | 49% | 1.288 | $22.42 | 1.42 | 77.54 / 56.003 |
| 60m | 81 | 14.44 | 16.50 | 57% | 1.227 | $27.40 | 1.41 | 96.253 / 71.457 |
| 120m | 81 | 24.60 | 21.00 | 59% | 1.614 | $47.72 | 1.60 | 121.818 / 87.219 |
| EOD | 79 | 49.46 | 31.75 | 62% | 2.493 | $97.44 | 2.14 | 165.411 / 114.066 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) **(primary)** | 81 | 81 | 100% | 80 | 61% | $3,069.60 | $38.37 | 1.77 | $908.30 | 33% | $1,794.80 / $1,274.80 | True | {'STOP_HIT': 31, 'TARGET_HIT': 47, 'DAY_ONLY_FLATTEN': 2}  |
| resting stop order at the level, next bar | 81 | 54 | 67% | 53 | 64% | $1,866.06 | $35.21 | 1.66 | $880.44 | 41% | $969.08 / $896.98 | True | {'TARGET_HIT': 32, 'STOP_HIT': 19, 'DAY_ONLY_FLATTEN': 2} {'ENTRY_NOT_TRIGGERED': 17, 'ENTRY_BRACKET_INVALID_AT_FILL': 10} |

- entry style `armed_trigger` → primary fill model `plan_fill`; fill-assumption inflation (plan − resting net): **$1,203.54**; skipped while a position was open: plan 0 / resting 0; EOD-bar-missing: 1

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: stop_too_wide 75, rr_below_minimum 61, min_confluence_grade 12, target_too_close 8
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 38, ENTRY_DETACHED_FROM_PRICE 14
- confluence grades: {'B': 6, 'A+': 51, 'WEAK': 9, 'A': 12, 'C': 3}; market conditions at signal: {'RANGE_BOUND': 38, 'TRENDING': 43}
- survivors of risk-structural checks: **4 / 81 (5%)**; survivors of risk + market-condition + entry-attached: 1 (1%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=4, net **$-6.42**, PF 0.94, both halves False
- bracket P&L of all-gate survivors: n=1, net $102.02, PF ∞
- bracket P&L of the structurally REJECTED candidates: n=76, net $3,076.02, PF 1.80 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 38, SIGNAL:RR_BELOW_MINIMUM 19, RISK:stop_too_wide 11, SIGNAL:ENTRY_DETACHED_FROM_PRICE 8, SIGNAL:WEAK_BAR_CLOSE 4, IOC_NOT_FILLED 1
- stages: {'signal': 69, 'risk': 11, 'fill': 1}; approved attempts 1 → IOC filled 0
- filled P&L: n=0, WR —, net **$0.00**, PF —, H1 $0.00 / H2 $0.00
- engine totals regardless of anchoring: 1 attempts, 0 filled, 0 resolved, net $0.00

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 38, SIGNAL:RR_BELOW_MINIMUM 19, RISK:stop_too_wide 11, SIGNAL:ENTRY_DETACHED_FROM_PRICE 8, SIGNAL:WEAK_BAR_CLOSE 4, IOC_NOT_FILLED 1
- stages: {'signal': 69, 'risk': 11, 'fill': 1}; approved attempts 1 → IOC filled 0
- filled P&L: n=0, WR —, net **$0.00**, PF —, H1 $0.00 / H2 $0.00
- engine totals regardless of anchoring: 1 attempts, 0 filled, 0 resolved, net $0.00

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 81 | 44 | 54% | 43 | 54% | $1,731.86 | 2.00 | True |
| 1 tick | D1 survivors | 4 | 2 | 50% | 2 | 0% | $-37.46 | 0.00 | False |
| 2 tick | all raw | 81 | 44 | 54% | 43 | 54% | $1,700.36 | 1.97 | True |
| 2 tick | D1 survivors | 4 | 2 | 50% | 2 | 0% | $-39.46 | 0.00 | False |
| 3 tick | all raw | 81 | 44 | 54% | 43 | 54% | $1,668.86 | 1.94 | True |
| 3 tick | D1 survivors | 4 | 2 | 50% | 2 | 0% | $-41.46 | 0.00 | False |

**Primary failure stage: `RISK_GATES_REMOVE_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "4/4", "signal_best_t_stat": 2.49, "documented_bracket_positive": true, "structural_survivor_share": 0.049, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.5}`

## 4HR Re-Trigger MES

`strat_4hr_retrigger` · MES · corpus `replay_corpus_v1_5m_4hr_audit` (2024-07-02 → 2026-06-26, 5m) · source: state_machine_4hr · day-only: True · walk-forward boundary 2025-06-13

### A. Raw signal

- candidates: **76** (76 campaigns); direction {'SHORT': 41, 'LONG': 35}; sessions {'new_york': 76}; 2024-07-05 → 2026-06-24
- documented stop width (ticks): median 46.0, p90 130.0, max 183.0; policy cap 60 → **37% of raw candidates exceed the cap**; median R:R 1.148

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 76 | -0.33 | -0.62 | 47% | -0.193 | $-3.12 | 0.89 | 11.174 / 11.944 |
| 60m | 76 | 0.89 | 2.75 | 57% | 0.371 | $2.98 | 1.08 | 16.451 / 15.056 |
| 120m | 76 | 2.13 | -0.25 | 47% | 0.744 | $9.19 | 1.21 | 21.431 / 18.941 |
| EOD | 74 | 7.87 | 7.88 | 61% | 2.045 | $37.86 | 1.80 | 31.784 / 25.922 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) **(primary)** | 76 | 75 | 99% | 74 | 53% | $167.98 | $2.27 | 1.07 | $663.05 | 33% | $802.97 / $-634.99 | False | {'TARGET_HIT': 37, 'STOP_HIT': 33, 'DAY_ONLY_FLATTEN': 4} {'ENTRY_BRACKET_INVALID_AT_FILL': 1} |
| resting stop order at the level, next bar | 76 | 51 | 67% | 51 | 55% | $177.02 | $3.47 | 1.12 | $397.79 | 44% | $549.25 / $-372.23 | False | {'TARGET_HIT': 27, 'STOP_HIT': 20, 'DAY_ONLY_FLATTEN': 4} {'ENTRY_NOT_TRIGGERED': 17, 'ENTRY_BRACKET_INVALID_AT_FILL': 8} |

- entry style `armed_trigger` → primary fill model `plan_fill`; fill-assumption inflation (plan − resting net): **$-9.04**; skipped while a position was open: plan 0 / resting 0; EOD-bar-missing: 1

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: rr_below_minimum 54, target_too_close 41, stop_too_wide 28, min_confluence_grade 17
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 42, ENTRY_DETACHED_FROM_PRICE 9
- confluence grades: {'A': 7, 'WEAK': 17, 'A+': 50, 'B': 2}; market conditions at signal: {'RANGE_BOUND': 42, 'TRENDING': 34}
- survivors of risk-structural checks: **11 / 76 (14%)**; survivors of risk + market-condition + entry-attached: 7 (9%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=10, net **$183.95**, PF 1.52, both halves False
- bracket P&L of all-gate survivors: n=7, net $167.14, PF 1.59
- bracket P&L of the structurally REJECTED candidates: n=64, net $-15.97, PF 0.99 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 42, SIGNAL:RR_BELOW_MINIMUM 16, SIGNAL:ENTRY_DETACHED_FROM_PRICE 5, FILLED:LOSS 4, IOC_NOT_FILLED 3, RISK:stop_too_wide 2, RISK:target_too_close 2, SIGNAL:WEAK_BAR_CLOSE 2
- stages: {'signal': 65, 'risk': 4, 'filled': 4, 'fill': 3}; approved attempts 7 → IOC filled 4
- filled P&L: n=4, WR 0%, net **$-224.67**, PF 0.00, H1 $-62.73 / H2 $-161.94
- engine totals regardless of anchoring: 7 attempts, 4 filled, 4 resolved, net $-224.67

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 42, SIGNAL:RR_BELOW_MINIMUM 16, SIGNAL:ENTRY_DETACHED_FROM_PRICE 5, FILLED:LOSS 4, IOC_NOT_FILLED 3, RISK:stop_too_wide 2, RISK:target_too_close 2, SIGNAL:WEAK_BAR_CLOSE 2
- stages: {'signal': 65, 'risk': 4, 'filled': 4, 'fill': 3}; approved attempts 7 → IOC filled 4
- filled P&L: n=4, WR 0%, net **$-224.67**, PF 0.00, H1 $-62.73 / H2 $-161.94
- engine totals regardless of anchoring: 7 attempts, 4 filled, 4 resolved, net $-224.67

### E. IOC fills and cost stress (tolerance 16 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 76 | 51 | 67% | 50 | 38% | $-346.50 | 0.75 | False |
| 1 tick | D1 survivors | 11 | 7 | 64% | 6 | 0% | $-277.63 | 0.00 | False |
| 2 tick | all raw | 76 | 50 | 66% | 49 | 39% | $-440.02 | 0.70 | False |
| 2 tick | D1 survivors | 11 | 7 | 64% | 6 | 0% | $-291.38 | 0.00 | False |
| 3 tick | all raw | 76 | 50 | 66% | 49 | 37% | $-533.77 | 0.65 | False |
| 3 tick | D1 survivors | 11 | 7 | 64% | 6 | 0% | $-305.13 | 0.00 | False |

**Primary failure stage: `RISK_GATES_REMOVE_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "3/4", "signal_best_t_stat": 2.04, "documented_bracket_positive": true, "structural_survivor_share": 0.145, "survivors_bracket_positive": true, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.636}`

## 60M 3-2-2 First Live MNQ

`strat_322_first_live` · MNQ · corpus `replay_corpus_v1_5m_4hr_audit` (2024-07-02 → 2026-06-26, 5m) · source: state_machine_322 · day-only: True · walk-forward boundary 2025-05-01

### A. Raw signal

- candidates: **34** (34 campaigns); direction {'SHORT': 17, 'LONG': 17}; sessions {'new_york': 34}; 2024-08-02 → 2026-06-11
- documented stop width (ticks): median 486.0, p90 878.0, max 1471.0; policy cap 120 → **100% of raw candidates exceed the cap**; median R:R 0.27

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 34 | 27.22 | 3.00 | 56% | 1.723 | $52.96 | 2.56 | 72.243 / 44.118 |
| 60m | 34 | 34.61 | 23.50 | 59% | 1.816 | $67.74 | 2.39 | 96.743 / 53.75 |
| 120m | 34 | 47.97 | 36.00 | 59% | 2.058 | $94.46 | 2.77 | 125.831 / 71.875 |
| EOD | 33 | -4.08 | -24.00 | 48% | -0.091 | $-9.65 | 0.95 | 170.909 / 146.5 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) **(primary)** | 34 | 33 | 97% | 33 | 97% | $2,532.66 | $76.75 | 13.57 | $201.48 | 41% | $1,383.34 / $1,149.32 | True | {'TARGET_HIT': 31, 'DAY_ONLY_FLATTEN': 1, 'STOP_HIT': 1} {'ENTRY_BRACKET_INVALID_AT_FILL': 1} |
| resting stop order at the level, next bar | 34 | 20 | 59% | 20 | 100% | $1,886.40 | $94.32 | ∞ | $0.00 | 50% | $941.68 / $944.72 | True | {'TARGET_HIT': 19, 'DAY_ONLY_FLATTEN': 1} {'ENTRY_BRACKET_INVALID_AT_FILL': 9, 'ENTRY_NOT_TRIGGERED': 5} |

- entry style `armed_trigger` → primary fill model `plan_fill`; fill-assumption inflation (plan − resting net): **$646.26**; skipped while a position was open: plan 0 / resting 0; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: rr_below_minimum 34, stop_too_wide 34, target_too_close 11, min_confluence_grade 6
- signal-layer structural failures: ENTRY_DETACHED_FROM_PRICE 9, MARKET_CONDITION_NOT_TRADABLE 1
- confluence grades: {'A+': 22, 'WEAK': 4, 'C': 2, 'A': 5, 'B': 1}; market conditions at signal: {'TRENDING': 7, 'RANGE_BOUND': 26, 'DEAD': 1}
- survivors of risk-structural checks: **0 / 34 (0%)**; survivors of risk + market-condition + entry-attached: 0 (0%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=0, net **$0.00**, PF —, both halves None
- bracket P&L of all-gate survivors: n=0, net $0.00, PF —
- bracket P&L of the structurally REJECTED candidates: n=33, net $2,532.66, PF 13.57 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:TREND_STRENGTH_BELOW_REQUIRED 26, SIGNAL:ENTRY_DETACHED_FROM_PRICE 5, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 1, SIGNAL:WEAK_BAR_CLOSE 1, SIGNAL:RR_BELOW_MINIMUM 1
- stages: {'signal': 34}; approved attempts 0 → IOC filled 0
- filled P&L: n=0, WR —, net **$0.00**, PF —, H1 $0.00 / H2 $0.00
- engine totals regardless of anchoring: 0 attempts, 0 filled, 0 resolved, net $0.00

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:TREND_STRENGTH_BELOW_REQUIRED 26, SIGNAL:ENTRY_DETACHED_FROM_PRICE 5, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 1, SIGNAL:WEAK_BAR_CLOSE 1, SIGNAL:RR_BELOW_MINIMUM 1
- stages: {'signal': 34}; approved attempts 0 → IOC filled 0
- filled P&L: n=0, WR —, net **$0.00**, PF —, H1 $0.00 / H2 $0.00
- engine totals regardless of anchoring: 0 attempts, 0 filled, 0 resolved, net $0.00

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 34 | 20 | 59% | 20 | 95% | $1,859.40 | 12.17 | True |
| 1 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 2 tick | all raw | 34 | 20 | 59% | 20 | 95% | $1,848.90 | 12.04 | True |
| 2 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 3 tick | all raw | 34 | 20 | 59% | 20 | 95% | $1,838.40 | 11.91 | True |
| 3 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |

**Primary failure stage: `RISK_GATES_REMOVE_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "3/4", "signal_best_t_stat": 2.06, "documented_bracket_positive": true, "structural_survivor_share": 0.0, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.0}`

## 12HR Miyagi MNQ (causal stop)

`strat_12hr_miyagi` · MNQ · corpus `replay_corpus_v1_5m_4hr_audit` (2024-07-02 → 2026-06-26, 5m) · source: research_miyagi · day-only: True · walk-forward boundary 2025-02-27

Extraction audit: `{"date_missing": [], "detector_candidates": 15, "invalid_bracket": [], "source": "docs/strategy-rules/evidence_12hr_miyagi/mnq_results.json", "stop_missing": [], "trigger_not_hit": ["2024-10-23", "2024-12-11", "2025-03-06", "2025-05-23", "2025-09-25", "2025-12-18", "2026-01-14"]}`

### A. Raw signal

- candidates: **8** (8 campaigns); direction {'SHORT': 6, 'LONG': 2}; sessions {'new_york': 8}; 2024-08-22 → 2026-02-11
- documented stop width (ticks): median 564.0, p90 805.0, max 805.0; policy cap 120 → **100% of raw candidates exceed the cap**; median R:R 0.503

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 8 | 13.78 | 3.25 | 62% | 0.484 | $26.08 | 1.58 | 87.781 / 40.625 |
| 60m | 8 | 22.56 | 22.50 | 75% | 1.381 | $43.65 | 3.00 | 95.531 / 49.625 |
| 120m | 8 | 60.84 | 42.25 | 88% | 2.669 | $120.21 | 14.00 | 136.375 / 50.594 |
| EOD | 8 | 114.78 | 34.25 | 75% | 1.505 | $228.08 | 6.14 | 208.188 / 86.969 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) **(primary)** | 8 | 8 | 100% | 8 | 88% | $525.91 | $65.74 | 2.85 | $284.48 | 84% | $-56.42 / $582.33 | False | {'TARGET_HIT': 7, 'STOP_HIT': 1}  |
| resting stop order at the level, next bar | 8 | 7 | 88% | 7 | 100% | $700.39 | $100.06 | ∞ | $0.00 | 83% | $194.31 / $506.08 | True | {'TARGET_HIT': 7} {'ENTRY_NOT_TRIGGERED': 1} |

- entry style `armed_trigger` → primary fill model `plan_fill`; fill-assumption inflation (plan − resting net): **$-174.48**; skipped while a position was open: plan 0 / resting 0; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: rr_below_minimum 8, stop_too_wide 8
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 6
- confluence grades: {'A+': 6, 'A': 2}; market conditions at signal: {'RANGE_BOUND': 6, 'TRENDING': 2}
- survivors of risk-structural checks: **0 / 8 (0%)**; survivors of risk + market-condition + entry-attached: 0 (0%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=0, net **$0.00**, PF —, both halves None
- bracket P&L of all-gate survivors: n=0, net $0.00, PF —
- bracket P&L of the structurally REJECTED candidates: n=8, net $525.91, PF 2.85 — what the gates threw away

### D2/D3. Full engine — no enabled_concepts entry on main; structural stage D1 is the only gate evidence

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 8 | 5 | 62% | 5 | 80% | $266.60 | 2.36 | False |
| 1 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 2 tick | all raw | 8 | 5 | 62% | 5 | 80% | $263.60 | 2.33 | False |
| 2 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 3 tick | all raw | 8 | 5 | 62% | 5 | 80% | $260.60 | 2.31 | False |
| 3 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |

**Primary failure stage: `RISK_GATES_REMOVE_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "4/4", "signal_best_t_stat": 2.67, "documented_bracket_positive": true, "structural_survivor_share": 0.0, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.0}`

## 12HR Miyagi MES (causal stop)

`strat_12hr_miyagi` · MES · corpus `replay_corpus_v1_5m_4hr_audit` (2024-07-02 → 2026-06-26, 5m) · source: research_miyagi · day-only: True · walk-forward boundary 2025-04-30

Extraction audit: `{"date_missing": [], "detector_candidates": 19, "invalid_bracket": [], "source": "docs/strategy-rules/evidence_12hr_miyagi/mes_results.json", "stop_missing": [], "trigger_not_hit": ["2024-07-12", "2024-07-17", "2024-09-19", "2024-10-23", "2025-03-06", "2025-05-23", "2025-09-25", "2026-01-14", "2026-02-05"]}`

### A. Raw signal

- candidates: **10** (10 campaigns); direction {'SHORT': 7, 'LONG': 3}; sessions {'new_york': 10}; 2024-08-22 → 2026-04-10
- documented stop width (ticks): median 155.5, p90 258.0, max 258.0; policy cap 60 → **80% of raw candidates exceed the cap**; median R:R 0.344

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 10 | -2.20 | 0.50 | 50% | -0.541 | $-12.48 | 0.64 | 10.45 / 10.525 |
| 60m | 10 | -2.02 | -0.25 | 50% | -0.438 | $-11.61 | 0.66 | 13.6 / 12.35 |
| 120m | 10 | 6.45 | 3.50 | 70% | 0.844 | $30.77 | 1.98 | 22.6 / 14.6 |
| EOD | 10 | 5.62 | 6.12 | 70% | 0.657 | $26.64 | 1.64 | 28.55 / 17.025 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) **(primary)** | 10 | 10 | 100% | 10 | 70% | $33.97 | $3.40 | 1.09 | $267.10 | 86% | $-78.01 / $111.98 | False | {'TARGET_HIT': 6, 'STOP_HIT': 3, 'DAY_ONLY_FLATTEN': 1}  |
| resting stop order at the level, next bar | 10 | 6 | 60% | 6 | 83% | $196.13 | $32.69 | 4.95 | $49.60 | 100% | $171.19 / $24.94 | True | {'TARGET_HIT': 5, 'STOP_HIT': 1} {'ENTRY_NOT_TRIGGERED': 4} |

- entry style `armed_trigger` → primary fill model `plan_fill`; fill-assumption inflation (plan − resting net): **$-162.16**; skipped while a position was open: plan 0 / resting 0; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: rr_below_minimum 10, target_too_close 8, stop_too_wide 8
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 5
- confluence grades: {'A+': 9, 'A': 1}; market conditions at signal: {'RANGE_BOUND': 5, 'TRENDING': 5}
- survivors of risk-structural checks: **0 / 10 (0%)**; survivors of risk + market-condition + entry-attached: 0 (0%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=0, net **$0.00**, PF —, both halves None
- bracket P&L of all-gate survivors: n=0, net $0.00, PF —
- bracket P&L of the structurally REJECTED candidates: n=10, net $33.97, PF 1.09 — what the gates threw away

### D2/D3. Full engine — no enabled_concepts entry on main; structural stage D1 is the only gate evidence

### E. IOC fills and cost stress (tolerance 16 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 10 | 9 | 90% | 9 | 67% | $144.18 | 1.46 | False |
| 1 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 2 tick | all raw | 10 | 9 | 90% | 9 | 67% | $129.18 | 1.40 | False |
| 2 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 3 tick | all raw | 10 | 9 | 90% | 9 | 67% | $114.18 | 1.35 | False |
| 3 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |

**Primary failure stage: `SIGNAL_NOT_DIRECTIONAL`** — flags `{"signal_positive_majority_of_horizons": false, "signal_positive_horizons": "2/4", "signal_best_t_stat": 0.84, "documented_bracket_positive": true, "structural_survivor_share": 0.0, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.0}`

## ORB Reclaim MNQ

`orb_reclaim` · MNQ · corpus `replay_corpus_v1_market_condition_fixed` (2025-07-23 → 2026-07-23, 15m) · source: predicate · day-only: False · walk-forward boundary 2026-01-27

### A. Raw signal

- candidates: **611** (335 campaigns); direction {'LONG': 611}; sessions {'new_york': 278, 'london': 333}; 2025-07-24 → 2026-07-22
- documented stop width (ticks): median 80.0, p90 80.0, max 80.0; policy cap 120 → **0% of raw candidates exceed the cap**; median R:R 2.5

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 607 | 0.20 | -0.75 | 48% | 0.097 | $-1.08 | 0.97 | 33.769 / 36.447 |
| 60m | 599 | -2.45 | 3.00 | 53% | -0.788 | $-6.38 | 0.88 | 48.518 / 53.981 |
| 120m | 595 | 0.84 | 6.50 | 55% | 0.184 | $0.20 | 1.00 | 68.884 / 77.651 |
| EOD | 574 | 11.46 | 24.62 | 56% | 1.189 | $21.43 | 1.14 | 151.222 / 167.066 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 544 | 544 | 100% | 544 | 53% | $17,072.63 | $31.38 | 2.58 | $358.32 | 2% | $6,541.07 / $10,531.56 | True | {'STOP_HIT': 257, 'TARGET_HIT': 287}  |
| resting stop order at the level, next bar **(primary)** | 544 | 459 | 84% | 459 | 45% | $-3,607.57 | $-7.86 | 0.78 | $4,021.48 | 4% | $-1,617.03 / $-1,990.54 | False | {'STOP_HIT': 253, 'TARGET_HIT': 206} {'ENTRY_BRACKET_INVALID_AT_FILL': 83, 'ENTRY_NOT_TRIGGERED': 2} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$20,680.20**; skipped while a position was open: plan 67 / resting 67; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: min_confluence_grade 62
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 235, ENTRY_DETACHED_FROM_PRICE 84, MARKET_CONDITION_NOT_TRADABLE 37
- confluence grades: {'A+': 456, 'A': 42, 'B': 51, 'WEAK': 55, 'C': 7}; market conditions at signal: {'TRENDING': 339, 'RANGE_BOUND': 235, 'DEAD': 11, 'CHOPPY': 26}
- survivors of risk-structural checks: **549 / 611 (90%)**; survivors of risk + market-condition + entry-attached: 278 (46%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=419, net **$-2,898.37**, PF 0.80, both halves False
- bracket P&L of all-gate survivors: n=245, net $-1,627.60, PF 0.81
- bracket P&L of the structurally REJECTED candidates: n=40, net $-709.20, PF 0.57 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 240, IOC_NOT_FILLED 163, FILLED:LOSS 68, SIGNAL:ENTRY_DETACHED_FROM_PRICE 43, FILLED:WIN 29, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 26, NO_ENGINE_ROW_AT_BAR 26, SIGNAL:WEAK_BAR_CLOSE 16
- stages: {'fill': 163, 'signal': 325, 'filled': 97, 'skipped': 26}; approved attempts 260 → IOC filled 97
- filled P&L: n=97, WR 30%, net **$-758.56**, PF 0.77, H1 $-542.34 / H2 $-216.22
- engine totals regardless of anchoring: 260 attempts, 97 filled, 97 resolved, net $-758.56

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 244, RISK:max_drawdown 228, SIGNAL:ENTRY_DETACHED_FROM_PRICE 43, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 26, IOC_NOT_FILLED 25, SIGNAL:WEAK_BAR_CLOSE 17, FILLED:LOSS 16, NO_ENGINE_ROW_AT_BAR 7, FILLED:WIN 5
- stages: {'fill': 25, 'signal': 330, 'filled': 21, 'skipped': 7, 'path': 228}; approved attempts 46 → IOC filled 21
- filled P&L: n=21, WR 24%, net **$-337.33**, PF 0.55, H1 $-337.33 / H2 $0.00
- engine totals regardless of anchoring: 46 attempts, 21 filled, 21 resolved, net $-337.33; first drawdown halt {'date': '2025-09-29', 'bar_ts': '2025-09-29T07:15:00+00:00'}

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 571 | 190 | 33% | 190 | 27% | $-2,081.70 | 0.69 | False |
| 1 tick | D1 survivors | 519 | 171 | 33% | 171 | 29% | $-1,451.58 | 0.75 | False |
| 2 tick | all raw | 571 | 190 | 33% | 190 | 27% | $-2,242.20 | 0.67 | False |
| 2 tick | D1 survivors | 519 | 171 | 33% | 171 | 29% | $-1,594.08 | 0.73 | False |
| 3 tick | all raw | 571 | 190 | 33% | 190 | 27% | $-2,401.70 | 0.66 | False |
| 3 tick | D1 survivors | 519 | 171 | 33% | 171 | 29% | $-1,735.58 | 0.72 | False |

**Primary failure stage: `SIGNAL_NOT_DIRECTIONAL`** — flags `{"signal_positive_majority_of_horizons": false, "signal_positive_horizons": "2/4", "signal_best_t_stat": 1.19, "documented_bracket_positive": false, "structural_survivor_share": 0.899, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.329}`

## ORB Reclaim MES

`orb_reclaim` · MES · corpus `replay_corpus_v1_market_condition_fixed` (2025-07-23 → 2026-07-23, 15m) · source: predicate · day-only: False · walk-forward boundary 2026-01-22

### A. Raw signal

- candidates: **626** (346 campaigns); direction {'LONG': 626}; sessions {'london': 316, 'new_york': 310}; 2025-07-24 → 2026-07-22
- documented stop width (ticks): median 40.0, p90 40.0, max 40.0; policy cap 60 → **0% of raw candidates exceed the cap**; median R:R 2.5

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 623 | -1.16 | -1.00 | 42% | -2.828 | $-7.28 | 0.63 | 5.933 / 7.58 |
| 60m | 614 | -1.35 | -0.12 | 49% | -2.305 | $-8.21 | 0.71 | 8.765 / 10.761 |
| 120m | 606 | -0.81 | 0.50 | 51% | -0.946 | $-5.53 | 0.85 | 12.481 / 15.283 |
| EOD | 588 | -0.65 | 1.00 | 52% | -0.383 | $-4.73 | 0.94 | 26.877 / 32.195 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 444 | 444 | 100% | 444 | 42% | $7,998.54 | $18.01 | 1.65 | $414.43 | 3% | $3,509.73 / $4,488.81 | True | {'TARGET_HIT': 187, 'STOP_HIT': 257}  |
| resting stop order at the level, next bar **(primary)** | 444 | 432 | 97% | 432 | 41% | $-1,840.62 | $-4.26 | 0.89 | $2,592.90 | 4% | $-397.48 / $-1,443.14 | False | {'TARGET_HIT': 177, 'STOP_HIT': 255} {'ENTRY_BRACKET_INVALID_AT_FILL': 10, 'ENTRY_NOT_TRIGGERED': 2} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$9,839.16**; skipped while a position was open: plan 182 / resting 182; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: target_too_close 81, min_confluence_grade 60
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 267, MARKET_CONDITION_NOT_TRADABLE 32, ENTRY_DETACHED_FROM_PRICE 10
- confluence grades: {'A+': 481, 'WEAK': 54, 'B': 48, 'A': 37, 'C': 6}; market conditions at signal: {'RANGE_BOUND': 267, 'TRENDING': 327, 'DEAD': 7, 'CHOPPY': 25}
- survivors of risk-structural checks: **492 / 626 (79%)**; survivors of risk + market-condition + entry-attached: 259 (41%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=354, net **$-1,122.66**, PF 0.92, both halves False
- bracket P&L of all-gate survivors: n=186, net $-40.27, PF 0.99
- bracket P&L of the structurally REJECTED candidates: n=78, net $-717.96, PF 0.68 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 241, FILLED:LOSS 116, NO_ENGINE_ROW_AT_BAR 97, IOC_NOT_FILLED 72, FILLED:WIN 61, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 17, WAIT_POSITION_OPEN 9, SIGNAL:WEAK_BAR_CLOSE 7, SIGNAL:ENTRY_DETACHED_FROM_PRICE 6
- stages: {'signal': 271, 'filled': 177, 'path': 9, 'skipped': 97, 'fill': 72}; approved attempts 249 → IOC filled 177
- filled P&L: n=177, WR 34%, net **$89.30**, PF 1.01, H1 $-222.57 / H2 $311.87
- engine totals regardless of anchoring: 249 attempts, 177 filled, 177 resolved, net $89.30

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 258, RISK:max_drawdown 176, FILLED:LOSS 52, NO_ENGINE_ROW_AT_BAR 50, IOC_NOT_FILLED 29, FILLED:WIN 21, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 20, SIGNAL:WEAK_BAR_CLOSE 10, SIGNAL:ENTRY_DETACHED_FROM_PRICE 6, WAIT_POSITION_OPEN 4
- stages: {'signal': 294, 'filled': 73, 'path': 180, 'skipped': 50, 'fill': 29}; approved attempts 102 → IOC filled 73
- filled P&L: n=73, WR 29%, net **$-441.79**, PF 0.82, H1 $-441.79 / H2 $0.00
- engine totals regardless of anchoring: 102 attempts, 73 filled, 73 resolved, net $-441.79; first drawdown halt {'date': '2025-12-11', 'bar_ts': '2025-12-11T16:30:00+00:00'}

### E. IOC fills and cost stress (tolerance 16 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 463 | 307 | 66% | 307 | 32% | $-1,941.87 | 0.83 | False |
| 1 tick | D1 survivors | 384 | 237 | 62% | 237 | 32% | $-1,525.76 | 0.84 | False |
| 2 tick | all raw | 463 | 307 | 66% | 307 | 32% | $-2,555.60 | 0.78 | False |
| 2 tick | D1 survivors | 384 | 237 | 62% | 237 | 32% | $-1,997.00 | 0.79 | False |
| 3 tick | all raw | 463 | 307 | 66% | 307 | 32% | $-3,163.12 | 0.74 | False |
| 3 tick | D1 survivors | 384 | 237 | 62% | 237 | 32% | $-2,462.01 | 0.76 | False |

**Primary failure stage: `SIGNAL_NOT_DIRECTIONAL`** — flags `{"signal_positive_majority_of_horizons": false, "signal_positive_horizons": "0/4", "signal_best_t_stat": -0.38, "documented_bracket_positive": false, "structural_survivor_share": 0.786, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.617}`

## ORB Breakout MNQ (source)

`orb_breakout` · MNQ · corpus `replay_corpus_v1_market_condition_fixed` (2025-07-23 → 2026-07-23, 15m) · source: predicate · day-only: False · walk-forward boundary 2026-01-14

### A. Raw signal

- candidates: **710** (359 campaigns); direction {'SHORT': 342, 'LONG': 368}; sessions {'new_york': 339, 'london': 371}; 2025-07-24 → 2026-07-23
- documented stop width (ticks): median 50.0, p90 50.0, max 50.0; policy cap 120 → **0% of raw candidates exceed the cap**; median R:R 2.2

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 710 | -1.31 | -2.38 | 48% | -0.547 | $-4.11 | 0.91 | 43.524 / 42.986 |
| 60m | 710 | -5.32 | -3.38 | 48% | -1.464 | $-12.11 | 0.82 | 60.681 / 63.345 |
| 120m | 697 | -7.11 | -1.00 | 49% | -1.442 | $-15.69 | 0.83 | 80.146 / 89.834 |
| EOD | 689 | -5.33 | -9.50 | 48% | -0.61 | $-12.15 | 0.93 | 156.811 / 170.716 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 583 | 583 | 100% | 583 | 78% | $20,526.16 | $35.21 | 6.79 | $82.44 | 1% | $9,825.18 / $10,700.98 | True | {'STOP_HIT': 129, 'TARGET_HIT': 454}  |
| resting stop order at the level, next bar **(primary)** | 634 | 226 | 36% | 226 | 53% | $-2,427.48 | $-10.74 | 0.50 | $2,460.08 | 10% | $-1,066.72 / $-1,360.76 | False | {'STOP_HIT': 102, 'TARGET_HIT': 124} {'ENTRY_BRACKET_INVALID_AT_FILL': 408} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$22,953.64**; skipped while a position was open: plan 127 / resting 76; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: min_confluence_grade 30
- signal-layer structural failures: ENTRY_DETACHED_FROM_PRICE 437, MARKET_CONDITION_NOT_TRENDING 247
- confluence grades: {'A+': 635, 'B': 29, 'WEAK': 30, 'A': 16}; market conditions at signal: {'TRENDING': 463, 'RANGE_BOUND': 247}
- survivors of risk-structural checks: **680 / 710 (96%)**; survivors of risk + market-condition + entry-attached: 151 (21%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=214, net **$-2,302.22**, PF 0.49, both halves False
- bracket P&L of all-gate survivors: n=128, net $-1,523.44, PF 0.47
- bracket P&L of the structurally REJECTED candidates: n=12, net $-125.26, PF 0.54 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 247, SIGNAL:ENTRY_DETACHED_FROM_PRICE 188, SIGNAL:NO_CANDIDATE_IN_ENGINE 83, IOC_NOT_FILLED 77, SIGNAL:WEAK_BAR_CLOSE 64, FILLED:LOSS 24, NO_ENGINE_ROW_AT_BAR 13, FILLED:WIN 10, SIGNAL:EMA_STACK_NOT_ALIGNED 4
- stages: {'filled': 34, 'fill': 77, 'signal': 586, 'skipped': 13}; approved attempts 111 → IOC filled 34
- filled P&L: n=34, WR 29%, net **$-393.32**, PF 0.53, H1 $-170.64 / H2 $-222.68
- engine totals regardless of anchoring: 111 attempts, 34 filled, 34 resolved, net $-393.32

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 247, SIGNAL:ENTRY_DETACHED_FROM_PRICE 188, SIGNAL:NO_CANDIDATE_IN_ENGINE 86, SIGNAL:WEAK_BAR_CLOSE 64, IOC_NOT_FILLED 57, RISK:max_drawdown 31, FILLED:LOSS 17, NO_ENGINE_ROW_AT_BAR 9, FILLED:WIN 6, SIGNAL:EMA_STACK_NOT_ALIGNED 5
- stages: {'filled': 23, 'fill': 57, 'signal': 590, 'skipped': 9, 'path': 31}; approved attempts 80 → IOC filled 23
- filled P&L: n=23, WR 26%, net **$-339.04**, PF 0.43, H1 $-170.64 / H2 $-168.40
- engine totals regardless of anchoring: 80 attempts, 23 filled, 23 resolved, net $-339.04; first drawdown halt {'date': '2026-03-16', 'bar_ts': '2026-03-16T11:00:00+00:00'}

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 677 | 76 | 11% | 76 | 32% | $-736.98 | 0.59 | False |
| 1 tick | D1 survivors | 659 | 69 | 10% | 69 | 32% | $-667.12 | 0.60 | False |
| 2 tick | all raw | 677 | 76 | 11% | 76 | 32% | $-799.98 | 0.57 | False |
| 2 tick | D1 survivors | 659 | 69 | 10% | 69 | 32% | $-724.62 | 0.57 | False |
| 3 tick | all raw | 677 | 76 | 11% | 76 | 32% | $-861.98 | 0.55 | False |
| 3 tick | D1 survivors | 659 | 69 | 10% | 69 | 32% | $-781.12 | 0.55 | False |

**Primary failure stage: `SIGNAL_NOT_DIRECTIONAL`** — flags `{"signal_positive_majority_of_horizons": false, "signal_positive_horizons": "0/4", "signal_best_t_stat": -0.55, "documented_bracket_positive": false, "structural_survivor_share": 0.958, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.105}`

## ORB Breakout MNQ (inverted lane)

`orb_breakout` · MNQ · corpus `replay_corpus_v1_market_condition_fixed` (2025-07-23 → 2026-07-23, 15m) · source: predicate · day-only: False · walk-forward boundary 2026-01-14

### A. Raw signal

- candidates: **710** (359 campaigns); direction {'LONG': 342, 'SHORT': 368}; sessions {'new_york': 339, 'london': 371}; 2025-07-24 → 2026-07-23
- documented stop width (ticks): median 50.0, p90 50.0, max 50.0; policy cap 120 → **0% of raw candidates exceed the cap**; median R:R 2.2

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 710 | 0.31 | 1.38 | 51% | 0.13 | $-0.85 | 0.98 | 42.486 / 44.024 |
| 60m | 710 | 4.32 | 2.38 | 52% | 1.189 | $7.15 | 1.12 | 62.845 / 61.181 |
| 120m | 697 | 6.11 | 0.00 | 50% | 1.239 | $10.73 | 1.13 | 89.334 / 80.646 |
| EOD | 689 | 4.33 | 8.50 | 52% | 0.495 | $7.19 | 1.05 | 170.216 / 157.311 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 588 | 588 | 100% | 588 | 3% | $-14,789.74 | $-25.15 | 0.06 | $14,842.76 | 29% | $-7,109.24 / $-7,680.50 | False | {'TARGET_HIT': 17, 'STOP_HIT': 571}  |
| resting stop order at the level, next bar **(primary)** | 662 | 185 | 28% | 185 | 10% | $-3,635.30 | $-19.65 | 0.21 | $3,688.32 | 28% | $-1,723.16 / $-1,912.14 | False | {'TARGET_HIT': 18, 'STOP_HIT': 167} {'ENTRY_NOT_TRIGGERED': 477} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$-11,154.44**; skipped while a position was open: plan 122 / resting 48; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: min_confluence_grade 30
- signal-layer structural failures: ENTRY_DETACHED_FROM_PRICE 437, MARKET_CONDITION_NOT_TRENDING 247
- confluence grades: {'A+': 635, 'B': 29, 'WEAK': 30, 'A': 16}; market conditions at signal: {'TRENDING': 463, 'RANGE_BOUND': 247}
- survivors of risk-structural checks: **680 / 710 (96%)**; survivors of risk + market-condition + entry-attached: 151 (21%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=172, net **$-3,439.06**, PF 0.20, both halves False
- bracket P&L of all-gate survivors: n=74, net $-1,148.52, PF 0.34
- bracket P&L of the structurally REJECTED candidates: n=13, net $-196.24, PF 0.35 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 247, SIGNAL:ENTRY_DETACHED_FROM_PRICE 188, SIGNAL:NO_CANDIDATE_IN_ENGINE 83, IOC_NOT_FILLED 77, SIGNAL:WEAK_BAR_CLOSE 64, FILLED:LOSS 24, NO_ENGINE_ROW_AT_BAR 13, FILLED:WIN 10, SIGNAL:EMA_STACK_NOT_ALIGNED 4
- stages: {'filled': 34, 'fill': 77, 'signal': 586, 'skipped': 13}; approved attempts 111 → IOC filled 34
- filled P&L: n=34, WR 71%, net **$292.68**, PF 1.62, H1 $117.36 / H2 $175.32
- engine totals regardless of anchoring: 111 attempts, 34 filled, 34 resolved, net $-393.32
- P&L sign-flipped from the engine's un-mirrored fills (mirror lane); entry tolerance differs (8 ticks) so treat as approximate

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 247, SIGNAL:ENTRY_DETACHED_FROM_PRICE 188, SIGNAL:NO_CANDIDATE_IN_ENGINE 86, SIGNAL:WEAK_BAR_CLOSE 64, IOC_NOT_FILLED 57, RISK:max_drawdown 31, FILLED:LOSS 17, NO_ENGINE_ROW_AT_BAR 9, FILLED:WIN 6, SIGNAL:EMA_STACK_NOT_ALIGNED 5
- stages: {'filled': 23, 'fill': 57, 'signal': 590, 'skipped': 9, 'path': 31}; approved attempts 80 → IOC filled 23
- filled P&L: n=23, WR 74%, net **$270.96**, PF 1.98, H1 $117.36 / H2 $153.60
- engine totals regardless of anchoring: 80 attempts, 23 filled, 23 resolved, net $-339.04; first drawdown halt {'date': '2026-03-16', 'bar_ts': '2026-03-16T11:00:00+00:00'}
- P&L sign-flipped from the engine's un-mirrored fills (mirror lane); entry tolerance differs (8 ticks) so treat as approximate

### E. IOC fills and cost stress (tolerance 8 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 668 | 109 | 16% | 109 | 17% | $-194.32 | 0.86 | False |
| 1 tick | D1 survivors | 648 | 101 | 16% | 101 | 17% | $-176.98 | 0.86 | False |
| 2 tick | all raw | 666 | 112 | 17% | 112 | 17% | $-302.76 | 0.79 | False |
| 2 tick | D1 survivors | 647 | 102 | 16% | 102 | 17% | $-272.46 | 0.79 | False |
| 3 tick | all raw | 666 | 114 | 17% | 114 | 17% | $-412.22 | 0.73 | False |
| 3 tick | D1 survivors | 647 | 104 | 16% | 104 | 16% | $-372.92 | 0.73 | False |

**Primary failure stage: `BRACKET_DESTROYS_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "3/4", "signal_best_t_stat": 1.24, "documented_bracket_positive": false, "structural_survivor_share": 0.958, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.156}`

## VWAP Hold MNQ

`vwap_hold` · MNQ · corpus `replay_corpus_v1_market_condition_fixed` (2025-07-23 → 2026-07-23, 15m) · source: predicate · day-only: False · walk-forward boundary 2026-01-29

### A. Raw signal

- candidates: **4579**; direction {'SHORT': 4579}; sessions {'asian': 1759, 'london': 1274, 'new_york': 1546}; 2025-07-23 → 2026-07-23
- documented stop width (ticks): median 30.0, p90 30.0, max 30.0; policy cap 120 → **0% of raw candidates exceed the cap**; median R:R 3.0

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 4544 | -0.41 | -1.50 | 48% | -0.516 | $-2.30 | 0.94 | 37.54 / 34.505 |
| 60m | 4483 | 0.22 | -1.25 | 49% | 0.195 | $-1.03 | 0.98 | 53.735 / 48.735 |
| 120m | 4341 | 0.36 | -2.25 | 48% | 0.222 | $-0.75 | 0.99 | 76.39 / 67.086 |
| EOD | 3181 | 4.85 | -12.75 | 46% | 1.177 | $8.23 | 1.05 | 179.791 / 155.514 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 2928 | 2928 | 100% | 2928 | 85% | $98,919.06 | $33.78 | 13.66 | $87.40 | 0% | $47,810.64 / $51,108.42 | True | {'TARGET_HIT': 2481, 'STOP_HIT': 447}  |
| resting stop order at the level, next bar **(primary)** | 4033 | 815 | 20% | 815 | 54% | $-3,942.70 | $-4.84 | 0.67 | $4,084.48 | 3% | $-1,877.68 / $-2,065.02 | False | {'TARGET_HIT': 463, 'STOP_HIT': 352} {'ENTRY_BRACKET_INVALID_AT_FILL': 3214, 'ENTRY_NOT_TRIGGERED': 4} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$102,861.76**; skipped while a position was open: plan 1651 / resting 546; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: min_confluence_grade 554
- signal-layer structural failures: ENTRY_DETACHED_FROM_PRICE 3385, MARKET_CONDITION_NOT_TRENDING 1896, MARKET_CONDITION_NOT_TRADABLE 913
- confluence grades: {'C': 189, 'B': 899, 'WEAK': 365, 'A+': 2747, 'A': 379}; market conditions at signal: {'RANGE_BOUND': 1896, 'CHOPPY': 551, 'DEAD': 362, 'TRENDING': 1770}
- survivors of risk-structural checks: **4025 / 4579 (88%)**; survivors of risk + market-condition + entry-attached: 140 (3%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=574, net **$-2,895.50**, PF 0.65, both halves False
- bracket P&L of all-gate survivors: n=96, net $-597.62, PF 0.56
- bracket P&L of the structurally REJECTED candidates: n=241, net $-1,047.20, PF 0.70 — what the gates threw away

### D2. Full engine, survival floors off

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 2079, SIGNAL:ENTRY_DETACHED_FROM_PRICE 1111, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 726, SIGNAL:WEAK_BAR_CLOSE 397, SIGNAL:NO_CANDIDATE_IN_ENGINE 105, IOC_NOT_FILLED 94, SIGNAL:SIGNAL_BAR_VOLUME_TOO_LOW 29, FILLED:LOSS 18, FILLED:WIN 10, NO_ENGINE_ROW_AT_BAR 10
- stages: {'signal': 4447, 'fill': 94, 'filled': 28, 'skipped': 10}; approved attempts 122 → IOC filled 28
- filled P&L: n=28, WR 36%, net **$-118.92**, PF 0.74, H1 $-115.20 / H2 $-3.72
- engine totals regardless of anchoring: 122 attempts, 28 filled, 28 resolved, net $-118.92

### D3. Full engine, frozen production risk floors

- per-candidate dispositions at the candidate's own bar: SIGNAL:MARKET_CONDITION_NOT_TRENDING 2079, SIGNAL:ENTRY_DETACHED_FROM_PRICE 1111, SIGNAL:MARKET_CONDITION_NOT_TRADABLE 726, SIGNAL:WEAK_BAR_CLOSE 397, SIGNAL:NO_CANDIDATE_IN_ENGINE 105, IOC_NOT_FILLED 94, SIGNAL:SIGNAL_BAR_VOLUME_TOO_LOW 29, FILLED:LOSS 18, FILLED:WIN 10, NO_ENGINE_ROW_AT_BAR 10
- stages: {'signal': 4447, 'fill': 94, 'filled': 28, 'skipped': 10}; approved attempts 122 → IOC filled 28
- filled P&L: n=28, WR 36%, net **$-118.92**, PF 0.74, H1 $-115.20 / H2 $-3.72
- engine totals regardless of anchoring: 122 attempts, 28 filled, 28 resolved, net $-118.92

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 4342 | 337 | 8% | 337 | 37% | $-1,085.94 | 0.79 | False |
| 1 tick | D1 survivors | 3899 | 222 | 6% | 222 | 38% | $-594.22 | 0.82 | False |
| 2 tick | all raw | 4342 | 337 | 8% | 337 | 37% | $-1,357.02 | 0.75 | False |
| 2 tick | D1 survivors | 3899 | 222 | 6% | 222 | 38% | $-771.60 | 0.78 | False |
| 3 tick | all raw | 4342 | 337 | 8% | 337 | 37% | $-1,623.76 | 0.71 | False |
| 3 tick | D1 survivors | 3899 | 222 | 6% | 222 | 38% | $-946.64 | 0.74 | False |

**Primary failure stage: `SIGNAL_NOT_DIRECTIONAL`** — flags `{"signal_positive_majority_of_horizons": false, "signal_positive_horizons": "1/4", "signal_best_t_stat": 1.18, "documented_bracket_positive": false, "structural_survivor_share": 0.879, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.057}`

## Transition failed-breakdown reclaim MNQ (full corpus)

`transition_failed_breakdown_reclaim` · MNQ · corpus `replay_polygon_5m` (2024-07-02 → 2026-06-26, 5m) · source: saved_json · day-only: False · walk-forward boundary 2025-07-04

Extraction audit: `{"bar_missing_in_corpus": 0, "price_offset_histogram": {"0.0": 3292}, "price_offset_levels": [0.0], "reanchor_suspect_dropped": 0, "saved_candidates": 3292, "saved_outcomes": {"LOSS": 1503, "NO_FILL": 33, "OPEN": 10, "WIN": 1746}, "source": "missed_move_transition_MNQ_costed.json"}`

### A. Raw signal

- candidates: **3292** (1 campaigns); direction {'LONG': 3292}; sessions {'new_york': 997, 'asian': 1283, 'london': 1012}; 2024-07-02 → 2026-06-26
- documented stop width (ticks): median 63.0, p90 174.0, max 798.0; policy cap 120 → **21% of raw candidates exceed the cap**; median R:R 0.615

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 3271 | 1.09 | 1.25 | 52% | 1.348 | $0.69 | 1.02 | 27.826 / 29.345 |
| 60m | 3226 | 2.09 | 2.88 | 54% | 1.864 | $2.69 | 1.07 | 39.413 / 41.4 |
| 120m | 3093 | 1.74 | 3.75 | 53% | 1.051 | $2.00 | 1.03 | 55.849 / 60.315 |
| EOD | 2286 | 4.18 | 10.75 | 54% | 0.854 | $6.88 | 1.05 | 141.684 / 157.749 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 3276 | 3276 | 100% | 3276 | 58% | $-10,977.73 | $-3.35 | 0.80 | $11,007.98 | 3% | $-4,698.53 / $-6,279.20 | False | {'TARGET_HIT': 1935, 'STOP_HIT': 1341}  |
| resting stop order at the level, next bar **(primary)** | 3276 | 3208 | 98% | 3208 | 59% | $-10,768.34 | $-3.36 | 0.80 | $10,792.32 | 3% | $-4,594.77 / $-6,173.57 | False | {'TARGET_HIT': 1919, 'STOP_HIT': 1289} {'ENTRY_NOT_TRIGGERED': 57, 'ENTRY_BRACKET_INVALID_AT_FILL': 11} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$-209.39**; skipped while a position was open: plan 16 / resting 16; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: rr_below_minimum 2998, min_confluence_grade 2717, target_too_close 2223, stop_too_wide 702
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 253, MARKET_CONDITION_NOT_TRADABLE 28
- confluence grades: {'A': 141, 'WEAK': 2624, 'A+': 230, 'C': 93, 'B': 204}; market conditions at signal: {'TRENDING': 3011, 'CONSOLIDATING': 253, 'CHOPPY': 28}
- survivors of risk-structural checks: **11 / 3292 (0%)**; survivors of risk + market-condition + entry-attached: 11 (0%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=11, net **$-166.03**, PF 0.23, both halves False
- bracket P&L of all-gate survivors: n=11, net $-166.03, PF 0.23
- bracket P&L of the structurally REJECTED candidates: n=3197, net $-10,602.31, PF 0.80 — what the gates threw away

### D2/D3. Full engine — no enabled_concepts entry on main; structural stage D1 is the only gate evidence

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 3276 | 3276 | 100% | 3276 | 58% | $-10,977.73 | 0.80 | False |
| 1 tick | D1 survivors | 11 | 11 | 100% | 11 | 9% | $-150.03 | 0.25 | False |
| 2 tick | all raw | 3276 | 3276 | 100% | 3276 | 58% | $-13,286.23 | 0.76 | False |
| 2 tick | D1 survivors | 11 | 11 | 100% | 11 | 9% | $-160.53 | 0.24 | False |
| 3 tick | all raw | 3276 | 3263 | 100% | 3263 | 58% | $-15,527.99 | 0.73 | False |
| 3 tick | D1 survivors | 11 | 11 | 100% | 11 | 9% | $-171.03 | 0.22 | False |

**Primary failure stage: `BRACKET_DESTROYS_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "4/4", "signal_best_t_stat": 1.86, "documented_bracket_positive": false, "structural_survivor_share": 0.003, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 1.0}`

## Transition failed-breakdown reclaim MNQ (May-Jul 2026 audit set, re-anchored)

`transition_failed_breakdown_reclaim` · MNQ · corpus `replay_corpus_v1_5m` (2024-07-29 → 2026-07-23, 5m) · source: saved_json · day-only: False · walk-forward boundary 2026-06-08

Extraction audit: `{"bar_missing_in_corpus": 0, "price_offset_histogram": {"0.0": 136, "7.75": 1, "9.5": 1, "12.75": 1, "16.0": 1, "292.75": 163}, "price_offset_levels": [292.75, 0.0], "reanchor_suspect_dropped": 12, "saved_candidates": 311, "saved_outcomes": {"LOSS": 133, "NO_FILL": 1, "WIN": 165}, "source": "missed_move_transition_MNQ_5m_full_2026-05-01_to_2026-07-08.json"}`

### A. Raw signal

- candidates: **299** (1 campaigns); direction {'LONG': 299}; sessions {'asian': 131, 'london': 85, 'new_york': 83}; 2026-05-03 → 2026-07-08
- documented stop width (ticks): median 100.0, p90 260.0, max 736.0; policy cap 120 → **41% of raw candidates exceed the cap**; median R:R 0.688

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 298 | 0.99 | 3.75 | 54% | 0.233 | $0.51 | 1.01 | 40.794 / 45.44 |
| 60m | 295 | -0.10 | 4.00 | 52% | -0.018 | $-1.67 | 0.97 | 57.772 / 64.347 |
| 120m | 286 | 5.54 | 15.75 | 58% | 0.645 | $9.60 | 1.11 | 85.217 / 91.324 |
| EOD | 193 | 2.28 | 9.25 | 52% | 0.125 | $3.08 | 1.02 | 193.573 / 228.492 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 299 | 299 | 100% | 299 | 58% | $-1,282.52 | $-4.29 | 0.82 | $1,844.36 | 14% | $310.73 / $-1,593.25 | False | {'STOP_HIT': 126, 'TARGET_HIT': 173}  |
| resting stop order at the level, next bar **(primary)** | 299 | 294 | 98% | 294 | 58% | $-1,306.12 | $-4.44 | 0.82 | $1,794.17 | 14% | $319.69 / $-1,625.81 | False | {'STOP_HIT': 123, 'TARGET_HIT': 171} {'ENTRY_NOT_TRIGGERED': 3, 'ENTRY_BRACKET_INVALID_AT_FILL': 2} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$23.60**; skipped while a position was open: plan 0 / resting 0; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: rr_below_minimum 269, min_confluence_grade 237, target_too_close 136, stop_too_wide 123
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 152, MARKET_CONDITION_NOT_TRADABLE 33
- confluence grades: {'B': 24, 'WEAK': 229, 'A': 16, 'A+': 22, 'C': 8}; market conditions at signal: {'RANGE_BOUND': 152, 'TRENDING': 114, 'CHOPPY': 24, 'DEAD': 9}
- survivors of risk-structural checks: **1 / 299 (0%)**; survivors of risk + market-condition + entry-attached: 0 (0%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=1, net **$-25.98**, PF 0.00, both halves None
- bracket P&L of all-gate survivors: n=0, net $0.00, PF —
- bracket P&L of the structurally REJECTED candidates: n=293, net $-1,280.14, PF 0.82 — what the gates threw away

### D2/D3. Full engine — no enabled_concepts entry on main; structural stage D1 is the only gate evidence

### E. IOC fills and cost stress (tolerance 32 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 299 | 299 | 100% | 299 | 58% | $-1,279.52 | 0.82 | False |
| 1 tick | D1 survivors | 1 | 1 | 100% | 1 | 0% | $-24.48 | 0.00 | None |
| 2 tick | all raw | 299 | 299 | 100% | 299 | 58% | $-1,492.02 | 0.80 | False |
| 2 tick | D1 survivors | 1 | 1 | 100% | 1 | 0% | $-25.48 | 0.00 | None |
| 3 tick | all raw | 299 | 299 | 100% | 299 | 58% | $-1,704.52 | 0.77 | False |
| 3 tick | D1 survivors | 1 | 1 | 100% | 1 | 0% | $-26.48 | 0.00 | None |

**Primary failure stage: `BRACKET_DESTROYS_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "3/4", "signal_best_t_stat": 0.65, "documented_bracket_positive": false, "structural_survivor_share": 0.003, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 1.0}`

## Transition failed-breakdown reclaim MES (Apr-Jul 2026 set, re-anchored)

`transition_failed_breakdown_reclaim` · MES · corpus `replay_corpus_v1_5m` (2024-07-29 → 2026-07-23, 5m) · source: saved_json · day-only: False · walk-forward boundary 2026-06-03

Extraction audit: `{"bar_missing_in_corpus": 5, "price_offset_histogram": {"0.0": 168, "0.75": 1, "1.25": 2, "1.5": 1, "2.0": 1, "62.5": 235}, "price_offset_levels": [62.5, 0.0], "reanchor_suspect_dropped": 5, "saved_candidates": 415, "saved_outcomes": {"LOSS": 175, "NO_FILL": 1, "OPEN": 2, "WIN": 227}, "source": "missed_move_transition_MES_costed.json"}`

### A. Raw signal

- candidates: **405** (1 campaigns); direction {'LONG': 405}; sessions {'london': 112, 'new_york': 145, 'asian': 148}; 2026-04-13 → 2026-07-23
- documented stop width (ticks): median 18.0, p90 44.0, max 132.0; policy cap 60 → **4% of raw candidates exceed the cap**; median R:R 0.565

### B. Time-exit directional control (no stop)

| Horizon | n | mean pts | median pts | hit rate | t-stat | mean net $ | PF (net $) | mean MFE / MAE pts |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 30m | 398 | 0.34 | -0.25 | 49% | 0.676 | $0.24 | 1.01 | 6.636 / 6.947 |
| 60m | 392 | 0.66 | 1.00 | 54% | 0.881 | $1.81 | 1.08 | 9.705 / 9.757 |
| 120m | 377 | 2.15 | 1.50 | 56% | 2.04 | $9.25 | 1.32 | 13.954 / 13.273 |
| EOD | 295 | 4.81 | 6.25 | 61% | 2.358 | $22.56 | 1.42 | 31.3 / 28.818 |

### C. Documented bracket (real PaperBroker, two fill models)

| Fill model | attempted | filled | fill rate | resolved | WR | net | expectancy | PF | max DD | top-5 conc. | H1 / H2 | both halves | exits / no-fill |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| plan-price fill (legacy, fills assumed) | 402 | 402 | 100% | 402 | 60% | $-1,261.11 | $-3.14 | 0.73 | $1,273.64 | 12% | $-681.87 / $-579.24 | False | {'TARGET_HIT': 246, 'STOP_HIT': 156}  |
| resting stop order at the level, next bar **(primary)** | 402 | 396 | 98% | 396 | 58% | $-1,327.32 | $-3.35 | 0.72 | $1,334.98 | 12% | $-727.74 / $-599.58 | False | {'TARGET_HIT': 243, 'STOP_HIT': 153} {'ENTRY_BRACKET_INVALID_AT_FILL': 3, 'ENTRY_NOT_TRIGGERED': 3} |

- entry style `close_confirmed` → primary fill model `resting_fill`; fill-assumption inflation (plan − resting net): **$66.21**; skipped while a position was open: plan 3 / resting 3; EOD-bar-missing: 0

### D1. Structural risk gates (path-independent, real RiskEngine checks)

- risk-rule failures across raw candidates: target_too_close 398, rr_below_minimum 386, min_confluence_grade 334, stop_too_wide 15
- signal-layer structural failures: MARKET_CONDITION_NOT_TRENDING 193, MARKET_CONDITION_NOT_TRADABLE 73
- confluence grades: {'WEAK': 329, 'A+': 29, 'B': 31, 'A': 11, 'C': 5}; market conditions at signal: {'RANGE_BOUND': 193, 'TRENDING': 139, 'DEAD': 16, 'CHOPPY': 57}
- survivors of risk-structural checks: **0 / 405 (0%)**; survivors of risk + market-condition + entry-attached: 0 (0%)
- bracket P&L (stage-C fills) of risk-structural survivors: n=0, net **$0.00**, PF —, both halves None
- bracket P&L of all-gate survivors: n=0, net $0.00, PF —
- bracket P&L of the structurally REJECTED candidates: n=396, net $-1,327.32, PF 0.72 — what the gates threw away

### D2/D3. Full engine — no enabled_concepts entry on main; structural stage D1 is the only gate evidence

### E. IOC fills and cost stress (tolerance 16 ticks at the decision-bar close)

| Slippage | Population | attempted | filled | fill rate | resolved | WR | net | PF | both halves |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 tick | all raw | 402 | 402 | 100% | 402 | 60% | $-1,258.61 | 0.73 | False |
| 1 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 2 tick | all raw | 402 | 394 | 98% | 394 | 55% | $-1,921.35 | 0.62 | False |
| 2 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |
| 3 tick | all raw | 402 | 370 | 92% | 370 | 54% | $-2,516.94 | 0.54 | False |
| 3 tick | D1 survivors | 0 | 0 | — | 0 | — | $0.00 | — | None |

**Primary failure stage: `BRACKET_DESTROYS_EDGE`** — flags `{"signal_positive_majority_of_horizons": true, "signal_positive_horizons": "4/4", "signal_best_t_stat": 2.36, "documented_bracket_positive": false, "structural_survivor_share": 0.0, "survivors_bracket_positive": false, "ioc_1tick_positive": false, "ioc_3tick_positive": false, "ioc_fill_rate": 0.0}`

## Method notes and limitations

- Stage A for `orb_reclaim` / `orb_breakout` / `vwap_hold` evaluates the strategy's own `_try_*` predicate on every bar through the engine's own `MarketState` builder; counts are per bar (campaign counts shown where the strategy has a natural campaign key). Sequential stages (C, E) enforce one position at a time, so overlapping bars are skipped, not double-counted.
- Stage A for the 4HR and 3-2-2 lanes walks the canonical pure state machines (`strategy/four_hr_retrigger.py`, `strategy/strat_322_first_live.py`) exactly as the engine calls them.
- Miyagi has no `enabled_concepts` entry on `main` (PR #362 was closed unmerged); its raw population is the research detector's own output with the documented causal stop recomputed via `_completed_one_hour_stop`, the same helper the #366 closure used. Its D1 stage is therefore the only gate evidence.
- Stage C runs two fill models on the same candidates: the legacy plan-price fill (what the standalone research studies assumed) and PaperBroker's `stop_market` resting order at the documented level (activated on the next bar, gap-through at the open, cancelled if untouched). The primary model follows the lane's entry style: armed-trigger state machines (4HR, 3-2-2, Miyagi) really do trade through their level on the signal bar, so the plan fill is honest for them; close-confirmed predicates and shadow setups confirm at bar close against a level the bar has already passed, so the resting order is their honest documented-bracket fill. The difference between the two is reported as the fill-assumption inflation.
- Transition-reclaim populations are saved shadow candidate sets: the full-corpus MNQ set (3,292 candidates on the Polygon price basis) plus the May-Jul 2026 MNQ (311) and Apr-Jul 2026 MES (415) sets the expectancy audit used, which come from TradingView continuous-contract exports and were re-anchored onto the corpus by the contract-roll offset observable per candidate (see each lane's extraction audit for the offset levels and the handful of bar-data outliers dropped). The detector lives in an uncommitted shadow-lane change and has no engine concept, so D1 is its only gate evidence. It is RANGE/CHOP-conditioned by construction, so `MARKET_CONDITION_NOT_TRENDING` is structural for it under the production `require_trending_condition` rule.
- The inverted ORB lane borrows the un-mirrored source lane's engine funnel (the paper build contract evaluates confluence and risk on the source signal and mirrors only at the broker) and uses the lane's 8-tick IOC cap for stage E.
- D2/D3 use `ioc_limit` fills. D2 disables only the survival floors (20% drawdown breaker, $150 daily loss, balance-tiered sizing → fixed 1 contract) so structural and signal-layer gates are visible unmasked; D3 keeps them. Both disable the strategy-permission policy gate and the isolated-lane `max_trades_per_day=3` cap, restoring the 2026-07-27 evidence posture the #367/#372 closures ran under; MES is re-admitted to `allowed_instruments` for the MES lanes. `require_trending_condition`, STRONG-trend, volume, EMA-stack, R:R, confluence, min-target and max-stop rules are all untouched.
- Stage B's next-bar-open entry deliberately ignores the strategy's entry mechanics; it measures whether the signal bar has directional information, not whether it is tradable.
- Dollar magnitudes are replay-scale historical evidence, not live-fill proof.

## Reproduction

```bash
python3 scripts/edge_decomposition_audit.py --data-root <main-tree>/data --transition-dir <main-tree>/logs \
  --logs logs/edge_decomposition --engine-jobs 3 \
  --out scripts/edge_decomposition_audit_results.json --report docs/edge-decomposition-audit-2026-09-07.md
```
