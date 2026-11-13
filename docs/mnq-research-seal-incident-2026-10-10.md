# MNQ post-seal exploratory-data breach — 2026-10-10

**Incident classification: RESEARCH-DATA CONTAMINATION.** This document preserves the incident and corrective controls; it is **not** a new strategy verdict, experiment, trial, or competing source of truth.

## Operator's binding MNQ historical boundary

- Historical strategy discovery, optimization, screen, retrospective comparison and ordinary MNQ backtesting must use **only CME sessions settling on or before 2026-06-26**. A June 29 seal reserves subsequent MNQ data for separately approved forward evidence, not arbitrary backtesting.
- **Do not query/load/inspect/score MNQ sessions June 29 or later** in any exploratory backtest. June 28 (Sunday evening) may be the *opening* of the June 29 Globex session; exclude by **CME session_end_date**, not just calendar/UTC timestamps. Fail closed if the session label or provenance is unknown.
- Keep previously registered forward collection and explicitly authorized single-look scoring segregated; this boundary does not instruct disabling, resetting or peeking a registered observer or modifying deployed systems.
- Mandatory prefetch AND prescore checks for all dated MNQ contracts, continuous contracts, Polygon/Massive APIs and SQL/CSV replays. The reusable source-only Python guard is `research/mnq_backtest_seal.py` with `tests/test_mnq_backtest_seal.py`. An agent using external SQL/APIs must enforce the boundary manually as well; merely adding the helper is not a universal runtime interceptor.

## What happened

ChatGPT performed **two separate exploratory MNQ screens on 2026-10-10** without first enforcing the existing June 29 seal:
1. Private ChatGPT Library: `/Futures Research/Futures Edge Discovery Screen - 2026-10-10.md`. This included 2026 Q3 MNQ contract data in exploratory strategy selection and follow-up screening.
2. Private ChatGPT Library: `/Futures Research/Futures Volume Shock Screen - 2026-10-10.md`. Its high-volume rejection and continuation screens included 2026 Q3 MNQ data.

Both inquiries used the sealed date range for exploratory learning. **The results as a whole—including negative classifications—are now labeled `CONTAMINATED_POST_SEAL`, cannot be used to promote, reject, select or re-tune any strategy, and must not be represented as clean out-of-sample evidence.** Do not retroactively restore validity by removing post-seal rows after seeing the answers. Do not delete or rewrite the original private source reports; they remain preserved as the incident audit trail and should be read only for incident reproduction, not investment decisions.

The operator reports **424 tests in the tally** after accounting for the two latest screens; do not silently subtract, re-score, repeat or reclassify the counts as valid evidence. The correct labels for those two screens are **counted exploratory attempts, invalid due to post-seal exposure**.

## Impact and limitations

- **Verified:** The exploratory vendor requests included dated MNQ post-seal bars in 2026 Q3, and screen conclusions were issued after viewing them. This is a **research-information leak across the holdout**.
- **Not verified:** Any actual VPS runtime journal, ledger, broker, execution parameter, deployed forward file or sealed trial's stored outcome was read or modified as part of the exploratory requests. No live or demo order action was requested. Thus do not infer that a deployed forward collector changed; however **do not claim future strategy-selection or holdout independence remains intact** without a separate independent governance determination.
- **Research disposition:** Both Oct 10 screen conclusions are disallowed as evidence. The already frozen/preregistered forward 4HR protocol should not be changed to accommodate these observations. Independent reviewer must decide whether/how this researcher-level leak affects any prospective selection/eligibility statement. No reset or premature score peek.

## Controls and future process

1. Before any MNQ exploratory data call, explicitly record the exact ticker(s), historical **session-end** minimum/maximum, intended held-out sample and prereg authority. End session must be **≤2026-06-26**.
2. Fail closed **before API/SQL/file fetch** if requested range crosses the seal; do not fetch a longer sample and filter inside a downstream SQL view.
3. Validate every returned row's MNQ source identity and `session_end_date` **before feature construction or scoring**. Any missing date, incorrect symbol, or post-seal row invalidates the entire dataset. Do not silently drop offending rows.
4. Register new hypotheses once, using only clean pre-seal discovery, independent untouched data, or another instrument's legitimately unsealed window—without transferring knowledge derived from the exposed MNQ future sample.
5. Claude/Codex independently checks new MNQ research requests for the seal. Grok and Cursor must read the hard stop in `AGENTS.md`; do not run the same two invalid screens again.
6. Preserve private numbers only in Library, public status here only, and keep the Strategy Inventory/trial ledger unaltered without their own approved evidence/state change.

**Status:** Historical-data seal rule and source-only guard drafted in a review PR; no research data acquisition, runtime flag, merge, deployment, broker/order or sealed-forward outcome scoring is authorized by these changes.
