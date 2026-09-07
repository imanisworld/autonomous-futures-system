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
