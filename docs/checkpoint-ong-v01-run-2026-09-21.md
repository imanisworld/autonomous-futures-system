# Checkpoint — ong-v0.1 options run in flight (2026-09-21)

**Purpose:** resume point if the session running this study is interrupted.
Do NOT interpret anything below as a result. Nothing has been written yet.

## Exact commit under test

`main` @ `83cee1e` (includes #873, #874, #876–#884; #885 open, not required
for this run — #885 only touches futures docs/research files).

## Exact command running

```
cd /Users/djb.a.e/MAINVSCODE/autonomous-futures-system
source <alpaca env: two ALPACA_ vars from .env, not shell-sourceable as-is>
python3 research/options_non_strat_underlying_geometry.py --out logs/ong_v01
```

Started as a background process from this session (local Mac), stdout/stderr
redirected to `logs/ong_v01_run_83cee1e.txt` (gitignored, local only).

## PID / background-task status at checkpoint time (2026-09-21, ~05:14 elapsed)

- Local process PID **38886**, state `SN` (sleeping, network-bound — normal
  for the Alpaca fetch loop), started via `nohup ... &` in this session's
  shell. This PID exists **only in this session's shell** — it is not a
  tracked background task with its own notification; if this session ends,
  the process may be orphaned/killed depending on the shell's job control.
- **If this session ends before the run completes, the safest assumption is
  the run did NOT finish** and must be re-launched fresh with the exact
  command above (idempotent: `ong-v0.1` performs no partial writes — see
  "Output paths" below).

## Output / log paths

- Console log: `logs/ong_v01_run_83cee1e.txt` (gitignored)
- Result dir (written only on successful completion): `logs/ong_v01/`
  (gitignored) — did not exist at checkpoint time, confirming no partial
  write. The runner writes JSON/MD only after the full population is
  resolved; there is no partial-output format to salvage.

## Completion state at checkpoint

**NOT COMPLETE.** 0 bytes of console output, no `logs/ong_v01/` directory,
no provider-error lines seen. The fetch (150-symbol + SPY/QQQ universe,
~120 sessions 2026-04-01→2026-09-18, 5m consolidated SIP bars) had been
running ~5 minutes at checkpoint time with no errors.

## Provider errors seen so far

None. (Console log is empty at checkpoint time — the runner does not print
incrementally during the fetch phase.)

## Related PR / branch

- **#885** (`claude/futures-orb-geometry-first-run`) — futures fng-v0.1 +
  orbx-v0.1 results and audit, already committed and pushed, independent of
  this options run. Not blocked on ong-v0.1.
- This options run has not yet produced any file to commit. When it
  completes, the raw JSON/MD under `logs/ong_v01/` will be saved verbatim
  before any interpretation, then a results doc + PR will follow the same
  pattern as #881/#885.

## Resume instructions for another session

1. Check `ps aux | grep options_non_strat_underlying_geometry` — if a
   process is still running, do not start a second one (it is not
   parallel-safe against the same `--out` path).
2. If nothing is running: `git status`/`git log -1` to confirm `main` is at
   `83cee1e` or later (a later commit is fine only if it did not touch
   `research/options_non_strat_underlying_geometry.py`,
   `alert_ranker/non_strat_coverage.py`, or the frozen universe file —
   diff first; if it did, the study is no longer "the exact frozen ong-v0.1
   population" and must not be silently re-run as if unchanged).
3. Re-run verbatim:
   ```
   python3 research/options_non_strat_underlying_geometry.py --out logs/ong_v01
   ```
   with the two `ALPACA_KEY`/`ALPACA_SECRET` (or `_API_` spelling) env vars
   set from `.env`.
4. On completion: save `logs/ong_v01/*.json` and `*.md` verbatim first
   (commit or attach), THEN audit raw rows (population identity, next-bar
   entry timing, tick alignment, stop-first resolution, H1/H2 split,
   fail-closed prior-session handling) before writing any interpretation or
   classification (WAIT / REJECT / PROMISING BUT UNPROVEN only — never
   VALIDATED from this study alone), matching the process already applied
   to fng-v0.1 in `docs/futures-rth-orb-long-geometry-first-run-2026-09-21.md`.
5. Do not alter any geometry cell, the universe, or the H1/H2 split after
   seeing results. Do not populate #875's geometry registry regardless of
   outcome.

## Explicit non-actions (per operator instruction)

- The study was not stopped or modified while running.
- No interpretation, classification, or promotion has been made.
- No geometry was altered.
- #875 remains untouched (HOLD).
