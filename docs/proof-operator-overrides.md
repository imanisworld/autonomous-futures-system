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
