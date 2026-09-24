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


---

## 2026-09-24 cleanup (post-PR #1018)

Main at audit: `e32d33b1ea814c7cfe3627d0bbd46f28f099971b`; re-checked against
`34177183d2a2abbb2442b4bd2dee3f6579875140` before deletion.

Disposition audit method (the repo squash-merges, so commit ancestry is never
used as proof of landing): every file each branch adds or modifies was compared
by blob to `main` at the same path and then to `main`'s whole tree by blob hash
(`absent` = branch blobs found nowhere on `main`); containment was checked
against all `archive/*`, `release/*` and `release-scope/*` refs; supersession
was taken from PR closure comments, blob comparison with the superseding
commits, and the private handoff log. Each tag below was pushed and
remote-verified (`ls-remote` peel == archived SHA) before its branch was
deleted, and each deletion was a lease on that exact SHA. Tag messages carry the
same Purpose and Disposition text.

The same cleanup also deleted 36 branches without tags: 35 whose tip equalled
their merged PR's head SHA, and `research/mnq-combined-portfolio-audit-20260922`
(`5a9f14b`), already preserved byte-for-byte by
`archive/pr915-mnq-combined-portfolio-audit-5a9f14b`.

| Original branch | Archived SHA | Archive tag | Purpose | Disposition |
|---|---|---|---|---|
| `audit/counterfactual-producer` | `22a96b34fdfd4470014d708ba84d4588f9c74cdf` | `archive/audit-counterfactual-producer-2026-09-24` | MNQ missed-opportunity counterfactual producer + reproduction scripts/tests (#593) | HISTORICAL: Closed unmerged; #593 closure asks to preserve as provenance. Tip is also contained in `research/mes-asian-d-ema-baseline`. No landed equivalent on main. |
| `audit/options-nonstrat-paper-track` | `18a0fb7d06eb3df3aaad00266d729c037335224c` | `archive/audit-options-nonstrat-paper-track-2026-09-24` | Options non-Strat fail-closed internal paper track + journal (#875, with #880 review fixes merged into this branch) | HISTORICAL: #875 closed 2026-09-23 as stale implementation; later geometry prereg on main replaced it. Code never landed. Contains `rebuild/options-nonstrat-paper-track-clean`. |
| `audit/options-shared-infra-acceptance-spec` | `1757e6176102f8df6e0679addf12ce5f5fc1f9a0` | `archive/audit-options-shared-infra-acceptance-spec-2026-09-24` | Docs: options shared-infrastructure acceptance spec (#654) | SUPERSEDED: Superseded by merged #655 (`ef508b3`), the stronger 60-blocker acceptance spec. |
| `audit/transition-repair` | `63b3ad8aa8382c785bebde1d05ba86922e4532f4` | `archive/audit-transition-repair-2026-09-24` | Transition repair IOC audit script, results JSON and probe tests (#528) | SUPERSEDED: Superseded by #659 (`research/transition-400t-canonicalization` `53cf721`), final verdict recorded on main by #666 (`cfc4d01`). Results JSON exists only here. |
| `brand/afsvp-palette-manifest` | `0a8faf83a0bdd816443f27b09520ef93ce00c147` | `archive/brand-afsvp-palette-manifest-2026-09-24` | AFSVP palette + manifest identity attempt (#837) | SUPERSEDED: Closed as contaminated diff; clean redo merged as #839 (`32a5240`). |
| `chatgpt/contract-identity-guard-implementation-20260924` | `9b2e2c84560f50799a38c4dcfec382eea89119e0` | `archive/chatgpt-contract-identity-guard-implementation-20260924-2026-09-24` | Draft contract-identity guard primitives + BracketOrder.contract_hint (#967) | SUPERSEDED: Superseded by merged #969 (`45d1a95`) observe-only guard and #976 (`ea928c6`); review items carried to future enforcement PR. |
| `chatgpt/paper-collection-rollups` | `f75b9ffcd14574871b9a4aea9236fdd42639a152` | `archive/chatgpt-paper-collection-rollups-2026-09-24` | EOD/EOW paper collection rollup script + systemd timers (#602) | SUPERSEDED: Superseded by merged #603 (`d93c580`) paper-collection Discord rollups. |
| `chatgpt/parallel-safety-backlog` | `b4933b84e91cc1cdea8f9fdc81ea8fc75aa556e0` | `archive/chatgpt-parallel-safety-backlog-2026-09-24` | Docs: parallel-safe futures backlog, C14 proof prereg, post-freeze release gate (#628) | SUPERSEDED: Superseded by merged #626 (`03b1a5c`); C14 later closed by #646/#647. |
| `claude/discord-plain-english-a` | `8b2a8bbb9c7cbda7196947f626958dbf521774e9` | `archive/claude-discord-plain-english-a-2026-09-24` | Discord plain-English batch A (no PR) | SUPERSEDED: Superseded by merged #932 (`799a89e`), which combined batches A-D; 26/28 branch blobs identical to #932. |
| `claude/discord-plain-english-b` | `8a2341a24429c96f4217265444e489f66b73bcbe` | `archive/claude-discord-plain-english-b-2026-09-24` | Discord plain-English batch B: watcher/drift-gate text (no PR) | SUPERSEDED: Superseded by merged #932 (`799a89e`); 14/15 branch blobs identical to #932. |
| `claude/discord-plain-english-c` | `d4d141fc6f75095ace88463da1928582a3f98c6a` | `archive/claude-discord-plain-english-c-2026-09-24` | Discord plain-English batch C: broker-safety/trade-close/live-switch text (no PR) | SUPERSEDED: Superseded by merged #932 (`799a89e`); 12/14 branch blobs identical to #932. |
| `claude/discord-plain-english-d` | `105c918bfaff0fb86eef833d5dbef9d4607f1a47` | `archive/claude-discord-plain-english-d-2026-09-24` | Discord plain-English batch D: options cards (no PR) | SUPERSEDED: Superseded by merged #932 (`799a89e`); 16/18 branch blobs identical to #932. |
| `claude/futures-orb-geometry-first-run` | `d93019b805a653f2a5efe8265cb1d73325bb90af` | `archive/claude-futures-orb-geometry-first-run-2026-09-24` | fng-v0.1 first-run record + orbx-v0.1 time-exit prereg and runner (#885) | HISTORICAL: Closed: auditor ruled not trusted evidence (prior-session bridging defect). orbx result recorded nowhere on main. |
| `claude/futures-signa-shadow-context-rebased` | `d29b9f9035130f1975b7c58e627e2e5a1c0f97a6` | `archive/claude-futures-signa-shadow-context-rebased-2026-09-24` | Futures Signa shadow-context collector + exact-join report, rebased (#574) | HISTORICAL: Closed as parked. All 5 files blob-identical to existing tag `archive/audit-futures-signa-shadow-context-2026-09-15`, but that tag does not contain this tip; tag preserves the #574 commit identity. |
| `claude/futures-signal-embed` | `7d5d8ac30831be66ae3b5720869d132be9316a08` | `archive/claude-futures-signal-embed-2026-09-24` | Futures Discord signal as embed card (#563) | HISTORICAL: Closed as parked presentation-only work; no superseder on main. |
| `claude/ong-v01-run-checkpoint` | `38c7604dc7e1a29e0b24f1be7fa9e73564db05b1` | `archive/claude-ong-v01-run-checkpoint-2026-09-24` | ong-v0.1 options non-Strat geometry raw report JSON + checkpoint (#886) | HISTORICAL: Closed; final audit (PR comment) REJECT for all 16 families. Raw report exists only on this branch. |
| `claude/paper-collection-digest` | `abfb8d6db0263699468f28b8d9085b00da8295fe` | `archive/claude-paper-collection-digest-2026-09-24` | Paper collection digest + systemd timers + notification route (#605) | SUPERSEDED: Superseded by merged #603 (`d93c580`) reporter/rollups (handoff 09-16). |
| `claude/supersede-859` | `8fce33c5908cf5508b5752fd48a55ff012dc781d` | `archive/claude-supersede-859-2026-09-24` | Doc marker: fast-regime-from-5m study SUPERSEDED, do not run (#861) | HISTORICAL: Closed "handled in the other thread", but the marker never landed: main still says PREREGISTERED STUDY, NOT RUN. |
| `codex/options-morning-handoff-status` | `48f6c2a1cc543febc9ff697765d0faed2d49f43a` | `archive/codex-options-morning-handoff-status-2026-09-24` | Options morning handoff refresh (#683) | SUPERSEDED: Superseded by merged #708 (`a07f28b`). |
| `codex/options-probe-direct-exec` | `9a08361cd257dfe307e224054385fdb462f5773f` | `archive/codex-options-probe-direct-exec-2026-09-24` | Public timestamp probe direct-exec fix (#688) | SUPERSEDED: Duplicate of merged #687 (`b15a3be`). |
| `codex/options-v1-universe-preflight` | `684bbf0620be46cbf89b8ebd78130022b9b83a8e` | `archive/codex-options-v1-universe-preflight-2026-09-24` | Options V1 149-symbol universe/capacity/contract preflight tooling (#583) | HISTORICAL: Closed as parked groundwork; no superseder. |
| `feature/mnq-trend-day-paper-cohort-20260922` | `cd784ddd2c8484a4dc340aae211618a8800c7c39` | `archive/feature-mnq-trend-day-paper-cohort-20260922-2026-09-24` | Default-off MNQ trend-day paper cohort + prereg (#910) | HISTORICAL: Closed: prerequisite #911 audit found no family clearing the gate; PR says preserve as abandoned design/reference. |
| `fix/options-212r-collector-causal-timing` | `efea64e04245fbce42087e48243bc75b1e38ae6a` | `archive/fix-options-212r-collector-causal-timing-2026-09-24` | 212R prospective collector causal-timing tightening (#734) | SUPERSEDED: Superseded by merged #735 (`ae056ca`) on top of #730 (`4816aa4`). |
| `research/asian-precursor-audit` | `98e101352083255ed0f3b657e25119d8b9f7fbb0` | `archive/research-asian-precursor-audit-2026-09-24` | Asian-session pre-signal precursor audit (#596) | HISTORICAL: Closed unmerged; code absent from main; no superseder. |
| `research/bos-mss-retest-event-study` | `a2799ef115b9cb1c2037f1376326e9b59d723eb3` | `archive/research-bos-mss-retest-event-study-2026-09-24` | Causal BOS/MSS first-retest event study (#594) | HISTORICAL: Closed as parked; PR asks to preserve as provenance. Never run. |
| `research/intraday-momentum-replication-v01` | `f3959fc82f8cae934a5f140f35a8ab5ed3e50a1c` | `archive/research-intraday-momentum-replication-v01-2026-09-24` | fim-v0.1 intraday-momentum replication prereg + runner (#887) | HISTORICAL: Closed after scored run; result recorded only in the PR comment, not on main. |
| `research/mes-asian-d-ema-baseline` | `af85df786b41451e09a0d4c473bf838b4a772e42` | `archive/research-mes-asian-d-ema-baseline-2026-09-24` | MES Asian D+EMA baseline portability producer + tests (#598) | HISTORICAL: Closed unmerged; Asia D+EMA lane retired 2026-09-24. Contains `audit/counterfactual-producer`. |
| `research/mnq-orb-rework-stage-a-20260923` | `f268f3dd02db0dd51501bb4ff5d0c72200490bb9` | `archive/research-mnq-orb-rework-stage-a-20260923-2026-09-24` | MNQ ORB Stage A v0.1 raw-signal prereg + runner (#984) | SUPERSEDED: Superseded by open #994 (`research/mnq-orb-stage-a-v02-20260924` `0776a9d`), same 60-cell screen at v0.2. |
| `research/mnq-orb-rework-stage-a-current-20260924` | `aedcaecc956efea3ad6c36a0778cdfbe19368ae3` | `archive/research-mnq-orb-rework-stage-a-current-20260924-2026-09-24` | MNQ ORB Stage A v0.1 on current main + trial ledger/manifest (#990) | SUPERSEDED: Closed as superseded by open #994 (`0776a9d`); diff to #994 is only v0.1 to v0.2 renames (4 files, 9 lines). |
| `research/mnq-orb-rework-stage-a-ledger-20260923` | `5b377d3e8da4fdc1451b6b5e26c3cb4279033016` | `archive/research-mnq-orb-rework-stage-a-ledger-20260923-2026-09-24` | MNQ ORB Stage A v0.1 with trial registration on stale base (#985) | SUPERSEDED: Superseded by #990, then open #994 (`0776a9d`). |
| `research/mnq-pdl-sweep-reclaim-execution-20260924` | `3fbaaf16ffc46744e599abe7d929f798af9b0cd2` | `archive/research-mnq-pdl-sweep-reclaim-execution-20260924-2026-09-24` | pdl-sr-b-v0.1 MNQ PDL sweep-reclaim honest-fill prereg, manifest, runner (#991) | HISTORICAL: Closed after scored run: BROKEN, retire. Trial-ledger row and result not on main; runner has a known 16:00 ET exit defect. |
| `research/mnq-pdl-sweep-reclaim-friction-20260924` | `d901e3ab12f79cfa84dfc614ce6a687e065fef95` | `archive/research-mnq-pdl-sweep-reclaim-friction-20260924-2026-09-24` | MNQ PDL sweep-reclaim friction screen prereg + runner (#995) | SUPERSEDED: Closed as duplicate of #991 (`research/mnq-pdl-sweep-reclaim-execution-20260924` `3fbaaf1`); never scored. |
| `research/mnq-sustained-trend-v1-prereg-20260922` | `dd853e70eb2a726f1da81cf7837e508e7aaf0d27` | `archive/research-mnq-sustained-trend-v1-prereg-20260922-2026-09-24` | MNQ sustained-trend continuation v1 prereg, detector spec, closure, tests (#912) | HISTORICAL: Closed DOES_NOT_CLEAR. Evaluator code is on main and #914 (`c2b3be3`) records the verdict, but prereg/spec/closure docs are absent. |
| `research/mnq-trend-day-existing-strategy-audit-20260922` | `e68e2da1a9509295295d2dcab3b9862b61eaf27e` | `archive/research-mnq-trend-day-existing-strategy-audit-20260922-2026-09-24` | MNQ trend-day existing-strategy frozen audit prereg + script (#911) | HISTORICAL: Closed; verdict referenced on main, but prereg and script are absent. |
| `research/mnq-vwap-delayed-failed-reclaim-stage-a-20260924` | `562d422422ac0ccc6ed966a3c6f649c973041a9a` | `archive/research-mnq-vwap-delayed-failed-reclaim-stage-a-20260924-2026-09-24` | MNQ delayed VWAP failed-reclaim prereg + runner (#996) | SUPERSEDED: Closed as duplicate of #992 (`research/mnq-vwap-failed-reclaim-3bar-20260924` `337698e`); never scored. |
| `research/mnq-vwap-failed-reclaim-3bar-20260924` | `337698e43d6a5ce092d9a6dd478e910ff447e22f` | `archive/research-mnq-vwap-failed-reclaim-3bar-20260924-2026-09-24` | vwap-fr3-a-v0.1 MNQ VWAP failed-reclaim <=3 bars prereg, manifest, runner (#992) | HISTORICAL: Closed after scored run: BROKEN, retire. Trial-ledger row and result not on main. |
| `research/thestrat-reference-audit-v01` | `f053c7b16686932aa6d5f2c0e5dd14234aeb591a` | `archive/research-thestrat-reference-audit-v01-2026-09-24` | TheStrat reference spec audit + 2-1-2 reversal v0.1 prereg and runner (#890) | HISTORICAL: Closed: H2 instability on both instruments. Tip is 4 commits past the PR head `2bfea35`; the tag keeps both. |
| `research/transition-400t-canonicalization` | `53cf721280d82288f0495d7b14e91af10ee9a189` | `archive/research-transition-400t-canonicalization-2026-09-24` | Transition 400t/30m canonicalization, parity/contract audits, slippage-stress results (#659) | HISTORICAL: Closed: WAIT, fails slippage robustness. #659 says final evidence is preserved on this branch; #666 (`cfc4d01`) holds only the summary. |

Branches deleted after the above tags were pushed and remote-verified: every
branch in the table above, plus:
- `rebuild/options-nonstrat-paper-track-clean` (`5c71e21`) — **no separate
  tag** (its tip is an ancestor of `audit/options-nonstrat-paper-track`, so it
  is fully contained in `archive/audit-options-nonstrat-paper-track-2026-09-24`)
- `codex/options-risk-opposite-direction-fixture` (`3836bec`) — **no tag**
  (its one file is byte-identical to `main`'s current version)
- `codex/options-selector-stale-audit` (`6e4a7b0`) — **no tag** (all 4 files
  byte-identical to `main`'s current version)

Kept, not deleted:
- `claude/452-runtime-memory-gate` — KEEP: handoff holds it until 2026-09-30;
  the runner-side memory gate is not on `main`.
- `claude/hotfix-c7798d4-plus-849` — KEEP: named as a rollback release ref in
  the handoff (same patch as merged #849). Could become a `release/*` ref.
- `hold/fixed-daily-loss-cap` — NEEDS REVIEW: same patch as #830 (closed "not
  approved") but deliberately kept as a `hold/` branch; needs an operator ruling.

Notes for the record:
- The trial-ledger rows for the scored-and-retired trials #991 (pdl-sr-b-v0.1)
  and #992 (vwap-fr3-a-v0.1), and for the ORB v0.1 trial
  `T-2026-09-24-prereg-mnq-orb-rework-stage-a-2026-09-23-01`, exist only in
  their archived branches, not in `main`'s `docs/research-trial-ledger.jsonl`.
- The three `research/mnq-orb-rework-stage-a-*` tags are superseded by #994,
  which is still open; if #994 closes unmerged they are the only copies of the
  v0.1 prereg.
