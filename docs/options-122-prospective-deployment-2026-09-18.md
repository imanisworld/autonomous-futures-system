# Options 1-2-2 prospective collector — deployment proof — 2026-09-18

## Verdict

**DEPLOYED / SCHEDULED / OBSERVATION ONLY / FIRST NATURAL RTH ACCEPTANCE ROW STILL PENDING.**

This deployment starts the preregistered `122-IEX-E1` evidence lane. It does not add 1-2-2 to V1, reserve risk, emit trade alerts, prepare orders, call a broker, or authorize DEMO/live trading.

## Frozen policy

Preregistration: `docs/options-122-prospective-collector-preregistration-2026-09-18.md`.

- provisional source: Alpaca IEX;
- delayed authority/reconciliation: consolidated SIP;
- cadence: 60 seconds;
- maximum exact-IEX-trigger -> completed selector capture lag: 120 seconds;
- delayed SIP reconciliation begins after the watch window is at least 16 minutes old;
- strategy stop/target/runner: unresolved;
- futures fixed-2R target: not imported;
- primary structural diagnostic: +1.0 frozen directional-2 reference-range reach, explicitly not a strategy win/loss rule.

## Release

Immutable service-specific release:

`36e73f1981850b66b043d849ce877c15bd1ab3e7`

Release integrity on the VPS:

- `release integrity: OK`;
- 1,346 files checked;
- exact service WorkingDirectory pinned to `/root/afs-releases/36e73f1981850b66b043d849ce877c15bd1ab3e7`;
- exact release venv/`PYTHONPATH` pinned in a service drop-in.

This release is independent of the futures-bot and options-scanner runtime pins.

## Systemd posture

Installed service: `options-122-prospective.service`.

Installed timer: `options-122-prospective.timer`.

Timer calendar was validated by `systemd-analyze` and is enabled/active:

`Mon..Fri *-*-* 09..16:*:00 America/New_York`

The collector itself distinguishes RTH collection from off-RTH/closed-session handling. The first scheduled activation after deployment is Monday 2026-09-21 at 09:00 ET; the 09:00–09:29 period is off-RTH/no-entry collection, RTH begins at the session open, and the 16:00 hour permits delayed reconciliation without new entries.

Hardening:

- `NoNewPrivileges=true`;
- `PrivateTmp=true`;
- `ProtectSystem=strict`;
- `ProtectHome=read-only`;
- only `/root/afs-shared/logs` is writable.

Dedicated evidence paths:

- `/root/afs-shared/logs/options_122_prospective.jsonl`;
- `/root/afs-shared/logs/options_122_source_trades/`.

The production `options_scanner.sqlite` is not an input/output path for this service.

## Pre-activation proof

A manual service start on the deployed release completed successfully while the exchange session was closed:

- status: `CLOSED_SESSION`;
- service result: `success`;
- armed/resolution/option-capture counts: 0;
- no evidence journal or raw-source directory was created by the closed-session proof;
- release integrity remained PASS.

Fresh-import testing for the collector loads no `execution`, `broker`, `webhook`, or `risk` module tree.

Local/CI proof before merge:

- 74 focused/adjacent tests passed for the main collector PR;
- 14 focused tests passed for the closed-session safety follow-up;
- repository CI, CodeQL, and analysis checks passed for both PRs.

The immutable release venv intentionally contains runtime requirements only and does not include `pytest`; VPS test execution was therefore not claimed from that venv. Release integrity and the deployed closed-session service proof were run on the actual immutable release.

## Runtime isolation proof

The deployment did not restart the existing trading services.

Before and after collector activation:

- futures-bot PID remained `1538180`;
- options-scanner PID remained `1198697`.

Both services remained active. The new collector timer is separately active.

## Weekend freeze

As of Friday night, deployment work for this lane is complete. Leave the release, timer, source policy, cadence, capture-lag threshold, reconciliation delay, and evidence paths unchanged through the first natural RTH collection. No further options deployment is needed before Monday unless a read-only audit finds a concrete defect or drift.

The correct weekend action is therefore **WAIT / preserve the frozen epoch**. A clean weekend with no market evidence is expected and is not a missing-data problem.

## First natural RTH gate

The lane is **scheduled but not yet accepted as producing causal option rows**.

The first acceptance proof still requires a natural RTH chain on this exact release:

`ARMED before break -> IEX reversal first -> selector capture <=120s -> production replay parity -> delayed SIP reconciliation`

A continuation-first, no-break, late capture, rejected provisional, or SIP-only reversal remains valid denominator/source evidence but is not a usable causal option-entry row.

No threshold, cadence, source, or endpoint may be changed inside `122-IEX-E1` after seeing Monday's outcomes. Any such change requires a new preregistered epoch.

## Current ruling

**1-2-2: COLLECTING RESEARCH EVIDENCE / NOT TRADE AUTHORIZED.**

No proof, no trade.
