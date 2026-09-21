# Webull sandbox option round-trip close proof — 2026-09-21

## Verdict

**BUILT / UNWIRED / SANDBOX PAPER ONLY / BOX PROOF REQUIRED.**

The repository already has an unwired Webull sandbox option entry lane
(`webull_sandbox_paper_orders.py`) that can preview, BUY_TO_OPEN, cancel an
unfilled order, and read order detail. The missing lifecycle piece was an
explicit close path for a filled paper option position.

This change adds that plumbing only. It does not wire the scanner, non-Strat
observer, internal paper journal, or any runtime service to Webull.

## Why this is now implementable

Webull's official OpenAPI changelog dated 2026-03-28 says the Place Order
endpoint added `position_intent` for option orders with these values:

- `BUY_TO_OPEN`
- `BUY_TO_CLOSE`
- `SELL_TO_OPEN`
- `SELL_TO_CLOSE`

Source:
https://developer.webull.com/apis/docs/changelog/

The official Options Trading page also documents:

- option `SELL` orders;
- `LIMIT`, `STOP_LOSS`, and `STOP_LOSS_LIMIT` option order types;
- no option `MARKET` order support;
- sell-side options support `DAY` time-in-force only.

Source:
https://developer.webull.com/apis/docs/trade-api/options/

The close lane therefore uses an explicit `SELL_TO_CLOSE` position intent,
`LIMIT`, and `DAY`. It does not send a generic SELL that could be mistaken
for opening a short option.

## New module

`options_manager/adapters/webull_sandbox_option_roundtrip.py`

It accepts a `WebullSandboxCloseRequest` plus the already-read
`WebullSandboxOrderDetail` for the entry ticket.

A close request is rejected unless:

1. the entry ticket id exists;
2. a distinct exit ticket id exists;
3. the exit id is <= 32 characters;
4. ticker/direction/quantity/strike/limit are valid;
5. quantity stays under the existing broker-boundary contract cap;
6. entry order detail is `OK`;
7. entry detail's client order id equals the claimed entry ticket;
8. entry state is exactly `FILLED`;
9. entry detail is terminal;
10. filled quantity is present and is >= close quantity.

It then reuses the existing Webull sandbox submit gate, so network activity
still fails closed unless all existing paper-only requirements are satisfied,
including:

- exact sandbox host;
- sandbox trading mode = paper;
- sandbox live trading = false;
- global Webull live API = false;
- live options trading = false;
- real-preview opt-in = true;
- sandbox-paper-submit opt-in = true.

## Close order

The generated order is exactly one single-leg option close:

```
side             = SELL
position_intent  = SELL_TO_CLOSE
order_type       = LIMIT
time_in_force    = DAY
option_strategy  = SINGLE
quantity         = proven filled quantity or less
client_order_id  = dedicated exit ticket
```

The exact close request is broker-previewed immediately before placement.
Preview failure prevents placement.

The entry-side $3 premium / $300 notional caps are deliberately not reused as
close-price caps. A profitable option may be worth more than the permitted
entry price; an exit safety layer must not trap a paper position merely because
its value increased. Quantity and sandbox/live safety gates remain enforced.

## Unknown outcome handling

Broker 4xx/business-rule errors are clean rejections.

A transport failure or server-side failure after the placement call is treated
as an unknown close outcome and carries:

`close_outcome_unknown_check_order_detail`

The caller must reconcile with order detail before any retry. Blind retry is
not permitted.

## Explicit non-authority

This module is intentionally not exported into scanner/runtime paths.

It does not:

- detect setups;
- pick option contracts;
- decide exits;
- read strategy state;
- submit from the options scanner;
- submit from the non-Strat observer;
- alter risk limits;
- touch futures;
- connect to `api.webull.com`;
- authorize live trading.

## Morning sandbox proof

A controlled proof needs the user's existing sandbox credentials/config on the
authorized machine and must be run during a supported option trading session.

Use one contract only.

Required sequence:

1. Verify all Webull sandbox/paper and live-off flags.
2. Verify the selected account is the same `INDIVIDUAL_CASH` sandbox account.
3. Discover one liquid option contract.
4. Create a unique entry ticket.
5. Broker-preview the one-contract BUY_TO_OPEN.
6. Submit it to sandbox.
7. Query order detail by entry client-order id.
8. If it remains unfilled, cancel it and prove the cancel/detail transition.
9. For round-trip proof, use a filled entry only.
10. Create a different exit ticket.
11. Build `SELL_TO_CLOSE` for the exact same symbol/strike/expiry/type and no
    more than the filled quantity.
12. Broker-preview the exact close request.
13. Submit the close.
14. Query close order detail until terminal using bounded/manual checks; no
    uncontrolled polling loop.
15. Verify the position count/quantity reflects the close.
16. Record sanitized entry/exit ids, timestamps, states and fill prices.
17. Do not log credentials, tokens or full account identifiers.
18. Reconcile the full round trip against the internal paper record.

Passing unit tests is not enough. Until this sandbox proof passes, this module
must remain unwired.

## Promotion gate

Only after the round-trip proof succeeds may a separate PR propose a
**default-off Webull paper mirror** for internally approved paper candidates.

That later PR must still preserve:

```
internal paper journal = evidence of record
Webull sandbox         = visibility/reconciliation mirror only
Webull live            = disabled
```

No result from Webull paper is strategy validation or evidence of live fills.
