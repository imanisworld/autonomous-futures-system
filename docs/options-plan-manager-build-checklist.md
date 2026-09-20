# Options plan-manager build checklist

This checklist is intentionally small and Phase-1 only.

## In this foundation

- [x] one evolving thesis per ticker/direction/setup/timeframe
- [x] required lifecycle statuses
- [x] repeated Signa dedupe counters
- [x] Signa excluded from actionability and conviction
- [x] existing target finder reused
- [x] target source provenance retained
- [x] unverified gamma-labelled levels ignored
- [x] explicit no-default high-conviction threshold
- [x] material-change vs telemetry-only update distinction
- [x] no order, broker, network, Discord, or execution imports

## Before wiring to alerts

- [ ] canonical Signa authority cleanup is merged
- [ ] canonical scanner/advisory outputs replace caller booleans
- [ ] persistent thesis journal/restart recovery exists
- [ ] structural-level collection has causal provenance
- [ ] contract selector is proven
- [ ] aggregate-risk budget has an explicit operator value
- [ ] $300 per-trade default has explicit policy review
- [ ] forward high-conviction threshold is pre-registered
- [ ] delayed SIP latency acceptance is complete

## Before any paper automation

- [ ] Phase-1 forward evidence demonstrates that the plan manager improves
      signal quality / alert usefulness without hiding valid setups
- [ ] no repeated state is counted as independent evidence
- [ ] targets and invalidations are reconstructable from the pre-trade journal
- [ ] every actionable alert can be tied to its exact proof packet

### Webull sandbox paper-broker lane

- [x] sandbox credentials are separate from live credentials
- [x] exact sandbox host is enforced
- [x] live Webull API and live options trading are fail-closed
- [x] sandbox Individual Cash account discovery is proven
- [x] sandbox read-only balance and positions are proven
- [x] option-contract metadata discovery is proven
- [x] adapter has no submit/cancel/replace/live-routing capability
- [x] official Webull SDK dependency is pinned to the proven 3.0.1 version
- [x] options/Webull regression suite passed locally (2,002 tests)
- [x] PR #822 CI is green and the adapter is reviewed/merged at `08cc827`
- [ ] exact single-leg option preview is accepted by Webull sandbox
- [ ] one controlled sandbox option order placement is proven locally
- [ ] order-detail state transition is verified
- [ ] cancel proof succeeds on an unfilled sandbox order
- [ ] paper request/response lifecycle is journaled without secrets
- [ ] retry/failure behavior is proven fail-closed
- [ ] explicit operator approval enables Phase-2 paper submission

Until every open item above passes, Webull remains **preview/read-only infrastructure**, not a paper execution path.

See `docs/options-webull-sandbox-adapter-2026-09-20.md`.
