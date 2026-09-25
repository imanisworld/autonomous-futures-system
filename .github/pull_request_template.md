# Pull Request

Unsolicited pull requests are not currently accepted.

Use a public issue for reproducible bugs or documentation feedback. Report
vulnerabilities privately through the repository Security page. Never include
credentials, account information, private logs, or proprietary strategies.

## Agent handoff

**Work owner:**  
**Implementer:**  
**Independent reviewer:**  

**Status:** IMPLEMENTED / QA / BLOCKED / READY FOR OPERATOR

**Scope:**  
**Acceptance criteria:**  

**VERIFIED:**  
- 

**UNVERIFIED:**  
- 

**CONTRADICTED / STALE:**  
- 

**Blockers:**  
- 

**Next agent:**  
**Required next action:**  

Use one primary implementer. For consequential changes, the implementer must not be the only independent verifier.

## Safety checklist

Repository-owner and automated dependency pull requests must confirm:

- [ ] Scope is bounded and unrelated files were not changed.
- [ ] The change does not enable or expand live execution.
- [ ] Broker / account / execution routing changes, if any, are explicitly identified and fail closed.
- [ ] Risk controls were not weakened to make the change pass.
- [ ] No credentials, secrets, private operational details, or secret-derived values are included.
- [ ] Relevant regression tests pass.
- [ ] Full required CI is green on this exact head before merge approval, or the CI blocker is explicitly reported as unresolved.
- [ ] Public documentation remains accurate.
- [ ] Runtime claims are backed by current runtime evidence, not documentation or prior chat.
- [ ] No merge, deploy, VPS mutation, broker mutation, or production env change was performed by an agent without explicit Operator authorization.

## Evidence

**Exact test commands/results:**  
- 

**Files reviewed / changed:**  
- 

**Known limitations / remaining unknowns:**  
- 
