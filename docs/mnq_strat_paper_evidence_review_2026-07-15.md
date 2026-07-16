# MNQ Strat paper-evidence collector — pre-deployment review

Status: **local review candidate only; not deployed**

- Review branch: `codex/mnq-strat-paper-evidence`
- Rollback commit: `1011b6bf84735a894adeaefa27e0ae747281bd62`
- Scope: MNQ only; `strat_22_reversal`, `strat_22_continuation`,
  `strat_32`, and `strat_322`
- Normal strategy execution, risk rules, broker configuration, live-trading
  flags, and existing ORB/VWAP lanes are unchanged.

## Files changed

- `config/settings.py` — four fail-closed lane mode settings
- `execution/mnq_strat_evidence.py` — isolated candidate/evidence lifecycle
- `execution/paper_broker.py` — backward-compatible restoration of a synthetic
  paper order identity and runner maximum-favorable state
- `ops/live_box_guard.py` — proof-pin coverage for the four new mode variables
- `ops/mnq_strat_evidence_monitor.py` — lane-generic evidence statistics
- `scripts/mnq_strat_evidence_monitor.py` — read-only monitor CLI
- `webhook/runner.py` — additive post-claim hook on authoritative MNQ decision
  bars only
- `tests/test_mnq_strat_evidence.py` — detection, isolation, lifecycle, and
  regression coverage
- `docs/mnq_strat_paper_evidence_review_2026-07-15.md` — this review packet

## Exact existing definitions reused

No pattern definition was added or reinterpreted.

- `strat_22_continuation`: `classify_sequence()` labels two consecutive
  directional bars of the same type (`2U-2U` or `2D-2D`) as continuation.
- `strat_22_reversal`: `classify_sequence()` labels opposite consecutive
  directional bars (`2D-2U` or `2U-2D`) as reversal, except for its existing
  higher-priority `3-2-2`/`1-2-2` classifications.
- `strat_322`: the existing classifier label `strat_322_reversal`, requiring an
  outside bar two bars back followed by opposite directional `2` bars.
- `strat_32`: the requested lane name maps only to the existing classifier and
  resolver name `strat_outside_continuation`: any bar, outside bar, then a
  directional `2` follow-through. Its existing resolver retains the relative
  volume minimum of `0.8`, entry one tick beyond the signal bar, six-tick stop
  buffer, and 2R target.
- The 2-2 and 3-2-2 brackets come from the existing
  `_missing_strat_family()` shadow resolver: entry one tick beyond the previous
  bar, stop one tick beyond its opposite side, and 2R target.

## Execution-path proof

The collector runs only after the normal journal has atomically claimed an
authoritative MNQ decision bar. It does not run on the five-minute retest path.
It is additive: returned evidence metadata cannot change the normal decision,
risk, permission, broker, or exit path.

Each detected classifier sequence maps to exactly one lane and one evidence
file. Rejected and accepted candidates are written before any hypothetical
outcome. Duplicate keys are lane/instrument/timestamp/direction scoped.

- `observe_only` does not construct a broker and cannot create an order.
- `paper_sim` constructs only `PaperBroker`; the collector contains no
  Tradovate adapter import or route.
- A paper fill uses the live decision-bar close plus the configured adverse
  slippage. Every order identity must begin `PAPER-` or the collector raises an
  isolation error.
- Broker flatness is proven only by a same-day, previously captured, read-only
  preflight snapshot. The collector never performs a broker read itself.
  Missing, stale, non-flat, or malformed evidence prevents paper entry.
- Lane state persists the synthetic order identity through entry, stop, target,
  runner observations, and outcome. Static exit ownership remains in
  `PaperBroker`; the existing runner formula is recorded as evidence without
  rerouting the order.
- `PaperBroker` is configured pessimistically: if stop and target are both
  touched on a bar with unknown path, stop wins.
- Net results deduct the existing MNQ round-trip commission model and use the
  existing MNQ tick size/value.

## Mode/configuration diff

Code defaults all four modes to `observe_only`. The requested initial activation
is intentionally **not installed** before review. If approved, the deployment
environment and matching proof pins would be limited to:

```text
MNQ_STRAT_22_REVERSAL_MODE=paper_sim
EXPECTED_PROOF_MNQ_STRAT_22_REVERSAL_MODE=paper_sim
MNQ_STRAT_22_CONTINUATION_MODE=observe_only
EXPECTED_PROOF_MNQ_STRAT_22_CONTINUATION_MODE=observe_only
MNQ_STRAT_32_MODE=observe_only
EXPECTED_PROOF_MNQ_STRAT_32_MODE=observe_only
MNQ_STRAT_322_MODE=observe_only
EXPECTED_PROOF_MNQ_STRAT_322_MODE=observe_only
```

No `.env`, deployment pin, strategy permission, risk, ORB/VWAP, schedule,
exit-mode, paper/live, or Tradovate setting is changed in this review commit.

## Verification

- Full repository suite: `3271 passed in 111.63s`
- Focused Strat/PaperBroker/webhook/ORB/VWAP/live-guard suite after final code
  changes: `335 passed in 16.55s`
- Final collector/PaperBroker check after the monitor robustness fields were
  completed: `50 passed in 0.15s`
- Python bytecode compilation: passed
- `git diff --check`: passed
- Ruff was not available in the workspace, so no Ruff result is claimed.

Covered invariants include independent detection, reversal/continuation label
separation, duplicate suppression, observe-only non-execution, PaperBroker-only
routing, `PAPER-...` identity, lifecycle ownership, conservative same-bar
resolution, flatness-evidence fail-closed behavior, monitor metrics, and
unchanged ORB/VWAP configuration/behavior.

## Deployment and rollback

Nothing has been deployed and the requested activation is not active. The
pre-change rollback point is
`1011b6bf84735a894adeaefa27e0ae747281bd62`. Deployment requires a separate
review decision and should remain SHA-bound and file-allowlisted.
