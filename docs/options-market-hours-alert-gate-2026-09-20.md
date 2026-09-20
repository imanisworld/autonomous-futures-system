# Options Market-Hours Alert Gate — 2026-09-20

## Purpose

Close the remaining alert-path gap where scheduled scans stopped outside regular trading hours, but a manual/webhook-triggered scan could still reach the Discord send boundary after the market closed.

This is an alert-delivery safety change only. It does not change setup detection, scoring, contract selection, V1 risk, paper evidence collection, Signa authority, or broker/execution behavior.

## Policy

The approved alert session remains **US-equity regular trading hours (RTH)**:

- normal session: 09:30-16:00 ET;
- early-close session: 09:30 to the calendar-defined early close;
- weekends and exchange holidays: closed;
- pre-market and after-hours: no actionable Discord alert;
- unknown/naive session timestamp: fail closed.

RTH remains the authority even when a particular option venue can quote beyond 16:00 ET. Extended-hours support remains a separate future observation-only project until explicitly proven and approved.

## Implementation

The candidate centralizes the decision in `alert_ranker.session_calendar.us_equity_rth_state()`.

Both:

- scheduled scanner market-hours checks; and
- the final `DiscordAlerter.send_if_eligible()` boundary

use the same session authority.

The Discord gate is deliberately at the final send boundary so scheduled, webhook, and manual scan paths cannot bypass it.

Suppression reasons:

- `market_not_open` — valid trading day before the RTH open;
- `market_closed` — after the session close, weekend, holiday, or after an early close;
- `market_session_unknown` — timestamp lacks usable timezone information.

Evidence/scans may still be recorded when a manual/webhook request arrives outside RTH; only the user-facing alert is suppressed.

## Proof

Focused regression:

- normal RTH alert still sends;
- 09:29 ET pre-market suppresses;
- 16:00 ET and later suppresses;
- weekend suppresses;
- Thanksgiving suppresses;
- Friday-after-Thanksgiving early close sends at 12:59 ET and suppresses at 13:00 ET;
- webhook-triggered after-close scan is still journaled but `alert_sent=false` with `market_closed`.

Focused suite: **56 passed**.

Full tracked project test suite in the isolated worktree: **6,383 passed / 7 skipped**.

The broad repository-root `pytest` command is not a valid project-suite command because ignored/private proof trees and the locally downloaded Webull SDK contain their own conflicting test packages. The authoritative tracked-project run is `pytest -q tests`.

## Scope guard

Changed paths are limited to:

- `alert_ranker/session_calendar.py`;
- `alert_ranker/scanner_legacy.py`;
- `alert_ranker/discord.py`;
- alert/session regression tests;
- this documentation/current-state note.

No futures, strategy, risk-rule, Webull, broker, execution, scheduler, source-policy, or V1 policy file is changed.

## Deployment state

**DEPLOYED / VERIFIED on options-scanner release `a0c34818faaad37b20d8c05249e8f5442d8d7141`.**

PR #824 added the centralized RTH/session gate. PR #825 closed the remaining delivery-time race by re-checking the actual delivery clock at the final Discord boundary while preserving the original scan/decision timestamp for scoring, evidence, sanity checks, and dedupe.

The production promotion used a curated options-only release based on the previously proven scanner release `c7798d4993d1ecfd872313cfc5c84da2cda6625d` plus the #824/#825 runtime/test delta. No broad `main` deployment was used.

Pre-promotion proof:

- curated diff: only `alert_ranker/discord.py`, `alert_ranker/scanner_legacy.py`, `alert_ranker/session_calendar.py` and their focused tests;
- focused market-hours/webhook proof: **22 passed**;
- broader options regression: **2,034 passed**;
- immutable release integrity: **1,394/1,394 files**;
- isolated options candidate boot: healthy, advisory-only, Public read-only, `order_supported=false`, account endpoints forbidden, temporary SQLite path.

Production proof after the options-scanner-only restart:

- running options cwd = `/root/afs-releases/a0c34818faaad37b20d8c05249e8f5442d8d7141`;
- production SQLite path unchanged at `/root/afs-shared/logs/options_scanner.sqlite`;
- pre/post counts unchanged: `scans=29,873`, `options_shadow_journal=9,362`, `options_contract_marks=2,511`, `options_selector_evidence=29`;
- health remained healthy/advisory-only; scheduler running; Signa context scheduler still enabled;
- Public provider remained read-only with `order_supported=false` and account endpoints forbidden;
- release integrity passed **1,394/1,394** on the running release;
- no-network deployed-release proof: **15:59 ET scan -> 16:01 ET delivery = `market_closed`, `sent=false`, zero HTTP requests**;
- futures PID and cwd were unchanged; futures remained on `c7798d4993d1ecfd872313cfc5c84da2cda6625d`;
- service-aware drift gate passed both immutable releases; repository-main differences remained informational only.

Rollback backup of the prior scanner pin was preserved on the VPS. No `.env`, strategy, risk, source-policy, broker, execution, futures, or evidence-cohort setting changed.

## Safety rule

No proof, no alert. No extended-session authority is implied by this gate.
