# Wide-stop day strategies — risk-policy options

Status: **DECIDED 2026-09-07 — Option B+ adopted by the operator.** No config, risk, strategy, or deployment change; `max_stop_ticks: {MNQ: 120}` and `min_rr_ratio: 2.0` stay as they are. The family is recorded in the Strategy Inventory as parked below the equity thresholds, with the pre-registered re-open criteria at the end of this memo. The hypothetical-ledger forward paper lane is a Build Queue item, not yet built.

Scope: 4HR Re-Trigger MNQ, 60M 3-2-2 First Live MNQ, 12HR Miyagi MNQ (causal stop). The 2026-09-07 edge-decomposition audit (#483, verdicts applied in #488) established that these three lanes have real directional signal and a documented bracket that keeps it, and that the account's global `max_stop_ticks: {MNQ: 120}` and `min_rr_ratio: 2.0` reject 95–100% of their candidates — the rejected set *is* the edge. Every number below is from the merged audit artifacts (`scripts/edge_decomposition_audit_results*.json*`), documented-bracket basis, one contract, $1.48 round-turn, 1-tick adverse. IOC fill rates for this family are 54–63% and the filled sets keep the bracket's sign, so bracket figures are the right sizing basis; expect IOC P&L at roughly 55–75% of bracket P&L.

## The constraint in one table

Account $1,500. MNQ tick $0.50. The 120-tick cap is $60/contract = **4.0% of equity** per trade.

| Lane | n | Stop ticks (med / p75 / p90 / max) | $ per contract at stop (med / p75 / p90 / max) | Median R:R | Share ≥ 2.0 R:R | Bracket net / PF / WR | Avg win / avg loss | Worst loss |
|---|---:|---|---|---:|---:|---|---|---:|
| 4HR MNQ | 81 | 254 / 395 / 610 / 1,229 | $127 / $198 / $305 / $614 | 0.94 | 25% | +$3,069.60 / 1.77 / 61% | +$144 / −$128 | −$361 |
| 3-2-2 MNQ | 34 | 472 / 633 / 878 / 1,471 | $236 / $316 / $439 / $736 | 0.24 | 0% (6% ≥ 1.0) | +$2,532.66 / 13.6 / 97% | +$85 / −$201 | −$201 |
| Miyagi MNQ | 8 | 522 / 644 / 754 / 805 | $261 / $322 / $377 / $402 | 0.48 | 0% (0% ≥ 1.0) | +$525.91 / 2.85 / 88% | +$116 / −$284 | −$284 |

Read the median-stop column as a fraction of the $1,500 account: **8.5% (4HR), 16% (3-2-2), 17% (Miyagi)** per contract per trade. The cap is not mis-tuned for these strategies; the strategies are mis-sized for this account.

Two further facts that bind any option:

- **Daily-loss floor.** `max_daily_loss: 150` (× contracts). A single 4HR loss exceeds $150 in 9 of 31 losses (worst −$361); a single 3-2-2 or Miyagi loss does so every time. With the cap lifted and nothing else changed, one normal loss ends the day.
- **Concentration.** 4HR: 83% of net P&L in the top 3 of 23 months; #372's cap-lift pass found 99.8% of profit in two months. 3-2-2's PF 13.6 rests on 32 wins / 1 loss at 97% WR — three more ordinary losers would roughly halve it. Miyagi n=8 supports no conclusion; it is in scope only because it behaves like the family.

## What a family-specific cap would admit (4HR MNQ)

| Family cap (ticks) | $ at cap | Admitted | Bracket net | PF | H1 / H2 | Losses > $150 |
|---:|---:|---:|---:|---:|---|---:|
| 120 (current) | $60 | 6 / 81 | −$111 | 0.00 | — | 0 |
| 200 | $100 | 28 | +$1,204 | — | — | — |
| 300 | $150 | 48 | +$1,653 | — | — | — |
| **400** | **$200** | **63** | **+$3,111** | **2.22** | **+$1,973 / +$1,138** | **4** |
| 400 + R:R ≥ 1.0 | $200 | 36 | +$3,077 | 3.13 | +$1,434 / +$1,643 | 2 |
| 600 | $300 | 72 | +$2,850 | 1.88 | +$1,992 / +$858 | 7 |
| none | — | 81 | +$3,070 | 1.77 | +$1,795 / +$1,275 | 9 |

400 ticks captures essentially all of the bracket P&L with a $200 worst-case per contract; the candidates between 400 and 1,229 ticks contribute nothing net. Adding R:R ≥ 1.0 halves the sample and keeps the P&L (the sub-1.0 R:R trades are net flat), which is the cleanest cell if a floor is wanted at all.

3-2-2 does not have a comparable cell: at 400 ticks it admits 12 (+$492), at 600 ticks 24 (+$1,790, PF 9.9); the R:R floor cannot be kept in any form (median 0.24). Miyagi admits 2 and 5 respectively.

---

## Option A — Family-specific stop and sizing budget

**Rule:** a named "wide-stop day strategy" family with its own per-strategy `max_stop_ticks`, its own R:R treatment, and dollar-risk sizing, run as an **isolated evidence lane** (own paper account, same posture as the inverted ORB lane) — never mixed into the global book.

Concrete parameters the evidence supports:

| Lane | Family cap | R:R floor | Per-contract worst case | Expected (bracket) | Notes |
|---|---:|---:|---:|---|---|
| 4HR MNQ | 400 ticks | 1.0 | $200 | 36 trades, +$3,077, PF 3.1, both halves positive | the only cell with a real sample and both halves |
| 3-2-2 MNQ | 600 ticks | none (replace with the existing confluence/trend gates) | $300 | 24 trades, +$1,790, PF 9.9 | PF is a 1-loss artifact; treat as unproven until ≥ 5 losses have been observed |
| Miyagi MNQ | 600 ticks | none | $300 | 5 trades | not tradeable on evidence; include only as a shadow member of the family |

Sizing: `contracts = floor(risk_budget_$ / (stop_ticks × $0.50))`, minimum 1, where `risk_budget_$ = X% × equity`. What X and equity are needed for the family cap to be affordable at 1 contract:

| Per-trade risk budget | 4HR at $200 worst case | 3-2-2 / Miyagi at $300 worst case |
|---:|---:|---:|
| 5% of equity | equity ≥ **$4,000** | equity ≥ **$6,000** |
| 10% of equity | equity ≥ $2,000 | equity ≥ $3,000 |
| at $1,500 | 13% per trade | 20% per trade |

The daily floor has to move with it: for one loss not to halt the day, `max_daily_loss` for this lane needs to be ≥ the lane's worst-case stop (i.e. $200–$300 at 1c), which at $1,500 is 13–20% of equity in a day — that is why this lane cannot share the $1,500 account's floor and must be its own ledger.

What it costs:
- `RiskEngine` gains per-strategy overrides for `max_stop_ticks` and `min_rr_ratio`, a dollar-risk sizing path, and a lane-scoped daily floor — new gate semantics, new tests, and a `risk_rules.yaml` version bump. The 120-tick cap was confirmed as an intentional account risk control in #366; Option A does not loosen it, it creates a second, separately-funded regime beside it.
- A new isolated paper lane with its own promotion path (forward-measurement gate before any promotion), plus runtime/watcher wiring.
- The honest expected outcome at 1 contract is small in dollars: 4HR ≈ +$3k per ~2 years bracket, ≈ +$1.7–2.3k after IOC, on a ~$4k ledger — about the same magnitude as one bad month's concentration risk.

What it buys: the only positive, both-halves, cost-robust edge in the book gets forward IOC-real evidence instead of sitting rejected; and the sizing question is answered by construction rather than by loosening a global control.

**A is defensible only if a ≥ $4,000 ledger is actually allocated to it.** At $1,500 the arithmetic does not close for any cell: the per-trade risk is 13–20% of equity and the daily floor halts after one loss.

---

## Option B — "Incompatible with this account size": park, shadow, re-open at an equity threshold

**Rule:** keep `max_stop_ticks: 120` and `min_rr_ratio: 2.0` exactly as they are. Record the family as **NOT TRADEABLE BELOW an equity threshold** in the Strategy Inventory, with the threshold derived from the 5% rule on the family cap: **$4,000 for 4HR MNQ, $6,000 for 3-2-2 / Miyagi**. Keep all three in shadow (`SHADOW_ONLY`, observe-only) so candidates keep journaling, and re-open the question when either (a) equity crosses the threshold, or (b) the shadow sample reaches a pre-registered size (4HR n ≥ 120, 3-2-2 n ≥ 60 with ≥ 5 losses observed).

What it costs:
- The edge stays unharvested. At current sizing that forgone edge is worth roughly +$0.8–1.5k/year after IOC at 1 contract — real but small, and smaller than the drawdown a 13–20%-per-trade regime would expose the account to.
- Reaching $4k from $1,500 depends on the rest of the book, which today is one active lane (inverted ORB, PROMISING BUT UNPROVEN). There is no near-term path for the threshold to be crossed organically; B is honestly "parked", not "queued".

What it buys: no code, no new risk semantics, no second regime to reason about; the audit's discipline holds (the risk architecture removed this edge *correctly for this account size*); shadow journaling grows the samples for free, and the concentration question (83% of 4HR P&L in three months) gets more months to resolve itself either way.

**B+ (recommended variant):** B, plus a **hypothetical-ledger forward paper lane** — the family runs in an isolated paper account explicitly labeled as a $4,000 / $6,000 hypothetical ledger, 1 contract, family caps as in Option A, IOC-real fills, no promotion path. It costs only runtime and produces the forward IOC evidence Option A would want, without touching the real account's controls or pretending the $1,500 account can carry the trades. The label matters: every result from that lane is reported as "hypothetical $4k ledger", never blended with the $1,500 book.

---

## Decision (2026-09-07): Option B+

**Option B+ now; Option A only if and when a ≥ $4,000 ledger is deliberately allocated to this family.**

The reason is arithmetic, not evidence quality. The signal is the best in the book, but at $1,500 every cell of Option A puts 13–20% of equity on a single trade and halts the day after one loss — that is not a risk policy, it is the absence of one. B+ keeps the global controls intact, keeps the inventory honest about *why* the family is parked (account size, not strategy failure), and starts the forward IOC-real record the family will need whichever way the equity question resolves.

Pre-registered re-open criteria, so this is not re-litigated ad hoc:
1. equity ≥ $4,000 (4HR) / $6,000 (3-2-2, Miyagi) on the real ledger, **or**
2. hypothetical-ledger forward lane shows ≥ 40 IOC-filled 4HR trades with both halves positive and top-3-month concentration < 60% of net, **or**
3. shadow sample reaches 4HR n ≥ 120 / 3-2-2 n ≥ 60 with ≥ 5 losses.

None of these are met today. Until one is, the inventory verdicts from #488 stand as written.
