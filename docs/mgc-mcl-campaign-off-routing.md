# MGC/MCL campaign-OFF routing decision

Status: **fail-closed compatibility decision; no execution expansion**.

Parent state: PR #585 made `MGC` and `MCL` collection-only roots for the
`cross_instrument_observation_v1` architecture. PR #586 added the evidence-quality
gate. This record resolves the remaining question about what an OFF campaign
means for MGC/MCL without weakening the collection-only execution boundary.

## Decision

Do **not** restore MGC/MCL to the pre-#585 runner path when the cross-instrument
campaign is OFF.

The pre-#585 webhook ingest allowlist recognized MGC/MCL, but allowing them to
fall through to `process_alert()` again would require weakening the new runner
backstop that returns before journal, DecisionEngine, RiskEngine, PaperBroker or
Tradovate code can run for any collection-only root.

The safer invariant is therefore:

| Root | Campaign OFF | Campaign ON |
| --- | --- | --- |
| MNQ / MES | existing trading route | existing trading route + configured observation hook |
| M2K / MBT | ignored by webhook router | observation-only |
| MGC / MCL | observation-only transport, campaign disabled, **no campaign bar/evidence/state write** | observation-only collection |

MGC/MCL OFF is intentionally **not byte-for-byte historical routing**. It is an
accepted fail-closed behavior change: an alert may be acknowledged/routed as
observation-only, but the disabled transport does not collect campaign data and
can never fall through into execution.

## What OFF proves

With `CROSS_INSTRUMENT_OBSERVATION` and
`CROSS_INSTRUMENT_OBSERVATION_EPOCH` unset:

- MGC/MCL cannot reach DecisionEngine, RiskEngine, PaperBroker, Tradovate, or an
  order route.
- The observation transport returns `OBSERVATION_ONLY` with
  `observation.enabled == false` and `bar_recorded == false`.
- No `cross_instrument_observation_v1.jsonl` row is written.
- No cross-instrument state file is created.
- No MGC/MCL BarHistory row is created by the disabled observation transport.
- A direct caller of `process_alert()` still hits the unconditional
  collection-only backstop before journal/risk/broker code.

The webhook app may still update its generic latest-webhook receipt snapshot for
an MGC/MCL alert. That receipt is not campaign evidence and must not be treated as
authoritative 15m collection health; the dedicated cross-instrument feed-health
proof remains authoritative.

## Deployment implication

An OFF deployment is safe from execution expansion, but it is not claimed to be
behavior-identical for an already-existing MGC/MCL webhook stream. Before an OFF
deployment, determine whether MGC/MCL alerts are currently being sent:

- if none are arriving, this is operationally a no-op for those roots;
- if they are arriving, expect them to be acknowledged as observation-only and
  omitted from the legacy runner/journal path.

That difference is intentional and must not be "fixed" by reopening the runner.

## Tests

`tests/test_mgc_mcl_campaign_off_routing.py` pins the contract:

1. router precedence keeps MGC/MCL observation-only while OFF;
2. disabled transport writes no campaign evidence/state/BarHistory;
3. direct runner calls cannot bypass the collection-only backstop.

## Not authorized

This decision does not authorize deployment, campaign activation, paper trading
of MGC/MCL, changes to TradingView alerts, broker routing, risk-rule expansion,
or live execution.
