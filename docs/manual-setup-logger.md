# Manual setup logger

**Status:** observe-only research evidence. This tool never detects a setup,
evaluates a gate, authorizes a trade, or calls a broker.

The sample clock starts only after the 15-minute feed has separately passed its
acceptance proof. Any records made earlier must be treated as preflight data.

## Record a setup

Create a JSON object containing:

- `strategy`, `contract_version`, `signal_timestamp`, `instrument` (`MNQ` or
  `MES`), and `direction` (`LONG` or `SHORT`)
- `original_bracket`: `entry`, `stop`, `t1`, and nullable `t2`
- `decision`: `TAKEN` or `SKIPPED`; skipped records require `skip_reason`
- `context`: optional `zone`, `vwap`, and `signa` snapshots. Each available
  snapshot requires `available=true`, `observed_at`, `source`, and a non-empty
  `data` object. Missing context is recorded explicitly and is never inferred.
- `provenance`: `source`, `recorded_by`, and optional `notes`

Then run:

```bash
python3 -m research.manual_setup_logger record \
  --input /path/to/setup.json \
  --log-dir /path/to/logs
```

To copy context from the existing observer, add:

```bash
--context-log /path/to/logs/strategy_context_observations.jsonl
```

The join requires exactly one row with the same normalized instrument and
timestamp. It will not use a nearest observation because no authoritative
freshness tolerance has been defined. The existing observer supplies zone and
VWAP data but not Signa, so Signa remains unavailable unless it was explicitly
observed and supplied with provenance.

## Resolve a setup

Resolution is a second append-only row keyed by the printed `setup_id`. A taken
setup requires its actual fill plus commission, fees, and slippage. A skipped
setup cannot contain a fill. `shadow_outcome.result` is one of
`STOP_FIRST`, `T1_FIRST`, `T2_FIRST`, or `NEITHER_BY_CUTOFF`. `T1_FIRST`
requires T1 without T2; `T2_FIRST` requires both target flags. The `t1_hit`,
`t2_hit`, and `stop_hit` booleans retain the complete path. Signal, fill, and
resolution timestamps must be causally ordered. Do not append a resolution
while the outcome is unknown: the setup row already records
`shadow_outcome.status=PENDING`, and the immutable resolution slot is reserved
for the final result.

```bash
python3 -m research.manual_setup_logger resolve \
  --input /path/to/resolution.json \
  --log-dir /path/to/logs
```

The resolver copies the original bracket from the immutable setup row and
hashes it. This prevents a later outcome from silently being evaluated against
adjusted levels.

Output is `manual_setup_observations.jsonl`. Setup identity is deterministic
from strategy, contract version, timestamp, instrument, and direction.
Duplicate setup and resolution rows fail closed.
