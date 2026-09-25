# Futures docs reconciliation — 2026-09-24

**Docs only.** No code, config, yaml, tests, Pine, or env change. Nothing here is a server check, a deploy authorization, or a lane stop.

> **Authority follow-up (2026-09-25):** strategy status truth is now locked to `docs/strategy-rules/Strategy_Inventory.md`; experiment history to `docs/research-trial-ledger.jsonl`; agent roles in `AGENTS.md`. This reconciliation note remains provenance for the 2026-09-24 fixes below — it is not a competing status authority.

Repository head when this note was written: `34177183d2a2abbb2442b4bd2dee3f6579875140` (`main`).

Where a claim comes from the private handoff log, it is marked **HANDOFF-REPORTED, NOT INDEPENDENTLY VERIFIED**. This note does not copy hostnames, addresses, account identifiers, balances, or secrets from that log. Git facts below were checked in this repository.

## Where the audit was wrong or incomplete

1. **README does not say the Asian session is off.** It said Asian was enabled for testing, on a narrower clock (Asian from 19:00, London ending 08:30, New York ending 12:00). That still conflicts with `risk_rules.yaml` v1.2.2. The README was corrected to the yaml clocks, not to a "no Asian session" story.
2. **The 2026-09-24 Miyagi study is not a file in this repository.** The private handoff (commit `9305200bc0d83ad098d27b35dd969b2ef13d23f5`, entry ~11:20Z) records a timing/entry study as SIGNAL REJECTED / RETIRE, and says the report was not committed here. The inventory now follows that handoff-reported verdict. It is not an independent re-run.
3. **`docs/futures-current-status-2026-09-22.md` did not say `cddef4a` was live.** Its latest runtime block names `45290e03` at 02:17Z on 2026-09-24, which is older than both `cddef4a` and the handoff-reported curated release. The "`cddef4a` is live" sentence is in `docs/futures-current-state-handoff.md` and in the morning HOLD doc.
4. **The S/D seal in the committed prereg is the validation protocol, not a one-line global ban.** `docs/prereg-mnq-sd-zone-lfull-hold-validation-2026-09-24.md` forbids detection and P&L on the validation files before the evaluation date, and forbids looking at that evaluation (including the trade count) before 2027-01-29. The handoff states a broader landmine: do not run detection, P&L, or trade counts on any MNQ bar after 2026-06-26 before 2027-01-29. Both are recorded under Open operator rulings. This note does not pick a winner.
5. **"Enforcement before Dec 11" is a handoff deadline, not something the code already schedules.** `execution/contract_identity.py` is observe-only. The design amendment also says enforcement stays off until a roll window has been observed. See ruling (c).

## 1. Runtime identity

| Fact | Evidence |
|---|---|
| Morning HOLD, 10:25 AM ET, last verified runtime named as `cddef4a4b6583f31007268789545c7be24345d75` (#1007), candidate `44202701` (#1015) | `87b6a4296837da0441d55dcc54e0c9971fb3b17a` (#1016); `docs/futures-post-close-deployment-readiness-2026-09-24.md` |
| Handoff-reported live release `41ae1881655c235a26288244c81b8df2a7c8c968`, switched 16:46:34Z (12:46 PM ET) | Private handoff commit `7889f31da6d125a23221906a0417a046c9951a34`. **Not independently verified.** |
| Branch `release/futures-cddef4a-curated-20260924` tips at that SHA. Merge-base with `cddef4a` is `cddef4a`. Six commits sit on top. | `git merge-base`, `git log cddef4a..41ae188` |
| Those six commits are cherry-picks of main #1003, #1008, #1011, #1010, #1013, and #1014. Stable patch-ids match. | `61064d5`=`d6358e2`, `f500fc0`=`58ab077`, `47c391f`=`223f5cd`, `6999567`=`6f2d75b`, `5ad88bd`=`cfd3313`, `41ae188`=`501b7b9` |
| The curated branch does not contain the docs/research commits the HOLD compare counted (#1009, #1012, #1015). Main has since moved past `44202701` to `34177183`. | `git log` on `main` and on the release branch |

**Change:** the HOLD doc now opens with a superseded-by-report banner and a pointer here. The morning HOLD text is kept. `docs/futures-current-state-handoff.md` and `docs/futures-current-status-2026-09-22.md` point at this note so older "verified release" blocks are not read as the post-12:46 identity.

## 2. Daily 2-2, MES 1-2-2, Asia D+EMA

`60bb47d87b63369238aa7fb7241fa3aafa53e900` (#986, 2026-09-24 00:07 ET) is on `main`. It adds the two retirement audits and updates the inventory row for MES 1-2-2. It does not update `docs/futures-current-state-handoff.md`.

| Lane | Verdict in that commit | File |
|---|---|---|
| MNQ Daily 2-2 | BROKEN for the current $5k design; retired as a promotion candidate | `docs/daily22-mes122-retirement-audit-2026-09-23.md` |
| MES 1-2-2 | BROKEN — execution/fill realism; retired as a promotion candidate | same file; inventory row already updated in #986 |
| MNQ Asia D+EMA | RETIRE; independent-year PF 1.07, second half negative | `docs/strat22rev-session22c-asiadema-retirement-audit-2026-09-24.md` |

The same commit also retires Strat 2-2 reversal and Session 2-2 continuation. Those two were not PROMISING campaign sections in the current-state handoff. The current-status focus list named the Asia and Session 2-2 collectors; that list now says they are retired as promotion candidates. Collectors were still running when the audits were written. This note does not stop them.

**Change:** status lines in sections 3, 4, and 5 of `docs/futures-current-state-handoff.md`, plus a banner at the top of that file.

## 3. 12HR Miyagi

The master-table and profile verdict were **PROMISING BUT UNPROVEN**. The 2026-09-24 timing/entry study that rules the lane out is the handoff entry above, not a committed doc. Inventory verdict is now **RETIRE — signal rejected**, with that citation in the Miyagi profile, the family-2 note, and the build-queue lines.

Headline recorded in the handoff, not re-computed here: causal decision clock; 2026-09-19 stop fix held; pooled p=0.212 versus a gate below 0.10; 8 MNQ fills and 10 MES fills; 0/15 MNQ stops inside 120 ticks; 0/34 reached 2R.

`ops/project_check/daily.py` reads the first bold span of the master-table verdict cell. The new span is `RETIRE — signal rejected` and does not contain `PROMISING BUT UNPROVEN`, so the dashboard classifier does not keep the old label.

## 4. README versus `risk_rules.yaml` v1.2.2

Checked against `risk_rules.yaml` (`version: "1.2.2"`), `config/settings.py` (`LiveTradingBlockedError` when yaml or `LIVE_TRADING_ENABLED` enables live trading), `context/wide_stop_execution.py` (route `tradovate_demo`, proof pins, refuses a non-demo env), and `gh repo view` (`visibility: PRIVATE`).

| README claim before this note | Current behavior |
|---|---|
| "this public repository" | Repository is private |
| Stop after 2 consecutive losses | `max_consecutive_losses: 9999` (off). `circuit_breaker_losses: 0` |
| Asian 19:00–03:00, London to 08:30, New York to 12:00, Asian "enabled for testing" | All three sessions allowed. `session_hours_et`: Asian 18:00–03:00, London 03:00–09:30, New York 09:30–17:00. `session_windows` and `session_cutoffs_et` are empty |
| Webhook "does not connect to a broker or place live orders" | Live orders are blocked at config load. Default path is paper. A gated Tradovate DEMO route exists and can place demo orders when armed |
| Allowed instruments include MES, MGC, and MCL | `instruments.allowed` is MNQ only. The others are commented out |
| Phase 4 Tradovate simulation still future; "live broker execution remains out of scope" | The DEMO route exists now. Live execution remains blocked at config load |

**Follow-up, not edited:** `SECURITY.md` still says "this public repository." Several audit-skill files say "this public repo" as a warning not to commit strategy reports. Those warnings still apply; the visibility sentence is stale.

## 5. Stale `risk_rules.yaml` rationale (yaml not edited)

| Location | Comment | Why it is stale |
|---|---|---|
| `instruments` header, about lines 19–24 | MES disabled for the isolated MNQ ORB Breakout inverse forward-paper lane; restore MES when that lane concludes | Inverse ORB is **RETIRED / NOT ACTIVE** (#517, 2026-09-08). Inventory and `docs/futures-current-status-2026-09-22.md` say the env pins are accounting isolation, not activation. The MES exclusion may still be wanted for other reasons. The comment's reason is the retired lane. |
| `daily_limits`, about lines 79–81 | `max_trades_per_day` capped at 3 for that same inverse forward-paper lane, and not a reversal of the 2026-06-17 throttle removal | The inverse-ORB justification is stale. The later comment on 2026-09-18 keeps 3 as a provisional account-safety ceiling because sealed studies did not support changing it. The value 3 was not changed. |

**Follow-up:** rewrite those comments in a reviewed config change. Do not treat this note as permission to edit `risk_rules.yaml` or to restore MES.

## 6. Open operator rulings

No ruling below is decided here.

### (a) #994 rerun versus the S/D validation seal

- PR **#994** (open, `research/mnq-orb-stage-a-v02-20260924`) says the next permitted research step is to rerun six WAIT cells, unchanged, on independent MNQ sessions after 2026-06-26. Stage B is not earned.
- Committed seal: `docs/prereg-mnq-sd-zone-lfull-hold-validation-2026-09-24.md` (frozen in #1019, `34177183`). Validation window is trading days 2026-06-29 through 2027-01-29. Existing local files for that period must not be run through detection or anything that produces P&L before the evaluation date. Evaluation is once, after 2027-01-29, and nothing in that look, including the trade count, may be opened early.
- Handoff commit `802e1073e16522f371dfdfdda30852d1d02178e7` states a broader landmine: do not run detection, P&L, or trade counts on any MNQ bar after 2026-06-26 before 2027-01-29, including `data/replay_polygon_parity_2026_07_16_09_22`.
- **Open:** whether #994's post-2026-06-26 MNQ rerun is forbidden by that seal until 2027-01-29, or only an S/D-validation use of those bars is forbidden. Do not run it until an operator says which reading governs.

### (b) Do armed MNQ wide-stop DEMO fills count as 4HR / 3-2-2 evidence?

- Code: `context/wide_stop_execution.py` runs Tradovate demo **additively** beside paper for 4HR Re-Trigger and 60M 3-2-2 only. Daily 2-2 is not demo-eligible. The demo order is an IOC limit with an 8-tick tolerance (`DEMO_ENTRY_EXECUTION_MODE = "ioc_limit"`, `FROZEN_MNQ_IOC_TICKS = 8.0`), placed from the candidate's entry, stop, and target (`context/wide_stop_demo_runtime_core.py`). The route refuses a live broker.
- The inventory's PROMISING 4HR and 3-2-2 rows cite the 2026-09-18 causal pre-armed studies, which are a different entry clock from an 8-tick IOC at the decision.
- The same 2026-09-24 handoff research set calls the lane as built (IOC 8 at the decision-bar close) destructive of the 4HR edge relative to a next-open fill, and says about half of the historical wide-stop candidates miss that IOC. That study was not committed here.
- **Open:** whether a fill from the armed demo lane, as built, is evidence for the pre-armed 4HR or 3-2-2 result, evidence only for the IOC-at-close implementation, or not evidence until the entry-clock defect is ruled on. This note does not relabel 4HR or 3-2-2.

### (c) Contract-identity guard is observe-only; enforcement and the Dec 11 roll

- #969 is merged and observe-only. `execution/contract_identity.py` says it never blocks an order. `record_observation` writes `"enforced": False`. `execution/tradovate_broker.py` logs `CONTRACT_IDENTITY observe: ... — not enforced` and does not return early.
- Next equity-index routing switch under the coded rule: `_ROLL_DAYS = 8`, and `docs/index-roll-rule-check-2026-09-23.md` says order routing switches on Friday 00:00 ET, 7 days before the nominal 3rd Friday. December 2026's 3rd Friday is 2026-12-18, so that switch is **2026-12-11**. The same note's observed TradingView pattern is about 3 business days before last trade, which lands near 2026-12-15 if last trade is 2026-12-18. Those are calendar readings, not a new roll rule.
- Handoff commit `15bf12507ef708eae8f5c3332dba0d524495f751` sets a deadline to build, review, and deploy flag-pinned enforcement **before 2026-12-11**, or to pick a stopgap for the window if it is not ready.
- `docs/contract-identity-current-contract-amendment-2026-09-24.md` says enforcement is not enabled until a real roll window has been observed in paper/observe mode, or equivalent roll-seam evidence is accepted.
- **Open:** whether enforcement must ship before 2026-12-11, or must wait until that window is observed. The guard stays observe-only until a later reviewed change.

## Files changed

| File | What changed |
|---|---|
| `docs/futures-docs-reconciliation-2026-09-24.md` | This note |
| `docs/futures-post-close-deployment-readiness-2026-09-24.md` | Superseded-by-report banner |
| `docs/futures-current-state-handoff.md` | Runtime banner; Daily 2-2, MES 1-2-2, and Asia D+EMA status |
| `docs/futures-current-status-2026-09-22.md` | Runtime pointer; Asia and Session 2-2 focus lines marked retired as promotion candidates |
| `docs/strategy-rules/Strategy_Inventory.md` | 12HR Miyagi verdict |
| `README.md` | Private repo, live-trading lock, DEMO route, sessions, consecutive-loss stop, allowed instruments |
