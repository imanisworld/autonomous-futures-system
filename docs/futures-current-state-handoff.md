# Futures — Current State Handoff

_As of 2026-09-02. This is the single current handoff. Do not recreate completed audits or reopen strategy work unless new evidence proves a defect._

## Verdict

**PAPER COLLECTION GO / STRATEGY VALIDATION WAIT.**

The futures collection stack is running on deployed release `b3d72f8`. A VPS OOM incident exposed a real infrastructure blocker; persistent swap and additive alert-only memory monitoring are now installed and the current runtime verdict is **MEMORY STABLE**. No strategy logic, risk rules, broker routing, campaign definitions, or deployed futures release were changed by the memory work.

There is still no basis to call any collection strategy validated. At the last check, no new post-epoch campaign candidate/outcome rows had appeared, so strategy evidence remains a natural-data wait.

## Current deployed/runtime state

- Deployed futures release: `b3d72f8`.
- Original collection evidence epoch marker: `2026-09-02T01:15:19Z`.
- `futures-bot` was OOM-killed once and restarted automatically on the same `b3d72f8` release.
- Post-restart evidence integrity was checked clean: no duplicate campaign/webhook evidence, lost/unmatched outcomes, lost pending/open state, or restart-window evidence corruption was found.
- All five campaign populations remain configured:
  - `vwap_hold / control`
  - `vwap_hold / modified`
  - `orb_reclaim / control`
  - `orb_reclaim / modified`
  - `vwap_rejection / observer`
- Feeds, TradingView alerts, journals, Tradovate health, and single-account routing checked clean.
- The old Tradovate account-routing ambiguity is therefore not a current blocker; do not revive an account-pin change unless the account landscape changes.
- No post-epoch campaign rows had appeared at the latest evidence check. Do not fabricate traffic or signals to force proof.

## Memory/OOM incident — resolved for collection, still monitored

The OOM root-cause correlation classified the incident as **BOT MEMORY GROWTH + OTHER PROCESS PRESSURE**, not a historical-report scan.

At the OOM review:

- `futures-bot`: about 789 MB
- IB Gateway: about 431 MB
- options ranker: about 298 MB
- VPS physical RAM: about 1.9 GB
- no swap existed at the time of the kill

No heavy report/history/backup job correlated with the incident.

### Remediation now active

- Existing 2 GB persistent `/swapfile` is active and present in `fstab`.
- Existing Codex dynamic memory guard was preserved.
- Existing journal-stall watcher fix was preserved.
- An additive watcher extension was deployed at the box level without changing `futures-bot`.
- Watcher reads OS `/proc` and kernel metrics directly; it does not poll expensive historical-report endpoints.
- It records one memory sample per tick to `memory.jsonl` and routes warning/critical episodes through the existing Discord error path.
- Monitoring is alert-only. It does not kill/restart/redeploy the bot or alter trading state.

Latest verified live tick after additive deployment:

- bot PID: `454299`
- `NRestarts`: `1`
- footprint (RSS + bot swap): about 623 MB
- process high-water RSS since the incident: 851 MB
- `MemAvailable`: about 557 MB
- total swap used: about 726 / 2048 MB
- bot swapped pages: about 4.5 MB
- paging during the tick: zero
- kernel OOM count: 1, no new event
- watcher warnings/criticals: none

The 851 MB high-water mark proves transient spikes can occur between five-minute samples. Current memory verdict is **MEMORY STABLE**, with monitoring armed to escalate on renewed pressure or sustained growth.

### Active memory thresholds

The pre-existing dynamic guard owns absolute RSS/headroom budgets so duplicate fixed alerts remain disabled. The additive checks cover the gaps:

- swap used: WARN 1400 MB / CRIT 1800 MB
- paging activity per tick: WARN 100 MB / CRIT 300 MB
- two-hour footprint growth: WARN +150 MB mostly rising / CRIT +250 MB any profile
- swap active/persistent check
- kernel OOM-count delta

Do not replace this watcher with an older standalone patch. It is intentionally additive because the live watcher already contains other verified work.

## IB Gateway — deferred, not a futures collection blocker

A separate read-only check found:

- The old IB Gateway watchdog unit is dead residue. Its script was removed months ago and its timer is now disabled.
- The gateway container's daily restart is clean and not an OOM event.
- The deployed futures release does not use IB Gateway, and no futures connection to its ports was found.
- The container currently consumes roughly 400 MB and is therefore meaningful reclaimable pressure on this 1.9 GB VPS.
- API port `4004` is currently published externally.

Do **not** stop/disable the container, remove dead units, or firewall port `4004` until dependency on the options/other system is checked. This is tomorrow's narrow follow-up; it does not block futures collection tonight.

## Repository state / completed cleanup

The Sept. 2 local cleanup is complete for everything already proven preserved:

- workspace returned to `main`
- tracked tree clean
- local `main` matched `origin/main` at pre-handoff-update baseline `4042d08c1cc5146d49a95786e8b981be55ffba1b`
- four stale tracked edits were discarded only after exact remote preservation was proven
- eleven stale untracked files were removed only after exact remote/tag preservation was proven
- no worktrees or unrelated branches were touched

Three local-only untracked directories were intentionally retained because they contain unique or active material:

- `codex/` — sole-copy/private evidence and artifacts; do not commit to the public repo or delete without an explicit preservation/disposal ruling
- `.agents/` — includes one `systematic-debugging/SKILL.md` file not yet proven preserved remotely
- `.codex-worktrees/` — contains registered active worktrees

These paths do not affect the deployed VPS or futures evidence collection. Do not redo the branch/preservation audit merely because they appear in `git status`.

## Approved futures fixes — already complete

Do not reopen the prior September defect list. The relevant fixes were already merged/reconciled before the current collection state, including:

- normal paper/replay fill parity
- fail-closed promotion gate
- exact five-population campaign reporting, including zero-count arms
- daily overall false-green correction
- trade/order identity persistence and joins
- durable why-no-trade evidence
- Strategy Inventory reconciliation
- VWAP Hold control/modified collection lanes
- ORB Reclaim control/modified collection lanes
- VWAP Rejection observer payload correction

The current MNQ inverse ORB lane remains paper-only and **PROMISING BUT UNPROVEN**. Do not tune its entries, stops, targets, filters, or risk settings during the evidence window.

## Strategy evidence classifications

`docs/strategy-rules/Strategy_Inventory.md` remains the strategy evidence source of truth.

- ORB Reclaim current/first_cross — **BROKEN — negative evidence**
- ORB Reclaim V4-R — **WAIT**
- 4HR Re-Trigger MNQ — **BROKEN FOR CURRENT EXECUTABLE FORM**
- 4HR Re-Trigger MES — **BROKEN / WAIT**
- 12HR Miyagi — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**
- 60M 3-2-2 First Live — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**
- ORB Breakout inverted evidence lane — **PROMISING BUT UNPROVEN**
- MES `strat_122` — **WAIT**

## Collection rules

- Paper only.
- No live broker execution.
- Do not alter strategy parameters to manufacture evidence.
- Do not fabricate signals or traffic.
- Do not redeploy just because repository `main` advances with unrelated/docs work.
- First natural campaign evidence should be checked for correct generating SHA/release provenance.
- A memory warning/critical, new OOM, unexpected futures-bot restart, stale feed, failed alert/journal write, or campaign-lane failure is grounds to investigate before trusting continued evidence.
- Promotion remains gated on the preregistered evidence requirements, including sufficient trading days and resolved filled economic outcomes; intermediate P&L is not promotion proof.

## Smallest next steps

1. Leave `b3d72f8` and strategy/risk configuration unchanged and continue natural paper evidence collection.
2. Allow the memory watcher to accumulate its two-hour growth window and ongoing history; intervene only on a proven warning/critical or new OOM/restart.
3. On the next natural campaign candidate, verify release provenance is attributable to `b3d72f8`.
4. Tomorrow, perform only the narrow dependency check for IB Gateway. If nothing else uses it, then separately consider stopping/disabling it, removing the dead watchdog units, and closing external port `4004`.
5. Do not redo repository cleanup, preservation audits, or completed futures defect audits.
