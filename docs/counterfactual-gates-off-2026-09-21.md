# Counterfactual — gates off, Sunday reopen 2026-09-20 22:00Z → 02:01Z

**Evidence only. No runtime, rule, or config change. Operator-requested after the
MNQ +469-pt move (29,659 → 30,128) went untraded.**

## Simple answer

MNQ missed the move because of a rule, not a bug. MNQ only trades when the engine
labels the market TRENDING. At 22:30Z and 22:45Z it labeled the market RANGE_BOUND,
saw the 2-2 continuation LONG both times, and refused both times. Both would have
hit their 2R targets. No other instrument is allowed to trade (`instrument_universe
= ['MNQ']`), so MES/MCL/MGC/MBT/M2K were never candidates for a paper entry.

## Method

- Source: the live engine's own gate-free candidate stream —
  `cross_instrument_observation_v1.jsonl` CANDIDATE rows with
  `signal_timestamp >= 2026-09-20T22:00:00Z` (61 rows, all six instruments, all
  shadow strategies, emitted regardless of the market-condition / regime /
  universe gates).
- Outcomes: the box's own resolver's OUTCOME rows for those candidates (15m bars,
  stop-first on ambiguous bars, 1 contract, gross). No new detection or fill logic
  was written; nothing re-derived from raw OHLCV.
- Snapshot taken 02:01Z from `/root/afs-shared/logs` files (read-only). Releases in
  window: `98c360d`/`a02320`/`ed1212`/`c7798d4` → `5c91602` (01:57Z, #849 only).
- Aggregation script: scratch, ~60 lines; reproducible by re-pairing
  candidate_id → OUTCOME. Not committed (no research logic to preserve).

## Result — resolved candidates (the number that counts)

**23 fills · 11W / 12L · +10.0R · +$625 gross (1 contract)**

| Instrument | candidates | resolved W/L | closed R | closed $ | unresolved |
|---|---|---|---|---|---|
| MNQ (only allowed) | 8 | 3/1 | **+5.0R** | +$359 | 4 |
| MCL | 13 | 3/1 | +5.0R | +$201 | 9 |
| MBT | 11 | 4/5 | +3.0R | +$102 | 2 |
| MGC | 7 | 1/2 | 0.0R | −$9 | 4 |
| M2K | 11 | 0/3 | −3.0R | −$28 | 8 |
| MES | 11 | 0/0 | — | — | 11 |

### MNQ detail — what each live gate cost

| bar (Z) | candidate | live verdict | gate | counterfactual |
|---|---|---|---|---|
| 22:30 | 2-2 con LONG | NO_TRADE | `MARKET_CONDITION_NOT_TRENDING` (RANGE_BOUND) | **WIN +2R** |
| 22:45 | 2-2 con LONG | NO_TRADE | `MARKET_CONDITION_NOT_TRENDING` (RANGE_BOUND) | **WIN +2R** |
| 23:00 | impulse-pullback LONG | not executable (shadow lane) | — | WIN +2R |
| 23:45 | 2-2 rev SHORT | NO_TRADE | `MARKET_CONDITION_NOT_TRADABLE` (DEAD) | open, mark −2.3R |
| 01:00 | 2-2 con LONG | NO_TRADE | `REGIME_NOT_FULL` | **LOSS −1R** (gate was right) |
| 00:00, 00:15, 00:45 | — | never evaluated (stall, fixed #849) | — | no candidate to test |

Net: the market-condition gate vetoed +4R of executable wins; the regime gate vetoed
a −1R loss.

## Caveats (do not over-read)

1. **One session, n=23, Sunday-night Asian.** Not evidence of expectancy.
2. **Resolved-set bias.** The 23 resolved are the fast movers (2R target or stop
   hit within the window). 38 remain unresolved; marked to the 02:01Z 1m close
   they net ≈ +0.1R *if* every entry filled, which the resolver has not confirmed.
   Excluded from the headline.
3. **Gross, 1 contract.** MNQ/MES have cost proofs; MCL/MGC/MBT/M2K rows carry
   `costs_note: no cost proof for this instrument; gross geometry only`.
4. **Why the gate exists:** the 622-day replay took 0/1274 trades outside TRENDING
   and the validated edge was earned entirely in TRENDING
   (`strategy/signal_engine.py` comment at the gate; `docs/missed-move-gate-sweep-
   622d-2026-07-09.md`). The 09-16 MNQ audits already found gates over-filter
   large-move windows without proving the blocked structures are net positive.
5. The universe rule (`risk_rules.yaml: instruments.allowed = [MNQ]`) and
   `require_trending` are rules changes → post-2026-09-30 freeze unless the
   operator rules otherwise.

## Status

- **No change made.** Ruling requested from operator on whether this session
  changes the standing gate decision; recommendation is to collect more sessions
  (the observation campaign captures the same data every night for free) before
  touching `require_trending`.
- Related: `docs/afsvp-public-surface-audit-2026-09-21.md` (same night),
  #849 (stall fix, released 5c91602 01:57Z).
