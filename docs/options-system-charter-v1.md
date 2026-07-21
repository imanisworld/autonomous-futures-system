# Options System Charter v1 (Increment O-001)

**Status:** DRAFT — awaiting Codex's independent audit and control-room
verdict (MERGE / REWORK / REJECT / HOLD). Docs-only; zero code, config,
test, broker, or runtime changes. No merge, no deployment, no order
submission, no futures-system touch. Nothing in this document authorizes
any code change by itself — every later increment still needs its own
control-room-authorized packet.

**Base SHA:** `origin/main` @ `8c58aaa0cad459e40b6018b724164e79095dec76`
(PR #298, "Add Historical-Engine Parity build...").
**Branch / worktree:** `claude/options-charter-v1`, isolated worktree at
`/tmp/afs_options_charter` (the shared checkout was on an unrelated
branch — `claude/tqqq-sqqq-v2-continuation-lane` — with other in-flight,
untouched work, so this increment never ran there).

**The single claim this increment establishes:**

> The standalone Options Plan Manager (`options_manager`) is the primary
> options system; the futures-linked options companion
> (`options_companion`) is a separate research-only lane whose evidence
> and authority cannot be mixed with the standalone system.

## Step 0 — preflight (reported, not just performed)

1. **Repository:** `/Users/djb.a.e/MAINVSCODE/autonomous-futures-system`,
   remote `https://github.com/imanisworld/autonomous-futures-system.git`.
2. **Fetched `origin` without mutating `main`**; `origin/main` =
   `8c58aaa0cad459e40b6018b724164e79095dec76` at fetch time.
3. **Worktree status:** the isolated worktree used for this increment is
   clean. The shared checkout (a separate directory) was on
   `claude/tqqq-sqqq-v2-continuation-lane` with unrelated untracked
   stocks-advisory files — left untouched; this increment never touched
   that checkout.
4. **`AGENTS.md` does not exist in this repository.** The only applicable
   repo-root instruction file is `CLAUDE.md` (worktree isolation per
   task, `claude/<task>` branch naming, PR-only merge to `main`, no
   `git add -A`) — followed throughout this increment.
5. **Existing options-related surface found** (read, not modified):
   `options_manager/` (Phase-1-only advisory pipeline: `packet_builder.py`,
   `risk_gate.py`, `contract_quality.py`, `validation/` package —
   `proof_packet.py`, `contract_quality_gate.py`, `advisory_decision.py`,
   `management_cases.py`, `fixture_status.py`,
   `position_management_checklist.py`, `morning_scan_packet.py`,
   `no_trade_reasons.py`), `options_manager/strategies/strat_212.py`,
   `options_companion/` (`evaluator.py`, `selection.py`, `chain_provider.py`,
   `signa_gate.py`, `store.py`), `risk/options_risk_engine.py`,
   `alert_ranker/` (scanner microservice, currently stopped on the box —
   port 8010 closed since 2026-07-02 per an unrelated prior operator
   decision), plus `docs/options-forward-proof-packet.md`,
   `docs/options-fixture-candidates.md`,
   `docs/options-management-case-evidence-audit.md`,
   `docs/options-monday-morning-input-template.md`,
   `docs/increment-14-polygon-options-client-design-audit.md`, and
   `tests/test_options_*.py` / `tests/test_alert_ranker.py` /
   `tests/test_options_companion.py`.
6. **Branch-claim check:** `claude/options-charter-v1` was already
   created and pushed in this same session (PR #302, CI green,
   unmerged) before this stricter spec was issued. This increment
   **reworks that same branch/PR in place** rather than opening a
   second, competing claim on O-001 — see the note at the top of this
   file. No other branch or open PR references O-001.
7. **Safety check — this increment cannot disturb:**
   - *The frozen futures runtime*: zero files under `execution/`,
     `webhook/`, `risk/risk_engine.py`, `strategy/`, `journal/`,
     `config/`, `context/`, `tradingview/` are touched — this increment's
     diff is exactly one markdown file (verified below).
   - *Existing open options branches*: none found besides this one
     (`git branch -a` / `git ls-remote --heads origin`, filtered for
     `option`/`charter`/`companion`, returned only this branch).
   - *Unmerged work*: the shared checkout's unrelated stocks-advisory
     untracked files were left untouched (different lane, different
     worktree).
   - *Any deployed service*: this is a local git branch/PR only; nothing
     here restarts, redeploys, or reconfigures the running box.
8. **Policy contradictions identified** (full detail and citations in
   §4 below): minimum DTE (14+ vs 0-2), dollar risk cap ($250 vs $300 vs
   $400), one-contract-default doctrine vs the standalone system's own
   coded default of 2 contracts, and underlying-invalidation vs a
   premium-stop percentage — the last of which has **no source anywhere
   in this repository** (see §4).

No blocker required a stop under these conditions — proceeding to the
charter itself.

## 1. System boundaries

- **`options_manager` is the primary standalone options system.** Every
  later options increment (truth ledger, behavioral audit, strategy
  edge, provider proof, forward capture) builds toward this system's
  doctrine, thresholds, and evidence bar.
- **`options_companion` is a separate, futures-linked research lane.**
  It has its own config (`CompanionConfig`), its own risk numbers, and
  its own DTE policy (§4) — none of it is inherited by or blended into
  the standalone system.
- **Companion evidence cannot satisfy standalone proof gates, and
  standalone evidence cannot validate the companion.** Neither lane's
  `FixtureStatus`/`ProofPacket`/backtest result counts toward the
  other's promotion criteria, ever.
- **Because futures is frozen (standing five-session evaluation
  freeze), the companion's posture is `HOLD / RESEARCH ONLY`** — it
  cannot become the main options lane, and no options-lane increment in
  this sequence upgrades, expands, or promotes it as a side effect. Any
  future request to change that posture is a separate, explicit
  control-room decision, not an implication of this charter.

## 2. Execution stages

| Stage | Definition | Current authority |
|---|---|---|
| **Phase 1 — Advisory only** | A candidate is scored/gated/reported (intake → `risk_gate.py` → `contract_quality_gate.py` → `ProofPacket`/`advisory_decision.py` → TAKE/WAIT/AVOID); no order of any kind is prepared. | **Current state for the standalone system.** `options_manager/app.py` runs `assert_live_options_trading_disabled()` at import/boot time — the module cannot even start if live trading were somehow enabled. |
| **Phase 2 — Paper automation** | A resolved advisory decision is carried through a manual paper lifecycle (`WATCHING → TRIGGERED → ACTIVE → EXITED/INVALIDATED/EXPIRED`) with realistic quote-side fills, no broker order. | **Not yet reached for the standalone system** — this is exactly what Lane 4 (O-401/O-402) is building toward. `options_manager/paper_sim.py` and `options_manager/dry_run_review.py` exist as Phase-1/Phase-2-adjacent building blocks but no forward-captured `ProofPacket` has ever been run through this lifecycle (§5). |
| **Phase 3 — Live assist with explicit human approval** | A human reviews a fully-gated, paper-proven candidate and manually places the real order themselves; the system never calls a broker order endpoint. `options_manager/human_confirm.py` and `order_ticket.py` exist as design/prep scaffolding for this stage. | **Not authorized.** No increment in the O-001–O-403 sequence reaches this stage; reaching it requires its own future, separate control-room authorization after Lane 4 (paper) evidence exists. |
| **Phase 4 — Conditional live execution** | The system itself could place or manage a real order. | **Prohibited by this charter, full stop.** No increment in this sequence proposes, designs toward, or authorizes this stage. Any future move toward it is out of scope for every increment listed here and requires a separate, explicit, future authorization this charter does not grant in advance. |

**Binding statement of current authority:**
- Standalone manager: **advisory/manual evidence only** (Phase 1). No
  repository evidence found of any already-authorized narrower or wider
  state — `assert_live_options_trading_disabled()` is unconditional.
- Companion: **hold/research only.**
- Automatic live options execution: **prohibited.**
- Broker submission of any kind: **prohibited by this increment** (and
  by every increment through O-403).

## 3. Default trading doctrine

- **Calls and puts only** — no spreads, no multi-leg structures, no
  futures options.
- **Primary research setup: `2-1-2` continuation**
  (`options_manager/strategies/strat_212.py`). Continuations, reclaims,
  and clean retests are preferred setup shapes; no other setup gets
  preregistered or backtested ahead of `strat_212` without a separate
  control-room decision (Lane 2, O-201 onward).
- **One-contract default** as an *operating* doctrine for how a human
  fills out a `ProofPacket`/contract-quality intake going forward — see
  §4 for the exact, sourced contradiction this creates against the
  standalone system's own coded default.
- **Preferred maximum loss per trade: $300** (per-trade premium/dollar-risk
  cap) — see §4 for full reconciliation against the $250/$400 figures
  found elsewhere in the codebase.
- **Maximum five open positions; total open risk at or below $1,000** —
  documented as doctrine; **not yet code-enforced** (no existing module
  sums risk across positions — `contract_quality_gate.py` and
  `risk_gate.py` both evaluate one packet/contract at a time). Enforcing
  this is deferred to its own future increment, not part of O-001–O-403.
- **No averaging down, no revenge trading, no chasing.** These are
  behavioral prohibitions this charter adopts as doctrine; they are not
  currently encoded as automated checks anywhere in `options_manager` or
  `options_companion` (no module inspects trade sequencing/history to
  detect any of the three). Like the portfolio ceiling above, this is a
  manual discipline until a future increment, if ever authorized, adds
  automated detection.
- **Underlying entry trigger and invalidation are mandatory** on every
  `ProofPacket` (`entry_trigger` / `underlying_invalidation` are
  required, non-optional fields per `validate_proof_packet()`).
- **Premium-risk control does not replace underlying invalidation.** The
  two are evaluated independently today (`proof_packet.py`'s
  `underlying_invalidation` vs `premium_stop` are separate fields;
  `contract_quality_gate.py` never reads `underlying_invalidation`, and
  `proof_packet.py`'s structural validation never reads contract
  premium) — this charter fixes that separation as doctrine so a future
  increment cannot collapse it to simplify a form.
- **Missing setup, invalidation, risk, liquidity, contract data, or
  market context produces `WAIT` or `AVOID`, never approval** — this
  already matches `advisory_decision.py`'s and
  `contract_quality_gate.py`'s fail-closed design (`DATA_BLOCKED`/`BLOCK`
  verdicts, never a silent pass) and this charter adopts it as binding
  doctrine, not just current code behavior.

## 4. Policy-contradiction resolution table

Every conflict below was found by direct source read at the base SHA
above, not assumed. Where this charter's spec expected a value
(45+ DTE swing preference; a ~20% premium-stop guidance) and no source
for it exists anywhere in this repository, that is stated explicitly as
**UNRESOLVED / NOT FOUND** rather than invented.

| Conflict | Sourced values | Resolution |
|---|---|---|
| **Minimum DTE** | `options_manager/validation/contract_quality_gate.py::DEFAULT_MIN_DTE = 14` ("avoid weeklies," override only via explicit `dte_exceptional=True`) vs `options_companion/evaluator.py::CompanionConfig.max_dte = 2` + `options_companion/selection.py`'s same-day/0-DTE preference before a 14:00 ET cutoff. | **Standalone policy stays 14+ DTE, unchanged.** Companion's 0-2 DTE is not adopted, not weakened, and never cited as evidence for or against the standalone threshold — it is a deliberately different product (same-day expression of a futures-timed signal), not the same policy misapplied twice. |
| **45+ DTE "swing preference"** | **Not found anywhere in this repository** — searched `options_manager/`, `options_companion/`, `docs/options*.md`, `.private-companion/*.md`. | **UNRESOLVED.** Not adopted, not invented. If a swing-DTE preference beyond the existing 14+ floor is wanted, it needs its own sourced decision (an operator statement, a prior document not yet found, or a fresh explicit choice) before a future charter revision can add it. |
| **Dollar risk cap** | `risk/options_risk_engine.py::OptionsRiskConfig` class default = **$250** (`max_premium_per_contract`/`max_total_premium`, and its own `from_dict()` fallback) vs `options_manager` — three independently-consistent modules all say **$300** (`config.py::risk_max_premium=3.00`/`risk_max_total_premium_dollars=300.00`, `models.py::max_premium=3.00`, `contract_quality_gate.py::DEFAULT_MAX_PREMIUM_DOLLARS`/`DEFAULT_MAX_DOLLAR_RISK=300.0`) vs `options_companion/evaluator.py::CompanionConfig.max_premium_per_contract`/`max_total_premium = 400.0`. | **$300 is the standalone system's cap** — already what `options_manager` does everywhere; no code change needed to comply. **$400 stays companion-only**, never adopted by the standalone system. **$250 is not a doctrine number** — it is the shared engine's own generic fallback for callers that don't specify one; any future standalone caller of `risk/options_risk_engine.py` must pass `$300` explicitly so this figure stops leaking in by default. |
| **One-contract default vs multi-contract permission** | The doctrine calls for a one-contract default, but `options_manager/models.py::OptionTradePacket.max_contracts` itself **defaults to 2**, and `options_manager/config.py::risk_max_contracts` **ceiling is also 2** (i.e. today a freshly-built packet already requests the maximum the system allows). `options_companion/evaluator.py` hardcodes `max_contracts=1` in its own risk config. | **Operating default is 1 contract**, per this charter — a human filling out a packet should not accept the model's own `max_contracts=2` default without deliberately choosing it. **More than one contract requires explicit risk justification and must stay within the $300 total-risk cap and the 5-position/$1,000 portfolio ceiling** (§3). This is a doctrine-level correction to an existing code default, not a claim that the code already enforces 1 — a future increment may propose changing `models.py`'s default to 1, but that is a code change and out of scope for this docs-only charter. |
| **Underlying invalidation vs ~20% premium-stop guidance** | **No source for a "~20% premium stop" figure exists anywhere in this repository** — searched `options_manager/`, `options_companion/`, `docs/options*.md`, `.private-companion/*.md`, `CONTRIBUTING.md`. The only 20% figure found repo-wide is the *futures* max-drawdown circuit breaker (`.private-companion/HANDOFF.md`, `FUTURES_SYSTEM_AUDIT_2026-06-23.md`) — an unrelated system. | **UNRESOLVED.** Not adopted, not invented. What the codebase *does* already establish (§3) is that `premium_stop` and `underlying_invalidation` are separate, independently-evaluated fields on every `ProofPacket` — that structural separation is confirmed doctrine regardless of what specific premium-stop percentage (if any) is eventually chosen. |

## 5. Evidence classes

Six classes, each with an explicit boundary on what it can and cannot
prove. A later increment must state which class its output belongs to
and must not promote evidence across classes without a recorded human
decision (the same convention `fixture_status.py` already uses for
promotions).

- **Historical account evidence** — reconciled Robinhood
  statement/order-history data (Lane 1, O-101/O-102/O-103): every BTO,
  STC, expiration, assignment, fee, cash movement. **Can prove**: real
  fills, realized P&L, timing, holding periods, behavioral patterns
  (churn, concentration, oversized trades). **Cannot prove**: candle
  structure, Strat setup validity, underlying entry trigger,
  invalidation, GEX, Signa, SPY/QQQ context, or contemporaneous decision
  quality — no contemporaneous source exists for any of the twelve
  fixture candidates already audited in
  `docs/options-fixture-candidates.md`.
- **Historical strategy/backtest evidence** — a backtest run over
  historical market data (Lane 2, O-202/O-203) using a preregistered
  rule set (O-201). **Can prove**: whether a *rule*, applied
  mechanically to past data, would have had edge after realistic costs.
  **Cannot prove**: that this specific account's real historical trades
  followed that rule, or that the rule will hold forward.
- **Prospective advisory evidence** — a `ProofPacket`/morning-scan
  candidate scored live by the Phase 1 pipeline, whether or not it is
  ever acted on. **Can prove**: what the pipeline would have recommended
  at the time, if properly logged with a contemporaneous source.
  **Cannot prove** anything about a candidate that is scored but never
  captured as a real `ProofPacket` per §1 of
  `docs/options-forward-proof-packet.md`'s rules.
- **Paper-simulation evidence** — a captured `ProofPacket` carried
  through the manual paper lifecycle to a resolved outcome (Lane 4,
  O-402), priced with realistic quote-side fills. **Can prove**: that
  the end-to-end process (capture → trigger → manage → resolve) works
  and produces an honestly-priced hypothetical result. **Cannot prove**
  durable edge on its own — 20-30 resolved opportunities prove the
  process, not the strategy (O-402's own stated scope).
- **Broker-demo evidence** — any future demo/paper-broker-integrated
  execution path (not built, not scoped in O-001–O-403). **Would prove**
  order-lifecycle mechanics (fills, rejections, latency) in a
  broker-realistic environment. **Would not prove** anything about
  setup quality beyond what paper-simulation evidence already
  establishes, and is explicitly out of scope for every increment in
  this sequence.
- **Live evidence** — an actual broker-executed real-money trade.
  Historical account evidence (above) already contains all the live
  evidence that exists today (past real trades). Under §1/§2's
  Phase-4-prohibited rule, **no increment in this sequence produces new
  live evidence** for the standalone system; that requires its own
  separate, explicit, future control-room authorization not granted by
  this charter.

**Cross-class rule (binding):** a lower class never counts as proof of a
higher one. Companion evidence, of any class, never counts as
standalone-system evidence of any class (§1).

## 6. Proof and promotion gates

The required sequence, matching the agreed operating model:

1. Reconciled historical truth ledger (O-101).
2. Behavioral audit (O-102).
3. Historical rule replay (O-103).
4. Strategy preregistration (O-201).
5. Underlying edge backtest (O-202) — **stop here if the underlying
   signal fails after costs/out-of-sample; options leverage cannot
   rescue a negative signal** (O-203 does not run if O-202 fails).
6. Contract-expression replay (O-203).
7. Provider-access proof (O-301).
8. Canonical data contract (O-302).
9. Prospective `ProofPacket` capture (O-401).
10. Manual paper lifecycle (O-402).
11. Independent evidence verdict — Codex (O-403): `CONTINUE COLLECTING`
    / `REWORK` / `REJECT` / `PAPER-AUTOMATION ELIGIBLE`.
12. Separate authorization for any higher execution stage (Phase 3/4,
    §2) — explicitly not granted by reaching step 11.

**Binding rules:**
- A merged PR does not authorize deployment.
- A deployment does not authorize promotion.
- No stage may inherit proof from another lane (§1, §5).

## 7. Governance

- **Control room** (the operator) authorizes each bounded increment and
  issues the final verdict (MERGE / REWORK / REJECT / HOLD) on every PR.
- **Claude implements**: creates the branch/worktree, writes the
  code/report/doc, writes tests, opens a **draft** PR. Claude cannot
  approve, merge, deploy, or promote its own work.
- **Codex independently audits**: verifies branch, diff, evidence,
  tests, leakage, and safety boundaries, and issues its own finding.
  Codex does not repair or take over Claude's branch — a rejected
  increment goes back to Claude, not to a Codex rewrite of the same
  branch.
- **One branch equals one claim.** No stacked branches without explicit
  control-room authorization.
- **Every increment packet states:** base SHA, one claim, allowed files,
  forbidden files, inputs/dataset hashes, required tests, acceptance
  criteria, stop conditions, explicit runtime authority (normally none),
  expected deliverables.
- **No mixed futures/options PRs.** No code, deployment, and evidence
  report bundled into one PR.
- **Before deleting any branch or worktree**, verify it contains no
  unique commits.
- **After every merge**: sync `main`, inspect changed paths, run
  targeted tests, run the full suite, then update
  `../AGENT_HANDOFF.md`.

## Validation (docs-only increment)

- **Changed-file list** (exact): `docs/options-charter-v1.md` renamed to
  `docs/options-system-charter-v1.md` (git-tracked rename, one file).
- **`git diff --check`**: clean (no whitespace errors) — see PR CI.
- **Confirmed no executable, test, configuration, futures, broker, or
  deployment file changed** — this is the only change in the diff; no
  `.py`, `.yaml`, `.yml`, `.env*`, or futures-path file is touched.
- **No runtime action occurred**: no service restarted, no deploy run,
  no order submitted, no branch merged.
- **Full `pytest -q`**: not required for a docs-only change per this
  repo's own convention (every prior docs-only PR in this lane — #273,
  #274, #279 — relied on CI's own test job rather than a manual local
  run); CI's `tests`/`Analyze (actions)`/`Analyze (python)`/`CodeQL`
  jobs all ran and passed on this PR (see PR #302 checks).

## Unresolved items (carried forward, not disguised as resolved)

1. **45+ DTE swing preference** — no source found; not adopted.
2. **~20% premium-stop guidance** — no source found; not adopted.
3. **5-position/$1,000 portfolio ceiling** and **no-averaging-down /
   no-revenge-trading / no-chasing** — documented as doctrine, not yet
   code-enforced; each would need its own future, explicitly-scoped
   increment to add automated detection.
4. **`models.py::max_contracts` default of 2** conflicts with the
   1-contract *operating* doctrine this charter sets — a future code
   increment could change the field default, but that is out of scope
   for this docs-only charter.

## Scope note

This charter is docs-only. It reconciles doctrine already implicit or
conflicting across `options_manager`, `options_companion`, and
`risk/options_risk_engine.py`; it does not modify any of those modules'
code, tests, or defaults. Every number and policy cited above was
verified by direct read of the current source at the base SHA in this
PR, not assumed.
