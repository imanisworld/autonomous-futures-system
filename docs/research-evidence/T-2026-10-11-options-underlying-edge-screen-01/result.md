<!-- trial_id: T-2026-10-11-options-underlying-edge-screen-01 -->
# Retrospective attempt record — 2026-10-11 underlying-price screen

**Ledger event:** `UNREGISTERED_ATTEMPT`  
**Disposition:** `INVALID_EVIDENCE` (missing prior ledger preregistration; not a claim that the reported arithmetic was disproved).

The three-family, 66-symbol Alpaca SIP 5-minute underlying-price screen was already completed before its attempt was entered in the shared research ledger. It cannot satisfy the repository's register-before-evidence ancestry rule. Do not cite its outcomes as ledger-governed evidence, a validated options edge, or promotion/readiness proof; do not rerun it to make the registration appear prospective.

**Original artifacts, unchanged:** `docs/research-notes/options-underlying-edge-2026-10-11/` — `README.md`, `results.json`, `episodes.jsonl`, `verify_results.py`, and `run_underlying_edge.py`. These remain historical/source-only provenance; this record is a pointer, not a replacement scored result.

**Source linkage:** merged PR #1222, commit `4d563332757a28ccf39928634101bab34044f2cc`, source baseline `52906d41c41889c31e84685723e335c58b92ee6d`, definition `underlying-edge-v1-2026-10-11`. Population: 66 symbols, Alpaca SIP RTH 5-minute bars, 2026-04-01 through 2026-09-08; 2026-09-09 through 2026-09-15 was sealed and not scored.

The variant manifest for this ID is **retrospective**, not evidence of pre-run freezing. Original reported operator decisions remain historical decisions, not ledger-authorized strategy verdicts. The current options authority remains `docs/options-current-state-handoff.md`.
