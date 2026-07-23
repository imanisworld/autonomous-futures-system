# Branch Archive Index

Record of local/remote branches removed during repo-hygiene cleanups, and the
durable annotated tag each one's commit was preserved under before deletion.

**Archived code is NOT approved, merged, validated, or deployed.** A tag here
means the commit is recoverable — nothing more. Reviving any of it requires
the same PR + review process as new work.

---

## 2026-07-25 cleanup (post-PR #310)

Disposition audit method: unique commits vs `main`, unique files vs `main`,
byte-diff of overlapping files against `main`'s current version, cross-checked
against `gh pr list --state all`, remote branches, and prior operator rulings
in session memory.

| Original branch | Archived SHA | Archive tag | Purpose | Disposition |
|---|---|---|---|---|
| `codex/box-cancelled-option-c-proof` | `701ceea5657f41e4a89ac72f72c7065001b90832` | `archive/codex-box-cancelled-option-c-proof-2026-07-25` | Standalone ops command to prove/verify box-cancelled option C handling | Unique, not active — no PR opened, no worktree, shared proof-packet base already superseded on `main` |
| `codex/direction-authority-audit` | `1757284b3ef4bad194a27b9a5ae0c3cd741e4a8f` | `archive/codex-direction-authority-audit-2026-07-25` | Standalone ops audit command for direction-authority checks | Unique, not active — no PR opened, no worktree, shared proof-packet base already superseded on `main` |
| `codex/options-fixture-candidate-surface` | `9b69d89cdfc1b324c038df4a67c3c5172d5b9466` | `archive/codex-options-fixture-candidate-surface-2026-07-25` | Options fixture-candidate HTTP status surface + transition-reclaim replay proof | Unique, not active — no PR opened, no worktree. Fully contains (byte-identical) `codex/transition-reclaim-shadow-lane`'s content, so that branch's deletion is covered by this tag too |
| `futures-options-lab-and-alert-age-telemetry-wip` | `d798f8b3411c4f29215380aa4a08ba27d63ce53e` | `archive/futures-options-lab-alert-age-telemetry-2026-07-25` | `/status/options-lab` live-status endpoint + alert-age telemetry + trade-attempt accounting on `webhook/app.py` | Unique, not active — oldest branch (2026-07-07), predates the `claude/*`/`codex/*` lane-naming convention, no PR. Confirmed via grep: this capability never landed on `main` despite heavy subsequent churn in that file |
| `codex/gex-shadow-enrichment` | `ce3794661ae89f9ed03f7f68d25b2b5d752b777b` | `archive/codex-gex-shadow-enrichment-2026-07-25` | GEX shadow enrichment: evidence readiness, fill realism, live-box-guard, webhook/replay/risk changes across 12 commits | **Fully superseded — safe to delete.** A deeper read-only audit (2026-07-25) compared each of the 6 most-divergent files (`webhook/app.py`, `replay/replay_engine.py`, `risk_rules.yaml`, `RUNBOOK.md`, `ops/live_box_guard.py`, `strategy/shadow_setups.py`) against `main`'s *current* content (not the branch's stale fork point). In every case `main` is a strict superset: the branch's `webhook/app.py` additions (`/status/fill-realism`, `/status/evidence-readiness`, etc.) are already present on `main` byte-for-byte; the other 5 files are missing substantial functionality `main` has since gained (runner-mode exits, IOC entry-fill modeling, per-instrument stop multipliers, live-direction-source history, the full `strategy_permission_gate` table, expanded `PROOF_CRITICAL_RUNTIME_OVERRIDES`, the Evidence-Chain Reconciliation runbook section, and more). The apparent large divergence was old-base drift, not unique unlanded capability. No unique code remains that isn't already on `main` in a more evolved form. The exact historical state remains protected by this pushed, remote-verified archive tag. The local branch is deleted; the archived commit is **NOT approved, merged, validated, or deployed** — recovering it requires the same PR + review process as new work. |

Branches deleted after the above tags were pushed and remote-verified:
- `codex/revert-session-hold-context-collector` — **no tag** (its only unique
  file, `context/strategy_context_observer.py`, was confirmed byte-identical
  to `main`'s current version; nothing to preserve)
- `codex/transition-reclaim-shadow-lane` — **no separate tag** (fully
  subsumed by `archive/codex-options-fixture-candidate-surface-2026-07-25`,
  confirmed via byte-diff of every shared file plus a file-list subset check)
- `codex/box-cancelled-option-c-proof`
- `codex/direction-authority-audit`
- `codex/options-fixture-candidate-surface`
- `futures-options-lab-and-alert-age-telemetry-wip`

Also deleted this cleanup (unrelated to the above): `claude/feed-gap-alarm`
(local-only, superseded in full by the merged `claude/feed-gap-alarm-v2` /
PR #297 — same file, later fully rewritten).
