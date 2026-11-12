# AFS-0332 — Track B daily heartbeat (SOURCE ONLY)

Status: QA / HOLD. Main base `94939d582fb376e94a055afdf0008dfcfe54cc9a`.
No release build, deployment, restart, timer change, broker action or VPS write.

## Existing gap

`afs-forward-lanes-daily-proof` previously confirmed futures-bot, health,
release link and pin; this could pass even when no Track B bar or setup was
evaluated. A zero-fill ledger therefore did not distinguish quiet trading from
a stopped collector. The existing `wide_stop_4k` P&L/trade journal remains
unchanged; this is separate observability evidence.

## Proposed behavior

- The MNQ paper 5-minute forward collector checkpoints each valid RTH bar it
  processes in `logs/track_b_heartbeat_state/YYYY-MM-DD.json`. Checkpoint
  fields distinguish **DecisionEngine evaluated** from a tick that fell in the
  strategy window but had no available decision. One bar timestamp is counted
  once even if resent.
- The existing after-16:00 ET DEMO EOD safety fallback seals a **single** row
  for that day's paper session in
  `logs/track_b_daily_heartbeat.jsonl`. The wrapper does not alter existing
  DEMO exit logic or require an open DEMO position. It never turns an absence of
  bars into `NO_SIGNAL`: an unobserved/incomplete day is marked incomplete.
- Each row has session date ET, bar count, last bar timestamp, 4HR evaluation
  count, whether a canonical signal fired, reason if none, whether the
  existing **paper** ledger produced an entry, fallback action and completeness.
  A valid no-signal day is distinct from an entry or unresolved signal.
- The daily proof checks the **last completed weekday** session and refuses
  missing, duplicate, malformed, incomplete or stale rows, stale end-of-day
  bars, and zero actual setup evaluations. After proof PASS it reports
  `OK — no signal`, `SIGNAL_NO_ENTRY`, or `ENTRY_RECORDED`.
- The retired six-micro lane (Oct 18, AFS-0332) is `RETIRED` with PASS when
  its timer is disabled. An unexpectedly active retired timer still fails.

## Safety limitations

No replay reconstruction, retroactive backfill, invented 5-minute ticks, or
changes to strategy, stop, position, account, fills, slippage, risk, or
execution. The heartbeat does not validate the strategy or imply P&L.
An observed 4HR decision with no candidate is valid no-signal evidence.
A missing DecisionEngine result cannot count as a completed evaluation.

The proof uses weekday sessions conservatively: an exchange holiday with no
collection fails visibly rather than silently being marked no-signal. Holiday
calendar handling requires separate verified scope. Existing installed proof
copy is under `/root/afs-shared/ops/` and will **not** automatically update
just because source merges; deployment/install is separately approved only
after the release-integrity hold clears.

Independent Grok exact-head review and green CI required. The current runtime
remains unverified by this PR. No merge or deployment is authorized.
