# CLAUDE.md

Follow `AGENTS.md` as the repository-wide operating contract.

## MNQ backtest seal — independent breaker requirement

Before reviewing or re-running any exploratory MNQ result, enforce `AGENTS.md`'s **2026-06-26 last full CME historical session**. Do not acquire, replay, inspect, tune or score the **2026-06-29 onward MNQ forward/sealed period** outside its own authorized registered forward protocol. An exploratory screen whose source includes post-seal data is `CONTAMINATED_POST_SEAL`; reject all strategy-promotion/validation conclusions, and do not try to repair the result after seeing outcomes. **Breaker checklist:** vendor request upper bound, contract-roll session label (not just UTC timestamp), complete eligible historical date window, any SQL row filtering before execution, and forbidden post-seal rows. `research/mnq_backtest_seal.py` is a helper, not automatic external-API enforcement. See `docs/mnq-research-seal-incident-2026-10-10.md` for the two invalidated October 10 ChatGPT screens and provenance. Distinguish legitimate registered forward observations from exploratory backtesting.

## Claude-specific guidance

- Prefer the **independent breaker / QA** role from `AGENTS.md` Research agent roles: review implementation and safety; do not become the primary experiment generator or invent new strategy variants.
- Use the existing commands in `.claude/commands/` instead of inventing duplicate audit procedures.
- For general futures state use `/futures-full-audit`; for a change review use `/futures-diff-review`; for deployment verification use `/futures-deployment-safety-audit`.
- Use the corresponding options commands for options work.
- These audits are evidence gates, not permission to deploy. Deployment remains a separate explicit operator-directed action.
- Futures strategy-status truth is `docs/strategy-rules/Strategy_Inventory.md`; options current-state truth is `docs/options-current-state-handoff.md`; experiment history is `docs/research-trial-ledger.jsonl`. Do not treat dated status/handoff docs as competing authorities.

## VPS / deployment

- The Claude VPS identity is `claude-audit`; do not substitute root or another agent's identity.
- Do not edit the live symlink target or immutable release directories in place.
- Work from a Git checkout/worktree and deploy only an exact reviewed SHA through the sanctioned controlled-release path.
- Never read or print production secrets merely because filesystem or sudo access makes them reachable.
- If Claude is granted a controlled deploy wrapper, use only that wrapper for privileged deployment actions; do not seek broader sudo access.
- Respect the deploy lock and all release/posture/integrity gates. Never force a lock without explicit operator authorization and proof that the existing lock is stale.

## Session closeout

Before reporting completion, verify the result from source and, when applicable, from the VPS runtime. State unverified items explicitly.

## Options breaker baseline

For options reviews, the current-state authority remains `docs/options-current-state-handoff.md`. In addition to execution-safety checks, verify the canonical evidence boundary when relevant:

- signal identity/provenance cannot be rewritten after the fact;
- registered epoch scope (setup, timeframe, universe/ticker, data source, effective dates) must match exactly;
- late/missed/gap/data-blocked/unregistered/counterfactual rows cannot contribute realised/scorable outcomes;
- chronology and source evidence must remain internally possible and fail closed when incomplete;
- a FROZEN epoch or green alert/risk pipeline is not enough to declare paper readiness;
- `READY FOR PAPER` requires the repository's current readiness contract plus prospective forward evidence under the frozen registered epoch. Otherwise use HOLD or the narrower forward-proof/preview classification.

When a change only moves ancestry or documentation and the reviewed code blobs are byte-identical, explicitly verify that fact rather than repeating the entire semantic audit.

