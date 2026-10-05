# Post-cap shadow observation plan — 2026-10-05

**Status:** IMPLEMENTED REPO-SIDE / READ-ONLY REPORTING ONLY / NO DEPLOYMENT AUTHORITY

## Decision

Keep the executable daily cap at **3 trades/day**.

After the cap is exhausted, collect **all** later selected setups rather than only
setup #4 or #5. An arbitrary observation cutoff would bias the evidence and would
not answer whether later opportunities are systematically better or worse.

The reporting split is:

- **#4** — first selected setup after the execution cap;
- **#5** — second selected setup after the execution cap;
- **#6+** — every later selected setup;
- **all post-cap** — combined descriptive view.

This does **not** change the execution cap, daily loss cap, strategy permissions,
broker routing, or live/paper posture.

## What already existed

The runner already does the most important safety part:

1. once the shared journal reaches the configured daily execution capacity,
   the runner sets `BLOCKED_MAX_TRADES`;
2. it still runs the DecisionEngine with `observe_past_capacity=True`;
3. a selected setup is journaled with `observed_decision=TRADE`, its complete
   setup/bracket, the execution block, and context;
4. it returns before stop-multiplier/sizing, RiskEngine, isolated proof lanes,
   broker construction, or order submission;
5. the actual journal trade count stays capped.

`tests/test_runner_budget_observation.py` already protects that behavior.

Therefore no futures-bot runtime change is required merely to **record** #4+.
The source data is already being preserved.

## Missing piece

The existing record did not provide a dedicated, easy-to-read post-cap outcome
report. The generic opportunity resolver also is not universally active under
the current runtime posture.

`scripts/post_cap_shadow_report.py` closes that reporting gap without touching
the runner.

It reads only:

- `journal_YYYY-MM-DD.jsonl`;
- `bars_<INSTRUMENT>_YYYY-MM-DD.jsonl`.

It selects only rows where:

- `decision == BLOCKED_MAX_TRADES`;
- `observed_decision == TRADE`;
- the setup exists;
- `execution_block.limit == 3` by default.

A cap mismatch fails closed.

## Outcome model

For each post-cap setup the report uses the repository's existing
`adaptive.opportunity_tracker.resolve_outcome` model:

- causal future bars only;
- same recorded timeframe when available;
- 8-hour observation horizon;
- one adverse tick on entry;
- one adverse tick on a stop;
- $5 round-trip commission;
- target limit receives no favorable slippage;
- same-bar stop + target resolves pessimistically to the stop.

This is an **isolated counterfactual opportunity model**. It is not a claim that
the exact broker would have filled the order, and it is not an uncapped
account-path replay. The report therefore sets
`broker_fill_parity_claimed=false`.

The distinction matters: after removing the three-trade cap, hypothetical
trade #4 could change daily P&L and therefore change whether a later hypothetical
#5/#6 would pass path-dependent risk gates. This first report intentionally does
not invent that alternate account path.

## Interpretation

Use this report to answer the first question only:

> Are selected setups appearing after trade #3 consistently useful, harmful, or mixed?

Do **not** use an early result to raise the cap automatically.

If the post-cap sample eventually justifies a cap-policy review, preregister a
separate path-dependent account comparison before changing `max_trades_per_day`.
That later study must preserve all other risk gates and must compare the current
three-trade policy against the alternate policy without post-hoc filtering.

## Usage

Read one date:

```bash
python scripts/post_cap_shadow_report.py \
  --log-dir /root/afs-shared/logs \
  --date 2026-10-05
```

Read all available journal days:

```bash
python scripts/post_cap_shadow_report.py \
  --log-dir /root/afs-shared/logs
```

Optional output file:

```bash
python scripts/post_cap_shadow_report.py \
  --log-dir /root/afs-shared/logs \
  --output /tmp/post-cap-shadow.json
```

The script is read-only unless `--output` is supplied, in which case it writes
only the requested report file. It never writes journals, BarHistory, broker
state, or runtime configuration.

## Runtime / 4HR boundary

This work does **not** modify or restart the currently deployed futures release
and does not alter the canonical 4HR natural-1m epoch. No deployment is required
to continue recording post-cap setups because the existing runner already
journals them.

No Polygon. No strategy tuning. No 3-2-2 change. No live authority.
