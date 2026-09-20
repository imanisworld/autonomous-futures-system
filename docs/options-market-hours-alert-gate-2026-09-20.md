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

**BUILT / TESTED / NOT DEPLOYED.**

The standing 2026-09-20 readiness ruling remains in force: no additional options-scanner deploy or restart before the first natural Monday RTH evidence window. Merging this infrastructure fix does not itself change the running scanner.

A later deployment must use the normal immutable options-scanner release process and prove:

1. the intended release pin;
2. production SQLite continuity;
3. `order_supported=false` / no broker-order path;
4. normal RTH alerts still eligible;
5. a controlled out-of-session alert attempt is suppressed;
6. futures service is not restarted.

## Safety rule

No proof, no alert. No extended-session authority is implied by this gate.
