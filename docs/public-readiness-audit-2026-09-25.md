# Public Exposure Audit Closeout — 2026-09-25

## Verdict

**HOLD / AUDIT ONLY — repository is already public.**

**Secret-discovery phase is CLOSED.** Combined Local + Cloud + Gitleaks evidence shows **0 confirmed secrets** and **0 likely secrets**. Credential rotation is **not** justified.

HOLD now means **known operational exposure + governance/containment**, not suspected secret leakage.

This audit is non-destructive. It does not change visibility, delete refs, rewrite history, rotate credentials, deploy, merge, or mutate runtime/broker state. Do **not** treat this document or PR #1037 as proof the repository is safe.

## Secret-discovery evidence (combined)

| Lane | Scope | Result |
|---|---|---|
| Local targeted history/ref audit | Full non-shallow clone; all origin heads + tags; pickaxe/path searches | **0 confirmed secrets** |
| Cloud GitHub surface audit | PR/issue/discussion text; inline reviews; available Actions artifacts; sample logs | **0 confirmed secrets** |
| Gitleaks full-history | v8.30.1; `--log-opts=--all`; 2133 commits; ~65 MB | **0 confirmed / 0 likely**; 35 hits all **false positives** |

TruffleHog was **not** run and is **not** required unless a deliberate second scanner is requested.

GitHub secret scanning: enabled; push protection enabled; open alerts empty at audit time.

### Gitleaks false positives (35)

- `release_manifest.json` path→SHA256 digests (`generic-api-key` / `discord-api-token` on Discord-named paths)
- `research/trial_ledger.py` Python `key="…"` trial IDs
- `tests/test_tradovate_bracket_verify.py` fixture `cid`/`secret` string

### Cloud surface coverage (complete for discussion text)

- Repository: public; default branch `main`; `protected: false`; rulesets empty
- Branches: **55** (32 `release/*`); tags: **254**; releases: **0**
- Pull requests scanned: **1033/1033** (bodies, comments, reviews, review threads)
- Inline review comments: **4/4**
- Issues scanned: **4/4** (`#627`, `#709`, `#711`, `#716`)
- Discussions: **1** (default welcome only)
- Wiki: enabled but no wiki repository/pages
- Actions: **4570** run metadata; **6/6** artifacts inspected (CodeQL SARIF only); sample successful CI logs clean
- Environments (public API): `hetzner-futures`
- Deployments (public API): 35 Railway entries under `beautiful-courtesy / production`
- Orphaned active workflow records: 9 ChatGPT apply/reconcile workflows listed in Actions API but absent from `main` tree

### Known operational exposure (not a credential)

- Public-looking VPS/box IPv4 literals scrubbed in `#392`/`#393` from tip trees, but still reachable via `origin/main` ancestry and **73** archive tag tips (`docs/env-and-deploy.md`, `ops/proof_30_mnq.py`, `scripts/test_webhook.sh`, `tests/test_atomic_release_script.py`). Do not publish the address.
- Server path layout disclosed in many PR bodies (`/root/afs-shared/...`, including `.env` / `.triage_key` **pathnames**, not values).
- Provider/host naming: Hetzner; systemd unit names (`afs-watcher`, `futures-bot`, etc.).
- PR `#393` webhook `?secret=` examples use `${SECRET}` env-subst, not a credential literal.

No evidence currently justifies credential rotation.

## What this audit branch contains

Bounded audit/candidate files only:

- `docs/public-readiness-audit-2026-09-25.md` (this closeout)
- `public-export-manifest.json` — **802 KEEP / 48 REVIEW / 789 PRIVATE**
- `public-candidate-overrides/` — sanitized `.env.example`, `.env.local.example`, `README.md`, `SECURITY.md`

Because the source repo is already public, the manifest is a **future sanitized-export / exposure-reduction specification**. It does not retract already-public history.

Audited base main SHA at branch creation: `62818f82210b1fc5a2680492e85c5f2ad1acab76`.

## REVIEW disposition

48-file REVIEW bucket remains **exclude-until-reviewed**.

Sanitized by override: `.env.example`, `.env.local.example`, `README.md`, `SECURITY.md`.

Exclude until rewrite: `RUNBOOK.md`, `CHANGELOG.md`.

Still need file-level review before clean-export inclusion: `CONTRIBUTING.md`, `risk_rules.yaml`, `.github/**`, `config/**`, `interactive-course/**`, `share/**`, `site/**`, `tests/fixtures/**`.

## Stale premises (corrected)

- Repo is **not** private; this is not a pre-publication gate.
- Problem is containment/governance/export, not “whether to make public.”
- Manifest “do not change source repository visibility” is obsolete as a visibility decision (already public); this audit still will not change visibility.
- README/SECURITY private-repo wording tracked in `#1038`.
- Prior audit wording that PR/issue/Actions surfaces were unscanned is obsolete for discussion text and available artifacts (Actions log exhaustiveness remains sampled only and is no longer a secret-discovery blocker).

## Remaining HOLD reasons (non-secret)

1. Historical operational material (including scrubbed box IP) remains in public Git history/tags.
2. `main` unprotected / rulesets incomplete (`#1036` + admin ruleset work).
3. Large public surface of archive/release/agent refs and operational docs.
4. 48 REVIEW paths not individually approved for any clean export.
5. Open decision: retain existing public history vs sanitized fresh root.
6. Orphaned active ChatGPT workflow records should be reviewed/disabled.
7. Ops check outstanding: whether the historically exposed VPS IP is still active (do not publish the address).

## Next actions (governance/containment — stop secret scanning)

1. Keep PR `#1037` draft/audit-only; do not merge as a safety certificate.
2. Protect `main` / add rulesets; finish `#1036` governance path.
3. Finish `#1038` public-doc wording.
4. `#1039` agent-instructions — **already merged**.
5. Review orphaned active workflows.
6. Ops check: is the historically exposed VPS IP still active? (outside this PR; do not publish the address).
7. Decide: accept public history vs sanitized fresh root. No ref deletion / history rewrite in this audit.
8. **Do not** spend more time scanning for secrets now. **Do not** run TruffleHog unless deliberately requested.

## Do not touch

- repository visibility
- branch/tag deletion
- Git history rewriting / force push
- credential rotation absent confirmed exposure
- VPS/runtime/broker state
- deployment
- merge of this audit as proof of safety
