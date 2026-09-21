# Futures paper-lane mirror → Webull sandbox (2026-09-21)

**Status:** adapter merged and **wired through `execution/paper_mirror_hook.py`**
(PaperBroker `execute_bracket` / `resolve_position` / `force_resolve`). Default
off: with `WEBULL_FUTURES_MIRROR_ENABLED` unset the hook returns after one env
read and never imports the mirror. When on, mirror calls run on a daemon thread
(never block a bar) and results append to `<log_dir>/webull_mirror_<date>.jsonl`
(operator-only; nothing reads it). No deploy until after the freeze.

## Why

The internal futures paper lanes (PaperBroker-simulated brackets) only exist in
the journal. The operator wants to watch them "play out" on a real paper
ledger. Webull's OpenAPI sandbox exposes a **FUTURES** paper account, so the
lanes can be mirrored there without touching Tradovate demo (the main lane) or
any decision/risk path.

## Sandbox facts (verified from a laptop, 2026-09-21 ~02:00Z)

| Check | Result |
|---|---|
| `get_account_list` | 5 paper accounts incl. `account_class=FUTURES` (margin, $1,000,000) |
| `preview_order` MESZ6 / MNQZ6, `instrument_type=FUTURES`, `market=US` | 200 |
| `place_order` MESZ6 BUY 1 LIMIT far off market, DAY | 200 → `SUBMITTED` (fees 0.74 total) |
| `get_order_detail` / `cancel_order` | 200 → `CANCELLED`, positions `[]` |
| Trading hours | **No** hours block on futures at 02:00Z Sunday (options sandbox is 8–16 ET only) |
| Market data | sandbox accepts only symbol `MESmain`; its 15m bars match `bars_MES_*.jsonl` |
| Combos (OTO/OCO) | not supported for futures → brackets are emulated as separate legs |

## Module

`execution/webull_sandbox_futures_mirror.py`

- `mirror_entry(BracketOrder, env)` → one single-leg order: LIMIT at `entry`
  (MARKET when `force_market_entry`). Stop/target are **not** sent.
- `mirror_exit(Fill, env, source_id=…)` → one MARKET order on the opposite
  side, only for fills that actually exited.
- `cancel_mirror_order(client_order_id, env)`, `get_mirror_order_detail(...)`.
- `client_order_id` = sha256(source id, leg)[:32] → idempotent per leg.
- Symbol = `_front_month_symbol` (same roll logic as Tradovate routing);
  roots without a computable front month are blocked.

Every call is gated **before** any client is created:

1. `integrations.webull_paper_config` must be paper-only-safe
   (sandbox host, paper mode, live flags false, secrets present).
2. `WEBULL_FUTURES_MIRROR_ENABLED=true` (default false).
3. Instrument root in `WEBULL_FUTURES_MIRROR_INSTRUMENTS` (default `MNQ,MES`).
4. Quantity capped by `WEBULL_FUTURES_MIRROR_MAX_CONTRACTS` (default 1).

Broker 4xx errors surface as `REJECTED / broker:<CODE>`; transport failures as
`ERROR`. Account ids and secrets never appear in results or logs.

## Env (box `.env`, post-freeze only)

```
WEBULL_SANDBOX_APP_KEY / WEBULL_SANDBOX_APP_SECRET   (sandbox pair only)
WEBULL_SANDBOX_BASE_URL=api.sandbox.webull.com
WEBULL_SANDBOX_TRADING_MODE=paper
WEBULL_SANDBOX_LIVE_TRADING_ENABLED=false
WEBULL_SANDBOX_API_ENABLED=true
WEBULL_SANDBOX_PAPER_TRADING_ENABLED=true
WEBULL_API_LIVE_ENABLED=false
WEBULL_FUTURES_MIRROR_ENABLED=false            # flip to true to mirror
WEBULL_FUTURES_MIRROR_INSTRUMENTS=MNQ,MES
WEBULL_FUTURES_MIRROR_MAX_CONTRACTS=1
```

## Not done yet

- Reconciliation: compare mirror fills vs paper fills daily (report-only).
- Release + `.env` keys on the box: after 2026-09-30.
