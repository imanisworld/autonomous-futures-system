# Futures — Current State Handoff

_As of 2026-09-07. This is the current futures evidence handoff. Do not restart cleanup, rerun settled strategy diagnostics, or reinterpret raw standalone edge as executable proof._

## Verdict

**PAPER ONLY / SEPT. 7 STRATEGY-DIAGNOSTIC SEQUENCE COMPLETE / NO STRATEGY OR GATE FIX JUSTIFIED.**

The interrupted testing sequence is now complete through transition-reclaim, inverse ORB, 4HR MNQ, Miyagi MNQ, and 60M 3-2-2. The only confirmed software/risk defect found in this work was the separate `$150` daily-loss semantic issue, which was corrected locally. The strategy studies below do not justify weakening risk gates or changing strategy logic.

## Do not redo

Completed and settled:

- repository/branch/worktree cleanup audit;
- transition-reclaim direction, matched-control, stop/time/MAE-MFE, IOC/market and runner studies;
- inverse-ORB current reproducible IOC audit;
- 4HR gate attribution, one-gate ablation, architecture audit, and full-engine trend-gate isolation;
- Miyagi MNQ gate attribution;
- 60M 3-2-2 gate attribution;
- prior VWAP Hold historical IOC/runner work.

## Sept. 7 strategy dispositions

### Transition-reclaim — BROKEN / CLOSED

- Current bracket is negative on MES and MNQ under realistic costs.
- Mirrored direction does not rescue it.
- IOC versus market entry makes essentially no difference.
- Runner materially reduces losses but does not recover positive expectancy.
- PR #475 was closed without merge; CI/reproducibility failure remains preserved.

**Action:** stop testing/tuning transition-reclaim unless genuinely new evidence appears.

### Inverse ORB — PROMISING BUT UNPROVEN / WAIT

Current reproducible population:

- 63 arms; fingerprint `f32b1b1d...19e2da2`;
- 57 IOC fills / 57 resolved;
- net after costs **+$1,026.64**;
- PF **5.284**;
- expectancy/fill **+$18.01**;
- max DD **$55.90**;
- H1 **+$546.08**; H2 **+$480.56**;
- Asian **+$63.62**; London **+$179.90**; New York **+$783.12**.

The older documented n=111 population is not reproducible from current canonical journals and remains stale context only.

**Action:** no strategy change; continue natural forward evidence collection.

### 4HR Re-Trigger MNQ — BROKEN FOR CURRENT EXECUTABLE FORM / HOLD

The standalone 81-candidate population showed positive raw/bracket evidence, and the first one-gate ablation made trend routing look suspicious. Full-engine isolation disproved a viable routing fix.

Full-engine trend isolation on the same 81 candidates:

| Arm | Result |
|---|---|
| Current | 1 attempt, 0 fills, `$0` |
| Exempt market-condition only | 37 then fail `TREND_STRENGTH_BELOW_REQUIRED`; 1 attempt, 0 fills |
| Exempt strong-trend only | identical to current; market-condition gate blocks first |
| Exempt both | 28 fail `EMA_STACK_NOT_ALIGNED`; 2 attempts, 1 fill, 1 loss, **-$7.98**, PF 0.00 |

The earlier positive `remove_trending` arm was not equivalent to full-engine behavior because downstream strong-trend and EMA-stack gates were not exposed.

**Action:** no routing fix, no gate loosening, no more 4HR work from this evidence path.

### 12HR Miyagi MNQ — BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS / HOLD

- canonical population: 8 candidates;
- 8/8 fail `rr_below_minimum`;
- 8/8 fail `stop_too_wide`;
- 6/8 fail `MARKET_CONDITION_NOT_TRENDING`;
- 0/8 structural survivors;
- all candidates confluence A/A+;
- diagnostic IOC ceiling at 1–3 adverse ticks: 5/8 fills, net **+$266.60 / +$263.60 / +$260.60**, PF **2.357 / 2.335 / 2.313**;
- H1 negative at every cost tier; H2 positive.

The current engine has no executable `strat_12hr_miyagi` path; a full-engine replay would require inventing strategy logic.

**Action:** do not widen stops, lower R:R requirements, or invent an execution path to rescue it.

### 60M 3-2-2 First Live — BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS / HOLD

- frozen population: 34 candidates;
- documented bracket evidence overall: **+$2,532.66**, PF **13.57**, H1/H2 positive;
- 34/34 fail `rr_below_minimum`;
- 34/34 fail `stop_too_wide`;
- structural survivors: 0/34;
- full engine with permissions neutralized: 26 `TREND_STRENGTH_BELOW_REQUIRED`, 5 `ENTRY_DETACHED_FROM_PRICE`, 1 `MARKET_CONDITION_NOT_TRADABLE`, 1 `WEAK_BAR_CLOSE`, 1 `RR_BELOW_MINIMUM`;
- approved attempts: 0; IOC fills: 0.

The positive all-candidate IOC ceiling is diagnostic only because no candidate survives current risk architecture.

**Action:** no strategy/risk/config change. Do not weaken account controls to make 3-2-2 executable.

### VWAP Hold MNQ NY — PROMISING BUT UNPROVEN

No historical rebuild is needed. Continue the already-running forward evidence campaign. Existing historical runner/IOC evidence remains context; do not recreate it.

## Fix policy from this evidence pass

- Proven software/safety defect → fix it.
- Strategy fails intentional risk controls → do not "fix" it.
- Apparent architecture mismatch → isolate through the full engine before changing routing.
- Raw positive bracket/IOC ceilings are not executable proof when structural survivors are zero.

Applied here:

- `$150` daily-loss semantics: **real defect; local fix justified**.
- Transition-reclaim: **no fix**.
- Inverse ORB: **no fix yet**.
- 4HR: **no gate/routing fix**.
- Miyagi: **no fix**.
- 3-2-2: **no fix**.

## `$150` daily-loss semantic fix — local/uncommitted status

Reported local change:

- `max_daily_loss` is an account-level floor and is no longer multiplied by proposed contracts;
- a 2-contract proposal is rejected once realized daily loss reaches -$150;
- contract sizing remains separate.

Files reported changed locally:

- `risk/risk_engine.py`
- `risk_rules.yaml`
- `tests/test_risk_engine.py`

Validation reported: 3 targeted tests passed. General pytest has not yet been reported as run, and the edits were reported uncommitted on the already-dirty primary worktree. Do not call this current-main until isolated, fully tested, and committed.

## Repo / hold items

- #475: closed without merge; transition BROKEN.
- #463: deliberate HOLD until 2026-09-30; do not retarget the stacked branch again.
- #446: options lane, separate from futures.
- Repo cleanup: closed; do not restart without new provenance evidence.

## Source-of-truth note

`docs/strategy-rules/Strategy_Inventory.md` predates this Sept. 7 evidence sequence. Until its next deliberate reconciliation, this handoff controls for the Sept. 7 evidence delta.

## Smallest safe next step

**No further strategy diagnostic is required from the interrupted test sequence.**

1. Finish packaging the already-made `$150` daily-loss defect fix: isolate the intended diff, run the full regression suite, and commit it if clean.
2. Continue the existing forward evidence campaigns for the still-promising lanes, especially inverse ORB and VWAP Hold.
3. Otherwise stop changing futures strategy/risk logic and wait for new evidence or the Sept. 30 #463 hold date.
