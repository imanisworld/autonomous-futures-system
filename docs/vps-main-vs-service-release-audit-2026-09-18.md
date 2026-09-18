# VPS main-vs-service release audit — 2026-09-18

Status: **RECONCILED / SERVICE-SCOPED RELEASES / NO BLIND MAIN DEPLOY**

## Question

The corrected drift gate reported 19 informational differences between repository `main` and the primary curated futures release. This audit determines which of those files actually belong on the VPS and, more importantly, **which running service** should own them.

Primary futures release at audit time:
`c538e2bc429d52c7d960c1d937d5e27ff4361896`

Repository main at first census:
`9703201671c7fed92285ecda63e82e04478a9ad1`

The VPS runs two independent services:
- `futures-bot` — `webhook.app` on port 8000;
- `options-scanner` — `alert_ranker.app` on port 8010, advisory-only, Public read-only provider, `order_supported=false`, account endpoints forbidden.

There is **no `options_manager` systemd service** and no listener for that package.

## Classification summary

Of the 19 original main-vs-futures-release differences:
- **2 SHOULD SHIP to the active options scanner** and have now been deployed there;
- **1 presentation-only futures notification file is safe to defer** until a future sanctioned futures release;
- **16 are intentionally excluded from VPS runtime** because they are audit/offline/replay/research/inactive-subsystem files.

No file should be copied into the futures release merely because it exists on `main`.

## Exact 19-file ruling

| File | Classification | Reason |
|---|---|---|
| `alert_ranker/market_data.py` | **SHOULD SHIP — options scanner** | Active `alert_ranker` runtime. #667 preserves Public option-chain bid/ask timestamps and frozen source identity; read-only metadata/provenance, no order capability. |
| `alert_ranker/paper_v1.py` | **SHOULD SHIP — options scanner** | Active scanner imports it. #667 carries quote source into paper contract fields; metadata only, no broker submission. |
| `notifications/observation_notifier.py` | **DEFER — presentation only** | Imported by futures observation transport, but #650 changes message formatting/readability only. No safety/evidence semantic need for a standalone restart. |
| `ops/project_check/demo_qualification.py` | **INTENTIONALLY EXCLUDED** | #644 is read-only Backtest→DEMO qualification tooling. Audit/offline acceptance gate; no runtime dependency. |
| `options_manager/app.py` | **INTENTIONALLY EXCLUDED** | Canonical options-manager advisory API foundation; no `options_manager` service exists on the VPS. |
| `options_manager/contracts/__init__.py` | **INTENTIONALLY EXCLUDED** | Package export for inactive options-manager selector subsystem. |
| `options_manager/contracts/selector.py` | **INTENTIONALLY EXCLUDED** | #656 pure deterministic selector foundation; PR explicitly added no runtime activation/deploy. Running scanner still uses `alert_ranker.paper_v1`. |
| `options_manager/contracts/selector_rule_v1.json` | **INTENTIONALLY EXCLUDED** | Frozen rule consumed by inactive selector foundation, not active scanner runtime. |
| `options_manager/quotes/__init__.py` | **INTENTIONALLY EXCLUDED** | Inactive options-manager quote-retention package. |
| `options_manager/quotes/quote_retention_rule_v1.json` | **INTENTIONALLY EXCLUDED** | Frozen rule for inactive canonical options-manager intake/evidence path. |
| `options_manager/quotes/replay.py` | **INTENTIONALLY EXCLUDED** | Offline quote replay/evidence tooling. |
| `options_manager/quotes/retention.py` | **INTENTIONALLY EXCLUDED** | #661/#667 quote-retention foundation; no active options-manager service. Running scanner provenance is handled by the two shipped `alert_ranker` files. |
| `options_manager/validation/advisory_decision.py` | **INTENTIONALLY EXCLUDED** | Canonical options-manager advisory coordinator; not imported by `alert_ranker.app` service. |
| `options_manager/validation/demo_qualification.py` | **INTENTIONALLY EXCLUDED** | #651 options Backtest→DEMO qualification gate; audit-only, no runtime. |
| `options_manager/validation/quote_retention_gate.py` | **INTENTIONALLY EXCLUDED** | #664 canonical options-manager quote-retention gate; no options-manager service is deployed. |
| `replay/candle_loader.py` | **INTENTIONALLY EXCLUDED** | #652 backtest/replay fidelity tooling; active VPS services do not need it for live collection. |
| `replay/replay_engine.py` | **INTENTIONALLY EXCLUDED** | Offline canonical replay engine; no active service imports it for runtime decisions. |
| `research/structural_level_features.py` | **INTENTIONALLY EXCLUDED** | P1/P3 structural-level research; PR #617 explicitly research-only/no runtime. |
| `research/structural_level_p2.py` | **INTENTIONALLY EXCLUDED** | P2 corpus/parity research; PR #620 explicitly research-only/no runtime. |

## Options-scanner deployment

The two justified scanner files were packaged as a service-specific immutable release based on the proven futures release `c538e2bc`.

Candidate/release:
`3b9770d8fed4ad1825cc325bab536ffea618a94e`

Changed runtime files only:
- `alert_ranker/market_data.py`
- `alert_ranker/paper_v1.py`

Changed test files:
- `tests/test_alert_ranker_public.py`
- `tests/test_options_paper_v1.py`

Proof:
- focused full alert-ranker/options-scanner regression cluster: **144 passed**;
- immutable candidate build: release integrity **PASS — 1,070 files**;
- standard isolated futures candidate verifier also passed with paper-isolated broker, proving the release remains futures-compatible;
- isolated options-scanner transient unit used port 39010 and a temporary SQLite DB;
- isolated health = healthy, advisory-only = true, Public provider read-only, `order_supported=false`, account endpoints forbidden;
- no candidate-unit errors/order/broker activity;
- transient unit removed after proof.

A root `pytest -q tests` run was not counted as proof because it stalled around 71% in an unrelated Git upstream-introspection subprocess (`git rev-parse --abbrev-ref @{upstream}`); the run was terminated rather than misreported as pass/fail. The changed scanner surface was covered by the 144 focused regressions.

## Production options-scanner activation

`options-scanner.service` is now pinned independently to:
`/root/afs-releases/3b9770d8fed4ad1825cc325bab536ffea618a94e`

The service-specific drop-in sets:
- `WorkingDirectory` to the exact release directory;
- `PYTHONPATH` to the exact release directory;
- `ExecStart` to that release's venv/uvicorn.

This avoids changing `/root/autonomous-futures-system` or restarting `futures-bot`.

Post-restart proof:
- options scanner PID changed as expected;
- cwd = exact `3b9770d…` release;
- health = healthy;
- advisory-only = true;
- provider = Public, read-only;
- `order_supported=false`;
- account endpoints forbidden;
- production SQLite path unchanged;
- aggregate open planned paper risk preserved at **$140**;
- latest/scans counts preserved at **10 / 10**;
- no scanner traceback/error/order/broker lines after restart.

Running-file proof:
- `PUBLIC_OPTION_CHAIN_SOURCE = public:/userapigateway/marketdata/{accountId}/option-chain` present;
- `quote_source` is carried into paper contract fields.

Futures bot was not restarted:
- futures PID unchanged;
- futures cwd remains `c538e2bc…`;
- futures health remains up with live trading disabled.

## Remaining prospective proof

The first natural market-hours Public option-chain response after deployment still needs to demonstrate whether both `bidTimestamp` and `askTimestamp` are present on usable contracts. If one side is missing, executable quote timestamp remains intentionally missing; #667 explicitly forbids synthesizing it from request/receipt time.

This is evidence collection, not a deployment blocker.

## Final ruling

**The box should not mirror `main`.**

- Futures runtime stays on its curated release.
- Options scanner now owns its separate curated `3b9770d…` release with the two justified provenance files.
- The other 17 original differences are not current release gaps; one is deferred presentation-only and 16 are intentionally offline/inactive.
