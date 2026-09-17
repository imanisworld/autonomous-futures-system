# Forward Evidence Readiness Audit — 2026-09-17

**Status:** AUDIT ONLY / PAPER ONLY. No campaign activation, epoch change, deploy, restart, outcome read, or R5 authorization.

## Question

Is there any known repository-side blocker that requires new feature work before the currently authorized paper/observation campaigns can keep collecting evidence?

## Verdict

**Collection can continue, but two known deployed-runtime defects limit specific evidence fields on some conditions. Neither justifies stopping the campaigns.**

The correct action is to keep collecting while preserving the known limitations, then repair them through the sanctioned release path.

## What is already structurally ready

- MNQ/MES paper and shadow evidence lanes exist.
- M2K/MGC/MCL/MBT collection-only routing is structurally separated from DecisionEngine, RiskEngine and broker execution.
- six-root cross-instrument observation collection exists independently of historical-corpus admission;
- journal/candidate/outcome provenance mechanisms exist;
- replay/corpus tooling exists for the admitted roots and is being extended under separate preregs;
- #621 repaired replay shadow-history continuity in the repo;
- paper/live posture remains separate from research admission.

No new strategy feature is required merely to let evidence accumulate.

## Known evidence limitations while the box remains on the older release

### #612 not yet deployed

The currently deployed pre-#612 runner can suppress later **main-runner strategy observation** after shared daily max-trade or consecutive-loss capacity is reached, because it returns before strategy evaluation.

Implications:

- this is conditional, not an all-day feed failure;
- it does not justify changing max-trades/loss limits;
- absence of later main-runner candidates on a capacity-exhausted day must not be interpreted as proof that no setup existed;
- the collection-only transport for M2K/MGC/MCL/MBT is architecturally separate and should not be promoted or demoted based on this defect without path-specific evidence.

### C8 not yet repaired

Affected MES decision rows can have top-level `market_condition=None` while the correct value remains in `context.market_condition`.

Implications:

- candidate/setup collection itself can continue;
- F7/F8-style cross-instrument regime/persistence analysis must use the documented repaired analysis field or remain NOT_TESTABLE as journaled;
- do not treat null top-level MES regime as a real `UNKNOWN` market state.

## Waiting/data gates that are legitimate

These are not engineering defects:

1. **M2K parity sample:** preliminary common-bar sample is too small; rerun after the preregistered minimum forward-session threshold.
2. **MGC/MCL/MBT:** historical parity waits on product-specific session definitions and proven dated-contract/roll provenance.
3. **R5:** remains blocked until the required parity/corpus gates are reviewed and explicitly authorized.
4. **Strategy validation:** forward sample size, multiple months, realistic fills and drawdown evidence require elapsed market data; they cannot be replaced by more code.
5. **Context-permission program:** hard review remains scheduled under its existing prereg; no new context gate should be added before that review.

## Daily evidence-quality interpretation until the runtime release

For every review/report generated from the current box, annotate:

- deployed SHA;
- whether daily capacity was reached before the end of the analysis window;
- whether rows rely on top-level MES `market_condition`;
- timeframe provenance;
- campaign/epoch identity;
- any feed-gap/roll/session-provenance flags already required by the relevant prereg.

If capacity was reached under the old runner, classify later main-runner setup absence as **OBSERVATION_CENSORED_BY_DEPLOYED_#612_DEFECT**, not `NO_SETUP`.

## No-work conclusion

There is no evidence-supported reason to add another strategy, gate, instrument execution route, or fallback merely to keep collection moving.

The safe parallel program is:

- collect continuously under current paper/observation posture;
- finish the known proof/repair tasks;
- deploy the narrow runtime repairs only after the freeze;
- let sample-size gates mature naturally.