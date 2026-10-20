# Underlying edge screen, 2026-10-11

Source-only research. No collector change, no trading authority, and no options-profitability claim.

**Ledger integrity correction (2026-10-18):** This screen ran before any governing trial registration and is now recorded retrospectively as `UNREGISTERED_ATTEMPT` / `INVALID_EVIDENCE` in `docs/research-trial-ledger.jsonl` (`T-2026-10-11-options-underlying-edge-screen-01`). The results, episodes, verifier, and historical operator choices below are unchanged source-only provenance. Reproduced calculations do not satisfy register-before-evidence, and these figures cannot support a strategy verdict, options expectancy, or promotion gate. See `docs/research-evidence/T-2026-10-11-options-underlying-edge-screen-01/result.md`. No rerun or retroactive preregistration is authorized.

Operator decision after the screen:

| Strategy | Decision | Reason |
|---|---|---|
| Higher-timeframe pullback | KEEP FOR RESEARCH | Best conditional 1R, with 14.4% actionable participation and 0.9% reaching 2R |
| Failed-break reclaim | KEEP FOR RESEARCH | Higher participation, with conditional 1R still below 50% |
| Compression → expansion | REJECT | Conditional 1R did not beat the matched baseline |

Neither kept family qualifies for an options-profitability trial. Compression is closed.

The scorer's mechanical label remains `PROMISING BUT UNPROVEN` for the two kept families because conditional 1R beat the baseline and stop-first stayed under 50%. That label is not a tradeable-edge verdict. The 1R comparison does not establish superior risk-adjusted expectancy.

The baseline does not reuse the strategy stop price. Both entries start from the structural stop known when the decision bar closes. The strategy keeps that price, so strategy R is the distance from the strategy fill to it. The baseline keeps the pre-entry distance from the decision close to that structural stop, then places a new stop price at the baseline fill minus that distance. A gap through the structural stop is still a missed entry. This wording replaces the earlier "same frozen stop price" description. The episode counts and rates are unchanged.

On setups where both entries filled, median strategy risk divided by median paired baseline risk was:

| Family | Paired fills | Median strategy risk | Median baseline risk | Median risk ratio |
|---|---:|---:|---:|---:|
| Higher-timeframe pullback | 1,023 | $0.83 | $0.135 | 4.179 |
| Failed-break reclaim | 6,677 | $0.60 | $0.155 | 3.682 |
| Compression → expansion | 2,661 | $1.32 | $0.515 | 1.986 |

Those dollar risks are the two distances above: strategy fill to the structural stop, and the pre-entry distance used to place the baseline stop.

Reproduce the table from the saved episode rows without refetching:

```bash
.venv/bin/python docs/research-notes/options-underlying-edge-2026-10-11/verify_results.py
```

A full refetch uses Alpaca SIP and the runner:

```bash
.venv/bin/python docs/research-notes/options-underlying-edge-2026-10-11/run_underlying_edge.py
```

Definition version `underlying-edge-v1-2026-10-11`. Scored source `52906d41`. Sessions 2026-04-01 through 2026-09-08. Sixty-six symbols. The 1-2-2 collector is unchanged.
