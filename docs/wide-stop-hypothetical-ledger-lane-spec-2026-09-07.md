# Wide-stop family — hypothetical-ledger forward paper lane (lane config spec)

Status: **APPROVED 2026-09-07 — operator approved D1–D7 as recommended (see §7). Build authorized per §8; nothing is built yet.**

Parent decision: Option B+ in
`docs/wide-stop-day-strategy-policy-options-2026-09-07.md` (decided 2026-09-07).
Inventory: Build Queue item 10 / Pending Research "Hypothetical-ledger forward
paper lane (B+)" in `docs/strategy-rules/Strategy_Inventory.md`.

This document fixes the lane's configuration before any code exists, so the
lane's forward record is pre-registered rather than tuned after the fact. It
changes no strategy, risk rule, fill model, or deployment. The global
`max_stop_ticks: {MNQ: 120}` and `min_rr_ratio: 2.0` are untouched by design.

---

## 1. Purpose (and what it is not)

The lane produces a **forward IOC-real record** for the three wide-stop day
strategies that the edge-decomposition audit (#483) found to have a real
directional signal and a bracket that keeps it, but which the $1,500 account's
stop cap and R:R floor reject 95–100% of the time. Re-open criterion 2 of the
B+ memo — ≥ 40 IOC-filled forward 4HR trades, both halves positive, top-3-month
concentration < 60% of net — can only be satisfied by such a lane.

It is **not** a promotion path. No result from this lane makes any member
`PAPER_ELIGIBLE` on the real book. If real equity ever crosses the parked
thresholds ($4,000 / $6,000), the question is re-opened by the memo's
criterion 1 and decided then, with this lane's record as evidence, not as
authorization.

---

## 2. Ledger

| Ledger | Members | Starting balance (hypothetical) | Family stop cap | Per-contract worst case at cap |
|---|---|---:|---:|---:|
| `wide_stop_4k` | 4HR MNQ | $4,000 | 400 ticks ($200) | $200 |
| `wide_stop_6k` | 3-2-2 MNQ; Miyagi MNQ (shadow member) | $6,000 | 600 ticks ($300) | $300 |

Two ledgers, not one, because the two caps are the 5%-rule thresholds for two
different equity levels and blending them would hide which threshold is being
tested. Each ledger is its own `PaperBroker` instance with its own balance,
peak, drawdown, daily state, and journal directory. The real $1,500 book's
`PaperBroker`, journal, daily state, and breakers are never read or written by
the lane.

Every journal row, report line, and dashboard cell the lane emits carries the
literal label **`hypothetical_ledger`** and the ledger name. No aggregation,
notification, or daily-check summary may add lane P&L to the real book's P&L.

---

## 3. Membership and per-member rules

| Member | Role | Stop cap | R:R floor | Other gates | Evidence for the cell |
|---|---|---:|---:|---|---|
| 4HR MNQ (`strat_4hr_retrigger`) | **fill-eligible** | 400 ticks | **≥ 1.0** | all existing confluence / trending / session gates unchanged | 36 admitted, +$3,077 bracket, PF 3.13, H1 +$1,434 / H2 +$1,643, 2 losses > $150 (memo §"What a family-specific cap would admit") |
| 3-2-2 MNQ (`strat_322`) | **fill-eligible** | 600 ticks | **none** (median R:R 0.24; the floor cannot be kept in any form) | existing confluence / trending gates unchanged | 24 admitted, +$1,790 bracket, PF 9.9 — a 1-loss artifact until ≥ 5 losses are observed |
| Miyagi MNQ | **shadow member only** | 600 ticks (for the journal's admissibility flag) | none | — | n=8 supports no cell; 6/8 also fail `NOT_TRENDING`. Additionally `strat_12hr_miyagi` is a research detector only (`research/detector_12hr_miyagi.py`) and is not wired into `signal_engine.py`, so the lane **cannot** fill it. It is journaled by the shadow path with the family caps recorded; nothing more |

Rules that apply to both fill-eligible members and are **not** operator
decision items (they follow from the memo or from the existing frozen lane
contract):

- **1 contract, always.** Dynamic sizing is recorded as a diagnostic only, as
  in the inverse ORB lane (`context/mnq_orb_breakout_inverse_paper.py`).
- **Entry:** production `ioc_limit`, the completed decision-bar close as the
  current market, one adverse tick frozen, pessimistic same-bar handling. The
  reference price is the **decision-bar close**, never the arrival bar's close
  — the VWAP Hold reconciliation (`docs/vwap-hold-reconciliation-2026-09-07.md`)
  showed the arrival-close reference is a 5-minute look-ahead that flipped a
  cell's sign. `scripts/vwap_hold_evidence_package.assert_decision_time_reference`
  is the offline check for this.
- **Exit:** the strategy's documented static bracket. No runner, no breakeven
  transform, no time-exit substitution.
- **Costs:** $1.24 round-turn commission plus the frozen slippage, at the
  metrics layer, exactly as the audit's stage E.
- **Candidate flow:** the lane consumes the *same* candidates the global
  `RiskEngine` sees and evaluates them under a lane-scoped copy of the risk
  rules with the two family overrides above. It never modifies the global
  `RiskEngine`, its rules, or its decision on the real book. A candidate that
  the global engine rejects for any reason *other than* `max_stop_ticks` /
  `min_rr_ratio` is rejected by the lane too (that is what "all existing gates
  unchanged" means).

---

## 4. Lane-scoped risk controls

| Control | `wide_stop_4k` | `wide_stop_6k` | Basis |
|---|---:|---:|---|
| `max_stop_ticks` (MNQ) | 400 | 600 | memo cells |
| `min_rr_ratio` | 1.0 | 0 (disabled) | memo cells |
| Daily loss floor | **$400** (10%) | **$600** (10%) | 2 × worst-case stop, so one max-loss does not halt the day — see decision item D2 |
| Max drawdown from peak | 20% ($800 / $1,200) | 20% | same survival floor as the real book (`max_drawdown_percent: 0.20`) |
| Contracts | 1 | 1 | fixed |
| Loss-streak / cooldown | as global | as global | unchanged |

When a lane floor trips, the lane halts **itself only**. It does not signal
the real book's breakers and the real book's breakers do not halt the lane.

---

## 5. Activation, isolation, and pins

Mirrors the inverse ORB lane's contract
(`docs/strategy-rules/MNQ_ORB_BREAKOUT_INVERSE_PAPER_BUILD_2026-07-27.md`):

- `WIDE_STOP_LEDGER_MODE=observe_only|paper_sim` (default `observe_only`; no
  demo or live value exists; invalid values fail closed at startup).
- `WIDE_STOP_LEDGER_EPOCH_START=<ISO-8601>` required when `paper_sim`; balances,
  peaks, and daily state are reconstructed from that boundary.
- `ops/live_box_guard.py` and the release manifest classify both variables as
  proof-critical; a demo release must pin
  `EXPECTED_PROOF_WIDE_STOP_LEDGER_MODE` and
  `EXPECTED_PROOF_WIDE_STOP_LEDGER_EPOCH_START`.
- The lane's journals live under their own root
  (proposed `logs/hypothetical_ledger/<ledger>/`), never under the real book's
  journal root.
- `ops/project_check/daily.py` reports the lane in a separate section labeled
  hypothetical, and treats "a fill in the lane for a strategy whose inventory
  verdict is PARKED" as expected, not as drift. (The current daily-check
  drift rule flags paper-enabled BROKEN concepts; the parked family is
  `BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS … PARKED`, so the exemption
  must be explicit and tested, the same way `derived_lane_source_notes` was.)
- The inverse ORB lane keeps running unchanged; the two lanes are independent.

---

## 6. Pre-registered evaluation

**Offline expectation, computed before the lane starts (build step 1):** replay
the 36-candidate 4HR cell and the 24-candidate 3-2-2 cell through production
`ioc_limit` at the frozen slippage, so the lane has a written expectation to be
measured against. The audit's un-capped 4HR IOC result (44/81 fills,
+$1,731.86, PF 2.00) is the closest existing number and is **not** the
capped-cell expectation.

**Build precondition, added 2026-09-08 — the lane must not fill into an invalid
bracket.** `PaperBroker` rejects an entry that fills beyond its own stop or
target (`ENTRY_BRACKET_INVALID_AT_FILL`, `execution/paper_broker.py:395-398`)
on its `stop_market` path **only**; its `ioc_limit` path applies no such check.
The marketable tolerance bounds only the *adverse* side, so a plan level that
has gone stale fills at the market on the favourable side, and once that
distance exceeds the stop distance the static stop sits between the fill and
the target — the position then "stops out" in profit and its label contradicts
its P&L. Measured for this family at the D4 contract
(`docs/ioc-limit-bracket-guard-readacross-2026-09-08.md`): **4HR 5 of 43 IOC
fills invalid at fill (11.6%), +$175.60 of phantom P&L, of which 2 fills and
+$129.54 fall inside the D3-approved cell — ~4% of its +$3,077**; 3-2-2 and
Miyagi are clean at 8 ticks. The evidence this spec rests on is *not* affected,
because `scripts/edge_decomposition_audit.py` applies its own
`_bracket_valid_at_fill`; the exposure is at runtime, where the lane would book
fills the offline expectation above excludes.

**Refined 2026-09-08 after measuring the blast radius — this is a harness
requirement, not a broker change.** The live path is already protected one layer
up: `strategy/signal_engine.py:1517` rejects a candidate as
`ENTRY_DETACHED_FROM_PRICE` when its stop and target no longer straddle the
live price, which is the same defect guarded at the signal layer. A survey of
18,723 recorded replay trades (6,845 of them `ioc_limit`) found **zero**
invalid-at-fill positions, with fill deviation capping at exactly the
configured entry tolerance — the signature of that gate rejecting detached
candidates upstream. The exposure is confined to harnesses that fill stored
candidates *outside* the signal engine and so never apply it.

The build must therefore, before the lane is enabled:

1. **assert bracket validity at fill in the build-step-1 replay**, and report
   what it rejected — that replay is exactly such a harness, and is where this
   lane would otherwise manufacture the artifact; and
2. keep the lane's runtime submission inside the signal engine's straddle
   check, claiming **no carve-out** analogous to `proof_market_entry_active`.
   If a carve-out ever becomes necessary, the broker-layer guard becomes
   necessary with it and this precondition must be re-opened.

The offline expectation and the forward record must be produced under the
*same* rule, and the checkpoint below must report invalid-at-fill occurrences
as their own line rather than blending them into net. This is a correctness
precondition, not a sizing question, and it does not change D4: the tolerance
value is not what governs it.

**Forward review checkpoint:** the earlier of **2026-12-07** or **40
IOC-filled 4HR trades** in `wide_stop_4k`. At that point report, per ledger:

- fills / armed, net after costs, PF, max drawdown vs the hypothetical balance;
- H1 / H2 split by fill date, top-3-month share of net;
- every candidate the lane admitted that the global engine rejected, with the
  rejecting gate, so the family cap's marginal contribution stays auditable;
- for 3-2-2: losses observed (the PF 9.9 cell is not evidence until ≥ 5);
- **invalid-at-fill occurrences** (added 2026-09-08): fills whose bracket was
  invalid at the fill price, reported as their own count and P&L line and never
  blended into net — see the build precondition above.

**Re-open trigger (memo criterion 2, unchanged):** ≥ 40 IOC-filled 4HR trades,
both halves positive, top-3-month concentration < 60% of net. Meeting it
re-opens the *policy question*; it does not promote anything.

**Stop-the-lane triggers:** either ledger hits its 20% drawdown floor; or the
checkpoint shows 4HR with both halves negative on ≥ 40 fills. Either outcome
is recorded in the inventory as evidence against the family at the tested
sizing, not silently reset.

---

## 7. Operator decision items

**Operator decision (2026-09-07): all seven approved as recommended.** The
"Recommendation" column below is therefore the approved configuration; the
"Alternative" column is recorded only so the road not taken stays visible.
D2's 10% daily floor is approved as deliberately looser, as a share of
equity, than the real book's floor: the lane exists to test the family at a
size where its stops are affordable, and a floor that trips after one normal
loss would only reproduce the $1,500 result on a bigger number.

| # | Decision | **Approved** (was: recommendation) | Alternative (not taken) |
|---|---|---|---|
| D1 | Two ledgers ($4k / $6k) vs one $6k ledger holding all three | **Two** — keeps the 5% thresholds separately testable | One $6k ledger; 4HR then runs with 5% = $300 headroom, which is looser than the memo's 4HR cell |
| D2 | Daily loss floor per ledger | **2 × worst-case stop** ($400 / $600 = 10%) | 1 × worst-case ($200 / $300 = 5%): one max-loss halts the day, which is the same failure mode B+ was avoiding; or no daily floor, drawdown floor only |
| D3 | 4HR R:R floor | **≥ 1.0** (36-trade cell, PF 3.13, both halves) | none (63-trade cell, +$3,111, PF 2.22, 4 losses > $150) |
| D4 | IOC marketable tolerance | **8 ticks**, matching the inverse ORB lane's frozen contract — unchanged 2026-09-08; the tolerance is not what governs the invalid-bracket exposure in §6, since it bounds only the adverse side | the audit's 1/2/3-tick sensitivity ladder run as a diagnostic alongside |
| D5 | Miyagi | **shadow member only** (cannot fill; not wired) | exclude entirely from the lane's journals |
| D6 | Session restriction | **none beyond each strategy's own documented session rules** | NY-only, which none of the three strategies' rules specify |
| D7 | Start date | **first demo release after the build merges**, epoch = that release's timestamp | backfill from a past epoch (rejected: a backfilled "forward" record is not forward) |

---

## 8. What the build will touch (for scoping, not authorization)

- New `context/wide_stop_ledger_paper.py` (mode, epoch, membership, family
  caps, ledger routing) modeled on the inverse ORB context module.
- Lane-scoped `RiskEngine` instantiation with an overlaid rules dict; **no
  change** to `risk/risk_engine.py` gate semantics or to `risk_rules.yaml`
  beyond a comment pointing at this spec.
- `config/settings.py` validation for the two env vars (fail closed), and the
  proof-critical pins in `ops/live_box_guard.py` / release manifest.
- Journal root and daily-check section per §5, with the parked-family
  exemption and its tests.
- Tests: mode parsing and fail-closed; one 4HR candidate at the median 254-tick stop with
  R:R between 1.0 and 2.0 is admitted by `wide_stop_4k` and rejected by the
  real book; a 3-2-2 candidate at 700 ticks is rejected by both; ledger
  isolation (real-book balance unchanged after a lane fill); daily-check
  labeling; look-ahead check on the fill reference.

## 9. Non-authorization

This spec does not authorize tuning any strategy, loosening any global risk
rule, external broker routing, live trading, or promotion of any member. It
authorized nothing until the operator answered §7 (done 2026-09-07), and now
authorizes only the paper lane described here.
