# Fault-injection promotion gate (U10)

Status: **evidence gate**. Runs only the in-memory `tests/fault_injection/`
suite. No production fault injection, no broker or box contact.

## Requirement

Promotion (`ops/project_check/promotion.py`, and through it the demo
qualification gate) requires `"fault_injection": {"manifest": "<path>"}` whose
manifest:

- was produced by `python -m ops.fault_injection_gate generate --out <path>`
  on a clean checkout with **no tracked or untracked files**;
- proves the **exact** code SHA the canonical evidence bundles were produced
  from (`canonical_evidence` → `code_sha`);
- carries the SHA-256 fingerprint of the FI suite files as committed at that
  SHA;
- reports every required scenario `PASS` (at least one passing test, no
  failure, no skip/xfail) with a clean pytest exit;
- is mechanically re-run by the verifier from a temporary `git archive` of
  the exact qualified SHA, so manifest PASS claims are not trusted by themselves;
- matches the scenario inventory mechanically rediscovered from the committed
  FI suite at the exact qualified SHA. New/removed scenarios block until the
  required inventory is deliberately reconciled.

Missing, stale (other SHA), different-suite/inventory, failed, skipped/xfailed,
outside-repository or non-generator manifests block promotion. A general green `pytest` run is not
FI proof.

Verify a manifest by hand:
`python -m ops.fault_injection_gate verify --manifest <path> --code-sha <sha>`.
Verification re-executes the committed FI suite at that SHA in a temporary
archive. Generation refuses output outside the repository or over a tracked
repository file.

## Defined scenarios (required)

FI-1, FI-2, FI-3, FI-4, FI-5, FI-6, FI-7, FI-8, FI-9, FI-10, FI-11, FI-13,
FI-15, FI-16, FI-17, FI-18, JW (journal write failure), LW (lane ledger write
failure). The list is derived from the suite's test names; a CI test fails if
the suite and the list drift apart.

## Referenced but not formally defined

| Name | Status |
|---|---|
| FI-12 | numbering gap — no test, doc or audit entry defines it |
| FI-14 | numbering gap — no test, doc or audit entry defines it |
| Auth / token loss | no FI scenario defined |
| Contract-roll mismatch | no FI scenario defined (U8 unit tests cover routing only) |

Defining these is an operator/system-lead decision; this gate does not invent
them.
