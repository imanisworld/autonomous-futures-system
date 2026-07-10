# Execution-state correction — design

**Scope:** futures lane only. Execution-state journal semantics only. No strategy,
risk-threshold, broker-API, routing, or config changes. Design + narrow implementation
with tests. No deploy / restart / live change.

## Verdict

`EXECUTION_STATE_BUG` is real and fixable at the journal-semantics layer without
touching broker behaviour. Root cause (confirmed by reading the deployed code):

- `webhook/runner.py:1196` writes `decision="TRADE"` (with `risk_check.result="APPROVED"`)
  **before** the broker call at `webhook/runner.py:1327` (`broker.execute_bracket`).
- `journal/journal_logger.py` derives *open position* and *trade count* purely from that
  row: `get_open_position()` (`:472`) and `_compute_daily_state()` (`:369`) match
  `decision=="TRADE" && risk APPROVED && no resolved outcome`. There is **no broker
  dependency** in that determination.
- So between the pre-broker write and the eventual outcome/cancel, and on every early
  `return` between `:1196` and `:1327` (`LIVE_TRADING_BLOCKED`, `SHADOW_NO_ORDER`,
  `ORDER_SUPPRESSED`), the journal shows a **phantom open** that risk/status treat as real
  until the 20-minute reconciler clears it. `ORDER_IDS` are only persisted post-OPEN and
  are missing whenever the flow never reaches a real fill.

Fix direction (the operator's): make `TRADE` mean *confirmed execution*, not *intent*.

## Proposed state model

Two journal states instead of one:

| Phase | Row written | Treated as open? | Counts toward trade limit? |
|---|---|---|---|
| Approved, pre-broker | `decision="TRADE_INTENT"` (full payload) | **No** | **No** |
| Broker returned OPEN **and** (order-ids present **or** PaperBroker) | `decision="TRADE"` (full payload) + `ORDER_IDS` | **Yes** | **Yes** |
| Broker returned non-OPEN (reject / IOC no-fill / naked-flatten) | `type="OUTCOME" result="CANCELLED"` (no-fill taxonomy fields) | No | No |
| Broker returned OPEN but **no order-ids** on a real broker | `type="OUTCOME" result="CANCELLED"` (`ORDER_CONFIRMATION_MISSING`) + loud alert | No — **fail closed** | No |

`TRADE_INTENT` is the *smallest compatible* naming choice: it reuses the existing
`decision` field (mirrors how the rejected path already sets `decision="RISK_REJECTED"`),
so every reader that keys on `decision=="TRADE"` ignores it automatically — no reader
needs to learn a new type. The confirmed row carries the **full** decision payload
(`setup`, `context`, `confluence`, `signa_status`, `gex_observed`, `bar_ts`,
`risk_check=APPROVED`) so all filled-trade analytics/audits see it exactly as before.

### Why `get_open_position` / `_compute_daily_state` need (almost) no change

They already match only `decision=="TRADE"`. Once only *confirmed* rows carry that label,
they automatically become "confirmed-execution only." Requirements #5/#6/#7 fall out for
free — **except** one reader bug the split exposes (below).

## Exact logic changes

### 1. `journal/journal_logger.py` — guard the CANCELLED reversal (REQUIRED, safety)

`_compute_daily_state()` (`:349`) currently *unconditionally* decrements `trade_count`
(and the session count) on a standalone `CANCELLED` outcome. That was correct only because
today every attempt writes a counted `TRADE` row immediately before its CANCELLED. Under
the new model a no-fill produces `TRADE_INTENT` (uncounted) + `CANCELLED`; an unguarded
reversal would then wrongly decrement a **prior filled trade's** count
(`TRADE→WIN→TRADE_INTENT→CANCELLED` would yield `trade_count=0`).

Change: only reverse when `has_open_position` is true (there is an open counted position
this cancel actually closes); always clear `has_open_position` afterward.

- Backward compatible: old `TRADE→CANCELLED` sequences still reverse (the TRADE row set
  `has_open_position=True` first), and reconciler-driven clears of legacy phantom opens
  still reverse (the phantom is open when its CANCELLED arrives).
- New-format `WIN → intent → CANCELLED` no longer erases the win's count.

### 2. `webhook/runner.py` — split intent from confirmed (REQUIRED)

- At the pre-broker `journal.log_decision` (`:1196`): for the **approved** case, write the
  row with `decision="TRADE_INTENT"`. Rejected path unchanged (`RISK_REJECTED`).
- After `fill = broker.execute_bracket(order)` returns `OPEN` (`:1414`+):
  - `requires_ids = not isinstance(broker, PaperBroker)` (PaperBroker legitimately has no
    order ids; Tradovate demo/live must).
  - If `requires_ids and not broker._last_order_ids`: **fail closed** — do not write the
    confirmed row, do not increment `trade_count`, do not set `has_open_position`; write a
    `CANCELLED` outcome tagged `ORDER_CONFIRMATION_MISSING`, log ERROR + fire the existing
    live-order-blocked Discord alert, return `BLOCKED_ORDER_CONFIRMATION_MISSING`.
    (Structurally this should never fire — `execute_bracket` only returns OPEN after setting
    `_last_order_ids` — so it is a defence-in-depth guard that surfaces broker/journal
    divergence loudly rather than silently marking open with no ids.)
  - Otherwise: write the confirmed `decision="TRADE"` row (same full `journal_entry`
    payload), then persist `ORDER_IDS` as today, increment the count, set open.
- Non-OPEN path (`:1342`) unchanged in spirit — it already writes a `CANCELLED` outcome and
  returns; it simply no longer has a phantom `TRADE` row to reverse (the intent row is inert).
- Bonus: the three early-return gates between the intent write and the broker call no longer
  strand a phantom open, because the intent row is non-open.

## Backward-compatibility plan

- **Readers are unchanged** except the guarded reversal, which is a strict superset of the
  old behaviour on old data. No migration of historical rows is required; daily-state is
  reconstructed per-day, so past-day journals are read exactly as before.
- Old-format same-day phantom `TRADE` rows (from before a deploy) are still cleared by the
  reconciler exactly as today.
- The confirmed row carries the full payload, so `get_open_position`, `evidence_readiness`
  (signa/context tracks), and gex-shadow pairing keep working for filled trades.

## Risk / status impact

- `risk_engine` gates (`has_open_position` at `:927`, `trade_count`) now count
  **confirmed executions only** — a failed attempt no longer consumes daily/session budget
  or blocks the instrument for the reconcile window. This is the intended correction.
- `/status`, `/status/today`, reconciler: all derive from `get_daily_state` /
  `get_open_position`, so they follow automatically. The reconciler now sees only
  broker-confirmed opens → pre-fill phantom-clears disappear.
- Discord `TRADE` decision-notify keys off `result["decision"]`, which stays `"TRADE"` only
  on the confirmed path — so the notify now fires on a confirmed fill, not on intent.

## Known, bounded ripple (out of scope here — documented follow-up)

Offline analytics that FIFO-pair `decision=="TRADE"` rows with OUTCOME rows
(`ops/fill_realism.py`, `scripts/fill_realism_report.py`,
`scripts/ioc_baseline_622d_analysis.py`, `scripts/session_audit.py` §2,
`scripts/missed_move_gate_sweep_622d.py`, `adaptive/journal_reader.py`,
`sources/gex_shadow_analysis.py`, `ops/evidence_report.py` attribution,
`ops/journal_label_audit.py`) will **undercount forward no-fill attempts**, because a
no-fill no longer leaves a `decision=="TRADE"` row to pair with its orphan `CANCELLED`.
Historical journals are unaffected. No-fill **counts** remain visible directly from the
`CANCELLED` OUTCOME rows (which are unchanged). These are manually-run audit tools, not
runtime-safety surfaces; fixing them (seed their pairing on `TRADE_INTENT`, allowlist the
label in `journal_label_audit`) is a separate, low-priority PR. Not touched here to keep
this change tightly scoped to execution-state safety.

`SHADOW_OUTCOME` and `range_signal` audits are untouched (separate type / field).

## Test plan

Journal-reader unit tests (`_compute_daily_state` / `get_open_position`):
1. `TRADE_INTENT` alone → not open, `trade_count==0`.
2. `TRADE_INTENT → TRADE(confirmed)` → open, `trade_count==1`.
3. `TRADE(confirmed) → WIN → TRADE_INTENT → CANCELLED` → `trade_count==1` (win not erased).
4. `TRADE_INTENT → CANCELLED` → `trade_count==0`, not open (no phantom, no wrong decrement).
5. Legacy `TRADE → CANCELLED` → `trade_count==0` (backward compat preserved).
6. Legacy phantom `TRADE` (open) then reconciler `CANCELLED` → reversed (compat).

Runner integration tests (`process_alert`):
7. PaperBroker OPEN → exactly one `TRADE_INTENT` + one confirmed `TRADE` row; decision TRADE.
8. Real-broker OPEN + order-ids → confirmed TRADE + ORDER_IDS; `has_open_position` true.
9. Real-broker OPEN + **no** order-ids → fail closed: no TRADE row,
   `BLOCKED_ORDER_CONFIRMATION_MISSING`, not open, CANCELLED written.
10. Real-broker non-OPEN → no TRADE row, CANCELLED written, not open.

Plus: update the tests that encode the old assumption; run `pytest -q` (full suite).

## Deployment risk

Low, but this is execution-critical: the change alters what "open" means in the journal.
The only reader change is a strict-superset guard. The runner change adds one fail-closed
branch and defers one label. No broker/order/config/threshold change. Deploy is **not**
part of this task; when the operator does deploy, the standard atomic-release + open-position
gate applies, and the first real TRADE attempt's `EXEC_TRACE` lines will show the new
intent→confirmed sequence.

## Safe implementation sequence

1. Journal-reader guard (#1) + its unit tests — provably backward compatible in isolation.
2. Runner split + fail-closed branch (#2) + runner integration tests.
3. Update tests encoding the old assumption; run targeted + full suite.
4. Push branch, open PR for operator review. **No deploy, no restart, no forced trade.**
