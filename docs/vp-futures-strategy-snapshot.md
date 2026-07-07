# VP Futures Strategy Snapshot — 2026-07-07

## Status

**Reference material only. Not a promoted strategy and not wired into `strategy/signal_engine.py`.**
This doc is a clean compilation of the manual Strat-based trading rules,
position-sizing table, and gate rules the operator has been trading by hand
(TradingView + Robinhood/Tradovate). It exists so these rules can be reviewed,
tested, and — if and when a strategy is promoted — implemented as a discrete
module under `strategy/` with its own replay evidence, following the same
promotion gate used for `orb_reclaim` (see
`docs/mes-orb-reclaim-deepdive-2026-07-06.md`). No code changes accompany this
doc.

## Position sizing (manual reference)

| Account size | Sizing |
|---|---|
| $0–$499 | Half portfolio per trade |
| $500–$999 | 1 contract |
| $1,000–$5,000 | 3 contracts, 3 main setups only |
| $5,000+ | Contact Ayce |

## Strat candle definitions

Matches the canonical types already implemented in `strategy/strat_classifier.py`
(`inside_bar`, `two_up`, `two_down`, `outside_bar`):

| Code | Meaning | Rule |
|---|---|---|
| `1` | Inside bar | High and low inside the prior candle's range |
| `2U` | Directional up | Breaks the prior candle's high only |
| `2D` | Directional down | Breaks the prior candle's low only |
| `3` | Outside bar | Breaks both the prior candle's high and low |

## Strategy 1 — 4HR Re-Trigger

Platform: TradingView, 4HR candles, extended hours ON, ET timezone.
**Reversal setup — not a 2-2 continuation.**

**Calls**
- 4AM candle = 2D
- 8AM candle = 2U (triggering candle); must trigger 2U then retrace below the trigger by 9:30 AM
- 9:30 AM check: price still below the high of the 4AM candle → setup valid
- Entry: break above the high of the 4AM candle, immediate — no 50% breach required
- Target: high of the 4PM ET candle

**Puts**
- 4AM candle = 2U
- 8AM candle = 2D (triggering candle); must trigger 2D then retrace above the trigger by 9:30 AM
- 9:30 AM check: price still above the low of the 4AM candle → setup valid
- Entry: break below the low of the 4AM candle, immediate
- Target: low of the 4PM ET candle

## Strategy 2 — 12HR Miyagi

Platform: TradingView, 12HR candles, extended hours ON, ET timezone.

**Candle sequence (1-3-1)**
1. 4PM candle: Inside (`1`) — setup begins
2. 4AM candle: Outside (`3`) — range expansion
3. 4PM candle: Inside (`1`) — establishes trigger
4. 4AM candle: live — must be 2U or 2D

**Trigger**: `(high + low of candle 3) / 2`. Same trigger price is used for both calls and puts.

**Entry conditions**
- Candle 4 opens 2U → price must be above the trigger at 9:30 AM → enter **puts** when price hits the trigger
- Candle 4 opens 2D → price must be below the trigger at 9:30 AM → enter **calls** when price hits the trigger

**Invalidation**
- If candle 3 (the inside bar) becomes an outside bar before 9:30 AM → setup void
- 60-minute flip after entry = exit for loss:
  - In puts: price breaks above the high of the last 60m candle
  - In calls: price breaks below the low of the last 60m candle

**Targets**
- First target: high/low of candle 3 (the inside bar)
- Final target: high/low of candle 2 (the outside bar)
- Sub-targets: drop to 4HR or 1HR for internal highs/lows inside the outside bar's range

## Strategy 3 — 60M 322 First Live

Timeframe: 60-minute chart.

- 8:00 AM: outside bar (`3`) forms
- 9:00 AM: directional bar (`2`) forms, direction irrelevant — mark its high and low
- 10:00 AM: directional bar in the **opposite** direction to the 9AM bar
  - 9AM was 2U → 10AM must be 2D → puts
  - 9AM was 2D → 10AM must be 2U → calls

Target: high of the 8AM outside bar. Approach: take base hits until confident.

## Strategy 4 — 4HR 2-2 Rev Retrigger

Timeframe: 4-hour chart.

- 4AM: directional bar (2D or 2U)
- 8AM: opens inside the prior bar, then triggers the opposite direction
  - 4AM was 2D → 8AM goes 2U → calls
  - 4AM was 2U → 8AM goes 2D → puts
- Wait for a pre-market pullback (wick), enter on the actual 2-2 reversal
- Price must stay below/above the trigger level before 9:30 AM
- Ultimate target: high or low of the candle **before** the 4AM bar
- Support: 60m and 15m pivot points for entry/exit guidance

## Strategy 5 — 1-3-1 Miyagi (ATH combo variant)

Same structure as Strategy 2, noted separately in the ATH Combos source material.

- Candle 4 goes 2U → 1-3-1 2U setup → take **puts**
- Candle 4 goes 2D → 1-3-1 2D setup → take **calls**
- Pre-market midpoint fade risk: if pre-market holds above/below the midpoint, that
  level may act as support/resistance rather than reversing — wait for the first hour
  to confirm before entering.

## 50% breach rule

Applies only where specified — **not** on the 4HR Re-Trigger or Miyagi setups, which
enter immediately on trigger.

- Calls: the live candle must push 50% of its own range above the trigger before entry
- Puts: the live candle must push 50% of its own range below the trigger before entry
- Example: a 10pt candle must close 5pts past the trigger before entry is valid

## FOMC strategy

Timeframe: 30-minute chart.

- Statement candle opens 11:00 AM PT (1:00 PM CT) — creates the range; do not trade this candle
- Conference candle opens 11:30 AM PT (1:30 PM CT) — finds direction
  - Breaks above the statement candle's high → calls
  - Breaks below the statement candle's low → puts

## Signa gate rules

- Weekly Action Card = direction filter, overrides the Daily card
- Daily Action Card = timing only
- Grade A or B required to enter; Grade C = watch only, no entry
- Weekly bullish + Daily bearish = pullback in an uptrend = potential long entry, **not** a reversal signal
- Weekly bearish → remove the symbol from the long watchlist entirely
- Screener output is discovery only — never enter directly from a screener hit
- Complete steps 3–5 (Action Card → chart → Ask Signa) before any entry

**Strat + Signa alignment**
- Enter only when the Signa weekly direction matches the Strat candle's direction permission
- 2U daily candle + weekly bullish = confirmed long setup
- A strong daily grade alone does not override an opposing weekly direction

## Relationship to the running system

These are the operator's manual/discretionary rules and are **separate** from the
strategies currently implemented and gated in this repo (see `strategy/signal_engine.py`,
`strategy/gex_gate.py`, `strategy/signa_gate.py`, `strategy/regime_classifier.py`).
Existing system context worth noting for any future integration work:

- Targets: MNQ/MES futures, options secondary
- Broker: Tradovate
- Signal source: TradingView webhooks, 30-second polling
- `strategy/regime_classifier.py` already outputs `RANGE_BOUND`, `CHOPPY`, and
  trending regimes
- GEX gate: price at/near GEX support with a confirmed bounce and clean room to
  the next resistance level
- Signa gate (as implemented): Grade A/B required, weekly direction match or neutral
- Daily candle type governs direction permission: `2U` = longs only, `2D` = shorts
  only, `1` = no trade, `3` = both directions with higher-timeframe confirmation
- Missing data resolves to a neutral status, not a crash or hard block
- 318+ passing tests at time of writing — any future promotion of these manual
  strategies must not break them

## Not yet answered

Promoting any of the strategies above into `strategy/` would need, at minimum:
replay evidence over a multi-quarter window, walk-forward robustness (per the
`orb_reclaim` promotion gate), and an explicit decision on which of MNQ/MES it
targets. None of that work is done here — this doc only records the rules as
given.
