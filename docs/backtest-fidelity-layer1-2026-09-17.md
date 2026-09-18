# Backtest Fidelity Layer 1 — replay parity + broker-fill calibration

Date: 2026-09-17
Status: **AUDIT ONLY / REVIEW REQUIRED / NOT DEPLOYED**

## Purpose

Raise the fidelity of future futures backtests before the separate Backtest -> DEMO qualification gate (#644) is allowed to trust them. This change does not promote a strategy and does not authorize DEMO or LIVE.

## C14 is already closed

C14 is not reopened here. Current main already contains the holiday-aware CME trade-date correction proven against TradingView/Pine fixtures in PR #646. The remaining C14-dependent work is evidence regeneration: rebuild the frozen replay corpus and rerun the affected P3 VWAP/HOD/LOD parity checks.

## Proven replay-fidelity defect: PWH/PWL dropped

The live path accepts Pine prev_week_high / prev_week_low and places them into MarketState.key_levels. strategy/confluence_scorer.py consumes those fields when deciding whether a target is near PWH/PWL.

Before this branch, canonical replay had no ReplayCandle fields for those weekly levels and ReplayEngine could not populate them. Therefore the same market facts could receive different confluence scores in live/demo and replay.

Existing P3 evidence already validates the bar-derived prior-week definition against Pine weekly copies: 100% within one tick on 3,161 eligible PWH rows and 3,161 eligible PWL rows. Known disagreements were source-gap contaminated.

## Replay fix

- ReplayCandle now carries prev_week_high / prev_week_low.
- ReplayEngine restores them into KeyLevels.
- csv_to_replay prefers Pine-native weekly fields when present and otherwise derives the immediately preceding Monday-started trading week's high/low using the shared cme_trading_day identity.
- polygon_to_replay uses the same helper.
- structural_level_corpus_build requires those fields in rebuilt corpora and bumps its tool identity from slc-build-v1.4 to slc-build-v1.5.

Missing source bars are not repaired or guessed. Gap/provenance evidence still decides whether a weekly extreme is admissible.

## Golden fidelity regression

tests/test_backtest_fidelity_golden.py freezes four boundaries:

1. Labor Day/C14 trading-week identity still points to the correct previous week.
2. Replay candle loading preserves PWH/PWL.
3. Replay restores those levels into KeyLevels.
4. A target-near-PWH fixture produces identical live/replay KeyLevels and confluence scoring.

This is a focused golden fixture, not a claim that every state field is globally proven identical.

## Broker-fill calibration

scripts/broker_fill_calibration.py is read-only evidence tooling. It measures exact external-broker entry fills only when a confirmed TRADE row has a broker client_order_id, no PaperBroker paper_order_id, and execution_audit.post_fill_validation with exact requested/actual entry and slippage diagnostics.

It reports exact audited fills, external CANCELLED no-fills, missing exact audits, classified no-fill rate, no-fill taxonomy coverage, and signed/adverse slippage distributions by instrument and overall.

It makes no broker calls, writes no journals, edits no config, changes no fill model, and does not invent a minimum calibration sample.

The journal can distinguish PaperBroker from an external broker but cannot independently prove Tradovate DEMO versus LIVE. The source-mode CLI label is therefore descriptive only; account mode must be reconciled separately.

## Still required

1. Independent review and CI.
2. Merge only if approved.
3. Rebuild frozen P-REPLAY with slc-build-v1.5.
4. Rerun the C14-affected P3 parity checks from the actual frozen/source data.
5. Keep C16 missing-bar VWAP sensitivity visible.
6. Run broker-fill calibration only against a source whose DEMO account mode is separately proven.
7. Any slippage-model change must be separately pre-registered and reviewed; do not tune from the same report.
8. Use #644 only after fidelity evidence is complete.

## Scope

Replay fidelity:
- replay/candle_loader.py
- replay/replay_engine.py
- scripts/csv_to_replay.py
- scripts/polygon_to_replay.py
- scripts/structural_level_corpus_build.py

Read-only proof/calibration:
- scripts/broker_fill_calibration.py
- tests/test_broker_fill_calibration.py
- tests/test_backtest_fidelity_golden.py
- this document

No strategy, risk, live broker, webhook execution route, risk_rules.yaml, env, deployment, or service behavior is intentionally changed.
