# Futures — Current State Handoff

_As of 2026-07-08. Read-only summary of where things stand; not a proof
artifact itself — see linked evidence for backing detail._

## Execution mode

- **Execution mode**: Tradovate demo (`BROKER=tradovate`, `TRADOVATE_ENV=demo`).
- **Live money**: blocked. `LIVE_TRADING_ENABLED` is hardcoded `false` and
  enforced independently at five layers: config load (`config/settings.py:load_config`),
  `RiskEngine._check_live_trading_disabled`, `TradovateBroker.execute_bracket`,
  `webhook/runner.py`'s `broker.is_live` cross-check, and same-day
  `live_preflight` arming (`execution/live_preflight.py`).
- **Paper simulator (`PaperBroker`, in-process)**: available in the codebase
  but not necessarily what's running on the box right now. The dashboard's
  "PAPER MODE" / "PAPER SYSTEM" labels are driven by `risk_rules.yaml:
  trading_mode.paper_mode`, not by `BROKER`/`TRADOVATE_ENV` — so the label
  does not by itself distinguish "Tradovate demo (real broker, demo
  account)" from "PaperBroker in-process simulator." Not a safety gap
  (`BROKER` env var controls the actual code path either way), but a
  display-only wording gap worth fixing in a small, display-only PR if
  useful.

## System posture

**HOLD / OBSERVATION ONLY.**

- Execution-safety patch (alert-freshness gate + Tradovate working-order
  recheck) is on `main` as commit `016c4e8`. Alert-age cutoff is in
  observe-only mode by design (`log_alert_age_only: true`) — zero production
  reps yet on the freshness gate.
- Note: an earlier session-branch attempt at this same patch
  (`claude/futures-system-audit-synl3f`, commit `164f934`) implemented the
  working-order recheck more broadly — checking both open *positions* and
  working *orders* (`has_open_exposure()`) — whereas `016c4e8`'s merged
  version checks working orders only (`_list_orders`/`WORKING_ORDER_STATUSES`
  in `webhook/runner.py`). That branch was abandoned as superseded/redundant
  rather than merged, per operator instruction (one source of truth). The
  narrower scope of the merged version is flagged here as a real, open
  question — not a confirmed bug — worth a deliberate decision (e.g. "is a
  bracket ever left with a naked position and no working child order in
  practice?"), not silently carried forward or fixed in this pass.
- Cancelled-row / Option C taxonomy proof: **BLOCKED** — requires actual
  Hetzner VPS journal access (`logs/journal_*.jsonl` or the deployed
  `LOG_DIR`), which is not reachable from a sandboxed code-checkout session.
  See the box-side proof checklist below for what to run once VPS access is
  available.
- No strategy, fill-model, runner, GEX, broker, or config work approved or
  in progress.

## Phase ladder (see full audit for detail)

F0 complete. F1 (frozen 30-trade paper-proof window) has never actually run
under a genuine freeze — the best real evidence is one audited week
(`docs/session-audit-2026-06-24-to-2026-07-01.md`): 29 TRADE decisions, 19
IOC-CANCELLED (66%), 4 FILLED-LOSS, 5 PHANTOM-CLEARED, **0 FILLED-WIN** —
and that window predates two relevant bug fixes. F2 is real but of mixed,
mostly unproven-in-production maturity. F3 (live-vs-replay comparison) does
not exist as specified.

Separately flagged, not yet resolved: the IOC-faithful 622-day backtest
(`docs/ioc-faithful-baseline-622d-2026-07-06.md`) found the "honest"
non-optimistic-fill backtest is ~zero/negative and would trip the 20%
drawdown breaker in month 1 — a strategy-viability question independent of
execution-safety mechanics, not resolved by this document.

## Box-side proof checklist (hand to whoever has VPS access)

```
Lane: FUTURES
Task: Box-side CANCELLED / Option C proof check
Mode: read-only only

Required output:
- hostname
- deployed SHA
- service health
- LIVE_TRADING_ENABLED value
- execution mode label
- current position state
- LOG_DIR path
- journal files checked
- latest journal timestamp
- post-taxonomy CANCELLED row count (cutoff 2026-07-07T18:35:33Z)
- option_c_recurrence count
- MISLABELED_FILL_SUSPECT count
- final verdict
```

## Open items

- Box-side CANCELLED-row / Option C proof check — blocked pending VPS access.
- PR #158 (VP futures manual strategy snapshot, docs-only) — not reviewed
  this pass.
- Dashboard paper/demo wording gap (above) — not fixed, flagged only.
- `MISLABELED_FILL_SUSPECT` / `option_c_recurrence` / an explicit "≥30
  resolved TRADE↔OUTCOME pairs, ≥10 filled" gate — none of these exact terms
  exist in this repo today; only `ops/proof_30_mnq.py`'s `filled_wl_count`
  logic was found. Not confirmed as missing-by-design — just not present as
  written here.
