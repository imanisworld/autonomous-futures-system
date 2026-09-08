# Inverse ORB canonical IOC proof

Status: **WAIT — population mismatch prevents promotion of the result**.

This audit replays the currently available journaled MNQ `orb_breakout` arms
after applying the frozen inverse paper transform. It does not change signal,
risk, or strategy logic.

## Frozen contract

- one MNQ contract
- inverse direction with mirrored structural stop and target
- static exit
- IOC limit with the inverse-lane 8-tick marketable tolerance
- one adverse tick of slippage
- $1.48 round-trip commission
- pessimistic same-bar stop-first resolution

## Population identity

- source: `logs/retest_baseline_off/MNQ/journal_*.jsonl`
- filter: approved `TRADE` rows with `setup.strategy == orb_breakout`
- current reproducible population: **63 arms**
- fingerprint: `f32b1b1d2fd5f5860d476b7c44f479d28abb8dc0445fccc21f8c7f27519e2da2`
- first arm: `2024-08-13T14:30:00+00:00`
- last arm: `2026-05-13T07:00:00+00:00`

The paper-build note's n=111 population and +$745.72 / PF 2.392 result are
not reproducible from the current local canonical journals. They are retained
as stale context, not substituted into this proof.

## Results

| Scope | Attempts | Fills | No-fills | Resolved | Net after costs | PF | Expectancy/fill | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Overall | 63 | 57 | 6 | 57 | +$1,026.64 | 5.284 | +$18.01 | $55.90 |
| H1 | 31 | 29 | 2 | 29 | +$546.08 | 5.883 | +$18.83 | $25.48 |
| H2 | 32 | 28 | 4 | 28 | +$480.56 | 4.760 | +$17.16 | $55.90 |

| Session | Attempts | Fills | Resolved | Net after costs | PF |
|---|---:|---:|---:|---:|---:|
| Asian | 8 | 6 | 6 | +$63.62 | 16.985 |
| London | 20 | 20 | 20 | +$179.90 | 2.707 |
| New York | 35 | 31 | 31 | +$783.12 | 7.009 |

## Decision

The available 63-arm replay is positive in both chronological halves under the
frozen realistic-fill contract. However, it is not the documented 111-arm
canonical population, so the inverse lane remains **PROMISING BUT UNPROVEN / WAIT**.
The exact machine-readable run, including every arm and outcome, is:
`scripts/inverse_orb_canonical_ioc_proof_2026-09-07.json`.

No deployment, permission, risk, or strategy changes were made.
