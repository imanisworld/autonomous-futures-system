# Options Webull Sandbox Adapter — 2026-09-20

## Purpose

This note records the verified Webull sandbox/paper boundary for the options system. It is a paper-feasibility lane only. It does not authorize live trading, automatic paper execution, or replacement of the existing market-data path.

## Current ruling

**SANDBOX FEASIBILITY AND BROKER OPTION PREVIEW SERVER ACCEPTANCE ARE PROVEN; ORDER SUBMISSION REMAINS DISABLED AND UNPROVEN.**

Webull is not an execution authority for the production options scanner. The current production options lane remains advisory/paper evidence only.

## Verified facts

Verified against Webull sandbox on 2026-09-20:

- sandbox endpoint: `api.sandbox.webull.com`;
- sandbox credentials are separate from live Webull credentials;
- `WEBULL_SANDBOX_TRADING_MODE=paper`;
- `WEBULL_SANDBOX_LIVE_TRADING_ENABLED=false`;
- `WEBULL_API_LIVE_ENABLED=false`;
- sandbox authentication/account list succeeds;
- the sandbox **Individual Cash** account is discoverable;
- read-only sandbox balance succeeds;
- read-only sandbox positions succeeds;
- sandbox account held zero positions during the proof;
- option-contract metadata discovery succeeds without relying on Webull paid quote snapshots;
- AAPL contract discovery returned 852 contracts in the adapter proof;
- official Python SDK version used/proven: `webull-openapi-python-sdk==3.0.1`.

No full account identifier, token, app key, or app secret is persisted in project documentation.

## What failed or remains unproven

The following are **not** proven:

- sandbox paper option order placement;
- order detail lifecycle after placement;
- sandbox cancel/replace behavior;
- paper fills;
- options quote quality, bid/ask, IV, Greeks, volume or open interest from Webull;
- any live-broker capability.

The options preview path is now separately proven. A controlled sandbox preview for `AAPL 2026-12-18 $350 CALL`, `BUY_TO_OPEN`, quantity 1, limit `$1.00` returned `PREVIEW_READY`, `currency=USD`, estimated cost `$100.00`, estimated transaction fee `$0.05`, `executable=false`, `submitted=false`, and no broker order id. No order was placed.

Paper placement/cancel proof remains open.

## Adapter candidate

PR **#822** is merged at `08cc82744883056c50961316d5ceb5b04c34e625`. The merged adapter retains the preview/read-only boundary described below.

The candidate:

- reuses the existing `OptionsBrokerPreviewRequest`;
- re-runs the existing options broker-boundary validation before preview;
- requires exact sandbox/paper configuration;
- requires live-options trading to remain disabled;
- resolves exactly one sandbox `INDIVIDUAL_CASH` account;
- supports read-only balance/positions;
- supports option-contract metadata discovery;
- supports broker preview only when the existing real-preview gate is explicitly enabled;
- returns `submitted=false`, `executable=false`, and no broker order id;
- exposes **no** `place_order`, `place_option`, `cancel_order`, `replace_order`, or `replace_option` capability.

Local regression proof before merge and again on merged `main`: **2,002 tests passed**.

The candidate changes only:

- `options_manager/adapters/webull_sandbox.py`;
- `options_manager/adapters/__init__.py`;
- `tests/test_webull_sandbox_options_adapter.py`;
- `requirements.txt`.

No futures strategy, futures risk, futures execution, webhook, scanner-strategy, or scheduling files are changed.

## Account-class correction

A parallel untracked dry-run mirror appeared during the work and hard-coded `INDIVIDUAL_MARGIN` as the Webull options account class. That assumption was not supported by the verified sandbox proof and was not required by the Webull options request shape inspected during implementation.

The competing mirror was quarantined outside the repository and was **not** merged. The verified sandbox target for this lane is the **Individual Cash** paper account.

## Market-data boundary

Do **not** cancel Polygon because this adapter exists.

Current source roles remain:

- **Polygon / existing qualified sources** — market data and historical/options evidence as already configured;
- **Signa** — observational/context/discovery evidence only;
- **Webull sandbox** — paper-broker feasibility, read-only account state, contract metadata, and broker preview candidate;
- **Webull live** — out of scope for execution; live account protection remains separate.

Webull quote/snapshot access previously returned `MARKET_DATA_NOT_SUBSCRIBED`. No Webull market-data purchase is required for this adapter phase.

## Required gates before paper automation

All of the following must pass before Webull may become a Phase-2 paper execution adapter:

1. **Complete:** PR #822 merged at `08cc827` with green CI.
2. **Complete:** exact options preview request accepted by Webull sandbox (`PREVIEW_READY`; no submission).
3. One controlled sandbox paper option order is placed locally against the intended paper account.
4. Order detail proves the expected state transition.
5. Cancel proof succeeds for an unfilled order.
6. No live endpoint or live key is reachable from the paper adapter.
7. Paper routing is hard-bound to the sandbox Individual Cash account.
8. Order journal records request, provider response, timestamps, and final paper state without secrets.
9. Failure/retry behavior is proven fail-closed.
10. A separate operator authorization explicitly enables Phase-2 paper submission.

Until then, the adapter remains preview/read-only infrastructure.

## Safety rule

**No proof, no trade.** Webull sandbox availability does not authorize automatic paper entry, and paper feasibility does not authorize live execution.


## 2026-09-21 addendum — sandbox paper-order lane (code only, UNPROVEN on the network)

`options_manager/adapters/webull_sandbox_paper_orders.py` adds, in a **separate module** so the preview adapter's no-submission guard test stays intact:

- `submit_sandbox_paper_option_order(...)` — re-runs the broker boundary, resolves exactly one sandbox INDIVIDUAL_CASH account, runs a broker preview, then places ONE single-leg `BUY_TO_OPEN` LIMIT DAY order with `client_order_id == ticket_id` (idempotent per ticket).
- `cancel_sandbox_paper_option_order(ticket_id, ...)` — cancel by client order id.
- `get_sandbox_paper_order_detail(ticket_id, ...)` — read-only order lookup (state, fill qty, avg price); does not require the submit opt-in.

Gates (all fail closed): sandbox host + paper mode + both live flags false; `live_options_trading_enabled=False`; `OPTIONS_MANAGER_BROKER_BOUNDARY_ALLOW_REAL_PREVIEW=true` **and** `OPTIONS_MANAGER_BROKER_BOUNDARY_ALLOW_SANDBOX_PAPER_SUBMIT=true` (new, default false). Stocks, multi-leg, SELL, market orders, replace and the live host are not reachable.

Intended use: a **mirror lane** — each internal paper entry/exit is also submitted to the sandbox and the broker order id / fill is logged beside the internal row. The internal shadow journal remains the evidence of record; broker paper fills are optimistic and must not replace it.

Status: **nothing imports this module at runtime** (test-enforced), no box config, no release. Real sandbox placement/cancel/fill lifecycle remains **UNPROVEN** until a controlled proof run; that proof, the scanner wiring, the release, and the `.env` keys are post-2026-09-30 items.
