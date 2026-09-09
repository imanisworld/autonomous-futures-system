# MES 1-2-2 — candidate-fallback repair counterfactual (2026-09-08)

Status: **EVIDENCE ONLY / PROMISING REPAIR HYPOTHESIS. No runtime, strategy-rule, risk, ranking, permission, config, broker, or deployment change.**

Source: PR #373's production-config executable-parity artifact (`scripts/mes_122_executable_parity_audit_results.json`, source blob `799073955c093a1ff8a5ffc428cb7834f6d2897e`). This study snapshots only the 16 executable rows and 7 same-bar preempted rows; the remaining 10/33 candidates had no engine decision because another position was already open and cannot be recovered by candidate fallback.

## Question

PR #373 established that MES `strat_122` had 33 canonical candidates, but only 16 were executable under the real production concept list. Seven more were preempted by another same-bar strategy candidate. The current `DecisionEngine` candidate loop can continue to the next candidate when `strategy_fallback_enabled` is true, but production has that flag false. Separately, the strategy-permission gate runs only after a winner has already been selected and has no fallback loop.

This study asks which of those two fallback locations actually matters for the historical 1-2-2 result.

## Control

The 16 executable rows reproduce PR #373 exactly:

| n | W/L | Net | PF | H1 | H2 | Max DD |
|---:|---:|---:|---:|---:|---:|---:|
| 16 | 5 / 11 | **+$120.00** | 1.421 | +$11.25 | +$108.75 | $121.25 |

## Existing candidate-loop fallback hypothesis

Four of the seven preempted 1-2-2 candidates were lost because the higher-ranked competing setup failed exactly `ENTRY_DETACHED_FROM_PRICE`:

| Date | Competing setup | 1-2-2 result |
|---|---|---:|
| 2025-10-24 | `orb_reclaim` | +$97.50 |
| 2026-02-20 | `vwap_hold` | +$80.00 |
| 2026-03-13 | `vwap_hold` | +$150.00 |
| 2026-03-26 | `vwap_hold` | +$75.00 |

Those four rows total **+$402.50**. Adding only those rows to the 16-trade executable control gives:

| n | W/L | Net | PF | H1 | H2 | Max DD |
|---:|---:|---:|---:|---:|---:|---:|
| **20** | **9 / 11** | **+$522.50** | **2.833** | **+$82.50** | **+$440.00** | **$121.25** |

The max drawdown does not increase and both chronological halves are positive after recomputing the 20-trade split.

## Permission-gate fallback is not the useful lever

The three preemptions involving `STRATEGY_NOT_PAPER_ELIGIBLE` total only **+$30.00**. Adding those instead gives 19 trades, +$150.00, PF 1.435. That is effectively the same weak/marginal profile as the current executable control.

Recovering all seven preemptions would produce 23 trades, +$552.50, PF 2.60, but that requires a broader post-selection fallback mechanism that does not exist today. The data do not justify building that broader mechanism: almost all of the improvement is already explained by the four candidate-loop `ENTRY_DETACHED_FROM_PRICE` cases.

## Interpretation

This is the first evidence-backed repair hypothesis for MES 1-2-2 that does not change the strategy itself:

- keep the 1-2-2 detector, entry, stop, target, R:R, and risk gates unchanged;
- keep the competing strategy's rejection unchanged;
- only test whether a lower-ranked, already-valid `strat_122` candidate should be evaluated when the higher-ranked candidate fails `ENTRY_DETACHED_FROM_PRICE`;
- do **not** globally enable fallback based on this 20-trade counterfactual, because the existing config switch is system-wide and could alter unrelated strategy competition.

## Verdict

**PROMISING BUT UNPROVEN REPAIR HYPOTHESIS.** The historical counterfactual improves from +$120 / PF 1.42 to +$522.50 / PF 2.83 with both halves positive and unchanged max drawdown, but n=20 remains below the 30-trade minimum and this is not yet a full-engine replay of the modified selection policy.

## Required next proof

Run an isolated full-engine replay on the same 313-day MES corpus with one experimental change only: permit fallback to the next candidate when the rejected higher-ranked candidate's code is `ENTRY_DETACHED_FROM_PRICE` **and the lower-ranked candidate is `strat_122`**. Everything else stays frozen. The control must reproduce the 16 / +$120 population first. Do not change runtime/config or enable global fallback unless that replay reproduces the counterfactual without collateral strategy changes.
