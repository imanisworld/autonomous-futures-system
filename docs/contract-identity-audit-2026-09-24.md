# Contract-identity audit — routed dated contract vs alert price basis (2026-09-24)

**Type:** read-only audit and design note. No code, config, Pine, corpus or runtime change.
**Basis:** `main@a67e23f`. All line references are to that commit.
**Companion to:** `docs/index-roll-rule-check-2026-09-23.md` (#960), which established the
safety requirement this note scopes: *before broker orders resume, the dated contract that
receives an order must be proven to be the dated contract underlying the alert's prices, or
the order must be blocked.*

## Question

1. What trustworthy dated-contract identity exists at alert creation time?
2. Where is the narrowest place to compare it with Tradovate's resolved contract and fail
   closed?

## Answer in one paragraph

**None today, on either side.** The TradingView alert names only the continuous symbol and
its root; the broker resolves a dated symbol from a calendar rule and has no market data of
its own to sanity-check the alert's prices against that contract. The identity therefore has
to be **added at the alert source**, and the compare belongs in the broker's bracket path
immediately after contract resolution, behind a pin-style enable flag, blocking on absence or
mismatch. No roll-date constant is involved in the check itself.

## 1. What the alert carries

The Pine indicator builds the JSON body itself and fires it with `alert()`
(`tradingview/risksentinel_context.pine:632`); `tradingview/full_context_alert_message.json.tpl`
is a reference copy of the shape. Instrument-identifying content:

| Field / built-in | Value on a continuous chart | Dated contract? |
|---|---|---|
| `ticker` (`{{ticker}}`) | `MNQ1!`, `MES1!`, `M2K1!` | no |
| `syminfo.root` (`.pine:397`) | `MNQ` | no |
| `syminfo.ticker` (`.pine:398`) | `MNQ1!` | no |
| `syminfo.tickerid` (`.pine:114`) | `CME_MINI:MNQ1!` | no |

No expiration built-in and no dated symbol is referenced anywhere in the script or the
template. The webhook model (`webhook/payload.py:35`) keeps `ticker` as a string; the app
reduces it to a root for routing (`webhook/app.py:92`) and the runner carries that root as the
order's `instrument` (`webhook/runner.py:2819`, `execution/broker_interface.py:23`). Entry,
stop and target are absolute prices from the alert (`entry`, `stop`, `target`,
`webhook/payload.py:113-115`).

**Conclusion:** at alert time the system knows the root and the prices, not the contract the
prices belong to.

## 2. What the broker knows

- `_front_month_symbol` (`execution/tradovate_broker.py:62`) derives the dated symbol from the
  nominal 3rd Friday minus `_ROLL_DAYS = 8` calendar days. #960 showed this disagrees with
  TradingView's observed switch by ~2 trade dates (MNQ/MES) and ~3 (M2K) per quarter, and
  ignores holiday-moved expiries.
- `_find_contract_id` (`.py:950`) requires that exact symbol to appear in `/contract/suggest`
  and fails closed otherwise (`CONTRACT_RESOLUTION_FAILED`, `.py:1103-1110`). The stored
  result is `id` and `name` only (`.py:1009-1011`).
- Tradovate REST exposes **no quote endpoint**; market data is WebSocket-only. `get_quote`
  (`.py:2706-2712`) returns the price last seen **from the TradingView webhook**
  (`set_last_price`). The box's Polygon key is blank. So the broker has **no independent
  price** for the resolved contract and cannot detect that an alert price belongs to a
  different contract.

**Conclusion:** a broker-only check is impossible with existing data. The compare needs an
identity supplied by the alert.

## 3. Where the check belongs

`execute_bracket` (`execution/tradovate_broker.py:1046`), immediately after
`_find_contract_id` succeeds (`.py:1103`) and **before** the order body is built
(`.py:1114` onward). This point already:

- runs behind the fail-closed account-routing guard `_verify_account_for_order` (`.py:786`),
  whose rollout pattern (unset pin = log only; set pin = block) is the one to copy;
- blocks on resolution failure via `_cancelled_fill`, so a new block reason lands in the
  existing no-fill taxonomy and journal path without a new mechanism;
- has the resolved symbol in hand (`_contract_symbol_cache[root]`), so the compare is a string
  equality between two normalized dated symbols.

Proposed shape (design only, not implemented here):

| Element | Rule |
|---|---|
| `BracketOrder.contract_hint: Optional[str]` | dated symbol the alert asserts its prices came from, e.g. `MNQZ6` / `MNQZ2026`; `None` when the alert could not establish it |
| enable flag (env, pin-style) | unset → log `alert=<hint> routed=<symbol>` per order, never block; set → enforce |
| `CONTRACT_IDENTITY_UNKNOWN` | flag set and hint is `None` → cancelled fill, no request to Tradovate |
| `CONTRACT_IDENTITY_MISMATCH` | flag set and normalized hint ≠ normalized routed symbol → cancelled fill, no request |
| normalization | root + month code + one-digit year on both sides; anything unparseable = mismatch |

The check contains **no calendar**. It does not replace `_ROLL_DAYS`; it makes a wrong
`_ROLL_DAYS` harmless by refusing the order instead of mis-pricing it.

## 4. Where the identity can come from (ordered by trust)

1. **Empirical price match in Pine — no constant, recommended first.** On each bar, request
   the close of the two candidate dated contracts for the root and emit the one whose
   close-to-close change matches the chart's change; emit `null` when neither matches. This
   is the method #960 used offline to establish the switch dates, applied per bar at the
   source. Budget: the script makes 5 `request.security` calls today; two more are within
   Pine v6 limits. Candidate names must still be generated per quarter, but a wrong or stale
   candidate list yields `null` → `CONTRACT_IDENTITY_UNKNOWN` → block, never a wrong order.
2. **A TradingView built-in that exposes the underlying contract of a continuous symbol**, if
   one exists. Not verified in this audit (TradingView Desktop was not running; it was not
   launched). One check with the chart open decides whether option 1 is needed at all.
3. **Tradovate WebSocket market data** to compare prices broker-side. A new dependency and
   connection lifecycle in the execution path. Not recommended as the first control.

## 5. What this control does not prove

- Name equality at order time, not that every price in the alert was formed on that contract
  intrabar around the exact switch instant. #959 and #960 established switch **dates** only.
- Nothing about research-corpus roll seams (Polygon `DEFAULT_ROLL_DAYS`), which #960 left as a
  separate parity follow-up.

## 6. Not done here (each needs its own review and GO)

- No change to `_ROLL_DAYS`, `_front_month_symbol`, the Pine script, the alert template, the
  payload model, `BracketOrder`, or any test.
- Ordered next steps if approved: (1) verify option 2 with TradingView open; (2) Pine change
  emitting `contract`; (3) payload + `BracketOrder` field; (4) broker check behind the flag,
  log-only first; (5) observe agreement across one quarterly switch; (6) set the flag.
  Broker-order execution remains blocked until step 6 is proven.
