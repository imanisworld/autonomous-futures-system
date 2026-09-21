# Webull options position-intent hardening + close-preview gate — 2026-09-21

## Verdict

**PAPER SANDBOX ONLY / CLOSE SUBMISSION STILL BLOCKED.**

This change updates the unwired Webull sandbox options adapter to match the
current Webull option-order contract more explicitly and builds the next
proof gate for a future paper round trip.

It does not wire Webull into the running options scanner and it does not add a
SELL order-placement path.

## Why this change exists

The current Webull API changelog records a 2026-03-28 addition of the
`position_intent` field for options orders with these values:

- `BUY_TO_OPEN`
- `BUY_TO_CLOSE`
- `SELL_TO_OPEN`
- `SELL_TO_CLOSE`

The existing sandbox adapter already described its entry as BUY_TO_OPEN, but
the generated broker payload carried only `side=BUY`; it did not explicitly
carry `position_intent=BUY_TO_OPEN`.

That ambiguity is unacceptable before any controlled paper-order proof.

The current Webull options documentation also states that options support BUY
and SELL, and that sell-side option orders use DAY time-in-force.

Reference:
- https://developer.webull.com/apis/docs/changelog/
- https://developer.webull.com/apis/docs/trade-api/options/

## Entry payload hardening

`options_manager.adapters.webull_sandbox._preview_orders()` now sends these
order-level fields explicitly for the existing sandbox entry path:

```text
instrument_type = OPTION
market = US
symbol = <underlying>
side = BUY
position_intent = BUY_TO_OPEN
time_in_force = DAY
```

The leg remains BUY for the exact CALL/PUT strike and expiration.

No runtime caller was added.

## Filled-entry close preview

`preview_sandbox_paper_option_close()` is a **preview-only** proof gate.

It accepts the original entry preview request plus the broker's read-only entry
order detail and refuses to build a close preview unless:

1. the sandbox/live safety configuration remains valid;
2. real broker preview is explicitly enabled;
3. the original entry still passes the local broker boundary;
4. the entry order detail is `OK`;
5. the entry state is exactly `FILLED` and terminal;
6. detail ticket/client-order identity matches the entry ticket;
7. filled quantity exactly matches entry quantity;
8. the close limit is finite and positive.

Only then does it ask Webull sandbox to preview:

```text
side = SELL
position_intent = SELL_TO_CLOSE
order_type = LIMIT
time_in_force = DAY
instrument_type = OPTION
market = US
same underlying / strike / expiration / CALL-or-PUT / quantity as entry
```

The close client-order id is deterministic and capped at Webull's documented
32-character limit.

The function has **no place call**. It returns `submitted=false`,
`executable=false`, and no broker order id.

## What remains blocked

Before any non-Strat or existing options paper lane can mirror filled positions
through Webull, the box must prove during supported option trading hours:

1. updated BUY_TO_OPEN preview is accepted with `position_intent`;
2. one controlled one-contract sandbox BUY_TO_OPEN placement is accepted;
3. order detail proves the entry state;
4. unfilled cancel still works;
5. a filled entry can produce an accepted SELL_TO_CLOSE preview;
6. only after that proof, implement/review a SELL_TO_CLOSE placement method;
7. then prove the complete filled entry -> close -> final position reconciliation;
8. prove duplicate/retry handling cannot create a second entry or oversell the position.

Until those are complete:

```text
internal option paper simulation = available where otherwise proven
Webull sandbox BUY_TO_OPEN code = present but unwired/default-off
Webull sandbox SELL_TO_CLOSE preview = code-only proof gate
Webull SELL_TO_CLOSE submission = NOT BUILT
live options execution = DISABLED
```

No proof, no run.
