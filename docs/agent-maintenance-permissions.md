# Cloud-agent VPS capabilities — permission policy proposal (NOT installed)

## Outcome sought

Use the existing Cursor Cloud SSH forced-command connection so approved operational tasks can be completed without the user's Mac. Extend the same approved, narrow-verb pattern only to other agents when their own authenticated connectivity is verified; no SSH-key sharing, wildcard sudoers or alternate unrestricted shell.

## Proposed division of responsibility

- **Cursor Cloud:** primary operator for preapproved narrow maintenance; separate approval before any service restart, candidate build or demo promotion. May write source PRs and execute reviewed fake-box tests. Must not self-approve an operation.
- **Claude:** source and adversarial QA, tests, runtime evidence review; narrow actual maintenance only if independently appointed to execute an already approved fixed action and its existing SSH connectivity is restored. Never both implement and independently approve the same operation.
- **Grok:** independent source/operational risk review, exact-SHA checks and research; may run authorized maintenance only if separately appointed to execute a previously approved fixed action, after connectivity is restored and GROK.md is explicitly amended by an operator-approved change. A Grok PASS is not an execution GO.

## Operation status and gates

| Operation | Decision | Can current helper do it? | Proof before enablement |
|---|---|---|---|
| Read-only audit | ALLOW on existing verb allowlist | No change to existing reads | Verify actual dispatcher identity and access |
| Options scanner 350M -> 600M | APPROVED maintenance goal, **installation still unapproved** | Prepared, not installed | Verified parent/host memory, valid root-owned specific approval, logged fixed argv, post-read |
| Options scanner 600M -> 350M | ALLOW only as verified paired rollback | Prepared, not installed | Valid root-owned prior apply receipt, post-read |
| Service restart | CONDITIONAL FUTURE ALLOW, **not currently enabled** | NO | Exact unit/action/operator GO, near-action account/order/campaign state, safe window, health, recovery; fake-box and independent review |
| Immutable demo build/verify | CONDITIONAL FUTURE ALLOW, **not currently enabled** | NO | Named SHA, source CI and review, dependency/lock assessment, isolated tests; separate build GO |
| Demo promote/rollback | CONDITIONAL FUTURE ALLOW, **not currently enabled** | NO | All release gates including fresh broker/lock state, full proof, explicit separate promote GO, approved rollback |
| Modify trading/risk code | PR AND REVIEW ONLY | NO | Versioned source, test and independent review; no hotpatch |
| Issue/cancel orders, alter broker/risk state, enable live | DENY | NO | Outside this proposal; no broker authority conveyed |
| General root/sudo, arbitrary shell/script, wildcard service/unit | DENY | NO | Not an acceptable capability |

## Auditability and authorization

Keep installed SSH forced command and its existing verbs. Allowlist action and exact arguments, lock scope to fixed targets, require independent authorization recorded in a root-owned unexpired action-specific approval, fail closed on missing/stale evidence, log time/identity/action/results, verify after each mutation, and record rollback capability. Root administration is a **one-time setup task** after separate approval. Every future sensitive action still needs a separate specific GO; a standing helper is not a standing deployment license.

## GROK.md reconciliation

Current `GROK.md` forbids Grok independently restarting services, changing VPS config, or deploying. That remains authoritative. A separate reviewed policy PR must explicitly permit **operator-approved, bounded action through an installed, logged interface** before giving Grok any mutating action. Do not silently bypass that document or conflate Cursor's account with Grok's.

## Installation boundary

**STOP before installation.** The existing options memory helper, offline fake-box tests and administrator procedure are the only executable pieces prepared in this package. No service-restart or release-promotion script, sudoers, authorization or CI claim has been provided for those future operations.
