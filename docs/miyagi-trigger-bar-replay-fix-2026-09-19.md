# Miyagi Trigger-Bar Replay Fix — 2026-09-19

## Verdict

**FIXED REPO-SIDE / RESEARCH ONLY / NO DEPLOYMENT.**

Binding preregistration:
`docs/prereg-miyagi-trigger-bar-replay-fix-2026-09-19.md`

Frozen prereg commit:
`38da280e2435f53387e034e1f1fca57040a70233`

The fix changes only the 12HR Miyagi research replay mechanics:
- the trigger-touch 5m bar is now immediately eligible for stop/T1 resolution;
- same-trigger-bar entry+stop ambiguity resolves pessimistically as STOP;
- stop-market gap-through uses the bar open as the base entry reference;
- adverse slippage is applied after that reference;
- a slipped fill outside the frozen stop/target bracket fails closed as
  `POST_FILL_INVALID_BRACKET`.

No detector, strategy definition, target, stop, risk cap, R:R, runtime wiring,
broker path, or deployment state changed.
## Frozen population parity

Source evidence populations:
- MNQ: 15 candidates;
- MES: 19 candidates;
- study range: 2024-07-02 through 2026-06-26.

Post-fix regeneration proves:
- candidate lists are byte-for-byte semantically identical;
- invalidation lists are identical;
- granularity-ambiguity records are identical.

Candidate identity hashes:
- MNQ: `58b57b59c79434359d55a650ed7d58e9f47ba95a84a744a08039531ce85bb59a`
- MES: `452b87fe2c8e2de86ab59befb58ce0ea1ca5f666fec2f73ae20960c0dcc8766a`

## Exact changed rows

MNQ 2024-09-18 SHORT:
- old: TARGET at 09:35 ET, +$54.51 at 2 ticks;
- fixed: STOP on the 09:30 ET trigger bar, -$72.99;
- reason: entry and stop were both touched inside the same 5m trigger bar and
  OHLC cannot prove favorable ordering.

MES 2025-12-04 SHORT:
- final LOSS/P&L is unchanged;
- stop timestamp moves from 09:35 ET to the correct 09:30 ET trigger bar.
## Regenerated base case — 2 adverse ticks

MNQ:
- 15 candidates;
- 8 fills;
- 6 wins / 2 losses;
- net **+$425.33**;
- PF **2.3220**;
- max drawdown **$321.73**;
- H1: 7 fills, **+$291.82**, PF 1.9070;
- H2: 1 fill, **+$133.51**.

MES:
- 19 candidates;
- 10 fills;
- 7 wins / 3 losses;
- net **+$138.85**;
- PF **1.5925**;
- max drawdown **$133.115**;
- H1: 6 fills, **+$140.685**, PF 2.0569;
- H2: 4 fills, **-$1.835**, PF 0.9819.

Miyagi remains **PROMISING BUT UNPROVEN / PARKED**. The fix removes replay
optimism; it does not validate the strategy or make it executable under the
current account risk architecture.
## Slippage sensitivity

MNQ:
- 1 tick: +$433.33, PF 2.3553;
- 2 ticks: +$425.33, PF 2.3220;
- 3 ticks: +$417.33, PF 2.2891;
- 4 ticks: +$409.33, PF 2.2567.

MES:
- 1 tick: +$163.85, PF 1.7223;
- 2 ticks: +$138.85, PF 1.5925;
- 3 ticks: +$113.85, PF 1.4708;
- 4 ticks: +$88.85, PF 1.3563.

MES H2 is negative at 2–4 ticks. MNQ H2 has only one fill. Neither population
is large enough for validation.
## Reproducibility

Regeneration script:
`scripts/miyagi_trigger_bar_replay_fix_regen_2026_09_19.py`

Outputs:
- `mnq_results_trigger_bar_corrected_2026-09-19.json`
  SHA-256 `78cb67769c03950939c85bd07d4bbe7acea2b5d26dc255b0c4618bc833ca98f2`
- `mes_results_trigger_bar_corrected_2026-09-19.json`
  SHA-256 `60498f1b27614d89aff96d2559cf666dc93f44a3275b6a431b2cb61b79b66af9`
- manifest SHA-256
  `0f38ef0f2b6dc6883764957deb3eb5a44b247ef1d59c3157b77a3b08d35d47a7`

The prior 2026-09-19 causal audit remains reproducible byte-for-byte with
historical result SHA-256
`a0c7c469a2a748f253d71e20a1dc414e4e4e93b5f1a602992a745333064be8d8`
because the superseded replay policy is preserved only inside that audit
harness for provenance.

No proof, no run.
