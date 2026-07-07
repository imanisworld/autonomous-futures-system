# Proof Operator Overrides

This file records operator-approved proof exceptions that must not be
implemented by rewriting append-only journals.

## 2026-07-06 - MES broker-verified win misbooked as CANCELLED

- Scope: operator proof review only
- Instrument: `MES`
- Session date: `2026-07-06`
- Trade window: approximately `14:45Z` to `15:3xZ`
- Broker evidence: demo account `realized_pnl = +60.60`
- Journal history: the trade's `TRADE` row was later paired with
  `OUTCOME=CANCELLED` / `exit_reason="phantom cleared"` because the pre-fix
  reconciler cleared a completed trade without checking entry fills first
- Root cause: fixed by `#146` / commit `4332d09`

### Operator ruling

Count this event as a broker-verified resolved win for manual proof review.

### Why the journal was not edited

- The live journal is append-only and should remain the historical record of
  what the box actually wrote at the time.
- Appending a synthetic `WIN` row would rewrite history and could corrupt
  TRADE-to-OUTCOME pairing used by journal-based proof tooling.

### Audit caveat

- Current automated proof tooling is journal-driven and MNQ-specific.
- A raw proof re-scan will not automatically count this MES win.
- Do not let a mechanical scan overrule this documented operator exception
  without reviewing the broker evidence and the incident note.

### Classification note

- This override is recorded as a verified `MES` win.
- It does not automatically change any running `MNQ`-only proof tally.

## 2026-07-07 - Full-history phantom-clear audit (19 additional cases)

### Background

The 2026-07-06 incident above proved the pre-`#146` reconciler could erase a
**real completed trade** behind an `OUTCOME=CANCELLED "phantom cleared"` /
`"auto-reconcile"` row. A same-day sweep for a second occurrence checked only
a recent window and found none, and was read as "no other erased trades" —
that reading was incomplete, not wrong for the window it covered.

On 2026-07-07 the full journal history
(`/root/autonomous-futures-system/logs/journal_*.jsonl`, 2026-06-08 onward)
was scanned using the production pairing function
`ops.proof_30_mnq.pair_resolved_trades` (imported and run for real over SSH,
per-instrument, not reimplemented) filtered for `exit_reason` containing
`"phantom cleared"` or `"auto-reconcile"`. This found **21 total
phantom-cleared rows** (5 `MNQ`, 16 `MES`). Two were already individually
reconstructed (07-02 `MES orb_reclaim` — genuine no-fill; 07-06
`MES orb_breakout` — the erased win above). The other **19 were audited for
the first time here**, using the same reconstruction method as the two
solved cases:

1. Pull each `TRADE` row's `setup.entry` / `setup.direction` and
   `context.close` (the decision bar's close — the market price at the
   moment the live IOC entry order was sent).
2. Compute the live IOC cap from
   `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES=16` (1.0 pt) /
   `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ=32` (8.0 pt) — the box's `.env` value,
   which matches the codebase's long-standing fallback default
   (`config/settings.py`) for the entire audited window; no evidence of a
   different historical value was found.
3. `LONG`: cap = entry + tolerance, marketable (fills) if `close <= cap`.
   `SHORT`: cap = entry - tolerance, marketable (fills) if `close >= cap`.
   This is the exact arithmetic that resolved both prior cases
   (07-02: entry 7584.00 + 4.00 = cap 7588.00 < close 7588.75 → correctly
   unmarketable; 07-06: entry 7573.75 + 4.00 = cap 7577.75 > close 7575.75 →
   marketable, and it did fill).
4. For every row found marketable (an erased-fill candidate), the box's own
   live 15-minute bar feed (`logs/bars_{MES,MNQ}_YYYY-MM-DD.jsonl`) was
   walked forward from the fill bar to find whichever of stop/target was
   touched first (`high`/`low` vs. level), the same win/loss determination
   the live resolver itself makes.

### Result: 13 of 19 are genuine no-fills (no change)

These decision-bar closes were beyond the live IOC cap — the order really
was unmarketable and `CANCELLED $0` is the correct, honest outcome, exactly
like the 07-02 case:

| Instrument | TRADE ts (UTC) | Strategy | Dir | Entry | Close | Cap | Overshoot |
|---|---|---|---|---|---|---|---|
| MNQ | 2026-06-11T07:30:01 | orb_reclaim | LONG | 28818.5 | 28848.75 | 28826.5 | 22.25 pt |
| MNQ | 2026-06-15T08:15:01 | orb_reclaim | LONG | 30565.75 | 30580.5 | 30573.75 | 6.75 pt |
| MNQ | 2026-06-15T14:30:01 | strat_4hr_retrigger | LONG | 30735.75 | 30759.5 | 30743.75 | 15.75 pt |
| MNQ | 2026-06-16T08:15:03 | orb_reclaim | LONG | 30848.75 | 30871.0 | 30856.75 | 14.25 pt |
| MES | 2026-06-15T20:00:03 | orb_breakout | LONG | 7616.75 | 7630.25 | 7620.75 | 9.50 pt |
| MES | 2026-06-16T03:45:12 | orb_breakout | LONG | 7616.75 | 7626.5 | 7620.75 | 5.75 pt |
| MES | 2026-06-16T11:15:15 | orb_breakout | LONG | 7616.75 | 7631.25 | 7620.75 | 10.50 pt |
| MES | 2026-06-16T14:30:03 | vwap_hold | SHORT | 7624.25 | 7612.0 | 7620.25 | 8.25 pt |
| MES | 2026-06-16T15:00:05 | orb_breakout | SHORT | 7623.75 | 7611.25 | 7619.75 | 8.50 pt |
| MES | 2026-06-19T02:15:10 | vwap_hold | SHORT | 7559.5 | 7544.75 | 7555.5 | 10.75 pt |
| MES | 2026-07-01T06:15:20 | vwap_hold | SHORT | 7535.5 | 7526.5 | 7531.5 | 5.00 pt |
| MES | 2026-07-01T07:15:19 | vwap_hold | SHORT | 7533.75 | 7515.5 | 7529.75 | 14.25 pt |
| MES | 2026-07-01T13:45:21 | vwap_hold | SHORT | 7527.5 | 7514.75 | 7523.5 | 8.75 pt |

No operator action needed on these 13; they are documented here only to
close out the full-history audit.

### Result: 6 of 19 were marketable — erased fills, not phantoms

Each was within the live IOC cap at decision time and should have filled.
Forward-walking the box's own 15-minute bar feed shows what actually
happened to each position. **Unlike the 07-06 case, none of these have live
broker confirmation** (the demo account history for these dates is gone) —
these are bar-data reconstructions, same evidentiary tier as the 07-02
no-fill determination, not broker-verified like 07-06.

#### 2026-06-08 - MES `strat_4hr_retrigger` LONG — erased LOSS

- Scope: operator proof review only
- Instrument: `MES`
- Session date: `2026-06-08`
- Trade window: `15:00:10Z` entry → stop hit in the `15:15:00Z-15:30:00Z` bar
- Evidence: `TRADE` entry=7469.75, stop=7467.5, target=7484.75, `context.close`=7473.25 → cap 7473.75 (entry+4.00) → marketable by 0.50 pt. Forward bars: fill-bar+1 (`15:00-15:15`) O=7473.25 H=7476.75 L=7468.5 C=7470.5 (no touch); fill-bar+2 (`15:15-15:30`) L=7453.75 ≤ stop 7467.5 → **STOP hit**, target (7484.75) never approached (max high across the sequence 7476.75).
- Reconstructed result: **LOSS**, entry≈7473.25 → stop 7467.5 = 5.75 pt = 23 ticks × $1.25 = **-$28.75** (1 contract)
- Root cause: same pre-`#146` reconciler bug — cleared a real filled+stopped-out position as a phantom because it never checked entry fills.
- Operator ruling (2026-07-07): **Count as a confirmed loss.** The reconstruction has no ambiguity (marketable by 0.50pt, single clean stop touch); ruled consistently with the equally clean 06-29/06-30 cases below.
- Why the journal was not edited: same as above — append-only, TRADE-to-OUTCOME pairing must not be corrupted.
- Audit caveat: reconstructed from the box's own 15m bar feed, not broker fill records (unavailable for this date). Automated `ops.proof_30_mnq` scans will not count this without this override being applied by a human/tool that reads this file.
- Classification note: recorded as a reconstructed `MES` loss; does not auto-change any running `MNQ`-only tally.

#### 2026-06-09 - MES `orb_breakout` LONG — erased LOSS

- Instrument: `MES`, session date `2026-06-09`, trade window `10:15:08Z` entry → stop hit `11:15:00Z-11:30:00Z`
- Evidence: entry=7447.25, stop=7444.75, target=7462.25, close=7449.0 → cap 7451.25 (marketable by 2.25 pt). Forward bars never approached target (max high 7456.0); bar+5 (`11:15-11:30`) L=7444.75 == stop → **STOP hit**.
- Reconstructed result: **LOSS**, entry≈7449.0 → stop 7444.75 = 4.25 pt = 17 ticks × $1.25 = **-$21.25** (1 contract)
- Operator ruling (2026-07-07): **Count as a confirmed loss.** Same reasoning as 06-08 above — marketable by 2.25pt, single clean stop touch, no ambiguity.
- Same root cause / journal-not-edited / audit-caveat / classification notes as above.

#### 2026-06-18 - MES `vwap_hold` SHORT — erased LOSS (marginal marketability)

- Instrument: `MES`, session date `2026-06-18`, trade window `11:15:11Z` entry → stop hit `12:30:00Z-12:45:00Z`
- Evidence: entry=7548.25, stop=7555.75, target=7525.75, close=7544.5 → cap 7544.25 (entry-4.00) → marketable by only **0.25 pt (1 tick)** — the thinnest margin of any case in this audit, worth extra scrutiny before counting. Forward bars: low never got within 6 pt of target 7525.75 (lowest low 7531.25); bar+6 (`12:30-12:45`) H=7559.5 ≥ stop 7555.75 → **STOP hit**.
- Reconstructed result: **LOSS**, entry≈7544.5 → stop 7555.75 = 11.25 pt = 45 ticks × $1.25 = **-$56.25** (1 contract)
- Operator ruling (2026-07-07): **Count as a confirmed loss.** Independently re-verified before ruling: the decision-bar close (10:45Z-11:00Z bar) was cross-checked against the locally-held Polygon feed (`data/replay_polygon/MES/MES_2026-06-18.jsonl`), a source independent of the box's own live bar feed. Both report the identical close, **7544.5**, with zero discrepancy — not a partial tick apart. The 1-tick marketability margin is therefore confirmed real, not a data-vendor rounding artifact, and this case is promoted out of "lowest-confidence" status.
- Same root cause / journal-not-edited / classification notes as above.
- Audit caveat (superseded): the original note below flagged that a 1-tick rounding difference between data vendors would flip this to a no-fill — the independent-source check above rules that out.

#### 2026-06-29 - MES `pdh_reclaim` LONG — erased LOSS

- Instrument: `MES`, session date `2026-06-29`, trade window `10:45:11Z` entry → stop hit `12:30:00Z-12:45:00Z`
- Evidence: entry=7462.0, stop=7455.0, target=7477.5, close=7462.0 (entry == close, unambiguously marketable). Forward bars never reached target (max high 7473.75, 3.75 pt short); bar+6 L=7455.0 == stop → **STOP hit**.
- Reconstructed result: **LOSS**, entry 7462.0 → stop 7455.0 = 7.0 pt = 28 ticks × $1.25 = **-$35.00** (1 contract)
- Audit caveat: the live bar feed has two missing 15m bars in this window (`11:00Z` and `11:45Z` absent — a live-feed gap, not a reconstruction choice); the highest high observed in the surrounding data (7473.75) stays well clear of the 7477.5 target, so a missed spike-and-reverse inside a gap is possible but would need an unusually large round-trip to change the outcome.
- Operator ruling (2026-07-07): **Count as a confirmed loss.** The 2-bar gap doesn't change the stop-hit conclusion given how far the surviving highs stay from target. Same root cause / journal-not-edited / classification notes as above.

#### 2026-06-30 - MES `pdh_reclaim` LONG — erased LOSS

- Instrument: `MES`, session date `2026-06-30`, trade window `10:15:02Z` entry → stop hit `12:15:00Z-12:30:00Z`
- Evidence: entry=7507.0, stop=7500.0, target=7522.5, close=7507.0 (entry == close, unambiguously marketable). Forward bars got as close as 7517.5 to the 7522.5 target (bar+4/+7) but never touched it; bar+9 L=7495.75 ≤ stop 7500.0 → **STOP hit**.
- Reconstructed result: **LOSS**, entry 7507.0 → stop 7500.0 = 7.0 pt = 28 ticks × $1.25 = **-$35.00** (1 contract)
- Operator ruling (2026-07-07): **Count as a confirmed loss.** No data gaps, entry==close unambiguous marketability, clean stop touch. Same root cause / journal-not-edited / classification notes as above.

#### 2026-07-01 - MNQ `orb_rejection` SHORT — erased fill, WIN/LOSS **UNRESOLVED**

- Instrument: `MNQ`, session date `2026-07-01`, trade window `16:00:09Z` entry
- Evidence: entry=30260.0, stop=30262.0 (tight, 2 pt / 8 ticks), target=30245.0, close=30254.75 → cap 30252.0 (entry-8.0) → marketable by 2.75 pt.
- Forward reconstruction attempted at three resolutions, all inconclusive because **both stop and target fall inside the same bar at every resolution tried**:
  - 15m bar (`16:00-16:15Z`): O=30254.75 H=30277.25 L=30222.75 C=30241.75 — both touched.
  - 5m bar (`16:00-16:05Z`): O=30254.75 H=30277.25 L=30235.0 C=30243.25 — both touched.
  - 1m bar (`16:00-16:01Z`, Polygon `MNQU6`): O=30254.75 H=30264.0 L=30245.0 C=30255.5 — both touched (low hits target exactly; high clears stop by 2 pt).
  - Second-resolution Polygon data is not available on the current API tier (403); no ORDER_IDS row was journaled for this trade (ORDER_IDS logging appears to have been silently absent box-wide on 2026-07-01, a separate finding worth its own follow-up), so there is no broker order id to query for a fill-by-fill record either.
- Reconstructed result: **genuinely unresolved**. Bounded outcomes: if target hit first → WIN, entry 30254.75 → target 30245.0 = 9.75 pt = 39 ticks × $0.50 = **+$19.50**; if stop hit first → LOSS, entry 30254.75 → stop 30262.0 = 7.25 pt = 29 ticks × $0.50 = **-$14.50** (1 contract either way).
- Operator ruling (2026-07-07): **Treat as unresolved/excluded.** Confirmed no further verification is possible: the second-resolution Polygon pull needed to disambiguate is blocked (403) on the current data-vendor tier, and no broker order id was journaled that day to check against. This stays a permanent data gap, not a guessed W or L — it does not count toward either side of the honest tally.
- Root cause / why-not-edited: same as above.
- Classification note: recorded as a confirmed marketable/erased fill with **indeterminate** W/L; not counted toward either a win or loss tally pending resolution or an operator decision to accept the bounded range.

### Summary / total impact of the 2026-07-07 audit

- 21 total phantom-cleared rows found since 2026-06-08; 2 previously solved (07-02 no-fill, 07-06 erased win); **19 newly audited here**.
- 13 of 19 confirmed **genuine no-fills** — no change, `CANCELLED $0` stands.
- 6 of 19 were **erased fills**, all previously invisible to the honest filled-trade count and P&L:
  - 5 MES, all reconstructed as **LOSSES**: -$28.75, -$21.25, -$56.25, -$35.00, -$35.00 → **-$176.25 total**.
  - 1 MNQ, **unresolved** W/L (bounded +$19.50 / -$14.50).
- None of this is broker-confirmed the way the 07-06 win was — every number above is a bar-data reconstruction and should be labeled as such if it enters any report.

### RESOLVED 2026-07-07 (evening) — operator rulings on all 6 pending cases

All 6 "(pending)" placeholders above are now resolved. Per-case rulings inline; summary:

- **All 5 MES cases counted as confirmed losses** (no exclusions): 06-08 -$28.75, 06-09 -$21.25, 06-18 -$56.25, 06-29 -$35.00, 06-30 -$35.00. Total **-$176.25**.
  - The 06-18 case (originally flagged as the lowest-confidence, 1-tick-margin call) was independently re-verified before ruling: the decision-bar close was cross-checked against a locally-held Polygon feed (`data/replay_polygon/MES/MES_2026-06-18.jsonl`), a source independent of the box's own live bar feed. Both agree exactly on 7544.5 — the 1-tick marketability margin is real, not a data-vendor rounding artifact.
- **1 MNQ case (07-01) confirmed permanently unresolved/excluded** — does not count toward either W or L. No further verification is possible (second-resolution Polygon data 403s on the current tier; no broker order id was journaled that day).
- **Net effect on the honest tally**: +5 filled trades (all losses, -$176.25 total) beyond the 07-06 erased-win case; +1 known unresolved data gap (excluded). This moves the honest cumulative P&L **down**, opposite direction from the 07-06 case — it does not net out against it.
- These are ops-proof-review facts only; they do not change any automated `ops.proof_30_mnq` MNQ-only tally by themselves. A human (or a future tool reading this file) must apply them. Next step (not done here): rebuild the honest filled-trade baseline incorporating these rulings.
