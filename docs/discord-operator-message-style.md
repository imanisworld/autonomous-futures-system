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
