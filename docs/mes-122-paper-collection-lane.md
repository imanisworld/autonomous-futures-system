# MES 15m 1-2-2 — isolated forward-paper collection lane (2026-09-09)

**This PR does not start collection.** It adds the lane, off by default
(`MES_122_PAPER_MODE=observe_only`). Activation is a separate, explicit box step,
listed at the end.

Approved after PR #553's per-leg execution-realism gate: at a genuine 1 adverse tick
per leg the corrected 40-trade population is **+$32.05 / PF 1.035**, population stable
(0 bracket-invalid, 0 disappeared). Status: **PROMISING BUT UNPROVEN**. Forward paper
evidence only — not validation, not a promotion path.

## Campaign contract (pinned in `tests/test_mes_122_paper_lane.py`)

| | |
|---|---|
| instrument / strategy | MES only, 15m `strat_122` only (`expected_timeframe_minutes=15` pinned in the lane config, not inherited) |
| contracts | fixed 1, all sizing and win-streak scaling off |
| starting balance | $1,500 |
| swings | allowed across sessions/weekends (the merged MES `strat_122` 8h stale-timeout exemption applies) |
| execution | **PaperBroker only** — no Tradovate, no live orders |
| entry / stop / target | unchanged canonical `strat_212_122` detector; no parameter changes |
| commission | $1.48 round turn |
| drawdown warnings | 20%, 25% |
| hard halt | 30% |
| judged on | the **realistic 1-tick-per-leg ledger**, not raw PaperBroker P&L |

## Why a lane and not a config flip

Three things make "add MES to `instruments.allowed` and `strat_122` to
`enabled_concepts`" the wrong change:

1. **It would not actually collect.** `risk_rules.yaml:395-403`: `DecisionEngine`
   ranks all candidates first and applies the permission gate only to the winner, so
   a higher-ranked SHADOW_ONLY strategy wins selection and returns NO_TRADE, silently
   suppressing the candidate rather than falling through to it. PR #373 measured this
   exactly — 7 of 33 MES 1-2-2 candidates preempted by a shadow-only `vwap_hold`, and
   it is the same effect that preempted 2 of the 3 out-of-sample trades. Isolation has
   to come from a config in which `strat_122` is the only enabled concept.
2. **It could reach Tradovate.** `BROKER=tradovate` is live on the box. The
   per-strategy paper-forcing conjunction (`webhook/runner.py:1118-1153`) has entries
   for `orb_reclaim`, `orb_breakout` and `vwap_hold` — none for MES `strat_122`. Only
   the box-wide `SCHEDULE_MODE` would stand between an approved setup and a demo
   order, which is exactly the dependency #552 was written to remove.
3. **It would disturb live collection.** The #545 campaign is collecting on its own
   epoch, and its handoff forbids adding parked strategies to the real-book
   `enabled_concepts`.

## How the lane works

`context/mes_122_paper_lane.py` is an additive observer, hooked in
`webhook/runner.py` immediately after `simulate` is derived — before the real book's
own gates, because MES is deliberately parked in `instruments.allowed` and would
otherwise return NO_TRADE above any later hook point. It re-evaluates the MES alerts
that **already arrive** (42 MES 15m bars on 2026-09-09) on its own isolated config
copy, and writes only to its own journal root.

- **Isolated config** — `lane_config()` returns a *copy*: MES-only universe,
  `strat_122` the only enabled concept, `PAPER_ELIGIBLE`, 1 contract, no scaling,
  $1,500. The shipped config is never mutated (pinned by a test).
- **Paper by construction** — the copy pins `paper_mode=True`. `webhook/runner.py:460`
  derives `simulate` from it; `:2183-2187` then selects `_paper_broker(...)` for
  execution and `:1150` makes `_using_tradovate_position` False for the position's
  whole lifecycle. This holds regardless of the box's `BROKER` or `SCHEDULE_MODE`, and
  a test asserts `_make_broker` is never called even with `BROKER=tradovate`.
- **Own journal root** — `logs/hypothetical_ledger/mes_122_1500`, never the real book's.
- **Own accounting epoch** — required when active, so unrelated historical P&L cannot
  trip the lane's drawdown halt while every loss inside the epoch counts normally.
- **Cannot break the real book** — the hook and the lane each sit in their own
  try/except; any failure is logged and the real book's decision is untouched. A test
  asserts the real book's `decision`/`resolution`/`risk`/`fill` are byte-identical
  with the lane on and off.
- **No recursion** — the lane's config carries a marker; a re-entrant call returns None.

## The realistic ledger (why the halt is not raw P&L)

Fill behavior is deliberately **unchanged** — PR #553 changed no runtime code and
neither does this. `restore_position()` still opens `strat_122` at the causal entry
with no adverse slippage, and same-bar `force_resolve()` exits still book the exact
structural price. Raw PaperBroker P&L is therefore optimistic, and forward paper
inherits exactly the same optimism (the forward path uses the same two primitives —
`webhook/runner.py:2455` and `:2417`/`:2425`).

So the lane keeps raw P&L for diagnostics and computes a **realistic ledger** for
everything that matters:

- every fill: **−1 adverse tick** on the entry;
- same-bar `force_resolve()` fills (identified by
  `execution_audit.source == "strat_212_122_same_bar_resolution"`): **−1 more tick**
  on the exit;
- ordinary market exits already carry the broker's own slippage and are not charged
  twice;
- **−$1.48** round-turn commission.

`realistic_balance`, `realistic_closed_trade_drawdown_percent`, the 20%/25% warnings
and the 30% hard halt are all computed from that ledger.
`raw_paper_balance_diagnostic_only` is reported alongside and never drives the halt.

**Every outcome path feeds the ledger** — fixed in review of this PR. The ledger
selects a lane's own OUTCOME rows by `strategy`, but the normal later-bar resolution
and the stale/price-mismatch force-close both journalled without one, so only same-bar
outcomes were being counted; balance, warnings and the halt were untrustworthy.
`strategy=_open_pos_strategy` is now passed on both paths (metadata only — fill
behavior is unchanged), and three regression tests cover normal resolution, the
realistic-balance arithmetic on a normal close, and a price-mismatch force-close. All
three fail without the fix.

### Closed-trade vs open-position drawdown

`realistic_closed_trade_drawdown_percent` walks **resolved outcomes only** and never
marks an open position bar by bar, so it is deliberately **not** labelled
mark-to-market (`drawdown_basis: "closed_trade_realistic"`) — the same distinction
#547 had to correct. Since this campaign is explicitly evaluating swing holds,
`open_position_exposure()` reports the live unrealized excursion separately on every
lane bar: raw unrealized dollars, the entry tick already incurred,
`realistic_mtm_equity`, and `open_position_mtm_drawdown_percent`.

That exposure is **observational only** — it never halts or force-closes. The 30% halt
remains on the closed-trade realistic ledger, as specified. A test asserts a deep
unrealized excursion sets `exceeds_halt_threshold_observational` while leaving
`halted` False.

Historical reference: max MTM drawdown at 1 tick per leg was **$229.11**. That is a
reference, not a guaranteed limit — crossing it is a review event.

## Activation — NOT done by this PR

Nothing collects until these are set on the box and the service is restarted:

1. `MES_122_PAPER_MODE=paper_sim`
2. `MES_122_PAPER_EPOCH_START=<fresh offset-aware UTC timestamp>` (required; the lane
   fails closed without it, and a naive timestamp is rejected at config load)
3. `systemctl restart afs-watcher` after the release, per the standing box invariant

Both variables are registered in `PROOF_CRITICAL_RUNTIME_OVERRIDES`
(`ops/live_box_guard.py`), so they appear in the release manifest and can be pinned
with `EXPECTED_PROOF_MES_122_PAPER_MODE` / `EXPECTED_PROOF_MES_122_PAPER_EPOCH_START`.

Before activating, verify: the deployed SHA contains this PR; `LIVE_TRADING_ENABLED=false`;
no stale lane state under `logs/hypothetical_ledger/mes_122_1500` from a prior epoch;
MES 15m alerts are still arriving; and the #545 lanes are unaffected.

## What this PR does not change

No `risk_rules.yaml` change. No change to `instruments.allowed`, `enabled_concepts`,
the permission gate, sizing, or any other lane's config. No fill-behavior change. No
deployment. No promotion path — paper evidence never converts to a live decision
automatically.

## Files

- `context/mes_122_paper_lane.py` — the lane (config isolation, realistic ledger, open-position swing exposure, observer)
- `webhook/runner.py` — one additive hook in its own error boundary, plus `strategy=` metadata on the two outcome paths that omitted it
- `config/settings.py` — `mes_122_paper_mode` / `mes_122_paper_epoch_start` + validation
- `ops/live_box_guard.py` — both vars registered as proof-critical
- `tests/test_mes_122_paper_lane.py` — 34 tests: pinned contract, isolation, no-Tradovate,
  realistic-ledger arithmetic, every-outcome-path coverage, timeframe pin, swing exposure,
  halt driven by the closed-trade realistic ledger, config validation
- this doc
