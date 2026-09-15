# Cross-instrument collection foundation

Scope: price metadata, symbol normalization, and forward-evidence identity only.
No strategy, feed subscription, broker allowlist, campaign population, or deployment
is added by this change. Audited base: `3da81b708c92d5151ee26bffc02b4f55ba507b37`.

## Contract metadata

Outright contracts only. Dollar point value is tick value divided by increment.
These are price units, not commission, session/calendar, or execution permission.

| Root | Increment before → after | Tick value before → after | Point value after |
|---|---|---|---|
| MNQ | 0.25 → 0.25 | $0.50 → $0.50 | $2 |
| MES | 0.25 → 0.25 | $1.25 → $1.25 | $5 |
| MGC | 0.10 → 0.10 | $10 → $1 | $10 |
| MCL | 0.01 → 0.01 | $10 → $1 | $100 |
| M2K | missing → 0.10 | missing → $0.50 | $5 |
| MBT | missing → 5.00 | missing → $0.50 | $0.10 |

ES/NQ values are retained. Unsupported canonical roots now raise before a paper
entry or restored position can mutate state. Resolution and post-fill validation
use the same metadata without generic fallback economics. Post-fill validation
already had correct MGC/MCL values; its MNQ/MES values are unchanged.

Authoritative sources, checked during the preceding audit on 2026-09-15:

- [CME equity micro specifications](https://www.cmegroup.com/articles/faqs/frequently-asked-questions-micro-e-mini-equity-index-futures.html)
- [CME Micro Gold](https://www.cmegroup.com/education/lessons/micro-gold-and-micro-silver-futures-product-overview)
- [CME Micro WTI](https://www.cmegroup.com/trading/energy/files/micro-wti-crude-oil-futures-fact-card.pdf)
- [CME Micro Bitcoin](https://www.cmegroup.com/trading/files/micro-bitcoin-futures-fact-card-retail-us.pdf)

## Symbol boundary

The existing exact-match parser additionally recognizes M2K and MBT roots,
exchange-prefixed symbols, continuous `1!` symbols, and CME month plus year
suffixes. Examples: `CME:M2KZ2026` → `M2K`, `CME:MBTH27` → `MBT`.
`MK2`, malformed suffixes, and unrelated symbols do not acquire a futures root.
PaperBroker itself requires a canonical root; callers normalize contract symbols
at the existing state-builder boundary. No month selection or rollover policy
is introduced. M2K IOC tolerance lookup retains the digit in its canonical root.
The HTTP ingestion allowlist is unchanged and still excludes M2K/MBT.

## Evidence identity and compatibility

Population key: `(strategy, instrument, variant, evidence_epoch)`.

- New candidate IDs include instrument and epoch even with a caller-supplied
  event ID. Report joins and dedupe also include the entire population key, so
  imported/reused candidate IDs cannot join across instruments or epochs.
- Open-position exclusion compares all four dimensions. New state keys include
  the population; existing legacy MNQ candidate-ID state keys remain intact.
- State read/modify/write is serialized across threads and, with `flock`, processes.
  Corrupt or unreadable state raises; it is not silently replaced with empty state.
- Only the exact frozen v1 campaign/schema may infer a missing legacy instrument
  as MNQ and missing legacy epoch as `forward_ab_2026_08_v1`. This is a stable
  identity for the existing cohort, not an epoch reset. Other instruments require
  an explicit epoch; explicit null or unknown identities are refused.
- Existing default MNQ candidate and outcome JSON values are byte-compatible.
  A regression digest of all five populations comes from the audited base.
  Historical JSONL is never rewritten; outcomes remain append-only.
- Writers and the retained-position resolver require configured membership. The
  shipped manifest remains exactly the five existing MNQ populations. Synthetic
  MNQ/MES multi-epoch manifests exist only in temporary test directories.
- New populations cannot inherit MNQ commission/slippage assumptions. Economic
  outcomes require explicit finite nonnegative costs. Missing cost proof cannot
  satisfy a report's economic sample gate.
- Counts, trading-day denominators, outcomes, cost sensitivity, and matched pairs
  are partitioned by instrument/epoch. Unconfigured populations are visible but
  cannot become review-eligible.

The bar resolver still consumes the caller's instrument-specific history. It
checks supplied instrument/epoch tags for contradictions; untagged legacy bars
retain their existing calling contract. This does not prove dated-contract
continuity across a roll, or authorize feeding new instruments.

## Monitoring

Every configured population is enumerated, including zero candidates/fills/outcomes.
Rows include instrument, strategy, variant, epoch, newest candidate/outcome/evidence
write timestamps, and a data timestamp only when evidence actually supplies one.
Missing data timestamps stay null: an evidence write is not presented as feed health.
No new feed heartbeat collector is activated.

## Execution and evidence boundaries

`risk_rules.yaml`, active campaign config, strategy modules, MES 1-2-2 lane,
webhook runner and app, Tradovate adapter, and wide-stop execution selector are
unchanged. Price metadata grants no execution permission. The real book remains
MNQ-only, with the same enabled concept and permission gates. No new strategy,
observation population, demo route, or live route is enabled.

Existing MGC/MCL historical P&L is not recalculated. New simulator runs use corrected
units; previously produced MGC/MCL economics require separate provenance review.
Strategy portability, commission/session/calendar proof for new collections,
contract-roll integrity, and activation remain separate work.

## Verification

Focused command (271 passed):

```sh
python3 -m pytest -q tests/test_cross_instrument_foundation.py tests/test_evidence_population_identity.py tests/test_forward_evidence_campaign.py tests/test_post_fill_validation.py tests/test_paper_broker.py tests/test_mes_122_paper_lane.py tests/test_orb_reclaim_campaign_isolation.py tests/test_canonical_vwap_observers.py tests/test_mnq_vwap_hold_early.py tests/test_tradovate_runner_contract.py
```

Full repository command (includes the complete futures suite): **5,311 passed,
7 skipped in 97.64 seconds**.

```sh
python3 -m pytest -q
```

Protected-file diff proof (must return no diff):

```sh
git diff --exit-code 3da81b708c92d5151ee26bffc02b4f55ba507b37 -- risk_rules.yaml config/forward_evidence_campaign.json webhook/runner.py webhook/app.py execution/tradovate_broker.py context/mes_122_paper_lane.py context/wide_stop_execution.py strategy
```
