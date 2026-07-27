# ORB Breakout marketable-limit inverse — stopped research pass

## VERDICT

**UNSAFE**

## FINAL DECISION

**REJECT**

The preregistered pass stopped on a candidate-identity invariant before
producing acceptable fixed-population or chronological-system-path promotion
evidence.

Continuing would require changing sizing, so no rerun, rescue pass, or
alternative candidate was tested.

## PREREGISTRATION SHA

`b2c586af8e2b624e93fe0bf18fbab4be15f2003d`

The first preregistration commit was followed by a pre-results metadata
correction to the session-restored attempt digest. The SHA above is the final
controlling freeze. No candidate P&L had been run before it.

## SOURCE CORPUS / ATTEMPT IDENTITY

- Base: exact #358 commit
  `74b14071822be46de46be3c2db0eff7c95b8fced`.
- Corpus: 626 files, 2025-07-24 through 2026-07-23.
- Corpus SHA-256:
  `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4`.
- Source: committed #358 marketable-limit raw artifact.
- Exact ORB Breakout fixed population: 111 approved attempts.
- Stable identity digest:
  `4e357bfc9e4a23c28fbbdf67e7f5cf99cbc40bb065e2e39684b29705b1192970`.
- Instruments: MNQ 111, MES 0.
- Original directions: LONG 88, SHORT 23.
- Sessions: London 71, New York 34, Asian 6.

## EXACT INVERTED RULE

The frozen rule changed only directional exposure:

- LONG became SHORT and SHORT became LONG.
- Planned entry stayed fixed.
- Absolute planned-entry-to-stop and planned-entry-to-target distances stayed
  fixed and were mirrored around entry.
- The source ORB Breakout detector, completed-bar signal, trend/VWAP/volume/
  GEX qualification, ranking, permissions, sessions, thresholds, 2.2R target,
  48-tick MNQ ORB stop offset, and once-per-direction/day behavior stayed
  frozen.
- Entry used #358's eight-tick marketable IOC: fill immediately at/inside the
  directional limit or cancel.
- Baseline used one adverse tick at entry and stop, clean target fills, $1.48
  commission, next-bar-or-later resolution, and stop-first handling for
  ambiguous stop/target bars.
- Candidate sizing was exactly one contract.

## CAUSALITY / FILL REALISM

The pre-run audit and 23 synthetic/broker tests passed:

- the signal bar completes before order construction;
- the completed close is the contemporaneous market observation, not future
  data;
- the IOC limit is bounded and fail-closed;
- resolution begins strictly on the next same-instrument bar;
- ambiguous later-bar stop/target straddles resolve at the stop;
- stop exits receive adverse slippage and targets fill as resting limits;
- mirrored bracket geometry remains valid;
- stable identity hashing and original-versus-inverse attribution reconcile.

No causal shortcut caused the stop.

## STOP CONDITION

The chronological inverse reached this stable source attempt:

| Field | Value |
|---|---|
| Date | 2025-10-15 |
| Bar timestamp | 2025-10-15T09:15:00+00:00 |
| Instrument/session | MNQ / London |
| Strategy | orb_breakout |
| Original direction | LONG |
| Required inverse direction | SHORT |
| Planned entry | 24924.0 |
| Original stop / target | 24911.5 / 24951.5 |
| Source #358 size | 1 contract |
| Chronological inverse recommendation | **2 contracts** |

Under the exact #358 dynamic sizing rules, prior inverted path outcomes had
changed the account balance enough to enter the 2-contract tier. The same
source attempt was one contract on the original path.

This creates an unavoidable choice:

1. continue at two contracts, violating the preregistered one-contract
   candidate; or
2. impose a one-contract hard cap, changing the frozen #358 sizing/path.

Both are prohibited. The preregistration explicitly required an abort rather
than silently choosing either.

## FIXED-POPULATION RESULT

**NOT ACCEPTED — study stopped.**

The fixed-population computation was not published or used after the
chronological sizing invariant failed. Publishing only the breaker-independent
side would omit the required system-path gate and could misleadingly promote a
candidate that is not the same strategy in chronological operation.

## SYSTEM-PATH RESULT

**NOT ACCEPTED — aborted on 2025-10-15 before full-corpus completion.**

Partial path output is invalid and is not reported.

## ORIGINAL VS INVERSE

No P&L comparison is accepted. The source and inverse cease to share the
frozen sizing identity before the chronological pass completes.

## TEMPORAL STABILITY

Not calculated from an accepted complete result.

## INSTRUMENT / SESSION

The source itself is already single-instrument:

- MNQ: 111 attempts.
- MES: zero.
- London: 71; New York: 34; Asian: 6.

It could not satisfy the cross-instrument promotion requirement even if the
aborted P&L had been positive.

## LONG / SHORT

Source original directions were 88 LONG and 23 SHORT, implying 88 inverse
SHORT and 23 inverse LONG. Outcome metrics are not accepted from the aborted
pass.

## COST SENSITIVITY

Not run beyond the baseline stop condition. No slippage tier was altered to
rescue the candidate.

## CONCENTRATION

Not calculated from an accepted complete result.

## DRAWDOWN / LOSING STREAK

Not calculated from an accepted complete result.

## BREAKER / PATH EFFECTS

The decisive path effect occurred before breaker comparison: inverted prior
outcomes changed account state enough to change the ORB Breakout contract
count from one to two.

That is a material strategy/path change, not a bookkeeping difference. A
future study would have to preregister either:

- a genuinely fixed one-contract replay for both original and inverse paths;
  or
- the frozen dynamic-sizing system with varying contracts.

Neither is authorized as a rescue pass here.

## DECISION BASIS

The prompt required the study to stop if exact inversion could not be
expressed without changing another strategy component. The one-contract and
unchanged-sizing requirements become mutually incompatible on the observed
chronological inverse path.

Classification is therefore **UNSAFE** and the final decision is **REJECT**.
There is no paper implementation plan.

No runtime code, #359, #360, Lane B, deployed box, broker, configuration, or
deployment was changed.
