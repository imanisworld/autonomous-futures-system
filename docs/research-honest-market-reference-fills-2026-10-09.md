# Honest market-price reference for new futures research

**Status: SOURCE-ONLY RESEARCH CAPABILITY — no deployment, no permission change, no live authorization.**

## Problem (verified in source)

`PaperBroker(entry_fill_model="market")` historically fills at `BracketOrder.entry` even if the order is submitted after the market has moved. For close-confirmed signals, that planned price may not be executable. This legacy behavior explains part of the inflated earlier backtest results.

The existing `ioc_limit` and `stop_market` models already address specific order types. Do not replace them with this new model.

## Corrected, opt-in research behavior

`market_at_reference` requires an explicit, **causally available** `market_price` and enters at that reference plus adverse slippage for LONG, minus adverse slippage for SHORT. Missing, zero, non-finite or otherwise invalid references raise `ValueError`; no position opens. Existing bracket-validity checks still reject fills beyond the stop/target.

For historical market-entry tests pass the **next available bar open**, not the close of the already-completed signal bar. The API cannot independently verify the reference timestamp; caller must prove it was available at the entry decision. Include reference-bar timestamp, quote source, slippage, and commissions in every result artifact. These are simulation assumptions, not guaranteed broker executions.

Example:

```python
broker = PaperBroker(entry_fill_model="market_at_reference", slippage_ticks=1)
fill = broker.execute_bracket(order, market_price=next_bar.open)
```

The strict model is available in `SystemConfig` via `ENTRY_FILL_MODEL=market_at_reference` **only for isolated research**. The default remains the legacy `market` to reproduce frozen experiments; do not treat a default plan-price result as profitability evidence. Live routing, existing strategies, existing sealed cohorts, and risk policies are unchanged.

## Independent acceptance gates

1. Unit tests prove actual reference-price fills and refusal on missing data.
2. Before use in any new strategy verdict: frozen rules, causal price timestamps, market open/close calendar, roll/gap treatment, commissions and adverse-fill stresses.
3. Report costed results, no-fills (for `ioc_limit`), open/invalid outcomes, drawdown, sample/halves, and independent data. No profitability status upgrade from this code change.
4. Review CI on the exact PR head. **No merge, deploy, broker/order routing, or live risk changes are approved here.**
