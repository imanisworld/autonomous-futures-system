# Inverse ORB — IOC entry-tolerance sensitivity (2026-09-08)

Read-only evidence. No strategy, risk, routing, config, deployment or PR #500
implementation was changed.

## Question

Is the frozen 8-tick IOC marketable tolerance too strict compared with wider values?

## Population — frozen, not regenerated

- source artifact: `scripts/inverse_orb_canonical_ioc_proof_2026-09-07.json`
- population fingerprint: `f32b1b1d2fd5f5860d476b7c44f479d28abb8dc0445fccc21f8c7f27519e2da2` **(verified)**
- 63 approved `orb_breakout` arms, `2024-08-13` → `2026-05-13`
- bars: `data/replay_polygon_5m/MNQ`

Held identical across every cell: 1 MNQ contract, inverse direction, mirrored
structural stop, mirrored static target, decision/arrival timing, 1 adverse tick
per market leg, $1.48 round-trip commission, pessimistic same-bar (stop before
target), no target priority, no lookahead, no runner, no market-entry substitution.

## Control reproduction — PASS

PR #491 shipped the proof with **no generator script**, so the model was
reconstructed from the frozen rows and re-validated against them:

| Check | Result |
|---|---|
| Fill gate (`adverse ticks ≤ 8` ⟺ FILLED) | **63/63 arms, 0 mismatches** |
| Exit model vs 57 frozen fills (reason, timestamp, gross) | **57/57 exact** |
| Net / gross / commission | +$1,026.64 / $1,111.00 / $84.36 — exact |
| PF / max DD | 5.283735 / $55.90 — exact |
| H1 / H2 | +$546.08 / +$480.56 — exact |

`scripts/inverse_orb_ioc_tolerance_sweep.py` reproduces this and emits the JSON.

**Reproducibility limit:** the sweep needs the 5-minute bar corpus at
`data/replay_polygon_5m/`, which is gitignored (`.gitignore:25`) and so is not in
a fresh clone — the harness fails with an explicit message rather than a stack
trace if it is absent. The committed JSON is the durable record. This is the same
constraint that left PR #491's proof with no generator at all.

## Sensitivity

| Tol | Fills | No-fills | Wins | Losses | Gross | Comm | **Net** | PF | Exp/att | Exp/fill | Max DD | H1 | H2 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4  | 56 | 7 | 2 | 54 | $1,059.00 | $82.88 | **+$976.12** | 5.073 | $15.49 | $17.43 | $55.90 | +$546.08 | +$430.04 |
| **8** | **57** | **6** | **3** | **54** | **$1,111.00** | **$84.36** | **+$1,026.64** | **5.284** | **$16.30** | **$18.01** | **$55.90** | **+$546.08** | **+$480.56** |
| 12 | 58 | 5 | 3 | 55 | $1,079.00 | $85.84 | +$993.16 | 4.636 | $15.76 | $17.12 | $89.38 | +$546.08 | +$447.08 |
| 16 | 59 | 4 | 4 | 55 | $1,126.50 | $87.32 | +$1,039.18 | 4.805 | $16.50 | $17.61 | $89.38 | +$546.08 | +$493.10 |
| 24 | 59 | 4 | 4 | 55 | $1,126.50 | $87.32 | +$1,039.18 | 4.805 | $16.50 | $17.61 | $89.38 | +$546.08 | +$493.10 |
| 32 | 60 | 3 | 5 | 55 | $1,165.50 | $88.80 | +$1,076.70 | 4.942 | $17.09 | $17.95 | $89.38 | +$583.60 | +$493.10 |

Sessions, tolerance 8 → 32: Asian +$63.62→+$101.14, London +$179.90 unchanged
(no marginal arms), New York +$783.12→+$795.66 with **PF falling 7.01 → 5.86**.

## Marginal fills

Only three arms in the whole population convert from no-fill to fill between 9
and 32 ticks:

| Bar ts | Dir | Planned | Arrival | Adv ticks | First tol | Fill px | Stop | Target | Outcome | Net |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 2025-10-16T14:45:00 | SHORT | 25106.50 | 25103.50 | 12.0 | 12 | 25103.25 | 25119.00 | 25079.00 | STOP_HIT | **−$33.48** |
| 2025-07-30T14:45:00 | SHORT | 23510.25 | 23506.75 | 14.0 | 16 | 23506.50 | 23522.75 | 23482.75 | TARGET_HIT | +$46.02 |
| 2024-11-12T07:30:00 | LONG | 21205.75 | 21213.50 | 31.0 | 32 | 21213.75 | 21193.25 | 21233.25 | TARGET_HIT | +$37.52 |

Three arms never fill within 32 ticks (60, 61 and 199 ticks adverse). One arm
(2025-07-24, 5.0 ticks adverse, +$50.52) is **lost** at tolerance 4 — which is
the entire reason tolerance 4 underperforms.

## Structural finding — the profit is not an adverse-tolerance effect

The contract caps *adverse* arrival at 8 ticks but leaves *favourable* arrival
uncapped, and stop/target are anchored to the **planned** entry rather than the
actual fill. Consequences, measured:

- 51 of 57 fills arrive **more than 8 ticks favourable**, and carry
  **+$1,020.02 of the +$1,026.64 — 99.4% of all profit**. 46 arms arriving
  >32 ticks favourable carry +$1,107.42.
- `result` is labelled by exit reason, not P&L: **37 of the 54 "LOSS" arms are
  profitable**, because the fill landed far enough favourable that the mirrored
  stop sits on the profit side.
- Diagnostic counterfactual (not a proposed contract): constrain arrival
  symmetrically to ±8 ticks and only 6 arms fill, netting **+$6.62 at PF 1.066**.

## Answer

**Widening beyond 8 ticks does not improve the strategy robustly.** It adds
chasing entries.

- The entire decision rests on **3 arms in 21 months**. Net moves +$50.06
  (+4.9%) at 32 ticks — under one average trade — while **max drawdown rises
  60%, $55.90 → $89.38**, and it rises at the *first* widening step (12) and
  never recovers.
- **8 ticks has the highest profit factor (5.284) and the highest expectancy
  per fill ($18.01) of any cell.** Every wider tolerance is worse on both.
  12 ticks is strictly worse than 8 on net, PF and DD simultaneously.
- Non-monotonic net (993 → 1,039 → 1,077) with monotonically worse PF is the
  signature of noise, not signal: the extra dollars come from two winners
  outvoting one loser, not from a better fill rule.
- New York — the densest session, 31 of 57 fills — sees PF fall 7.01 → 5.86
  for +$12.54. That is the cell with the most data and it degrades.
- 4 ticks is also rejected: it forfeits a genuine +$50.52 fill that arrived
  within a realistic marketable band.

**Recommended tolerance: 8 ticks.** Keep the frozen contract.

## PR #500

**Keep the frozen 8-tick contract.** Nothing here justifies reopening it, and
the sensitivity is too thin to carry a change.

The larger caveat is independent of tolerance and should not be resolved by
touching it: 99.4% of this population's profit comes from uncapped favourable
arrival against planned-entry-anchored stops. Tolerance is not the lever that
governs that, and the inverse lane's **PROMISING BUT UNPROVEN / WAIT** verdict
should stand until it is examined on its own terms.
