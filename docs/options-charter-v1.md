# Options Charter v1 (Increment O-001)

**Status:** DRAFT — awaiting Codex audit and control-room verdict (MERGE /
REWORK / REJECT / HOLD). Docs-only; zero code, config, or runtime
changes. Nothing in this document authorizes any code change by itself —
each later lane still needs its own control-room-authorized increment
packet.

**Base:** `origin/main` @ `8c58aaa0cad459e40b6018b724164e79095dec76`
("Add Historical-Engine Parity build...", PR #298).

**Claim this PR establishes:** a single, settled options doctrine that
every later options increment must inherit, so no later branch can
quietly invent its own numbers or scope. This must merge before any
other options increment in the Lane 1-4 sequence begins.

## 1. Lane architecture

- **`options_manager` is the primary, standalone options system.**
  Every future options increment (truth ledger, behavioral audit,
  strategy edge, provider proof, forward capture) builds toward this
  system's doctrine, thresholds, and evidence bar — not `options_companion`'s.
- **`options_companion` is a separate research experiment tied to
  futures**, not an alternate or fallback options lane. It shares no
  doctrine, no risk config, and no evidence with the standalone system
  by default (see §6).
- **Because futures is frozen (see the standing five-session evaluation
  freeze), the companion cannot become the main options lane** — it
  stays research/observational-scope only for the duration of that
  freeze and is not upgraded, expanded, or promoted as a side effect of
  any options-lane increment. Any future request to promote it is a
  separate, explicit control-room decision, not an implication of
  anything in this charter.
- **No live options execution.** The only sanctioned pipeline for the
  standalone system is: advisory read → manual forward capture
  (`ProofPacket`) → paper simulation lifecycle → evidence review. There
  is no increment in the current sequence (O-001 through O-403) that
  ends in a live order. `options_manager/app.py`'s existing
  `assert_live_options_trading_disabled()` boot-time check remains the
  enforcement mechanism and this charter does not propose changing it.

## 2. Instrument and position scope

- **Calls and puts only.** No spreads, no multi-leg structures, no
  futures options, in this doctrine.
- **Advisory/paper only** — see §1; no exceptions carved out here.
- **One-contract default.** Every `ProofPacket` / contract-quality
  evaluation assumes 1 contract unless a specific packet explicitly
  states and justifies a larger size. This is a documentation default,
  not a new code-enforced ceiling — `ProofPacket.max_contracts` and
  `ContractQualityInput` already carry a size field; this charter fixes
  what a human should default that field to, it does not add a new
  validator.
- **Underlying entry and invalidation remain separate from premium
  controls.** A setup's entry trigger and invalidation level are always
  expressed in underlying price terms (matching `ProofPacket`'s existing
  `entry_trigger` / `underlying_invalidation` fields), never derived from
  or blended with the premium-based `premium_stop`. The two are
  evaluated independently; a contract can fail the premium/liquidity
  side of the gate while the underlying thesis is still intact, and vice
  versa. This is already how `proof_packet.py` and
  `contract_quality_gate.py` are structured today — this charter fixes it
  as doctrine so a future increment doesn't collapse the distinction to
  simplify a form.

## 3. Risk ceilings — reconciled

Three different dollar figures exist in the codebase today and this
charter is the first place that reconciles them explicitly:

| Figure | Where it actually lives | What it is |
|---|---|---|
| $250 | `risk/options_risk_engine.py` (`OptionsRiskConfig.max_premium_per_contract` / `max_total_premium` class defaults) | The shared risk engine's own generic fallback default — not itself a product policy. Any caller that doesn't override it gets $250, which is why it shows up in places that never deliberately chose it. |
| $300 | `options_manager/config.py` (`risk_max_premium=3.00`), `options_manager/models.py` (`max_premium=3.00`), `options_manager/validation/contract_quality_gate.py` (`DEFAULT_MAX_PREMIUM_DOLLARS` / `DEFAULT_MAX_DOLLAR_RISK` = `300.0`) | The standalone system's own, deliberately-chosen figure — consistent across three independent modules. |
| $400 | `options_companion/evaluator.py` (`CompanionConfig.max_premium_per_contract` / `max_total_premium` = `400.0`) | The companion lane's own override, passed into the shared engine at construction time. Scoped to the companion only. |

**Reconciliation (binding):**
- **$300 is the standalone system's preferred maximum risk per trade**
  (premium and dollar-risk cap) — this matches what `options_manager`
  already does in three places; no code change is required for the
  standalone system to comply with this charter.
- **$400 stays a companion-only override.** It is not adopted by the
  standalone system, and companion activity is never blended into or
  counted toward the standalone system's risk ceilings (see §6).
- **$250 is not a doctrine number at all** — it is the shared engine's
  own class default for callers that don't specify one. Any future
  standalone-system caller of `risk/options_risk_engine.py` must pass
  `$300` explicitly rather than relying on that default, so this figure
  stops silently leaking into places nobody deliberately chose it.

## 4. Portfolio-level ceilings (new doctrine, not yet code-enforced)

- **Five concurrent positions, maximum.**
- **$1,000 aggregate open risk, maximum**, across however many of those
  five positions are open at once — this is a portfolio-level ceiling on
  top of the existing $300 per-trade cap, not a replacement for it (five
  positions at the full $300 cap would be $1,500, which this ceiling
  disallows; in practice this means either fewer than five full-sized
  positions open at once, or some positions sized below the $300 cap).
- **This charter documents the rule; it does not implement it.** No
  existing module in `options_manager` currently sums open risk across
  positions — `contract_quality_gate.py` and `risk_gate.py` both
  evaluate one packet/contract at a time. Enforcing this ceiling is
  deferred to a later, explicitly-scoped increment (not part of O-001
  through O-403); until that increment lands, this ceiling is a manual
  discipline a human applies when deciding whether to open a new
  candidate, not a system-enforced gate.

## 5. Primary setup

- **2-1-2 continuation** (`options_manager/strategies/strat_212.py`) is
  the primary setup this charter's downstream lanes are built around.
  This does not exclude other setups from later consideration, but Lane
  2 (strategy edge, O-201/O-202/O-203) targets this one first, and no
  other setup gets preregistered or backtested ahead of it without a
  separate control-room decision.

## 6. DTE policy — reconciled

- **`options_manager`'s existing policy is 14+ DTE preferred**
  (`DEFAULT_MIN_DTE = 14` in `contract_quality_gate.py`, "avoid
  weeklies," override only via an explicit `dte_exceptional=True` flag
  on the packet).
- **`options_companion`'s policy is 0-2 DTE**
  (`CompanionConfig.max_dte = 2`, same-day/0-DTE preferred before a
  14:00 ET cutoff, per `options_companion/selection.py`).
- **These are not the same policy misapplied in two places — they are
  two deliberately different, non-comparable products.** The companion
  was built around same-day expression of a futures-timed signal; the
  standalone system was built to avoid exactly that decay/gamma profile.
  **Reconciliation: the standalone system's charter DTE policy is 14+
  preferred, unchanged from what `options_manager` already enforces.**
  The companion's 0-2 DTE policy is not adopted, not weakened, and not
  treated as a competing "real" policy to arbitrate — it stays scoped to
  companion-only research (§1, §6) and is never cited as evidence for or
  against the standalone system's DTE threshold.

## 7. Evidence tiers (binding definitions for every later lane)

Four tiers, in increasing order of what they can prove. A later
increment must say which tier its output is and must not promote
evidence across tiers without a human decision recorded the same way
`fixture_status.py` already records promotions by hand:

- **Historical evidence** — reconstructed after the fact from broker
  statements, order history, and candle data. This is what all twelve
  existing fixture candidates in `docs/options-fixture-candidates.md`
  are (HOOD, EBAY, AMD, the four ORCL packets, FITB, BAC, NOK, ADP, ARM,
  QCOM), and what Lane 1 (O-101/O-102/O-103) produces. **Historical
  evidence can prove what actually happened to real money — fills,
  P&L, timing, behavior patterns.** It can never prove that a specific
  chart setup (2-1-2, reclaim, GEX, Signa) was genuinely present and
  acted on at the time, because no contemporaneous source exists for
  any of the twelve candidates. Historical evidence is never sufficient
  by itself to promote a `FixtureStatus` to `CLEAN_COMPLETE_FIXTURE` or
  to claim a strategy has edge.
- **Forward evidence** — a `ProofPacket` (or `morning_scan_packet.py`
  candidate) filled out live, before or at entry, with a real
  contemporaneous source (screenshot, alert log, dated note) per
  `docs/options-forward-proof-packet.md`'s existing rules. As of this
  charter, **zero forward-evidence packets have ever been captured** —
  this is Lane 4's (O-401) entire purpose, and the single biggest gap
  in the options lane today.
- **Paper evidence** — a forward-evidence packet carried through the
  full manual paper lifecycle (`WATCHING → TRIGGERED → ACTIVE → EXITED /
  INVALIDATED / EXPIRED`, per O-402) to a resolved outcome, priced with
  realistic quote-side fills (ask-in/bid-out, never midpoint). Paper
  evidence proves the *process* works end to end. Per the existing O-402
  scope, 20-30 resolved paper opportunities can prove the process works;
  it does **not** by itself establish durable edge.
- **Live evidence** — an actual broker-executed trade. Under this
  charter's §1 "no live options execution" rule, no increment in the
  current sequence produces live evidence for the standalone system. The
  only live evidence that exists today is the historical evidence
  described above (real past trades, already resolved) — new live
  evidence requires a separate, explicit, future control-room decision
  that is out of scope for this charter and every increment listed in
  it.

**Cross-tier rule (binding):** a lower tier never counts as proof of a
higher tier. Historical evidence never counts as forward evidence
(matches the existing `ProofPacket` "no post-hoc promotion" rule).
Companion evidence, of any tier, never counts as standalone-system
evidence of any tier (§1, §6).

## 8. Scope note

This charter is docs-only. It reconciles doctrine already implicit or
conflicting across `options_manager`, `options_companion`, and
`risk/options_risk_engine.py`; it does not modify any of those modules'
code, tests, or defaults. Every number and policy cited above was
verified by direct read of the current source at the base SHA in this
PR, not assumed — see the reconciliation tables in §3 and §6.
