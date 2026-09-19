# Preregistration — Futures Causal / Mechanics Audit — 2026-09-19

## Verdict scope

**AUDIT ONLY / NO RUNTIME CHANGE / NO DEPLOYMENT.**

Purpose: search the remaining futures system for the same defect classes already
proven in 4HR Re-Trigger and 60M 3-2-2 First Live: delayed completed-bar entry,
use of higher-timeframe information before it was causally complete, and
live/replay formula divergence.

This audit does not tune strategy thresholds, widen risk rules, add execution
authority, or reinterpret outcomes to rescue a strategy.

## Frozen base

Repository base at preregistration:
`36e73f1981850b66b043d849ce877c15bd1ab3e7`
## In scope

1. **12HR Miyagi**
   - classify documented trigger as touch/break vs close-confirmed;
   - compare research detector trigger timestamp with its fill/replay timestamp;
   - determine whether a completed 5m/15m bar introduces avoidable latency;
   - verify stop/target references use only completed information.

2. **MES 1-2-2 / shared 2-1-2, 1-2-2 state logic**
   - map setup bar, trigger rule, decision timestamp, and executable timestamp;
   - compare canonical state-machine semantics with runner/replay/paper lane;
   - look for off-by-one completed-bar or session-boundary drift.

3. **Higher-timeframe aggregation used by production futures logic**
   - identify 1H/4H/12H/Daily aggregates built from lower-timeframe bars;
   - prove each reference bar is complete at the decision/trigger time;
   - flag any path that can consume the bar that is only completing at the same
     instant as an earlier intrabar trigger.
4. **Live/replay formula parity**
   - 4HR and 3-2-2: static formula/state parity only; do not rerun outcome studies;
   - MES 1-2-2: compare canonical strategy state with paper/replay wrappers;
   - Miyagi: identify whether a production parity target even exists.

5. **Timestamp/session mechanics**
   - bar-open vs bar-close convention;
   - ET conversion / DST handling;
   - inclusive/exclusive session endpoints;
   - missing-reference-bar behavior;
   - equality vs strict-break semantics where documented.

## Explicitly out of scope

- Daily 2-2 trigger timing: already active in separate worktree/audit.
- LC_ZONE v1 redesign: remains HOLD.
- ORB/VWAP strategy rescue or retuning.
- new strategy variants.
- risk-cap or R:R relaxation.
- paper-fill, DEMO, or live execution expansion.
- broker submission.
- prospective observer evidence scoring.
## Classification rules

A finding is a **BLOCKER** when the current implementation can:
- act later than a documented live touch/break and materially alter entry state;
- use a not-yet-complete HTF candle at the true decision time;
- produce different setup/trigger/stop/target state between canonical live and
  replay formulas for identical information;
- cross a documented session boundary incorrectly;
- fail open on missing causal reference data.

A finding is **NOT A DEFECT** when:
- the strategy explicitly requires a completed candle/close;
- later information is used only after the rule-defined decision time;
- the difference is presentation-only and cannot affect setup, trigger, bracket,
  risk, or execution state.

## Evidence standard

For each strategy/path record:
- rule source;
- code source;
- earliest knowable setup time;
- earliest legal trigger time;
- actual decision/evaluation time;
- stop/target reference time;
- live/replay state-machine source;
- whether missing data fails closed;
- verdict: CLEAN / DEFECT / PARITY GAP / NOT EXECUTABLE / NEEDS CONTROLLED TEST.

No profitability conclusion may be drawn from this audit.

No proof, no run.
