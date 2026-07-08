# Options Fixture-Candidate Inventory (Increment 25B, updated 25F, 25G)

Static, hand-authored inventory of every real trade candidate discussed
as a possible scanner-identification proof fixture, tracked via
`options_manager/validation/fixture_status.py`
(`FixtureStatus` / `FixtureCandidate` / `build_fixture_candidate_inventory()`).

This layer answers a question neither `RealSetupFixture` nor
`ManagementCase` answers on its own: *is this real trade candidate usable
as a scanner-identification proof yet, and if not, why not and what would
it take?* A candidate can be a real, profitable, well-managed trade
(`ManagementCase`) without its setup being a usable scanner proof
(`FixtureStatus`) — conflating the two has been the recurring failure
mode this inventory exists to stop.

As of this increment, **no candidate has reached `CLEAN_COMPLETE_FIXTURE`.**

| Ticker | Window | Status | Best future use |
|---|---|---|---|
| HOOD | 2026-06-12/2026-06-15 | `PENDING_PROOF_FIXTURE` | Support-hold continuation scanner-proof fixture, pending a planned-level source |
| EBAY | 2026-04-30/2026-05-01 | `SPECIAL_CASE_FIXTURE` | Management-case test for real-vs-post-exit target hits (corrected by PR #221) |
| AMD | 2026-02-05/2026-02-06 | `SPECIAL_CASE_FIXTURE` | Regression test for premarket-trigger / RTH-already-through-target handling |
| ORCL | 2026-03-23/2026-05-22 | `INCOMPLETE` | 4 reconciled packets (A-D); Packet B is a `MANAGEMENT_CASE` candidate, none is a scanner-proof candidate |
| FITB | 2026-05-04/2026-05-08 | `SPECIAL_CASE_FIXTURE` | Management case for early invalidation-then-recovery decision logic |
| BAC | 2026-05-01/2026-05-04 | `INCOMPLETE` | Broker-verified loser, pending candle reconstruction |
| SPXW | unknown | `SCALP_NOISE` | Not recommended for this proof lane |
| NVDA | 2026-07-07/2026-07-08 | `SCALP_NOISE` | Not pursued for this proof lane |
| NOK | 2026-05-15/2026-06-12 | `MANAGEMENT_CASE` | Scaled-exit-over-weeks test (corrected by PR #221) |
| ADP | 2026-04-29/2026-05-07 | `REJECT` | Reject as described; real 2-contract trade contradicts the claim |
| ARM | 2026-04-30 | `REJECT` | Reject as described; real same-day scalp contradicts the claim |
| QCOM | unknown | `REJECT` | Reject; named $200C contract does not exist in the account |

## Why each status, and what would change it

**HOOD** — fills, P&L, and the full 5-minute candle sequence are all
confirmed, and the recalled 92/95/100 levels line up with the
reconstructed candles almost exactly. What's missing is a *contemporaneous*
source (screenshot, note, alert) proving those levels were the actual plan
rather than a good post-hoc match. That single artifact is the only thing
that promotes this candidate. Note: a separate, unrelated HOOD $70P exp
2026-05-01 trade (BTO 2026-04-30, STC 2026-05-01, net ~-$30) also appears
in the May 2026 broker statement -- it is a distinct position from this
$100C exp 2026-06-18 fixture window and must not be conflated with it.

**EBAY** — fills are fully confirmed and corrected (PR #221). The setup
itself has a ~55-minute whipsaw before the real trigger candle, and
target 2 only confirmed post-market after the position was already
closed. Nothing will resolve this to `CLEAN_COMPLETE_FIXTURE` — the
whipsaw and the post-exit target print are permanent features of the
real trade, not missing data.

**AMD** — fully reconstructed, but the trigger fired premarket while the
official RTH decision bar was already through target. Kept permanently
as a regression fixture for that convention edge case, not a candidate
for promotion.

**ORCL** — real watchlist membership confirmed. Every other claimed
detail (Signa Score, Minervini count, trigger/invalidation/target) has
no corroborating source anywhere in this repo or the account — verified
by direct search (`Minervini` returns zero hits; `Signa` only matches the
substring "signal" in unrelated code). Promotable only if that source is
found. `ORDER_RECONCILED / PACKETS_CLASSIFIED` (Increment 25G): full
Jan-May 2026 order-history pagination separated the account's real ORCL
activity into 4 distinct contracts by expiration/strike/lifecycle:

- **Packet A** — $170C exp 2026-04-10: BTO 2026-03-23 1x $1.52, STC
  2026-03-24 1x $0.81, net -$71/-46.7%. `CLEAN_PACKET` as an order
  lifecycle; a secondary clean-loser candidate, not yet
  candle-reconstructed.
- **Packet B** — $200C exp 2026-05-15: BTO 2026-05-04 09:44 ET 2x $1.87,
  STC #1 2026-05-06 11:51 ET 1x $3.61, STC #2 2026-05-07 09:30 ET 1x
  $5.80, net +$567/+151.6%. Candle-reconstructed: entry rode a genuine
  multi-day underlying rally, both exits sold into strength, and the
  final exit landed in the same 5-minute bar where spot came within
  ~$0.55 of the $200 strike. `MANAGEMENT_CASE` candidate — no proven
  setup, trigger, invalidation, or target source; the value here is the
  scaled-exit-into-strength management pattern, not scanner proof.
- **Packet C** — $210C exp 2026-05-15: three same-day round trips (two
  on 2026-05-11, one on 2026-05-14), each fully flat before the next
  opened. `SCALP_NOISE`.
- **Packet D** — $210C exp 2026-05-22: BTO 2026-05-15 1x $1.80, STC
  2026-05-19 1x $0.48, net -$132/-73.3%. Candle-reconstructed: clean
  single BTO/STC lifecycle, but no recognizable setup, trigger,
  invalidation, or target in the candle data. `INCOMPLETE /
  CLEAN_ORDER_PACKET / CANDLE_RECONSTRUCTED / NO_PROVABLE_SETUP` — a
  clean order packet is not a clean fixture.

None of these four packets promotes ORCL to `CLEAN_COMPLETE_FIXTURE`.

**FITB** — broker-statement confirmed and fully candle-reconstructed
(Increment 25E/25F): FITB $50C exp 2026-06-18, BTO 2026-05-04 ($2.20 +
$2.10), STC 2026-05-08 ($1.75 + $1.76), net -$79.20. Entry was real and
coherent around the $50 level, but the $49.50 invalidation was breached
intraday same-day as entry, then price recovered and produced a +30% MFE
(2026-05-06) before failing again -- exit at roughly -18% blended was a
delayed management decision after a round trip, not a clean immediate
invalidation exit. No target was ever defined. This disqualifies it from
`CLEAN_COMPLETE_FIXTURE`; it is better modeled as a `management_cases.py`
case for early-invalidation-then-recovery decision logic than pursued
further as a scanner-proof fixture.

**BAC** — broker-statement confirmed (Increment 25E): BAC $55C exp
2026-05-08, BTO 2026-05-01 (3x $0.12), STC 2026-05-04 (2x $0.05 + 1x
$0.05), net -$21.28. Real, broker-verified loser, but candle-level
reconstruction (underlying spot/range, MFE/MAE) has not yet been run --
pending the same pass already completed for FITB.

**SPXW / NVDA** — the account's actual trading pattern here is
high-frequency, same-session 0DTE scalping (SPXW: dozens of closing
trades/day; NVDA: a 9-minute 0DTE scalp alongside a same-day-open put
and a ~1-day hold). Structurally not what a trigger→hold→target fixture
needs.

**NOK** — real position confirmed and corrected (PR #221), but the exit
was 3 separate sells over 5 weeks — a management/scale-out lesson, not a
single clean trigger-to-exit event.

**ADP / ARM** — real trades exist in the account, but contradict the
originally described narrative in size, shape, and (for ARM) the sign of
the P&L outcome. See PR #221 and `docs/options-management-case-evidence-audit.md`
for the full reconciliation. Rejected as described; a differently-framed
candidate built from the real trade shape could be considered separately,
but does not exist yet.

**QCOM** — the specific $200C contract named does not exist anywhere in
the account's order history; three unrelated real QCOM trades do.

## Scope note

This inventory is static and hand-authored — nothing here is derived
from a candle fetch, option-chain lookup, broker call, or the scanner
path. Promotions (e.g. HOOD → `CLEAN_COMPLETE_FIXTURE`) happen by editing
the relevant candidate's builder function in `fixture_status.py` directly
once new evidence exists, the same way `ManagementCase.evidence_status`
is a hand-set field rather than an auto-classifier's output.
