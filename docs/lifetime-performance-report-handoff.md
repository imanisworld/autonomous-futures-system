# Read-only lifetime paper-accounting by ticker and change epoch

**Status: SOURCE-ONLY / AUDIT ONLY / WAIT.** Existing release-integrity
blocker prohibits VPS deployment, promotion, repin, service restart and
Discord cutover. This report runs against **operator-provided, consistent copies**
of evidence. It neither logs in to a broker nor changes a service, collector,
strategy, risk limit, scheduled report, or sealed research trial.

## Purpose

Answer how much documented *paper* profit/loss exists for each futures ticker
and each options underlying, and separate results by actual, documented change
cohorts. Do not add counterfactual gains to account P&L. This does not prove a
change caused better performance or that trading live would be profitable.

Implementation: `ops/lifetime_performance_report.py` (stdlib, stdout JSON).
It operates only on local JSONL and SQLite copies. **Do not commit raw account
data, private journals, hashes of credentials, option contracts, or P&L exports
to public GitHub.** Summaries belong in a private operator artifact location.

## Inputs and read-only usage

Prepare independently verified snapshots later, using an authorized read-only
route. A file existing on disk is not proof that it covers inception or is the
current scanner ledger.

```bash
python -m ops.lifetime_performance_report \
  --journal-dir /private/path/copied-futures-journals \
  --options-db /private/path/consistent-options-scanner.sqlite \
  --options-epochs docs/options_v1_evidence_epoch.json \
  > /private/path/lifetime-performance.json
```

Add `--text` to print an operator-friendly ticker/epoch breakdown instead of JSON.
Missing priced closes are shown as **INCOMPLETE** with a separately labeled
priced subtotal. The optional text output remains private/offline.

The command has no Discord, network, or broker calls and writes **only to
stdout** (shell redirection shown is operator-owned). Return code 2 marks
partial/unreconciled input; inspect the JSON and do not call it a trusted total.
Missing input files or bad epoch definitions produce a nonzero error. Do not
run a direct read on a mutating production SQLite database to claim a coherent
snapshot. No VPS actions are approved by this document.

## Existing systems reused

- Futures: approved TRADE decisions joined to OUTCOME strictly by exact
  `paper_order_id` **across all supplied daily files**, matching the identity
  principle in `adaptive/journal_reader.py`; no cross-day or per-market FIFO.
  Independently reprice gross from recorded actual fill entry/exit prices,
  direction, contract quantity and canonical `config.futures_contracts` tick
  values; mismatches against journal dollars block complete figures.
  Unjoinable, duplicate and orphan rows are surfaced and excluded from verified
  dollars; CANCELLED/non-outcome rows never become wins.
- Futures observation-only: `SHADOW_OUTCOME` latest candidate revision by
  lane, strategy, market and evidence epoch. It reports **ticks only**, never
  account dollars. Do not add ticks across different contract multipliers or
  treat shadow outcomes as fills. Existing day-specific
  `ops/shadow_daily_pnl_report.py` remains the source of its defined
  commission/slippage assumptions.
- Options: records from `options_shadow_journal`, independently repriced
  from recorded entry option ask, exit bid and contract quantity (100-share listed
  option multiplier), cross-checked against stored `pnl_dollars`; boundaries from
  `docs/options_v1_evidence_epoch.json`. Those boundaries are the same
  id-plus-timestamp rules specified by unmerged PR #1071. The new lifetime
  reader does **not** merge or supersede that PR's current-epoch
  counterfactual-by-reason auditor; it only reuses the epoch accounting rule.
  Acquisition timestamp/id define the cohort, **not** resolution date.
- Options ACTIVE and COUNTERFACTUAL remain separate. Counterfactual is
  excluded. Ambiguous or missing lane markers are excluded from official ACTIVE
  totals and counted; prices missing on closed rows remain missing, not zero.
  Financial wins are positive recorded dollars, independently of structural
  WIN/LOSS labels. Scanner paper P&L is ASK entry, BID exit, **no commission**.

## Understanding the report

| Output field | Meaning |
|---|---|
| `futures.paper_trade_lifetime` | Total priced approved-paper journal outcomes in supplied history |
| `futures.paper_by_ticker` | By exact instrument token preserved from source |
| `futures.paper_by_ticker_and_change` | Before/after, **only** where independently documented change boundaries are supplied |
| `futures.identity_problems` | Missing ID, duplicate IDs, orphan OUTCOME, still-open attempts |
| `futures.shadow_observations_by_ticker` | Hypothetical ticks by market, **not dollars** |
| `options.lifetime_active` | ACTIVE scanner-paper priced subtotal; may be incomplete |
| `options.active_by_ticker_and_epoch` | Exact underlying and registered V1-EPOCH-1/2/3 cohort |
| `options.unknown_lane_excluded` | Cannot classify historical row safely |
| `options.epoch_boundary_conflicts` | ID/timestamp disagree on cohort |
| `*.complete_pnl_usd` | **null** when any closed row is unpriced |
| `*.priced_subtotal_usd` | Visible priced subset; do **not** call it lifetime total when incomplete |
| `lifetime_source_coverage_verified` | **false until independent archive/start/epoch verification** |

**Fee basis:** independently recomputed dollars are GROSS, before all commissions and fees. `fees_included=false`; `net_after_fees_usd=null`. Fee differences must not be guessed away.\n\nThe scripts intentionally do not guess an initial deposit, broker balance,
missing fills, options commission, futures trade attribution by deployed
release, or historical gaps. An all-time ledger result is **not** account
equity and is **not** a realized broker statement. The date-range and
record-count comparisons to original systems must pass before calling this
"since the start."

## Futures before/after changes (optional, do not invent dates)

The futures journals have no universal historical deploy/version tag. Do not
split them by a GitHub merge date or by a guessed service restart. Until
a reviewer identifies the actual installed behavior-change timestamps,
`CHANGE_BOUNDARIES_UNVERIFIED` is the only change-period label.

After independent box/deploy provenance review, an operator may provide a
private JSON file under `--futures-changes`:

```json
{
  "changes": [
    {
      "instrument": "MNQ",
      "label": "operator-reviewed-change-label",
      "effective_utc": "2026-09-16T00:00:00Z",
      "evidence_ref": "reference-to-reviewed-deployment-proof"
    }
  ]
}
```

The example date/label are illustrative, **not** a claim that MNQ changed
then. The tool checks syntax and partitions by trade-entry UTC time. It cannot
authenticate the supplied `evidence_ref`; that is an independent human gate.
Do not use sealed MNQ future-cohort outcomes to select or tune rules.

## Acceptance and next step (Claude read-only, separate GO)

1. Independently confirm archive completeness from the true start date:
   all journal days, no truncation/rotation holes, exact approved trade identities,
   and consistent options database copy (including WAL).
2. Confirm live/demo account posture and whether each journal is observed,
   paper, demo or broker-realized; cross-check independently with the existing
   proof paths without combining different authority populations.
3. Reconcile options all-time subtotals with existing
   `ops/options_daily_pnl_report.py` and #1071 cohort measurement; flag missing
   price fields, corrected closes, commissions excluded and cohort transitions.
4. Obtain independently verified futures deployed behavior-change boundaries
   only if available; otherwise leave comparisons explicitly unknown.
5. Produce a **private** report with ledger coverage, per-ticker before/after
   totals and caveats. Do not use it as backtest selection evidence.
6. No VPS deploy, restart, cron/Discord addition, broker execution, trial
   alteration or strategy change until the release-integrity defect is resolved,
   independently verified and the operator separately approves deployment.

**Definition of done for this PR:** Source/test/docs pass CI on exact head;
independent review confirms no execution path; ready for operator to run
against explicitly authorized, consistent offline evidence copies.
**Current financial figures: UNKNOWN; no production journal or SQLite data was
read for this PR.**

AFS-0308/AFS-0309: reviewed price-recomputation fixes merged via PR #1234;
this follow-up PR adds source-provenance and comparison truthfulness only.
No trading execution, live orders, deployment or service restarts.

## Remaining source-data verification and honest comparison (read-only handoff)

This report can now compute internally consistent **gross** arithmetic against
copied data but intentionally exits with code 2 because nothing in a local
file snapshot authenticates an entire historical account from inception.
`pnl_complete` is limited to the **supplied rows**; it never establishes that
the ledger covers all days, all accounts, all fills or all released rule versions.
Review `verification_blockers`, `since_inception_verified=false`,
`execution_provenance_verified=false` and
`options_open_side_history_verified=false` before describing any figure.

**Futures:** `account_mode`, `execution_mode`, `broker_mode`, and
`trade_mode` are checked when explicitly stamped. A ticker, cohort or gross
aggregate with even one unverified execution-mode row is labeled **INCOMPLETE**
and has `complete_pnl_usd=null`; its `priced_subtotal_usd` is a partial,
unverified subtotal and cannot be described as paper/demo profit. Explicit
PAPER and DEMO records remain independently classified in
`journal_by_execution_mode`. The report displays
paper/demo separately and excludes explicitly live rows from its paper/demo
subtotal. Missing/unknown journal modes are **UNSTAMPED**, not presumed
paper; no entry in this source can authenticate a broker fill or account by
itself. Independently correlate order IDs, releases, account identifiers
(redacted), broker transaction books, and installed source provenance.

**Options:** V1 source is purchased-premium/cash-only, confirmed in
`alert_ranker/paper_v1.py` and `alert_ranker/account_equity.py`. This is a
**code-scope observation**, not verification of every historical database row.
Explicit `SELL_TO_OPEN`, contradictory opening-side metadata and other
unsupported opening intent are flagged, excluded from buy-only P&L
arithmetic and make the related totals incomplete. Rows without an explicit
opening side are counted as **UNSTAMPED BUY-ONLY ASSUMPTION**. Bearish PUT
purchases and underlying SHORT signals are **not** classified as short-option
positions on that basis alone. Confirm the historical option contract,
order intent, quantity, multiplier and opening/closing bid/ask provenance
before promoting buy-only assumptions to verified facts.

**Comparisons:** Gross average per priced closed outcome, priced win rate,
entry-date UTC span and realized gross closed-trade drawdown are descriptive
only. Where closure price, price reconciliation, date or intratimestamp order
is missing, corresponding statistics remain unavailable. Realized
closed-trade drawdown is not intratrade MAE, mark-to-market portfolio
drawdown, broker account equity drawdown, or maximum capital required.
For **options cash/equity/drawdown** reuse the already-existing
`alert_ranker/account_equity.py`. For fee/slippage stress reuse the scenarios
in `alert_ranker/v1_diagnostics.py`. Do not add their independent stress
estimates to canonical gross ledger dollars or invent a net figure.

### Claude read-only steps once authorized evidence access exists

1. Verify the unresolved release-integrity incident **read-only**. Any
   installation, promotion, service restart, collector toggle or cutover remains
   prohibited until the incident is fixed and independently reverified;
   this report's source merge changes none of that.
2. Obtain offline-consistent **copies** of all original futures daily journals
   and the options SQLite ledger including WAL contents. Document source host
   identity (redacted), archive manifest, hashes, true first/last event,
   missing/unreadable days, duplicate IDs, and active/completed counts.
   Never place raw records in GitHub, a public artifact or Discord.
3. Reconcile the futures order-id join against account/broker transaction
   evidence to classify paper, demo and live, including no-fill cancels,
   same-day/cross-day exits, bust/corrections, orphan outcomes and zero-trade
   days. Preserve any unresolved or duplicated record as a hard failure,
   not as a zero-dollar trade.
4. Reconcile all options underlying tickers by unique contract/position,
   `paper_policy_id`, buying or selling at opening, quantity, actual ask/bid,
   multiplier, close reason and known unpriced/unclassified outcomes.
   Check V1-EPOCH-1/2/3 boundaries against **installed scanner version and
   activation dates**; do not infer from GitHub merge timestamp.
5. Run the new script only against the authenticated offline input copies.
   Compare aggregate/ticker/epoch results against
   `ops/options_daily_pnl_report.py`, the earlier #1071 epoch auditor
   (separate unmerged scope), existing forward-evidence summaries, and the
   source event counts. Compare source values to independent repricing; do not
   dismiss mismatches as fees without evidence.
6. Obtain documented fee/slippage schedule and execution-price source **for
   each historical epoch**, or preserve `net_after_fees_usd=null`. Provide a
   separate scenario table only if the assumptions are openly identified.
7. Produce a **private**, human-reviewed comparison with each instrument,
   strategy, change timestamp and reason, priced/unpriced trade counts,
   gross subtotal, net **UNKNOWN** if costs unverified, average gross trade,
   realized outcome drawdown (when chronologically provable), existing
   options mark-to-market drawdown (separately), and time/sample coverage.
   Keep proposed changes separate from observed outcomes; no causal claims.
   Respect all MNQ sealed-cohort boundaries when viewing or sampling data.

**Stop condition:** The operator has a documented lifetime coverage census
and truthful per-ticker/per-change result, with gaps and uncertainty plainly
marked. No broad rewrite, new scheduled jobs, report posting, or strategy
tuning is authorized.
