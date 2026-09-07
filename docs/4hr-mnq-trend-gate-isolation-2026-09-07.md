# 4HR MNQ Trend-Gate Isolation

Status: **AUDIT ONLY — no strategy, configuration, permission, risk, or deployment changes.**

The same frozen 81-candidate 4HR MNQ population was replayed through the full
engine under four arms. Detached-entry, stop-width, R:R, weak-close, session,
IOC, commission, slippage, and pessimistic same-bar handling were unchanged.

Execution contract: one MNQ contract, 32-tick IOC tolerance, one adverse tick,
$1.48 round-trip commission, pessimistic same-bar resolution.

| Arm | Ordered blockers | Attempts | Fills | Resolved | Net | PF | Max DD | H1 / H2 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Current | 38 market-condition; 19 R:R; 11 stop-width; 8 detached; 4 weak-close; 1 IOC no-fill | 1 | 0 | 0 | $0.00 | n/a | $0.00 | n/a / n/a |
| Exempt market condition only | 37 strong-trend; 20 R:R; 11 stop-width; 8 detached; 4 weak-close; 1 IOC no-fill | 1 | 0 | 0 | $0.00 | n/a | $0.00 | n/a / n/a |
| Exempt strong trend only | Same as current; market-condition gate blocks first | 1 | 0 | 0 | $0.00 | n/a | $0.00 | n/a / n/a |
| Exempt both | 28 EMA-stack; 22 R:R; 11 stop-width; 9 detached; 8 weak-close; 1 volume; 1 IOC no-fill; 1 filled loss | 2 | 1 | 1 | **-$7.98** | **0.000** | $7.98 | $0.00 / -$7.98 |

## Conclusion

- Exempting `MARKET_CONDITION_NOT_TRENDING` alone does not restore execution;
  `TREND_STRENGTH_BELOW_REQUIRED` becomes the dominant next blocker.
- Exempting `require_strong_trend` alone has no effect because the market-condition
  gate runs first.
- Exempting both still does not produce a viable population. `EMA_STACK_NOT_ALIGNED`
  becomes the dominant remaining quality gate, and the only resolved trade loses.
- Therefore this test does **not** support a narrow two-gate 4HR routing fix yet.
  Do not touch detached-entry or any other retained gate based on this result.

The earlier one-gate frozen IOC ablation's positive `remove_trending` arm was
not an ablation at all: its 43 candidates are the 81 minus the 38 that fail the
trending gate, i.e. an apply-only-trending arm (see the corrected
`4hr-mnq-gate-ablation-2026-09-07.md`). A true one-gate ablation from the
attribution ledger admits 4 candidates without the trending gate, consistent
with this replay's 1-2 attempts; the stop-width cap is the only gate whose
removal admits a materially larger population (12). This replay confirms that
without changing production code.

Temporary replay outputs were kept outside the repository; this document is the
human-readable audit record.
