# Futures — Validated vs. Unvalidated (Current Status)

_As of 2026-07-08. Read-only status doc — separates what has been proven
from what has not, so nobody over- or under-claims where the system
actually stands. Not a proof artifact itself; see the linked evidence for
backing detail. No code, config, or trading behavior is affected by this
document._

## Verified

- **Runtime mode**: Tradovate demo execution (`BROKER=tradovate`,
  `TRADOVATE_ENV=demo`). Confirmed via `webhook/app.py` broker-account
  panel logic and `risk_rules.yaml` comment (`# Broker: Tradovate demo
  (env=demo)`).
- **Live money is blocked**: `LIVE_TRADING_ENABLED` is hardcoded `false`
  and enforced independently at five layers — config load
  (`config/settings.py:load_config`), `RiskEngine._check_live_trading_disabled`,
  `TradovateBroker.execute_bracket`, `webhook/runner.py`'s `broker.is_live`
  cross-check, and same-day `live_preflight` arming
  (`execution/live_preflight.py`). No bypass found in any path reviewed
  this session (webhook runner, `main.py` CLI, replay engine, adaptive
  shadow runner).
- **Historical journals exist**: `docs/session-audit-2026-06-24-to-2026-07-01.md`
  is real, checked-in evidence from the actual box (29 TRADE decisions in
  one week). `docs/proof-operator-overrides.md` documents one real,
  broker-verified MES win (+$60.60, 2026-07-06).
- **Audit/proof tooling exists and runs**: `ops/proof_30_mnq.py`
  (`filled_wl_count` proof bar, freeze-timestamp scoped),
  `scripts/session_audit.py`, `scripts/journal_label_audit.py` (label
  audit — 403 rows, 0 issues on the last run reported), and the newly
  merged `scripts/strategy_intent_audit.py` (#219).
- **Test suite passes**: 1443 passed, 1 pre-existing skip, 0 failed (full
  suite, run this session against this branch's now-superseded commit;
  not re-run against current `main` tip in this pass).
- **Journal labeling for replay risk-rejects is correct**: `#217` normalizes
  `RISK_REJECTED` labeling in replay-mode journal output (audit/labeling
  only, no risk-logic change).
- **Candidate-audit observability exists**: `#218` adds rank/winner/
  fallback/failed-gate metadata to the candidate audit trail (additive,
  no change to selection or evaluation logic).

## Unverified

- **Post-taxonomy CANCELLED-row behavior (Option C detector)**: the
  detector (#205) exists and is merged, but there is no confirmed
  post-`2026-07-07T18:35:33Z` CANCELLED row to test it against yet.
  **Status: waiting on box-side journal check** (blocked from this
  sandboxed session — no VPS access).
- **Strategy edge / viability**: the only real evidence window
  (`docs/session-audit-2026-06-24-to-2026-07-01.md`) shows 29 TRADE
  decisions, 19 IOC-CANCELLED (66%), 4 FILLED-LOSS, 5 PHANTOM-CLEARED,
  **0 FILLED-WIN** — and that window predates two relevant bug fixes
  (Signa-direction fix, reconciler phantom-clear fix `4332d09`), so it
  doesn't cleanly represent the current system either. Separately, the
  IOC-faithful 622-day backtest
  (`docs/ioc-faithful-baseline-622d-2026-07-06.md`) found the "honest"
  non-optimistic-fill backtest is ~zero/negative and would trip the 20%
  drawdown breaker in month 1 — an open strategy-viability question, not
  resolved by any of this session's execution-safety or observability
  work.
- **Fresh strategy-intent-audit output**: `scripts/strategy_intent_audit.py`
  (#219) has been proven locally against a fresh replay, but the active
  demo box is still running an older release (`d1f0d4f`) and today's
  remote journal does not yet contain the new candidate-audit fields
  (only old-shape `candidate_audit` on one `vwap_hold` trade). Needs a
  box sync + fresh journal rows before this tool's output means anything
  in production.
- **Forward-measurement gate**: no explicit "≥30 resolved TRADE↔OUTCOME
  pairs, ≥10 filled, 0 corrupt rows, 0 unmatched fills, 0 manual overrides"
  gate exists in this repo as written — only `ops/proof_30_mnq.py`'s
  `filled_wl_count`/30-trade-target logic was found under different field
  names. Not confirmed as missing-by-design, just not present as
  described.
- **Working-order recheck scope**: the merged gate (`016c4e8`,
  `webhook/runner.py`) checks working *orders* only
  (`_list_orders`/`WORKING_ORDER_STATUSES`), not open *positions* without
  a working order. Whether this gap is ever hit in practice is unproven —
  no evidence of a naked-position-without-working-order case has been
  found or ruled out. **Do not patch until box evidence shows this
  actually occurs** (per operator decision this session).
- **Production firings of the auth circuit breaker / reconciler**: both
  are code- and unit-test-verified only; no raw box logs have been
  inspected to confirm either has actually tripped in production.
- **Dashboard mode-label accuracy**: `webhook/app.py`'s "PAPER MODE" /
  "PAPER SYSTEM" labels are driven by `risk_rules.yaml: paper_mode`, not
  `BROKER`/`TRADOVATE_ENV` — so they don't distinguish Tradovate demo from
  the PaperBroker in-process simulator. Display-only, not a safety issue,
  not yet fixed.

## What this means right now

Nothing here changes the system's posture: **futures stays HOLD /
OBSERVATION ONLY.** The "Verified" list is infrastructure and tooling
proof (the plumbing exists and works); the "Unverified" list is entirely
evidence proof (whether the plumbing has actually been exercised
correctly in production, and whether there's a real trading edge at all).
No further code, config, or trading-behavior change should be made on the
strength of the "Verified" column alone — every open item above requires
either box-side journal evidence or a fresh, genuinely frozen proof
window before it can move to "Verified."
