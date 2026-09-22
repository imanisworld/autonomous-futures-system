# Release-retention safety — incident and fix — 2026-09-21

## Verdict

**RESOLVED / DEPLOYMENT HOLD LIFTED FOR THIS DEFECT / FAIL-CLOSED PRUNING REQUIRED.**

This note records an infrastructure retention defect that removed an immutable release while a standalone systemd evidence collector still referenced it. It does not authorize trading, change a strategy, or alter any risk/broker/order path.

## Incident

The 1-2-2 prospective collector remained pinned to immutable release `36e73f1981850b66b043d849ce877c15bd1ab3e7`, but the release directory disappeared after later releases were created.

The collector then failed at process launch with `203/EXEC`. Those failed launches occurred before collector evidence writes. The affected collection window is therefore missing prospective evidence, not negative strategy evidence, and it must not be backfilled or relabeled as prospective.

The old pruning rule retained a small newest-release window plus the primary and rollback release. It did not prove that an otherwise older release was unreferenced by an independent systemd service/drop-in or running process before deletion.

## Recovery

The exact collector release was reconstructed from its source commit and frozen dependency set. Release identity and dependency parity were verified before the original service pin was restored.

A closed/off-RTH validation completed without manufacturing a prospective row. The collector's frozen policy and evidence gate were not changed.

## Required retention invariants

Release deletion is allowed only after complete reference discovery.

The corrected external deploy-pruning path now follows these rules:

- protect the primary/current release and rollback target;
- inspect all systemd services and timers, not only units with project-specific names;
- preserve release references found in effective unit properties, unit fragments, and drop-ins;
- preserve releases referenced by running-process cwd/exe paths;
- handle legitimate systemd shapes conservatively, including not-found ghosts, bare templates, masked units, concrete template instances, and source-only static services;
- fail closed when a release-root reference is unresolved or discovery is incomplete;
- validate candidate directory identity/metadata before deletion;
- perform a second complete discovery immediately before deletion;
- if references, inventories, processes, or candidate metadata change between scans, delete nothing;
- dry-run uses the same discovery/candidate logic but performs no deletion.

Real-host validation eventually completed with a clean dry-run after host-specific systemd shapes were added to regression coverage. The final private pruning test suite passed 61 tests / 26 subtests, and shell syntax validation passed.

## Operational behavior

Process or unit churn may cause a pruning attempt to skip. That is acceptable and intentional: retaining an extra release is safer than deleting one under uncertain reference state.

Any operation that creates or changes a systemd reference to an immutable release should use the same deployment mutex as release deployment. Two discovery passes reduce the race window but do not make uncoordinated reference mutation safe.

A separate options-scanner rollback-reference design may be evaluated later. Do not weaken retention rules merely to preserve unreferenced historical scanner releases.

## Evidence impact

No missed collector interval created valid prospective evidence. No backfill is authorized.

The next evidence gates remain unchanged:

- 1-2-2: first natural scheduled RTH causal acceptance row under the frozen `122-IEX-E1` policy;
- Signa request-budget fix: natural market-hours proof without manual provider hammering.

**No proof, no run.**
