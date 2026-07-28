# MNQ/MES 4HR Re-Trigger — executable-parity audit closure

## Verdict

**MNQ: BROKEN for current executable form.** **MES: BROKEN / WAIT.**

4HR Re-Trigger was chosen as the "reference strategy" for this audit precisely because it
was the strongest-looking surviving lane (#334: MNQ n=80, net +$3,069.60, PF 1.774, both
walk-forward halves positive, paper-forward active since #335). Running its exact known
candidates through the real `ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker`
path — including a hypothetical ceiling pass with every candidate parity-defect gate
removed — collapses MNQ to **1 real fill out of 81 known candidates (1.2%)**, identical to
the unmodified baseline. MES recovers a modest population (12/76, up from 7/76) but fails
this project's own frozen H2-negative bar. Neither number changes with a parity patch: the
gates actually blocking both instruments are legitimate, already-correct risk/quality
controls, not the parity-defect class found in the Miyagi/3-2-2 audits. **No runtime code
change is proposed or justified by this evidence. Evidence/research only.**

## Why this effort exists

Per operator instruction (2026-07-27): after the strategy-inventory reconciliation (#369),
take the strongest surviving paper-forward lane and prove the executable system trades it
the way #334's evidence claims, the same standard already applied to Miyagi (#366) and
3-2-2 (#367). 4HR was picked specifically because it looked like the one lane that might
NOT need a parity patch — the audit was designed to find out either way.

## Method — three passes

### Pass 1 — real-engine baseline (`scripts/four_hr_retrigger_parity_audit.py`)

Isolated (`enabled_concepts=["strat_4hr_retrigger"]` only, both instruments enabled for
evidence purposes — production excludes MES), **continuous** multi-day replay per
instrument (one persistent `ReplayEngine` processes all 621 corpus files in order — 4HR's
reference-day logic spans day boundaries, so day-isolated replay would break it).
`entry_fill_model="market"` — matches both #334's own assumption and the config production
default (unlike Miyagi/3-2-2, which needed `ioc_limit`). Corpus: `data/replay_polygon_5m`
(#334's own raw source, 2024-07-02..2026-06-26) regenerated offline (no network) through
today's `scripts/polygon_to_replay.py::derive_candles()` to backfill the
`reconstructed_market_condition`/`market_condition_status` fields #334's original corpus
predates — gitignored, not committed (`data/replay_corpus_v1_5m_4hr_audit/`).

**Result: 157/157 known candidates (81 MNQ / 76 MES) accounted for — 0 missing, 0 extra, 0
direction mismatches vs #334.** Full detail:
`scripts/four_hr_retrigger_parity_audit_results.json`.

| Gate | Count | % of 157 |
|---|---:|---:|
| `MARKET_CONDITION_NOT_TRENDING` | 80 | 51% |
| `RR_BELOW_MINIMUM` | 39 | 25% |
| `ENTRY_DETACHED_FROM_PRICE` | 13 | 8% |
| `stop_too_wide` | 13 | 8% |
| **`REACHED_RISK_APPROVED`** | **8** | **5%** |
| `target_too_close` | 2 | 1% |
| `EMA_STACK_NOT_ALIGNED` | 2 | 1% |

**By instrument: MNQ 1/81 (1.2%) reaches a real fill; MES 7/76 (9.2%).** MNQ's evidenced
population (n=80, net +$3,069.60) collapses to a single trade (SHORT, 2025-09-24, WIN, net
$102.02). The 8 survivors match #334's own direction/result/P&L almost exactly (off only by
the $1.48 commission #334 already subtracts) — engine mechanics are faithful on what does
clear every gate; the defect is upstream, in which candidates ever reach the engine's
approval path.

Grep-confirmed (same standard as Miyagi/3-2-2): zero rules-doc/detector/#334-study basis for
TRENDING/RR/EMA-stack on `strat_4hr_retrigger`; it is not on `signal_engine.py`'s
`_TRENDING_GATE_EXEMPT` (only `strat_322_first_live` is, on `main`). `stop_too_wide` (169-323
ticks vs 120-tick MNQ cap) judged legitimate/preserved — same family as the
correctly-preserved `max_stop_ticks` finding for Miyagi/3-2-2. `ENTRY_DETACHED_FROM_PRICE`
judged legitimate for 4HR specifically, unlike 3-2-2 — that gate assumes market-order fill
semantics, and 4HR's `entry_fill_model` genuinely is `"market"` (3-2-2 used `ioc_limit`,
which is exactly why the same gate was a confirmed defect there).

### Pass 2 — hypothetical ceiling pass (`scripts/four_hr_retrigger_ceiling_pass.py`)

Operator-authorized in-process monkeypatch, mirroring [PR #365](https://github.com/imanisworld/autonomous-futures-system/pull/365)'s
already-tested exemption pattern: exempt `strat_4hr_retrigger` from exactly
`MARKET_CONDITION_NOT_TRENDING`, `RR_BELOW_MINIMUM`, `EMA_STACK_NOT_ALIGNED`. Preserve
everything else (`max_stop_ticks`/`stop_too_wide`, `ENTRY_DETACHED_FROM_PRICE`,
`target_too_close`, `min_confluence_grade`, session filters, detector logic, fill model,
commission/slippage, pessimistic same-bar handling). `STRONG_TREND_GATE_EXEMPT` deliberately
left untouched — operator's exact spec named only three gates, and it had 0 hits in the
baseline (later shown to be a masking artifact, not inertness — see below).

Run from a worktree of `claude/paper-execution-parity-fixes` (PR #365) so the real,
already-tested exemption machinery is reused verbatim — three existing frozenset class
attributes widened at runtime to include `strat_4hr_retrigger`; zero method-body changes,
zero committed file changes to `main`.

A smoke test surfaced that `TREND_STRENGTH_BELOW_REQUIRED` (STRONG-trend) — 0 hits in the
Pass 1 baseline — was not actually inert: `evaluate()` checks gates sequentially with early
returns, and TRENDING (checked first) always intercepted candidates before STRONG-trend
could ever fire. A companion offline script
(`scripts/four_hr_retrigger_gate_overlap_offline.py`, pure Python, no engine, independently
re-derives TRENDING/STRONG-trend/EMA-stack/RR at each trigger bar from corpus fields) later
confirmed all 80 baseline TRENDING-blocked candidates also hard-fail STRONG-trend.

**First run of the ceiling pass was invalid and discarded as decision evidence.**
`_MIN_RR_GATE_EXEMPT` exists as two independent class attributes —
`DecisionEngine._MIN_RR_GATE_EXEMPT` (signal layer, checked first) and
`RiskEngine._MIN_RR_GATE_EXEMPT` (risk layer, checked second) — PR #365's own code
deliberately keeps them separate to avoid a circular import. Only the risk-layer one was
patched on the first attempt; per-candidate diff against the baseline showed all 23
RR-blocked MNQ candidates unchanged, proving the counterfactual was never actually
exercised. Fixed (both attributes patched, membership asserted pre/post) and smoke-tested
before rerunning.

**Corrected run — binding acceptance check passed.** Summary counter:
`MARKET_CONDITION_NOT_TRENDING=0`, `RR_BELOW_MINIMUM=0`, `EMA_STACK_NOT_ALIGNED=0`. 157/157
candidates classified, 0 extra, 0 missing, 0 direction mismatches. This is the authoritative
result below. Full detail: `scripts/four_hr_retrigger_ceiling_pass_results.json`.

## Result

### MNQ (n=81) — **BROKEN for current executable form**

**1/81 fills (1.2%) — unchanged from the Pass 1 baseline.** Exempting all three candidate
parity-defect gates rescued zero additional MNQ trades; the single fill is the same
2025-09-24 SHORT (+$103.50 gross vs #334's +$102.02 net) — not a new candidate.

| Gate (ceiling pass) | Count |
|---|---:|
| `TREND_STRENGTH_BELOW_REQUIRED` | 37 |
| `stop_too_wide` | 31 |
| `ENTRY_DETACHED_FROM_PRICE` | 8 |
| `target_too_close` | 4 |
| **`REACHED_RISK_APPROVED` (filled)** | **1** |

All four blocking gates are preserved/legitimate — none were exempted. Gate-overlap
cross-check (offline model): of the 80 TRENDING-blocked candidates, 0 fail TRENDING or
EMA-stack in isolation (they always co-fail with something else); 31/81 fail RR only; 30
fail all three exempted gates simultaneously — moot, since the real bottleneck sits
downstream of all three, in STRONG-trend and the stop cap.

**Decision: population still tiny because legitimate gates dominate. No parity patch
justified — extending PR #365's exemption pattern to `strat_4hr_retrigger` would not change
MNQ's real executable population (1 -> 1).**

### MES (n=76) — **BROKEN / WAIT**

**12/76 fills (15.8%)**, up from the Pass 1 baseline's 7/76 — a real rescue. 6W/6L, net
**+$381.25**, PF **1.854**, avg trade $31.77, max drawdown $376.25 (thin sample, single-DD
dominated). Direction split: LONG n=6/3W/+$68.75, SHORT n=6/3W/+$312.50 (same
winner-concentration pattern already flagged for MES 1-2-2). **H1 net +$655.00 (n=6) vs H2
net -$273.75 (n=6)** — fails the same frozen H2-negative criterion the ORB Reclaim V4-R
study (#368) failed on.

One known candidate (2025-05-26) reached risk-approval but never filled
(`EOD_BAR_MISSING`) — see the flagged correction below; it does not change this count or
verdict.

**Decision: population returns under the hypothetical exemption, but economics collapse in
H2 and n=12 is too thin to trust the positive PF regardless. BROKEN/WAIT — not promotable,
and the same "no proof, no patch" logic applies: rescuing 5 more candidates does not clear
this project's own bar for a real strategy.**

## Flagged, deliberately out of scope: `EOD_BAR_MISSING` on 2025-05-26 is a session-calendar
defect, not 4HR evidence

The one MES candidate that reached risk-approval but never filled (2025-05-26) is **not** a
generic corpus/data artifact. 2025-05-26 is Memorial Day — a known early-close holiday
session — and the shared day-only-exit policy
(`execution/day_only_exit.py`) assumes a normal full-length session ending at the standard
4:00 PM ET close. On an early-close day the expected EOD bar never arrives, so the day-only
flatten logic has nothing to trigger on and the position is left open at file end
(consistent with what the corrected ceiling-pass log captured: *"day-only strategy
strat_4hr_retrigger (MES) still open at 2025-05-26 file end -- day-only-flatten logic
failed"*). A quick corpus sanity check corroborates this: 2025-05-26 has 228 five-minute
bars vs 276 on an adjacent normal trading day — consistent with a shortened session, not
missing data.

This is a **separate, unresolved session-calendar/EOD-policy defect** — shared
infrastructure (`day_only_exit.py`), not specific to 4HR, and it affects any strategy whose
candidate happens to trigger on a known early-close date. It does not change the MNQ or MES
verdict above (MNQ is already 1/81 regardless; MES's one excluded candidate was already
excluded in #334's own known-results file, so the population count is unaffected either
way). **Not fixed here — do not conflate with the 4HR strategy-viability question.** A
session-aware day-only exit (recognizing the CME/CBOT holiday-early-close calendar) is a
distinct, separately-scoped piece of work, not authorized as part of this closure.

## Bottom line vs Miyagi (#366) / 3-2-2 (#367)

Unlike those two audits — where the same class of zero-basis gates *was* the load-bearing
defect and removing it rescued a real population — for MNQ 4HR, removing all three
candidate-defect gates changed nothing. The real killers (STRONG-trend, the stop cap) are
legitimate and correctly preserved. **No committed patch is justified for either
instrument.** #334's evidenced MNQ population (n=80, +$3,069.60) essentially never reaches
the real pipeline, and this is a genuine strategy/gate-fit problem, not a parity bug — the
strategy chosen specifically to be "the reference strategy that proves the system trades
things correctly" instead demonstrates that the strongest-looking surviving lane does not
survive contact with the real engine.

## Runtime impact

**None.** `strategy/four_hr_retrigger.py`, `strategy/signal_engine.py`, and
`risk/risk_engine.py` are untouched by this PR — the ceiling pass is an in-process
monkeypatch that only ever ran inside a temporary worktree, never committed to any branch
that deploys. MNQ 4HR stays paper-forward deployed exactly as-is (#335), unaffected by this
audit; MES stays excluded, unaffected. [PR #365](https://github.com/imanisworld/autonomous-futures-system/pull/365)
remains HOLD — this audit gives it no new justification to merge (its only scoped
beneficiaries, `strat_322_first_live`/`strat_12hr_miyagi`, are both already BROKEN; this
audit confirms `strat_4hr_retrigger` would gain nothing from being added to its scope
either).

## Artifacts

- `scripts/four_hr_retrigger_parity_audit.py` + `scripts/four_hr_retrigger_parity_audit_results.json`
  — Pass 1 baseline driver + results
- `scripts/four_hr_retrigger_gate_overlap_offline.py` + `scripts/four_hr_retrigger_gate_overlap_offline_results.json`
  — offline per-candidate gate-overlap attribution (no engine)
- `scripts/four_hr_retrigger_ceiling_pass.py` + `scripts/four_hr_retrigger_ceiling_pass_results.json`
  — Pass 2 corrected, authoritative ceiling-pass driver + results. Requires checking out
  `claude/paper-execution-parity-fixes` (PR #365) to actually run — the script references
  the audit corpus/known-results by absolute path into the main working tree, matching the
  pattern already established by #367's `strat_322_parity_validation.py`.
- Regenerated corpus (`data/replay_corpus_v1_5m_4hr_audit/`) is gitignored, not committed —
  reproducible offline from `data/replay_polygon_5m` via `scripts/polygon_to_replay.py::derive_candles()`.
