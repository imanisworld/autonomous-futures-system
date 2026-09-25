# Public Readiness Audit — 2026-09-25

## Verdict

**HOLD — DO NOT CONVERT THIS EXISTING REPOSITORY TO PUBLIC YET**

The current repository is not ready for an in-place visibility change.

The safer path is to prepare a **clean public repository / fresh public root** from an explicitly curated file set, then move active development there only after the public tree and workflow are verified. Keep this existing repository private as the historical/operational archive unless a later full-history scrub proves conversion-in-place is safe.

## Why

1. **History surface is large.** Current GitHub state exposes at least 53 branches on the first 100-result page, including 32 `release/*` branches, plus 254 archive tags returned by GitHub.
2. **Current main contains operational material that should not automatically become public.** Examples include VPS/deploy documentation, runtime handoffs, release identities, account-pin procedures, server paths, endpoint layouts, and broker/demo operating notes.
3. **README/security posture assumes privacy.** `README.md` explicitly says the repository is private, while `SECURITY.md` says proprietary strategy notes, production configuration, operational notes, account identifiers, deployment SSH details, journals, scanner databases, reports, and replay output should not be public.
4. **A simple current-tree deletion is insufficient.** Branches, tags, historical commits, PR discussions/diffs, Actions metadata/artifacts, and old refs can remain accessible after a visibility change.
5. **The repository has extensive historical CI and PR surface.** GitHub reports 4,558 workflow runs at audit time. This does not prove sensitive artifacts exist, but it materially expands the publication surface that would need verification.

## What I Verified

### Repository state

- Repository: `imanisworld/autonomous-futures-system`
- Visibility at audit: **private**
- Audited main SHA: `62818f82210b1fc5a2680492e85c5f2ad1acab76`
- Releases API returned: **0 releases**
- Branches returned on first page: **53**
  - `release/*`: 32
  - `cursor/*`: 9
  - `archive/*`: 3
  - `claude/*`: 2
  - `docs/*`: 2
  - `research/*`: 2
  - `hold/*`: 1
  - `release-scope/*`: 1
  - `main`: 1
- Archive tags returned: **254**
- Workflow runs reported by GitHub: **4,558**

### Current-tree secret pattern check

Default-branch code search found **no obvious current-main matches** for common literal secret signatures such as:

- `BEGIN OPENSSH PRIVATE KEY`
- `AKIA`
- `ghp_`
- `github_pat_`
- `ssh-rsa`

This is **not a historical secret scan** and does not prove the repository is safe to publish.

Searches for credential variable names found expected placeholders/test fixtures such as Tradovate credential environment-variable names. Those are configuration interfaces, not proof of exposed real credentials.

### Current files requiring classification

The following are examples of public-readiness candidates, not an exhaustive final list.

#### SANITIZE BEFORE PUBLIC

- `.env.example`
  - blank credential placeholders are appropriate;
  - current file also documents operational server paths, runtime evidence paths, status endpoints, broker/demo operating details, and deployment-oriented pins.
- `.env.local.example`
  - contains only a local placeholder secret in the reviewed version, but still documents private/operator surface behavior.
- `README.md`
  - currently states the repo is private;
  - names Hetzner VPS usage, systemd service `futures-bot`, port `:8000`, operational status endpoints, demo broker behavior, and dashboard controls.
- `SECURITY.md`
  - must be rewritten for a public-repository threat model and public vulnerability-reporting posture.
- `docs/env-and-deploy.md`
- generic deployment/runbook docs that reveal real operating topology, paths, service names, or operator procedures.

#### REMOVE FROM PUBLIC REPOSITORY / KEEP PRIVATE

Operational evidence and handoff records such as:

- `docs/futures-current-state-handoff.md`
- `docs/futures-post-close-deployment-readiness-2026-09-24.md`
- `docs/vps-main-vs-service-release-audit-2026-09-18.md`
- `docs/vps-minimal-release-prep-2026-09-18.md`
- runtime-specific deployment/release audit notes;
- real box-state provenance;
- account-pin proof records;
- private operator handoffs;
- real journals/logs/scanner databases/reports if present;
- any file whose primary purpose is proving what happened on the private runtime rather than documenting reusable source behavior.

#### REVIEW FOR PROPRIETARY / PUBLIC INTENT

These may be safe from a credential standpoint but can expose trading IP or research evidence:

- `data/**`
- `research/**`
- raw trade corpora and JSONL/CSV evidence files;
- strategy research reports;
- trial ledgers;
- canonical evidence datasets;
- private strategy doctrine;
- historical performance evidence.

Publication here is a business/IP decision, not merely a secret-removal decision.

#### GENERALLY APPROPRIATE TO KEEP PUBLIC AFTER REVIEW

- reusable source code;
- unit tests with synthetic fixtures;
- generic deterministic risk-control code;
- schemas;
- sanitized configuration examples;
- public-safe documentation;
- `AGENTS.md` safety contract;
- GitHub issue/PR templates;
- generic replay examples that contain no private or proprietary evidence.

## Historical Surface Findings

### Branches

The repository has many release and historical working branches. An in-place public conversion would expose those refs unless they are removed first.

Deleting files from `main` does not sanitize those branches.

### Tags

GitHub returned **254 archive tags**. Their names preserve historical working states and some operational provenance.

One archived ref is named:

`archive/box-live-snapshot-20260908-233554-snapshot-2026-09-09`

The reviewed target commit itself did not prove a credential leak, but the existence of hundreds of archived refs means every retained tag becomes part of the public attack/review surface.

### Pull requests and issues

A visibility change would also make historical PRs/issues and their discussions publicly visible unless GitHub behavior or specific records are handled separately.

This audit has **not** yet exhaustively reviewed all historical PR bodies, comments, inline reviews, attachments, or closed issue content for private operational information.

### GitHub Actions

GitHub reports 4,558 workflow runs.

This audit has **not** yet exhaustively inspected historical logs or downloadable artifacts for sensitive runtime data.

## Current Secret-Safety Assessment

### VERIFIED

- No obvious current-main literal private-key/GitHub-token/AWS-key signatures were found by the limited default-branch code searches performed.
- Current reviewed env examples use blank or obvious placeholder credential values.
- Operational and runtime-specific material is present in current `main`.
- The repo has a large branch/tag/history surface.

### UNVERIFIED

- Whether any historical commit ever contained a real credential.
- Whether any archived branch/tag contains a real credential or personal/runtime identifier.
- Whether historical PR/issue comments include sensitive runtime data.
- Whether historical Actions logs/artifacts include secrets, account identifiers, private URLs, logs, or box information.
- Whether all research/data files are intended for public release.

## Recommended Architecture

### Preferred: clean public operational repo

1. Keep the current repository private as the historical/operational archive.
2. Create a clean public repository or fresh-root public history from an explicit allowlist of files.
3. Copy only reviewed public-safe source, tests, schemas, safety contracts, and sanitized docs.
4. Do not copy old branches, archive tags, private PR history, runtime handoffs, or historical Actions artifacts.
5. Configure branch protection/rulesets and agent handoff enforcement in the new public repo.
6. After validation, decide whether active development/deployment should migrate to the new repo.
7. Keep runtime secrets and private operational evidence outside the public repository.

This avoids destructive history rewriting and sharply reduces the amount of historical material that must be proven clean.

### Alternative: convert this repo in place

Only consider this after all of the following are complete:

- full Git-history secret scan;
- branch-by-branch and tag-by-tag disposition;
- PR/issue/comment/attachment audit;
- Actions logs/artifact audit;
- removal or rewrite of sensitive commits;
- credential rotation for any credential ever exposed;
- deletion of unsafe branches/tags/refs;
- public-safe rewrite of current docs;
- independent verification of the rewritten repository.

This path is materially higher risk.

## Required Fixes Before Any Public Visibility Change

### Blockers

- Decide **clean public repo** vs **in-place history rewrite**.
- Classify the complete current tree into:
  - KEEP PUBLIC
  - SANITIZE
  - KEEP PRIVATE / REMOVE FROM PUBLIC
  - HISTORY PURGE REQUIRED
- Identify whether any real credentials/account identifiers/private URLs ever entered Git history.
- Audit PR/issue historical content.
- Audit Actions logs/artifacts.
- Separate proprietary strategy/research material from public educational source.

### Should-fix later

- public-safe README;
- public SECURITY policy;
- public CONTRIBUTING policy;
- branch/ruleset protections;
- automated secret scanning;
- automated public-file allow/deny checks;
- agent-handoff validation workflow.

## Do Not Touch Yet

Until the public target architecture is chosen and independently reviewed:

- do not change repository visibility;
- do not force-push or rewrite `main`;
- do not delete archive tags/branches merely to make the count smaller;
- do not rotate credentials unless an actual exposure is found;
- do not alter live/demo broker, VPS, deployment, or runtime configuration;
- do not merge this audit as proof that publication is safe.

## Safe Next Step

**Build a machine-readable/public-reviewable file allowlist from current `main` and create a clean sanitized public-candidate tree on a separate branch or separate repository.**

The public candidate should contain no inherited history until the allowlist is accepted.

Only after that candidate is reviewed should the Operator decide whether to create/migrate to a public repository.
