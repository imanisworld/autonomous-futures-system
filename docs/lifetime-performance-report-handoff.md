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
  Unjoinable, duplicate and orphan rows are surfaced and excluded from verified
  dollars; CANCELLED/non-outcome rows never become wins.
- Futures observation-only: `SHADOW_OUTCOME` latest candidate revision by
  lane, strategy, market and evidence epoch. It reports **ticks only**, never
  account dollars. Do not add ticks across different contract multipliers or
  treat shadow outcomes as fills. Existing day-specific
  `ops/shadow_daily_pnl_report.py` remains the source of its defined
  commission/slippage assumptions.
- Options: records from `options_shadow_journal`, using its recorded
  `pnl_dollars` and source cohort boundaries from
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

The scripts intentionally do not guess an initial deposit, broker balance,
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

AFS-0308 review: changes pending; do not merge.
