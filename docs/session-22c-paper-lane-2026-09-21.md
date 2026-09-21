# Session-scoped 2-2 continuation forward paper lane (H6 / H7)

**Built 2026-09-21 from the pre-registration in
`prereg-mnq-volume-label-and-sunday-reopen-2026-09-21.md` (H6, H7). Default OFF.
Paper-only by construction. Arming it is an `.env` change + restart and
therefore a post-2026-09-30 step or an explicit operator ruling.**

## What it is

An isolated sibling lane (same pattern as the Asia D+EMA cohort, #595) that
takes the two cells of the 313-day grid that beat every random permutation and
runs them prospectively, with their own campaign id, epoch, state file and
evidence file:

| Sub-lane | Bars | Filter | Target | In-sample (grid) |
|---|---|---|---|---|
| `asia` (H6) | `session == asian`, not in the Sunday window | candidate direction == payload EMA trend (UP→LONG / DOWN→SHORT); label **TRENDING or RANGE_BOUND** (CHOPPY / DEAD excluded) | **1.5R** | grid n=1,049 PF 1.25; one-at-a-time n=553 PF 1.20; by label RANGE_BOUND 1.42 / TRENDING 1.26 / CHOPPY 0.83 / DEAD 0.89 |
| `sunday` (H7) | Sun 22:00Z ≤ ts < Mon 01:00Z | **none** | **1.0R** | grid n=139 PF 1.75; one-at-a-time n=85 PF 1.54 |

Candidate source is the runner's existing observe-only `shadow_candidates`,
strategy `strat_22_continuation_observed` only. Fill is the canonical
PaperBroker `ioc_limit` at the decision-bar close (1 adverse tick, 32-tick
tolerance, pessimistic both-hit). One open position per sub-lane; a second
candidate while busy is journaled `CANDIDATE_SKIPPED_BUSY`. Open positions
expire at the CME 18:00 ET observation-day roll and are never carried.

It never touches the real book, the demo account, the other lanes' files, or
the decision the runner returns for the bar.

## Files

- `context/session_22c_paper_lane.py` — the lane (imports the cohort's fill /
  advance / resolve functions; no second implementation).
- `tests/test_session_22c_paper_lane.py` — 24 tests: default OFF + no I/O,
  exact-token activation, settings validation, proof pins, no broker import,
  Sunday window boundaries and precedence, EMA filter + CHOPPY/DEAD exclusion,
  1.5R / 1.0R re-anchoring, family restriction, per-day/per-lane dedupe,
  independent sub-lane positions, busy skip, day-roll expiry, pre-epoch
  recording, wrong instrument/timeframe ignored, invalid state fails closed.
- `config/settings.py` — `session_22c_paper_mode` (`off` | `paper_sim`),
  `session_22c_paper_epoch_start`; validation mirrors the cohort's.
- `ops/live_box_guard.py` — both env names are proof-critical (pin with
  `EXPECTED_PROOF_SESSION_22C_PAPER_MODE` / `…_EPOCH_START` when armed).
- `ops/evidence_registry.py` — weekly registry row `session_22c` with the H6/H7
  thresholds encoded.
- `webhook/runner.py` — hook next to the Asia cohort hook, wrapped so an
  exception can never affect the bar's decision.
- Runtime files (only when armed): `<LOG_DIR>/session_22c_lane/evidence.jsonl`
  and `state.json`.

## Arming (not done; requires release + operator GO)

```
SESSION_22C_PAPER_MODE=paper_sim
SESSION_22C_PAPER_EPOCH_START=<UTC ISO, e.g. 2026-10-01T00:00:00+00:00>
EXPECTED_PROOF_SESSION_22C_PAPER_MODE=paper_sim
EXPECTED_PROOF_SESSION_22C_PAPER_EPOCH_START=<same>
```

then a serialized restart (`afs-deploy.sh --lock … --restart … --unlock`,
watcher restart). The code must be on the box first (this is on `main`, not in
release `5c91602`). Rollback = remove the four lines, restart.

## Scoring (per the prereg, one look)

- H6: `lane == asia` OUTCOME rows with `signal/entry_ts` after the epoch; PASS
  = n ≥ 80, net $ > 0 after costs, PF ≥ 1.10, win ≥ 40%.
- H7: `lane == sunday`; PASS = ≥ 6 Sundays and n ≥ 30, net $ > 0, PF ≥ 1.20,
  win ≥ 55%.
- `EXPIRED` rows are scored at the roll-bar mark as the cohort does; the grid
  used a 48-bar horizon, so a scorer may also truncate at 48 bars using
  `bars_seen` — state which was used.
- PASS authorizes a proposal only (session-scoped rule in the executable
  path, demo lane first). Nothing here changes the live decision path.

## Why paper_sim first, not demo

The grid's edge is at 1–1.5R targets, where one extra tick of slippage per
side moves PF materially. The PaperBroker fill model is the same one every
other lane's evidence was produced with, so H6/H7 results are comparable to
the rest of the ledger. Routing to the Tradovate demo account is the *second*
step, once the paper series confirms the grid and real-fill slippage can be
measured against it.
