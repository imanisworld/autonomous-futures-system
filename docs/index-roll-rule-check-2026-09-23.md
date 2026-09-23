# Equity-index roll-rule check — MNQ / MES / M2K (2026-09-23)

**Type:** read-only evidence note. No code, config, corpus or runtime change.
**Basis:** `main@991df5f`. Companion to the MCL finding in the 2026-09-23 data-parity audit
(TradingView MCL1! observed rolling 1 business day before the listed last trade date).

## Question

The live bot's signals and prices come from TradingView continuous charts (`MNQ1!`, `MES1!`,
`M2K1!`). Research corpora (Polygon) and Tradovate order routing each choose the front quarterly
contract with their own calendar rule. On which days do those rules disagree with TradingView?

## Method

TradingView Desktop, daily bars, for each root: the continuous `1!` series and the six dated
contracts `U2025, Z2025, H2026, M2026, U2026, Z2026`. For every session in the window
[1st, ~22nd] of each roll month, the continuous series' close-to-close change was matched
exactly against the change of the expiring (O) and the next (N) contract. Changes are
used rather than levels because the continuous chart is back-adjusted. Every session matched
exactly one of O or N; no session was ambiguous. TradingView daily bars are stamped with the
session-open date (Sunday for Monday's session); dates below are converted to CME trade dates.

## Result — observed TradingView switch dates (5 of 5 quarters, all three roots)

TradingView does not publish a fixed offset. Its documented method sets each symbol's switch
rule from that symbol's volume behavior and applies it across the symbol's history, and the
chart's Contract Switch markers show the actual from/to contracts and dates
(TradingView support: "How is the switching date of contracts determined in continuous
futures?" and "Switching continuous futures contracts"). The offsets below are therefore an
**empirical five-quarter pattern**, not a rule we can rely on for the future.

| Quarter | Expiry (last trade) | MNQ1! / MES1! first trade date on new | M2K1! first trade date on new |
|---|---|---|---|
| Sep 2025 | Fri 2025-09-19 | Tue 09-16 | Wed 09-17 |
| Dec 2025 | Fri 2025-12-19 | Tue 12-16 | Wed 12-17 |
| Mar 2026 | Fri 2026-03-20 | Tue 03-17 | Wed 03-18 |
| Jun 2026 | **Thu 2026-06-18** (Juneteenth Fri) | Mon 06-15 | Tue 06-16 |
| Sep 2026 | Fri 2026-09-18 | Tue 09-15 | Wed 09-16 |

- **MNQ1! and MES1! (observed):** on the new contract from the trade date **3 business days
  before the expiring contract's last trade date** in all five quarters (switch at the 18:00 ET
  session open the evening before).
- **M2K1! (observed):** **2 business days before** the last trade date in all five quarters —
  one day later than MNQ/MES.
- The observed offset counts from the *actual* last trade date: in June 2026 expiry moved to Thursday
  06-18 for the Juneteenth holiday (CME's published 2026 roll calendar lists June 18), and
  TradingView rolled a day earlier accordingly. Our tests assume the nominal 3rd Friday
  (`tests/test_tradovate_rollover.py:41`, `tests/test_polygon_client.py:33` use 2026-06-19).
- Sep 2026 MNQ agrees with the live observation already recorded in the parity audit
  (switch 2026-09-14 22:00Z, trade date 09-15).

Roll spread (new − expiring, daily close on the Thursday before expiry week), for scale:
MNQ 212–297 pts, MES 50–68 pts, M2K 15.7–21.5 pts across the five quarters.

## Our rules (as coded on main)

| Component | Rule | Switches to new contract |
|---|---|---|
| Research corpora — `sources/polygon_client.front_contract` (`DEFAULT_ROLL_DAYS=8`) | new when `date >= 3rd Friday − 8 calendar days` | **Thursday**, 8 days before the 3rd Friday |
| Order routing — `execution/tradovate_broker._front_month_symbol` (`_ROLL_DAYS=8`) | old while `ET calendar date <= 3rd Friday − 8` | **Friday 00:00 ET**, 7 days before the 3rd Friday |

Both use the nominal 3rd Friday and ignore holiday-moved expiries.

## Disagreement windows vs TradingView (per quarter)

| Component | MNQ / MES | M2K |
|---|---|---|
| Research corpora (Polygon) | 3 trade dates (Thu, Fri, Mon) on the new contract while TV is still on the old | 4 trade dates (Thu, Fri, Mon, Tue) |
| Order routing (Tradovate) | ~2 sessions: Fri 00:00 ET → Mon 18:00 ET | ~3 sessions: Fri 00:00 ET → Tue 18:00 ET |
| June 2026 (holiday expiry) | corpora 06-11, 06-12; routing Fri 06-12 only | corpora 06-11 → 06-15; routing 06-12 → 06-15 |

The June 2026 corpus row reproduces the "June roll days 06-11 / 06-12" found independently in
the #929 step-0 decomposition.

## What this means

1. **Research-vs-research comparisons are unaffected** (both sides use the same Polygon rule).
   **Live-vs-research parity** will show 3 (MNQ/MES) or 4 (M2K) mismatched trade dates per
   quarter; those days should be treated as roll-seam days, as MCL's were.
2. **Order routing carries a price-basis risk in its window.** Entry, stop and target prices
   are taken from the TradingView alert (`order.entry`, stop and target as absolute prices)
   and sent unchanged to the contract `_front_month_symbol` selects. From Friday 00:00 ET
   until TradingView rolls, the alert prices come from the expiring contract while orders
   go to the next one, which trades ~210–300 MNQ points / ~50–70 MES points higher.
   Nothing in the order path converts between the two.
   - **Not observed in practice:** the June and September 2026 windows show no broker orders
     in the journal (June: 3 OUTCOME rows with no fill; September: shadow posture). This is a
     code-path risk, not a recorded incident.
   - It only matters once the bot places orders again; the current posture is shadow/paper.
3. **The observed M2K offset differs from MNQ/MES.** This is one more reason not to encode any
   inferred offset as a routing rule (see below).

## Not done here (each needs its own review and GO)

- No change to `DEFAULT_ROLL_DAYS`, `_ROLL_DAYS`, any corpus, the #929 / #947 frozen
  evaluators, or order routing.
- **Safety requirement before broker orders resume:** the dated contract receiving an order
  must be proven to be the same dated contract underlying the alert's price basis. If that
  identity cannot be established, the order is blocked (fail closed). The fix must **not**
  replace `_ROLL_DAYS=8` with another inferred constant such as "3 business days";
  TradingView's switch is per-symbol and volume-derived, so any mimicked calendar can drift.
- Separate, lower-priority follow-up: roll-seam exclusion for live-vs-research parity checks
  on the dates above.
