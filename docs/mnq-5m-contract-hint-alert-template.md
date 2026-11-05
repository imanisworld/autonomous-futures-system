# MNQ 5-minute `contract_hint` alert template

Observability only. This note does not enable `CONTRACT_IDENTITY_GUARD_ENFORCED`
and does not change order routing. The runtime must not invent a hint when an
alert omits one. A missing hint stays missing and is counted.

Every MNQ 5-minute TradingView alert should send the dated contract the prices
came from. Example for the December 2026 Nasdaq micro:

```json
{
  "ticker": "MNQ1!",
  "timeframe": "5m",
  "contract_hint": "MNQZ2026"
}
```

`contract_hint` is the dated symbol (`MNQH2026`, `MNQM2026`, `MNQU2026`,
`MNQZ2026`), not the continuous `MNQ1!` ticker and not a one-digit year.
The compare step already accepts `MNQZ2026` against a routed `MNQZ6` when the
session date supplies the century. That compare stays observe-only until an
operator turns enforcement on.

Do not set `CONTRACT_IDENTITY_GUARD_ENFORCED` from this template.
