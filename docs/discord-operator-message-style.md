# Discord Operator Message Style

Status: active presentation standard.

Use the paper-collection card style as the baseline for operator-facing Discord messages.

## Required shape

Messages should show the operator summary, not raw evidence dumps.

Preferred sections:

- Title / lane
- Status
- Proof or evidence
- Safety / authority boundary
- Action
- Pending gate or artifact reference

## Raw evidence boundary

Discord should not paste long hash lists, full file inventories, raw JSON, raw stack traces, or unbounded collector output by default.

Raw details belong in:

- JSON artifacts
- SQLite rows
- journal/log files
- Git docs
- gate stdout when run manually

Discord should link or name the artifact/log location and keep the body short.

## Required safety language

For observation-only lanes, include the relevant boundary in the message or footer:

- read only
- observation only
- no promotion
- no deploy/restart action
- no risk/order/execution authority

## Standard examples

### Drift gate

Title: `AFS Drift Gate · Attention`

Sections:

- Status: unexpected drift detected
- Summary: `N differ · N missing · N extra`
- Top items: bounded list without hashes
- Action: review raw gate output/log before deploy/reseed

### Daily pass

Title: `Read-only daily pass`

Sections:

- Status
- Evidence audit
- Action
- Artifact

## Shared card layout (every channel)

Plain-text alerts are posted as paper-collection-style embed cards by
`notifications/discord_card.py` — the DiscordRouter (heartbeat, signal, error,
daily_report, observation, deployment, signa), the legacy webhook alert path,
system notifier, options companion, Tradovate session alerts, force-close,
feed-gap alarm, gate-condition report, health digest and weekly review.

Write the text so it lays out well:

- line 1 = title (emoji/status word sets the color: FAIL/DOWN/ERROR red,
  WARN/STALE/GAP amber, OK/PASS/RECOVERED green, otherwise blurple)
- `Key: value` lines = inline fields
- `**Section**` line, then its lines = a full-width field
- last line `READ ONLY …` / `[read-only …]` / `-# …` = footer

Senders that already build embeds (paper collection, paper decisions, options
scanner, drift gate) pass through unchanged. If Discord rejects a card (HTTP
400) the original text is re-sent, so layout can never drop an alert. The
read-only watcher still posts text: it installs as standalone files outside the
release and is not covered.
