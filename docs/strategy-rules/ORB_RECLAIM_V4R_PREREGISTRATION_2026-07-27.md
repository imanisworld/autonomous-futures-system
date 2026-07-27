# ORB Reclaim V4-R — preregistration

**Status: FROZEN before any replay is run.** This document is committed on its
own, before the study executes. No parameter, gate treatment, corpus, or
window in this document may change after seeing results. Any deviation
discovered mid-study gets reported as a deviation, not silently absorbed.

## 0. Terminology reconciliation (do not silently reuse Pass 1's labels)

Pass 1 (`docs/orb-reclaim-strategy-rework-diagnosis-2026-07-27.md`,
`origin/claude/orb-reclaim-strategy-rework`, uncommitted to `main`) computed
two distinct, independent features that its own prose conflated:

- **`true_reclaim`** (Pass 1's actual code, `scripts/orb_reclaim_rule_anatomy.py`)
  = 1 iff **any** earlier same-day bar at the same ORB level closed above the
  ORB high — no pullback/rejection requirement at all. This is a weaker
  condition than the documented pattern the variable name implies.
- **`prior_rejected_high`** (also Pass 1's code) = 1 iff an earlier same-day
  bar at the same ORB level independently satisfied the proven `rejected_high`
  transition (previous close `> orb_high`, current close `<= orb_high`) — this
  IS the causal encoding of "rejected above, then pulled back through/below."

**"V4" in Pass 1** = `NY session AND true_reclaim` (the weaker flag). Its
numbers (uncensored n=24, +$568.48, PF 1.813, both halves positive; canonical
n=6, PF 7.615) describe **that** condition and are retained below, relabeled
**V4-original**, as a distinct, separately-tracked population — never silently
overwritten by this study's results.

**This study — "V4-R"** = `NY session AND prior_rejected_high AND this bar is
reclaimed_high` (the stricter, actually-documented pattern). V4-R is a new
population, not a rerun of V4-original. Both are reported side by side.

## 1. Hypothesis

`NY + prior-rejected-then-reclaimed` (V4-R) materially outperforms the
currently-implemented first-cross definition (`orb_reclaim` as coded on
`main` today — any close-cross above the ORB high, no rejection
requirement, all sessions), without relying on lookahead or unrealistic
fills, and without cherry-picking the evaluation window after seeing
results.

## 2. Frozen causal definition

All fields below are read directly from
`data/replay_corpus_v1_market_condition_fixed`'s own `orb_status` column,
which is produced by `scripts/polygon_to_replay.py`'s `derive_orb_status` —
the SAME function object `tests/test_replay_orb_status.py` proves is
identical to `scripts/csv_to_replay.py`'s version, which in turn mirrors the
live Pine `orb_status_for()` transition logic verbatim (proven in Pass 1 and
independently re-verified for this study). No new state-machine logic is
introduced — V4-R is a POST-HOC label over the same proven transition
primitives already in the corpus, computed causally (looking only at bars up
to and including the current one).

- **ORB level identity**: an earlier bar counts only if its `orb_high` /
  `orb_low` matches the trigger bar's `orb_high` / `orb_low` exactly (same
  session's ORB, no cross-session carry) — same tie-break Pass 1 used.
- **`rejected_high` bar** (must occur strictly before the trigger bar, same
  day, same ORB level): `orb_status == "rejected_high"` on that bar.
- **Trigger bar**: `orb_status == "reclaimed_high"` on the current bar, AND
  at least one earlier same-day, same-level bar satisfied `rejected_high`
  above.
- **Session filter**: `state.session == "new_york"` on the trigger bar (the
  same `session` field the real engine assigns — not independently
  recomputed).
- **Attempt index**: NOT restricted in the primary V4-R population (unlike
  Pass 1's finding that 2nd-attempt-of-day was toxic — that goes into
  robustness detail, §6, not the primary filter, since the operator's V4-R
  spec did not include an attempt-index restriction).

## 3. Frozen entry/bracket/fill mechanics — IDENTICAL to current production

This study tests which candidates should trigger the strategy, not a new
strategy. The bracket is byte-identical to `strategy/signal_engine.py`'s
`_try_orb_reclaim` (confirmed current on `main`):

- Entry: `ORB high + 2 ticks`
- Stop: `max(ORB low − 4 ticks, entry − 80 ticks[MNQ] / 40 ticks[MES])`
- Target: `entry + max(2.5 × risk, 15 points)` — the 15pt floor is
  `_enforce_min_target_distance`, applied identically to every other
  strategy sharing this helper.
- Direction: LONG only (matches the current, unmodified detector).

**Fill model: `entry_fill_model="ioc_limit"`**, explicitly pinned — this
matches PR #352's own precedent for this exact strategy (its session-isolated
lanes are literally named `MNQ_london_ioc_1tick` etc.), and is consistent
with every other honest-fill lane in this repo this session (Miyagi, 3-2-2).
Production's ambient code default is `"market"` (`config/settings.py:216`,
no override in `risk_rules.yaml`/`.env`) — noted for transparency, not used,
since a market fill would be the less-realistic (and historically
over-optimistic) choice for backtest evidence.

**Entry tolerance**: canonical per-root values `MNQ=32 / MES=16` ticks,
explicitly pinned in-memory rather than trusting the ambient `.env`
(currently `MNQ=16/MES=8` — the same parked drift finding flagged during the
Miyagi/3-2-2 work, not re-litigated here, same "pin the documented value"
resolution both of those studies used for comparability).

**GEX gate**: inert in this corpus — `data/replay_corpus_v1_market_condition_fixed`
carries no `gex_regime` field, and `_gex_allows_orb` explicitly returns
`True` (does not block) when GEX data is absent. Noted, not silently
ignored.

## 4. Corpus / window

`data/replay_corpus_v1_market_condition_fixed` (both `MNQ` and `MES`, 313
daily files each, 2025-07-24 → 2026-07-23 — the #338-corrected,
runtime-parity-valid market-condition tagging). This is the **full available
canonical-quality range** for this study, not a narrowed window — it is
exactly the same range PR #352's own raw-trades substrate covers (verified:
first/last dates match to the day), so using it is not a reduction from
Pass 1's own evidence base. A secondary, older 15m cache
(`data/replay_polygon`, 622 files, 2024-07-01→2026-06-26) exists but predates
the #338 market-condition parity fix and carries no corrected tag — **not
used**, since this study needs to audit the TRENDING gate, which depends
directly on `market_condition` accuracy.

5-minute data (`data/replay_polygon_5m`) may be used as a **secondary
diagnostic only** — MFE/path detail on individual trades — never for signal
detection, matching Pass 1's own precedent and the confirmed fact that this
strategy's trigger is fully causal at 15m bar-close.

**Window will not change after seeing results.**

## 5. Primary pass criteria

All of the following, evaluated on the **filled population** (§7):

- Positive net P&L after costs (commission + slippage)
- Profit factor > 1.2
- Positive expectancy per fill
- Both H1 (2025-07-24→2026-01-23) and H2 (2026-01-24→2026-07-23) positive
- No single calendar month contributing a majority of net P&L
- Realistic fill model (satisfied by construction — ioc_limit, §3)
- No lookahead (satisfied by construction — all V4-R fields are causal
  transitions over bars strictly before the trigger, §2)
- Max drawdown acceptable relative to `max_stop_ticks`-constrained account
  architecture (reported, not an independent numeric threshold — judged in
  context like every other strategy this session)

**Sample-size tiers** (evaluated on resolved/filled trades):
- **n < 30**: automatically capped at PROMISING BUT UNPROVEN at best,
  regardless of how strong the other metrics look.
- **n = 30–49**: still weak evidence even if all criteria pass.
- **n ≥ 50**: eligible for stronger consideration, conditional on every
  other criterion also passing.

**V4-R will not be promoted to VALIDATED from this single historical corpus
under any outcome.** At best this pass produces PROMISING BUT UNPROVEN
pending forward paper evidence, consistent with every other strategy's
promotion path in this repo.

## 6. Scope — both rule-anatomy and full executable-system audit

### 6.1 Rule-anatomy question
- Reproduce V4-original's Pass 1 numbers exactly (porting-fidelity check) to
  confirm this study's re-implementation of the shared primitives
  (`orb_status`, session, bracket math) is faithful before trusting V4-R's
  own numbers.
- Report V4-R as its own, distinct population (§0) — never conflated with or
  silently substituted for V4-original.

### 6.2 Executable-system audit
Run V4-R's raw detector population through the real
`ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker` path, isolated
(`enabled_concepts=["orb_reclaim"]` only, both MNQ and MES — both
confirmed `PAPER_ELIGIBLE` and enabled in `risk_rules.yaml` for this
strategy). Audit, per candidate, exactly which gate (if any) stops it:

- `max_stop_ticks` (RiskEngine)
- `min_confluence_grade` (RiskEngine)
- `min_target_points` (signal + risk layer — `orb_reclaim` is NOT known to
  be carved out of either enforcement point, unlike `strat_322_first_live`)
- `min_rr_ratio` (signal layer, `risk_reward.min_rr_ratio: 2.0`)
- `ENTRY_DETACHED_FROM_PRICE` (signal layer — `orb_reclaim` is explicitly
  NOT carved out per the guard's own comment: "its detachment question
  belongs to the entry-refresh shadow lane")
- `MARKET_CONDITION_NOT_TRADABLE` (CHOPPY/DEAD veto)
- `MARKET_CONDITION_NOT_TRENDING` (`require_trending_condition` — `orb_reclaim`
  is NOT in `_TRENDING_GATE_EXEMPT`, so this applies at full strength)
- `require_strong_trend` / EMA-stack-adjacent quality gates
- `min_signal_bar_volume` (if reached inline for this strategy — confirmed
  not referenced directly inside `_try_orb_reclaim`, audited anyway since
  it's evaluated earlier in `evaluate()` regardless of which strategy is
  being confirmed)
- Session restrictions, fill realism, same-bar ambiguity (own IOC-limit
  entry vs. stop/target same-bar pessimistic handling)

**Decision rule, same as established this session:** a gate with no basis in
`orb_reclaim`'s own canonical rules/detector/documented pattern that
materially removes V4-R's evidenced candidates is reported as a parity
defect candidate — never silently fixed. An explicit, pre-existing
account-risk control (`max_stop_ticks`, `min_confluence_grade`, and any
other genuine risk-architecture rule) is preserved unless independently
disproven by the same audit standard used for every prior gate this session
(grep the strategy's own rules/detector for the concept; if present,
preserve; if absent AND material, flag and stop for authorization before
touching any runtime file).

**No engine, config, signal, or risk file is modified during this evidence
run.** Any fix, if one is ever authorized, happens in a separate PR after
this report is reviewed — exactly the Miyagi/3-2-2 precedent.

## 7. Required comparison — three populations, reported separately

| Population | What it answers |
|---|---|
| **Raw V4-R detector population** | Does the rule itself have edge, before any runtime gate is applied? |
| **Runtime-filtered population** | What would the real system actually allow through (reaches `RiskEngine`, approved or rejected there)? |
| **Filled population** | What would really reach execution (approved + IOC-limit fill achieved)? |

Each population reported for:
- Full period, H1, H2
- Calendar-year / monthly concentration (top-N winner share, worst month)
- MNQ vs. MES (if both remain legitimately populated after gates)
- LONG vs. SHORT (N/A — LONG-only by definition, noted not omitted)

**All three populations compared against both**:
- **V4-original** (Pass 1's `NY + true_reclaim`, reproduced fresh on this
  corpus for a fair like-for-like comparison, not just quoted from Pass 1)
- **Current production `orb_reclaim`** (first-cross, no session/rejection
  restriction) — same corpus, same three-population breakdown, so this
  study also produces the first real full-engine audit of the strategy AS
  DEPLOYED TODAY, not just the V4-R candidate.

## 8. Reproduction plan

Scripts to be written (research-only, this branch,
`claude/orb-reclaim-v4r-study`):
- `scripts/orb_reclaim_v4r_detector.py` — raw-population detector (both
  V4-original and V4-R, plus unrestricted first-cross, all off the same
  corpus read).
- `scripts/orb_reclaim_v4r_runtime_audit.py` — full-engine isolated replay,
  per-candidate gate classification (mirrors
  `scripts/strat_322_parity_validation.py`'s trigger-bar-anchored method,
  adapted for a 15m-native, non-day-scoped, multi-attempt-per-day strategy).
- Results committed as JSON artifacts alongside the final report, matching
  the Miyagi/3-2-2 evidence-closure pattern.

## 9. Freeze

This document is the complete, frozen specification. The study now proceeds
exactly as written. Any material discrepancy found mid-study (e.g., a
population that doesn't reproduce, a corpus gap, a gate whose behavior
doesn't match this document's description) gets reported as a deviation
before the affected part of the study continues — not silently patched into
alignment with this document after the fact.
