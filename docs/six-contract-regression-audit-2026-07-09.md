# 6-Contract Regression Audit — 2026-07-09

## Question being answered

Operator hypothesis: there was a period when MES/MNQ traded 6 contracts and appeared to
work, and a subsequent contract-sizing-related fix/change may have inadvertently altered
signal eligibility, risk validation, stop placement, gate behavior, or fill/cancel logic —
possibly explaining why MNQ has recently stopped qualifying and MES has shown "invalid
setup" behavior.

Constraints honored: no `demo_proof`/`proof_builder` code built, no second demo account
required or referenced, no production behavior changed, no "normal variance" conclusion
without actual before/after distributional evidence.

## Step 1 — did a 6-contract period exist? Yes, confirmed exact dates.

Searched the box's real (non-replay) journal (`/root/afs-shared/logs/journal_2026-*.jsonl`,
36 files, `2026-06-01` through `2026-07-09`) for `"contracts": 6` on `setup`/`outcome` blocks.

Every occurrence falls in a 3-day window:

| Date | Instrument | Decision | Strategy | Result |
|---|---|---|---|---|
| 2026-06-09 12:45–13:30Z | MNQ, MES (×3) | `RISK_REJECTED` (session_trade_limit) | orb_reclaim / orb_breakout | never filled — rejected before sizing mattered |
| 2026-06-10 07:30Z | MES | `TRADE` → `OUTCOME` | vwap_hold | **WIN, +$390.00** (52 ticks × 6c) — the one real 6-contract fill |
| 2026-06-10 15:45Z | MES | `RISK_REJECTED` (max_stop_ticks) | — | never filled |
| 2026-06-11 07:30Z | MNQ | `TRADE` → `OUTCOME` | orb_reclaim | `CANCELLED` — "auto-reconcile: journal showed open but broker is flat (phantom cleared)"; **not a real fill**, a phantom position |

No `contracts: 6` row exists before 2026-06-09 or after 2026-06-11 in any journal file
through 2026-07-09. **Only one 6-contract trade in this system's history actually filled
and resolved: the 2026-06-10 MES vwap_hold win.** The MNQ 6-contract "trade" was a phantom
that got auto-reconciled away with zero P&L. This matches — and is narrower than — the
operator's "appeared to work" recollection: it worked exactly once, by one filled trade.

## Step 2 — root cause: sizing tier computed off the real demo balance, not the paper ladder

Reconstructing balance from journaled `OUTCOME.pnl_dollars` starting at the paper
`starting_balance` (`$1,500`) put the account at **$1,438.25** immediately before the
2026-06-10 6-contract trade — nowhere near the `risk_rules.yaml` `$18,000+` tier that maps
to 6 contracts. That ladder should have produced 1 contract.

The actual mechanism: **2026-06-04 ~09:15 ET, demo execution went active**
(`PAPER_MODE=false`, `BROKER=tradovate`, `TRADOVATE_ENV=demo`) — confirmed via project
memory and `webhook/runner.py:1120-1128`, which sources `account_balance` from
`broker.get_account_balance()` (the **real Tradovate demo account balance**, commonly
funded around $50,000) once a broker is attached, not from the journal-reconstructed paper
balance. A ~$50k demo balance lands squarely in the `$18,000+` → 6-contracts tier of the
existing balance-tiered `sizing_rules` ladder in `risk_rules.yaml` — the ladder itself never
changed; it was simply being fed the wrong balance for a demo/live context.

The same day demo execution went active (2026-06-04), commit `0955f4e` ("fix(exec): accurate
demo/live accounting — 1-contract cap, persist contracts, bracket-matched exit") *added* a
`max_contracts_hard_cap` mechanism (`risk/risk_engine.py:_cap_contracts`, config field +
`MAX_CONTRACTS_HARD_CAP` env override) specifically to clamp demo/live sizing to 1 contract
regardless of the balance-tiered result, while leaving paper sizing free to scale. **The code
landed 2026-06-04, but the env var was never set on the box** — so the cap had zero effect —
until **2026-06-11**, when `MAX_CONTRACTS_HARD_CAP=2` was appended to the box `.env` and the
service restarted (confirmed via project memory). The box's current `.env` now has
`MAX_CONTRACTS_HARD_CAP=1` — tightened further at some point after 06-11 not captured in this
audit's git/journal evidence (memory says the 06-11 value was `2`; live value today is `1`).
This is a **memory staleness correction**, not a new finding requiring action here.

This fully explains the 3-day window: it opens exactly when the sizing-tier ladder started
seeing the real demo balance with no cap yet wired up (06-04 code merged, but inert without
the env var), and closes exactly when the env var was finally set (06-11).

## Step 3 — did the fix touch anything beyond contract count?

Read `risk/risk_engine.py` in full. `_cap_contracts()` is called only from
`recommended_contracts()` (`webhook/runner.py:1128`, `main.py:186`) — applied *after* the
balance-tier lookup, as a pure `min()` clamp on the integer contract count. It has no
interaction with:

- **Signal eligibility** — `strategy/signal_engine.py:_iter_enabled_setups` (the
  `enabled_concepts`/`disabled_concepts_per_instrument` gate) is a fully separate code path,
  never touched by `0955f4e`.
- **Gate/regime/trend evaluation** — `quality_gates`, `require_strong_trend`,
  `allow_moderate_*`, `require_htf_alignment` all live in `strategy/` and `context/`, not
  `risk_engine.py`'s sizing methods.
- **Stop placement** — `_try_orb_reclaim` / `_try_vwap_hold` etc. compute `stop`/`target`
  before `setup.contracts` is even assigned; the cap runs strictly downstream.
- **Fill/cancel logic** — `PaperBroker`/`TradovateBroker` fill resolution takes
  `contracts` as an input for P&L sizing only; `0955f4e`'s `resolve_position` fix (matching
  the exit price to the correct bracket child rather than "last account fill") is a
  **separate, real bug fix bundled in the same commit** — but it changes how an exit is
  *priced*, not whether a signal is *admitted*.
- **Session filters** — `per_session_limits`/session-window logic is evaluated earlier in
  the risk pipeline and is balance/contract-count independent.

**Verified empirically, not just by code-reading**: daily `TRADE`/`NO_TRADE`/`RISK_REJECTED`
decision counts for both instruments show no shift across the 06-11 boundary —

| Date | MES | MNQ |
|---|---|---|
| 06-08 | NO_TRADE 85, TRADE 2 | NO_TRADE 89 |
| 06-09 | NO_TRADE 81, RISK_REJECTED 4, TRADE 1 | NO_TRADE 82, RISK_REJECTED 2, TRADE 1 |
| 06-10 | NO_TRADE 85, TRADE 1, RISK_REJECTED 1 | NO_TRADE 86 |
| **06-11 (cap fix lands)** | NO_TRADE 85, RISK_REJECTED 4 | NO_TRADE 89, TRADE 1 |
| 06-12 | NO_TRADE 78, TRADE 3, RISK_REJECTED 1 | NO_TRADE 78, RISK_REJECTED 1 |

Evaluation volume (78-89/day) and the TRADE/NO_TRADE/RISK_REJECTED mix stay in the same
range before and after 06-11 for both instruments — no gate-distribution shift coincides
with the cap fix. This is the same distributional-proof standard the operator required
elsewhere this session (not "processed roughly the same count," but decision-type mix held
steady too).

## Classification

**`RISK_FIX_ONLY_NO_SIGNAL_EFFECT`**

The 2026-06-04/06-11 hard-cap fix changed contract count only. It did not touch signal
eligibility, gate behavior, stop placement, or session filtering, and the before/after
decision-distribution check above shows no coincident shift in any of those dimensions.

## Does this explain the current (2026-07-08/09) MES "invalid setup" / MNQ
## "nothing qualifies" behavior?

**No.** This incident is temporally and mechanically disconnected from the current concern:

- **Temporal**: the entire 6-contract window and its fix are dated 2026-06-04 to
  2026-06-11 — a full four weeks before this week's MES/MNQ posture questions
  (2026-07-08/09, addressed separately in PR #239's MES narrowing and the
  entry-detached/missed-move/shadow-strategy diagnostic chain, PRs #236-#241).
- **Mechanical**: the fix is a sizing clamp (`_cap_contracts`), and Step 3 shows zero
  interaction with any eligibility, gate, stop, or fill-cancel code path — there is no
  mechanism by which it could produce "invalid setup" or "nothing qualifies" behavior a
  month later.

**If the operator wants the current MES/MNQ posture issue root-caused, that requires a
separate investigation scoped to the actual recent time window** (the last 1-2 weeks) and
recent commits — not this historical incident. This audit closes the 6-contract question on
its own terms; it does not stand in for that separate investigation.

## Scope

Read-only. No `execution/`/`risk/`/`config/`/`webhook/`/`broker*`/`strategy/`/`main.py`/
`risk_rules.yaml` diff — this is a docs-only forensic report built from box SSH journal
reads (`/root/afs-shared/logs/`, real demo journals, not replay data) and `git log`/`git show`
archaeology on already-merged commits. No `proof_builder`/`demo_proof` code. No second demo
account referenced or required. No production behavior changed.
