---
name: "source-command-futures-fill-audit"
description: "Migrated source command `futures-fill-audit`"
---

# source-command-futures-fill-audit

Use this skill when the user asks to run the migrated source command `futures-fill-audit`.

## Command Template

# /futures-fill-audit

Purpose:
Audit whether no-fills are killing edge or protecting the account from bad entries. This is a distinct question from `/futures-cancelled-audit` (is the CANCELLED label honest) and `/futures-strategy-audit` (does the strategy have edge) — this audit asks whether the *fill mechanism itself* (IOC limit, slippage tolerance) is well-tuned, using only confirmed-honest data as input.

Core rule: No proof, no run. No fill-behavior change (limit offset, order type, slippage tolerance) may be made on the basis of this audit alone — this audit only measures; it does not authorize a change.

Required checks:
- `no_fill_reason` and `order_type` fields, for rows postdating the no-fill taxonomy deploy (`3c7a6b044cc8`, 2026-07-07T18:35:33Z) — report how many rows actually carry these fields vs. how many are still pre-taxonomy and reason-less
- `broker_status_raw` (or nearest equivalent field actually present in the journal) at time of cancel
- `signal_timestamp` vs. `submit_timestamp` vs. `cancel_timestamp` — the latency between decision and order submission, and between submission and cancel, per row where available
- `seconds_until_cancel` — is the IOC window itself well-understood, or could a longer resting window plausibly change outcomes (report as a hypothesis to test via replay/shadow, never as a recommendation to implement directly)
- Fill rate by strategy, by instrument, and by session — a blanket "68% no-fill" number (the finding that motivated the original no-fill taxonomy work) hides whether the problem is concentrated in one strategy/session or spread evenly
- Expectancy per decision (not per fill) — the only honest way to ask "would filling more of these signals have helped or hurt," since a no-fill's counterfactual P&L is unknown and must not be assumed positive or negative
- For each `MISLABELED_FILL` row found by `/futures-cancelled-audit`: whether it represents a fillable trade that was wrongly blocked (an argument no-fills are sometimes over-aggressive) — but do not generalize from a handful of anomalies to a systemic claim
- Whether any no-fills coincide with the strategy's better-performing setups (i.e. no-fills disproportionately blocking what would have been winners) or with its worse setups (no-fills disproportionately protecting against what would have been losers) — this requires forward bar reconstruction of a no-fill's hypothetical entry, same method as the phantom-clear audit, and must be reported as a reconstructed hypothesis, not a certainty

Forbidden actions:
- Do not modify IOC limit offsets, slippage tolerance (`ENTRY_SLIPPAGE_TOLERANCE_TICKS_*`), order type, or any execution-path code.
- Do not place trades or run a live/demo test order as part of this audit.
- Do not commit or push.
- Do not recommend a specific tick/offset change as a conclusion of this audit alone — the hard rule below governs any such change.
- Do not generate or commit a report containing strategy names, per-strategy fill rates, or price levels to this public repo — write it to `private/` (gitignored).

Hard rule for any future fill-behavior change (not something this audit itself does):
No fill behavior change unless: (1) no-fill causes are counted and categorized (via the no-fill taxonomy and `/futures-cancelled-audit`), (2) an alternative (e.g. +1 tick, +2 tick, market entry) has been replayed or shadow-tested against the same historical decisions, (3) expectancy per decision improves under the alternative, and (4) tail risk / drawdown does not worsen under the alternative. All four, not any one.

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

Fill Mechanism Classification:
PROTECTIVE / COSTLY / MIXED / INCONCLUSIVE

Why:
2-5 decisive reasons.

What I Verified:
- taxonomy field coverage (post- vs pre-deploy) checked
- fill rate by strategy/instrument/session checked
- expectancy per decision computed
- /futures-cancelled-audit's MISLABELED_FILL findings cross-checked, if any
- no-fill counterfactual hypothesis attempted where data allows, clearly labeled as reconstruction not fact

Problems Found:
Separate:
- blockers
- warnings
- minor cleanup

Required Fixes:
- must-fix before drawing any fill-tuning conclusion
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Safety gates:
- Classification is INCONCLUSIVE, not PROTECTIVE or COSTLY, while the post-taxonomy sample is 0 or near-0 (per `/futures-forward-measurement-gate`) — there is not yet enough reason-coded data to say which way no-fills are cutting.
- Any open `MISLABELED_FILL` finding from `/futures-cancelled-audit` caps this audit at HOLD until resolved — a fill-quality conclusion built on a possibly-mislabeled dataset is not safe to draw.
- This audit never itself authorizes a fill-behavior change, regardless of how compelling a finding looks — see the hard rule above.
- A counterfactual ("this no-fill would have won") is always a reconstruction with stated assumptions, never reported as a confirmed fact.

Safe next step:
If INCONCLUSIVE (the expected current state — 0 post-taxonomy rows as of 2026-07-07), the safe next step is to wait for `/futures-forward-measurement-gate`'s minimum threshold, then re-run this audit. Never "try a small fill change and see" as a safe next step — that is exactly the shortcut the hard rule above exists to block.
