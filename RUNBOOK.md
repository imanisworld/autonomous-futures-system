# Runbook — Autonomous Futures Paper-Trading System

Operational procedures for running, debugging, and maintaining the system.

---

## 1. Starting the System

### Prerequisites

```bash
# Python 3.10+ required
python --version

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Review .env — confirm LIVE_TRADING_ENABLED=false
```

### Run a Single Evaluation Cycle

```bash
python main.py --market-state data/sample_market_state.json
```

### Run Against a Custom Market State

```bash
python main.py --market-state /path/to/your_market_state.json
```

### Dry Run (validate config and market state only, no decision)

```bash
python main.py --market-state data/sample_market_state.json --dry-run
```

### Start TradingView Webhook Receiver

This receives live TradingView alerts and still runs paper-only. It does not
place broker orders.

```bash
python -m webhook
```

Check local health:

```bash
curl http://127.0.0.1:8000/health
```

Run the terminal doctor when something feels stuck:

```bash
python3 scripts/doctor.py
python3 scripts/doctor.py --json
```

Before controlled live/demo validation, confirm the live box drift guard is
green in the doctor output or `/status/diagnostics`. The guard is read-only and
checks the active checkout branch, commit, `risk_rules.yaml` hash, repo/log
paths, and runtime evidence source against the `EXPECTED_*` pins in `.env`.

Open the read-only dashboard:

```text
http://127.0.0.1:8000/
```

Read status JSON:

```bash
curl http://127.0.0.1:8000/status/today
curl 'http://127.0.0.1:8000/status/history?days=7'
curl http://127.0.0.1:8000/status/latest-webhook
curl http://127.0.0.1:8000/status/strategy
curl 'http://127.0.0.1:8000/status/fill-realism?days=7&recent_limit=20'
curl 'http://127.0.0.1:8000/status/review?date=2026-05-23&mode=eod'
```

For one concise, read-only view of the MES trend-consolidation collector and
all four MNQ Strat evidence lanes, run:

```bash
python3 scripts/evidence_lane_health.py --log-dir logs
python3 scripts/evidence_lane_health.py --log-dir logs --json
python3 scripts/evidence_lane_health.py --log-dir logs --date 2026-07-20
```

The default view counts the current UTC day and distinguishes no pattern
matches from stale 15-minute delivery, all-candidate rejection, paper-fill
starvation, pending MES orders, and open paper positions. Historical dates do
not apply current feed freshness or current open-state warnings.

`/status/fill-realism` is read-only and journal-derived. It reports the actual
resolved no-fill rate overall and by setup, the requested sample window, unresolved
attempt count, and recent recorded misses. It does not infer fills from bar prices,
replay, broker state outside the journal, or hypothetical alternate entries.

The review endpoint supports `mode=morning` or `mode=eod` and does not write
review artifacts. Dates must use exact `YYYY-MM-DD` format. Use the daily
summary CLI when you want files written. CLI review artifacts are written by
atomic replacement with unique temp files under a `.daily_review.lock` to
reduce partial-file and interleaving risk.
The CLI exits with a usage error instead of a traceback for invalid dates,
missing risk config, or live-trading block errors.

### Runtime Evidence Freeze

For live/demo-paper evidence on the active Hetzner box, use only these sources:

```text
/root/autonomous-futures-system/logs/journal_YYYY-MM-DD.jsonl
/root/autonomous-futures-system/logs/errors.log
/status/today
/status/broker-account
```

Do not use local ignored `logs/`, replay folders, screenshots alone, Discord
messages alone, or Tradovate P&L alone as proof of an end-to-end system trade.
The next proof window is the next 30 resolved MNQ live/demo-paper trades from
the active box journal after config freeze.

First-class read-only report endpoint:

```bash
curl -s "http://127.0.0.1:8000/status/proof/mnq-30?freeze_ts=2026-06-23T17:00:00Z"
```

Equivalent CLI wrapper:

```bash
cd /root/autonomous-futures-system
python3 scripts/proof_30_mnq.py --freeze-ts 2026-06-23T17:00:00+00:00
```

Use the actual config-freeze timestamp. Both paths read journals/API status
only; they do not write files or touch orders.

At config freeze, pin the active box before running live preflight:

```bash
cd /root/autonomous-futures-system
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
sha256sum risk_rules.yaml
```

Set those values in `.env` as `EXPECTED_LIVE_BRANCH`,
`EXPECTED_LIVE_COMMIT`, and `EXPECTED_RISK_RULES_SHA256`, then run
`python3 scripts/doctor.py --strict` and `/status/live-preflight`.

The same guard inventories proof-critical environment overrides. Any active
override must be pinned to its exact value as `EXPECTED_PROOF_<NAME>` (for
example `EXPECTED_PROOF_VWAP_ENTRY_MAX_DISTANCE_TICKS=12`). Use `<unset>` to
pin absence and catch a knob being introduced mid-window. The guard reports
these values through doctor, `/status/diagnostics`, and live preflight; it never
edits `.env`, configuration, Git state, or broker state.

The guard also reports the PR #96/#99 security-sensitive runtime state. It uses
the loaded application config to say whether `/webhook/manual` is effectively
inert, and reports only presence and distinct-value counts for `WEBHOOK_SECRET`,
`TRADINGVIEW_WEBHOOK_SECRET`, and `TRADINGVIEW_WEBHOOK_SECRET_NEXT`. Secret
values, hashes, prefixes, and lengths are never returned. A missing primary
secret or enabled manual controls is an error; a primary secret without a
distinct staged rotation alias is a warning (and fails `doctor --strict`).

This is repo/process evidence, not an end-to-end deployment attestation. It
cannot prove which systemd unit, reverse proxy, container, or TradingView alert
is active, or that TradingView has switched to the staged secret. Confirm those
separately on the active box without printing credentials.

### Evidence-Chain Reconciliation

Use this workflow for any single trade whose status matters to proof
readiness — a trade you are about to count, exclude, or ask an operator to
rule on. It always starts from the narrow evidence sources above; it never
substitutes replay, Discord, screenshots, or broker P&L alone.

1. **Check journal truth first.** Find the `TRADE` decision row (must have
   `risk_check.result == APPROVED`) and its paired `OUTCOME` row for the same
   instrument, in file order. This pairing is what `scripts/proof_30_mnq.py`
   and `/status/proof/mnq-30` use — the same journal, the same pairing rule,
   every time.
2. **Check `/status/today`** for the same trading day. Confirm `trade_count`,
   `wins`, `losses`, and `today_pnl_dollars` are consistent with what the
   journal shows for that instrument and day.
3. **Only when journal truth looks suspect** — a `CANCELLED`/no-fill outcome
   on a setup that plausibly filled, a reconciler-authored outcome
   (`session: "reconcile"` or an `exit_reason` mentioning phantom/auto-reconcile),
   or a mismatch against `/status/today` — check `/status/broker-account` for
   the same instrument and approximate window. This is the only case where a
   broker-side number is allowed to inform a ruling; it is never consulted
   first, and it never overrides the journal by itself.
4. **Classify the trade into exactly one bucket:**
   - **Normal proof-eligible resolved trade** — journal `TRADE` paired with a
     real `OUTCOME` (`WIN`/`LOSS`/`BREAKEVEN`), no reconciler involvement, no
     broker mismatch. Counts automatically; no operator action needed.
   - **Legitimate `CANCELLED` / no-fill** — the entry-fill tolerance genuinely
     was not met at the decision bar (verified against candle/tick data or
     `/status/fill-realism`, not assumed). Correctly excluded from proof;
     no operator action needed.
   - **Reconciler-touched but correctly resolved** — a reconciler outcome row
     exists, but broker evidence confirms the reconciler's own resolution was
     right (e.g. the position really was flat, or really was a loss booked at
     the correct price). No operator action needed beyond noting it happened.
   - **Broker-verified exception requiring manual ruling** — broker evidence
     contradicts a journal-recorded `CANCELLED`/no-fill outcome for a trade
     that resolved a different way. This is the only bucket that produces an
     operator exception (see below). It is never resolved by editing the
     journal.

**Decision rule when journal and exception ledger disagree:** the automated
proof checker (`scripts/proof_30_mnq.py` / `/status/proof/mnq-30`) is the
single source of truth for the normal MNQ path. A documented operator
exception in `docs/proof-operator-overrides.md` is authoritative only for the
specific trade it names. If a mechanical proof re-scan and a documented
exception ever appear to disagree about the same trade, **stop and record a
ruling** — do not average the two readings, do not silently prefer one, and
do not extend an exception's scope by inference to any other trade.

### Recording An Operator Exception

When a trade lands in the "broker-verified exception" bucket:

1. **Never edit the journal.** It is append-only; a synthetic outcome row
   would corrupt the same `TRADE`-to-`OUTCOME` pairing the proof tooling
   relies on for every other trade in the file.
2. **Add an entry to `docs/proof-operator-overrides.md`** with only
   public-safe facts: instrument, approximate session date/window, the
   broker evidence value, the journal history that produced the wrong
   outcome, the root cause (with a commit/PR reference if fixed), the
   operator ruling in one sentence, why the journal was not edited, and an
   explicit classification note stating which count (if any) the exception
   does **not** change.
3. **Do not put tally state, thresholds, or gate math in that file.** The
   exact live proof count, the specific gate criteria being evaluated, and
   any per-exception weighting are operator process state, not public repo
   content — keep those in operator-side notes outside this repository.
4. **The exception stands alone.** It does not retroactively change what the
   automated checker reports for that instrument, and it does not change any
   other trade's classification. A later full proof re-scan will not
   automatically pick it up; that is expected, not a bug — see the audit
   caveat pattern in the existing `docs/proof-operator-overrides.md` entries.

### Distinguishing "Strategy Not Ready" From "Evidence Chain Inconsistent"

These look similar from a summary metric alone and need different responses:

- **Strategy not ready** looks like: the proof checker runs clean (no journal
  read errors, no unmatched outcomes, broker account consistent with the
  journal), the trades it counted are real, and the result is simply not yet
  where it needs to be. The fix is more evidence over time, or a strategy/exit
  change — not a reconciliation exercise.
- **Evidence chain inconsistent** looks like: unmatched outcomes, journal read
  errors, a reconciler-authored outcome row, or `/status/today` numbers that
  do not match what the journal shows. The fix is reconciliation (this
  workflow) before the count means anything — trusting the raw number here
  would be trusting a broken measurement, not a bad result.

Treat `trade_count`, `today_pnl_dollars`, and raw journal outcome tallies as
**operational indicators, not unquestionable truth**, whenever a reconciler
row or a fill-status incident touches the window you are evaluating. They are
reliable by default; they stop being reliable the moment this workflow's step
3 trigger fires, until reconciliation closes it out.

For a repo-local inventory of every reconciler/phantom/naked/auto-flatten
outcome, run:

```bash
python3 scripts/reconciler_outcome_audit.py --journal-dir /root/autonomous-futures-system/logs
```

The audit is read-only. It groups outcomes already covered by
`docs/proof-operator-overrides.md` or by the post-fix completed-trade
reconciler path separately from unaudited rows that still need broker
verification follow-up.

### Repo Docs vs. Operator Memory — Handoff Rule

Neither side is sufficient alone for an exception case:

- **This repository** holds public-safe incident facts and the process above
  — what happened, what evidence proved it, what the ruling was, and why the
  journal was not touched. Safe to be public: no thresholds, no live tally
  state, no forward-looking gate math.
- **Operator-side process notes** (outside this repository) hold the
  sensitive proof-gate methodology: exact thresholds, tally rules, freeze
  logic, and the current live count toward any gate. This is where "is this
  exception enough to change a go-live decision" gets decided.
- An exception case is only fully documented when both exist and reference
  each other. A repo note without an operator ruling is an unresolved
  incident; an operator ruling without a repo note is unauditable.

### Repo/Process Safety Routines (`ops.project_check`)

Three manually-invoked, read-only routines close repeat failure classes
(branch/worktree confusion, promoting a strategy on standalone research
instead of the real executable path, and repo/journal/deployed-state drift)
without adding scheduled automation:

```
python -m ops.project_check session-start   # start of a work session: git/worktree/PR state + active-lane snapshot
python -m ops.project_check precommit       # before commit/push: fails closed if branch/worktree moved since session-start
python -m ops.project_check promotion --strategy <name> [--instrument MNQ]  # traces a strategy through the real journal-recorded pipeline
python -m ops.project_check daily           # PR/branch hygiene, evidence preservation, strategy source-of-truth, trade-chain integrity
```

None of the four commit, push, pull, reset, rebase, checkout, delete a
branch/worktree, drop a stash, create/delete a tag, cancel an order, flatten
a position, or touch risk/strategy/broker code — see the module docstrings
in `ops/project_check*.py` for exactly what each reuses and what it reports
`UNKNOWN` for instead of guessing.

### Runner shadow proof

Before enabling live trailing, set `RUNNER_SHADOW_ENABLED=true` and leave
`RUNNER_LIVE_ENABLED=false`. The live `process_alert` path appends read-only
observations to `runner_shadow_evidence.jsonl` in the configured log directory.
Check `runner_shadow` in `/status/today` or `/status/diagnostics`, or run doctor.

`recent_path_evidence` proves only that an open position received a
same-instrument bar through the live runner-shadow path. `proof_sufficient`
additionally requires that the trail armed and proposed a moved stop; only that
state clears `live_trailing_blocked`. The payload includes the instrument, setup
when available, armed/moved state, and proposed stop. Replay results do not write
this evidence and therefore cannot satisfy the live-path proof.

### Research Evidence Readiness

Use the unified, read-only scorecard to see which observation tracks are
inactive, collecting, sample-limited, data-quality blocked, or ready for human
review:

```bash
curl -s 'http://127.0.0.1:8000/status/evidence-readiness?days=30'
```

The same payload appears under `evidence_readiness` in `/status/today` and
`/status/diagnostics`; doctor prints a compact informational summary. Research
status never changes operational health, runs a collector, enables a strategy,
or changes a gate. `READY FOR REVIEW` authorizes only human replay/paper review.
RangeSignal and shadow setup observations remain `COLLECTING` until a causal
future-bar resolver produces fee/slippage-adjusted outcomes; observation counts
alone cannot satisfy promotion criteria.

### GEX Shadow Analysis

**GEX is optional enrichment across the whole system. No GEX vendor
subscription exists or is required.** The options lane runs fully without it:
a missing regime or flip yields `GEX_UNAVAILABLE` plus a warning, gamma-wall
targeting is skipped, and the GEX component drops out of both `context_score`
and `context_score_max`. No neutral regime and no flip level is ever
substituted. A GEX-less evaluation can reach `CAUTION` but never `VALID` —
the system operates honestly on Signa + SPY/QQQ + higher-timeframe context
without claiming a confirmation it does not have.

`gex_observed` snapshots are observe-only. When a producer journals compact
`gex_observed` records, this analysis scores them against resolved outcomes — it
never changes `DecisionEngine`, `RiskEngine`, or trade gating. The separate
observe-only GEX producer is rebuilt on the Public.com chain feed.

That in-house producer is **not** a substitute for a vendor feed and must not be
wired into trade approval. Collect and compare it separately first; it earns the
gate on measured evidence or not at all. No GEX proof means no GEX-based
decision.

Important distinction: payload-provided `state.gex` fields are not the same as
`gex_observed`. The active decision path currently calls `strategy/gex_gate.py`,
so payload fields such as `call_wall`, `put_wall`, `gex_flip`, and mid-range
levels can hard-reject trades. Do not describe that path as journal-only until it
is separately audited or changed.

Start the server with `GEX_SHADOW_ANALYSIS_ENABLED=true`, then measure whether
the lane deserves promotion beyond journaling:

```bash
curl -s 'http://127.0.0.1:8000/status/gex-shadow?days=30'
```

Inspect the returned GEX shadow summary, or `gex_shadow_analysis` inside
`/status/today` for the current day only. Promotion requires enough measured resolved
trades (`min_sample`, default 20), positive expectancy in candidate cohorts,
and clearly worse cohorts that can be replayed or shadow-run before any gate is
enforced. Until then, `verdict.status` remains `JOURNAL_ONLY` or
`NO_PROMOTION_YET`.

For the PR #91 enrichments specifically, inspect `enrichment_evidence`. It reports
field coverage and expectancy separation for `delta_bias`, trade-direction
alignment, `spot_vs_flip`, distance-to-flip buckets, and primary/secondary wall
context. `ENRICHMENT_CANDIDATE_ONLY` means aggregate separation has not repeated
across both chronological halves. The fields have earned more than journal space
only when the status becomes `ENRICHMENT_PROMISING`; that still authorizes
replay/shadow validation, never a live gate.

### Optional Discord Output

Discord notifications are read-only and disabled by default. To test them
locally, add a Discord webhook URL to `.env` and set:

```env
DISCORD_NOTIFICATIONS_ENABLED=true
DISCORD_NOTIFY_DECISIONS=TRADE,RISK_REJECTED,BLOCKED_MAX_TRADES,BLOCKED_LOSS_LOCKOUT
```

Do not commit the real Discord webhook URL. Notification failures are logged but
do not block TradingView ingestion or paper-risk checks.

Preview the message without sending:

```bash
python -m notifications --dry-run
```

Send a smoke-test message only after `.env` has a local Discord webhook URL and
notifications are enabled:

```bash
python -m notifications
```

### Signa API Placeholder

`SIGNA_API_KEY` is reserved for future signal or market-data work. The key must
remain local, and the current system only exposes boolean readiness in
`/health`. Do not add network calls until the Signa payload is mapped into the
market-state schema and tested in shadow mode.

TradingView cannot call `localhost`; expose port `8000` with a public HTTPS
tunnel and paste this shape into the TradingView webhook URL field:

```text
https://YOUR-PUBLIC-TUNNEL/webhook/alert?secret=YOUR_LOCAL_SECRET
```

Set `WEBHOOK_SECRET` in `.env` to require the secret query string. Leave it
blank only for local testing.

Paste-ready alert message templates:

```text
tradingview/smoke_test_alert_message.json.tpl
tradingview/full_context_alert_message.json.tpl
```

TradingView smoke-test alert message:

```json
{
  "ticker": "{{ticker}}",
  "timestamp": "{{time}}",
  "open": {{open}},
  "high": {{high}},
  "low": {{low}},
  "close": {{close}},
  "volume": {{volume}},
  "timeframe": "{{interval}}",
  "market_condition": "CHOPPY"
}
```

Start with `market_condition: "CHOPPY"` for the first live-data smoke test so
the expected output is `NO_TRADE`. After the webhook path is verified, replace
that with real indicator context.

TradingView full-context alert message:

```json
{
  "ticker": "{{ticker}}",
  "timestamp": "{{time}}",
  "open": {{open}},
  "high": {{high}},
  "low": {{low}},
  "close": {{close}},
  "volume": {{volume}},
  "timeframe": "{{interval}}",
  "avg_volume": 1,
  "vwap": 0,
  "orb_high": 0,
  "orb_low": 0,
  "orb_status": "inside",
  "market_condition": "CHOPPY",
  "trend_direction": "SIDEWAYS",
  "trend_strength": "WEAK",
  "previous_day_high": 0,
  "previous_day_low": 0,
  "previous_day_close": 0,
  "price_vs_pdh": "below",
  "price_vs_pdl": "above",
  "current_bar_type": "two_up",
  "previous_bar_type": "inside_bar",
  "two_bars_back_type": "two_up",
  "strat_sequence": "strat_212",
  "strat_trigger": "continuation",
  "strat_direction": "LONG"
}
```

Replace the placeholder `0` and classification values with real values from a
Pine indicator. Omit unknown context fields until they are computed. Classified
`strat_212` and `strat_122` context may create paper setups only when enabled
and still must pass bracket and risk checks.

---

## 2. Running Tests

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_risk_engine.py -v

# Test that live trading is blocked
pytest tests/test_live_trading_blocked.py -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing
```

---

## 3. Reviewing Decisions

All decisions are logged to `logs/journal_YYYY-MM-DD.jsonl`.

```bash
# View today's journal
cat logs/journal_$(date +%Y-%m-%d).jsonl | python -m json.tool

# Count decisions by type
grep -o '"decision": "[^"]*"' logs/journal_$(date +%Y-%m-%d).jsonl | sort | uniq -c

# View only TRADE decisions
grep '"decision": "TRADE"' logs/journal_$(date +%Y-%m-%d).jsonl

# View only rejections with reason
grep '"decision": "NO_TRADE"' logs/journal_$(date +%Y-%m-%d).jsonl
```

### Generate Morning And End-Of-Day Reviews

The review layer is read-only. It reads the journal and writes review artifacts
under `logs/`.

```bash
python -m agent.daily_summary --date $(date +%Y-%m-%d) --mode morning
python -m agent.daily_summary --date $(date +%Y-%m-%d) --mode eod
```

---

## 4. Daily State Reset

The system tracks trade count and loss streak per calendar day.

- **Automatic**: The system reads the current day's journal to compute daily state at startup.
- **Manual reset**: Delete or archive today's journal file. The system starts fresh with 0 trades, 0 losses.

```bash
# Archive today's journal
mv logs/journal_$(date +%Y-%m-%d).jsonl logs/archive/
```

---

## 5. Adding a New Market State File

Market state files must conform to `market_state.schema.json`.

```bash
# Validate a market state file
python -c "
from context.market_context import MarketStateLoader
loader = MarketStateLoader('risk_rules.yaml')
state = loader.load('data/your_file.json')
print('Valid:', state)
"
```

---

## 6. Common Errors

### `LiveTradingBlockedError`

**Cause**: `LIVE_TRADING_ENABLED=true` in config or environment.  
**Fix**: Set `LIVE_TRADING_ENABLED=false` in `.env` and `risk_rules.yaml`.

```bash
grep LIVE_TRADING .env
grep live_trading risk_rules.yaml
```

### `DataQualityError: Stale data`

**Cause**: Market state timestamp is older than 5 minutes.  
**Fix**: Provide a fresh market state file. Check the `timestamp` field.

### `DataQualityError: Missing required field`

**Cause**: A required field in market state JSON is null or absent.  
**Fix**: Review `market_state.schema.json` for all required fields. Populate them.

### `RiskRejection: R:R below minimum`

**Cause**: Calculated R:R < 2.0.  
**Fix**: This is expected behavior. The setup is correctly rejected. Adjust the setup or accept NO_TRADE.

### `SchemaValidationError`

**Cause**: Market state JSON does not conform to schema.  
**Fix**: Validate against `market_state.schema.json`. Check enums (instrument, session, etc.)

---

## 7. Checking System Health

```bash
# Verify config loads correctly
python -c "from config.settings import load_config; c = load_config(); print('Config OK:', c)"

# Verify risk rules load
python -c "from risk.risk_engine import RiskEngine; r = RiskEngine(); print('RiskEngine OK')"

# Verify paper broker initializes
python -c "from execution.paper_broker import PaperBroker; b = PaperBroker(); print('PaperBroker OK, is_live:', b.is_live)"
```

---

## 8. Log Locations

| Log | Path | Format |
|-----|------|--------|
| Decision journal | `logs/journal_YYYY-MM-DD.jsonl` | JSONL |
| Error log | `logs/errors.log` | Plain text |
| Risk rejections | Embedded in journal | JSONL |
| Daily review | `logs/daily_review_YYYY-MM-DD.md` | Markdown |
| Trade grades | `logs/trade_grades_YYYY-MM-DD.csv` | CSV |
| Review payload | `logs/review_YYYY-MM-DD.json` | JSON |
| Webhook server | stdout plus `logs/system.log` when engine path runs | Text |

---

## 9. What To Do If Something Looks Wrong

1. Check `logs/errors.log` first
2. Re-run with `--dry-run` to isolate config vs data issues
3. Run `pytest tests/ -v` to confirm rules haven't been violated
4. Check that `LIVE_TRADING_ENABLED=false` — this is the first thing to verify
5. Review the most recent journal entry for the rejection reason

---

## 10. Escalation: Seeing Unexpected TRADE Decisions

If the system is generating trades that seem wrong:

1. **Do not manually edit the journal.** It is append-only by design.
2. Check `risk_rules.yaml` — confirm values match your reviewed local policy
3. Run `pytest tests/test_risk_engine.py -v` — all rules must pass
4. Review the market state file used — confirm it accurately represents conditions
5. Add a test case that covers the unexpected scenario before changing any logic
