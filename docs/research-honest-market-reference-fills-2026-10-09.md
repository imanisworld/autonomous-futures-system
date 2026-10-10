# Honest market-price reference for new futures research

**Status: SOURCE-ONLY RESEARCH CAPABILITY — no deployment, no permission change, no live authorization.**

## Problem (verified in source)

`PaperBroker(entry_fill_model="market")` historically fills at `BracketOrder.entry` even if the order is submitted after the market has moved. For close-confirmed signals, that planned price may not be executable. This legacy behavior explains part of the inflated earlier backtest results.

Strict research fills bypass the canonical broker's optional Webull sandbox-mirror hook while preserving the contract hard-cap guard. No outside order may be emitted by this research path.

The existing `ioc_limit` and `stop_market` models already address specific order types. Do not replace them with this new model.

## Corrected, opt-in research behavior

`ResearchReferencePaperBroker` is a separate, research-only wrapper around the hash-pinned canonical `PaperBroker`; the audited broker implementation is unchanged. Its `market_at_reference` mode requires an explicit, **causally available** `market_price` and enters at that reference plus adverse slippage for LONG, minus adverse slippage for SHORT. Missing, zero, non-finite or otherwise invalid references raise `ValueError`; no position opens. Existing bracket-validity checks still reject fills beyond the stop/target.

For historical market-entry tests pass the **next available bar open**, not the close of the already-completed signal bar. The wrapper cannot independently verify the reference timestamp. `ReplayEngine` enforces the next same-market/timeframe bar open, while other direct callers must prove their quote timing. Include reference-bar timestamp, quote source, slippage, and commissions in every result artifact. These are simulation assumptions, not guaranteed broker executions.

Example:

```python
from execution.research_reference_paper import ResearchReferencePaperBroker

broker = ResearchReferencePaperBroker(entry_fill_model="market_at_reference", slippage_ticks=1)
fill = broker.execute_bracket(order, market_price=next_bar.open)
```

`ReplayEngine` now obtains the next same-instrument, same-timeframe bar's **open** with exact adjacent-bar timing for this strict model. Missing/gapped future bars cause a hard refusal; the research result must not silently fill. The wrapper also forces post-fill verification of actual bracket risk/R:R. Pre-resolved 2-1-2 / 1-2-2 entries are explicitly excluded because they use an already-triggered position rather than a fresh market order. The replay calls post-fill validation for strict fills.

The strict model is available in `SystemConfig` via `ENTRY_FILL_MODEL=market_at_reference` **only for isolated research**. It is selected by the offline `ReplayEngine`, never by the production broker factory. Enabling it outside that path is unsupported and must fail closed. The default remains the legacy `market` to reproduce frozen experiments; do not treat a default plan-price result as profitability evidence. Live routing, existing strategies, existing sealed cohorts, and risk policies are unchanged.

## Independent acceptance gates

1. Unit tests prove actual reference-price fills and refusal on missing data.
2. Before use in any new strategy verdict: frozen rules, causal price timestamps, market open/close calendar, roll/gap treatment, commissions and adverse-fill stresses.
3. Report costed results, no-fills (for `ioc_limit`), open/invalid outcomes, drawdown, sample/halves, and independent data. No profitability status upgrade from this code change.
4. Review CI on the exact PR head. **No merge, deploy, broker/order routing, or live risk changes are approved here.**
