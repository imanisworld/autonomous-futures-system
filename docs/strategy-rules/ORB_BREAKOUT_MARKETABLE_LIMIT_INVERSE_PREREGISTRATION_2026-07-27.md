# ORB Breakout marketable-limit inverse — preregistration

Frozen: 2026-07-27, before this isolated candidate's P&L was run.

Status at freeze: **NEW CANDIDATE — UNTESTED**

This is one research-only directional counterfactual. It must not contact the
deployed box, modify #359, modify ORB Reclaim #360, change Lane B, alter
runtime/configuration, or create a rescue variant after results.

## Frozen source

- Base code SHA:
  `74b14071822be46de46be3c2db0eff7c95b8fced` — the exact #358
  execution-mode evidence commit.
- Corpus:
  `data/replay_corpus_v1_market_condition_fixed`.
- Corpus range: 2025-07-24 through 2026-07-23.
- Corpus files: 626, 313 each for MNQ and MES.
- Corpus tree SHA-256:
  `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4`.
- Source attempt artifact:
  `scripts/execution_mode_corpus_comparison_raw.jsonl`.
- Source artifact SHA-256:
  `800c6a33212710a172bc4ff8bcca7a1f7ecc3e4ce437624d1bc0ecd05c79ba23`.

The fixed population is every row in that committed artifact where
`arm == "marketable_limit"` and `strategy == "orb_breakout"`: **111 approved
attempts**, dated 2025-07-24 through 2026-07-21.

All 111 source attempts are MNQ: 88 original LONG and 23 original SHORT; 71
London, 34 New York, and 6 Asian. MES contributes zero source attempts under
the frozen permissions/disabled-concept posture. No attempt may be added to
the fixed-population analysis to manufacture instrument diversity.

## Exact attempt identity

Paper order UUIDs are deliberately excluded because the replay mints new
random UUIDs. Stable attempt identity is:

`(date, bar_ts, instrument, strategy, original_direction, session)`.

The canonical identity digest is constructed by:

1. projecting the six fields above from the 111 source rows;
2. recovering `session` from the same instrument/timestamp in the frozen
   corpus because the #358 raw artifact omitted that field;
3. sorting by date, `bar_ts`, instrument, original direction, then session;
4. serializing one compact JSON object per line with sorted keys; and
5. hashing the UTF-8 byte stream with SHA-256.

Frozen attempt-identity digest:
`e60289df82101c30abc5251850a15d6e6829aba41c1ca839bf62e58727a5286f`.

Any missing, extra, conflicting, or duplicate stable identity aborts the
study.

## Frozen original ORB Breakout rule

The source population is whatever the exact #358 chronological engine
approved after its frozen market-condition, trend-strength, EMA, GEX,
strategy-permission, ranked-selection, risk, account, position, and duplicate
gates. Those gates are not reinterpreted.

The detector itself is:

- 15-minute completed bars.
- London uses the frozen London ORB fields; Asian and New York use the
  session's normal ORB fields.
- Above breakout:
  - `orb.status == "above"`;
  - price above VWAP;
  - trend direction UP;
  - close at least two ticks above ORB high;
  - relative volume at least 1.2 when available;
  - original direction LONG;
  - planned entry `orb_high + 2 ticks`.
- Below breakout:
  - `orb.status == "below"`;
  - price below VWAP;
  - trend direction DOWN;
  - close at least two ticks below ORB low;
  - relative volume at least 1.2 when available;
  - original direction SHORT;
  - planned entry `orb_low - 2 ticks`.
- The frozen GEX eligibility rule remains active.
- Repeated continuation attempts remain governed by the frozen once-per-
  direction/day state.
- MNQ ORB stop offset is 48 ticks. The detector's frozen max-stop cap is
  preserved.
- Target distance is exactly 2.2 times the planned-entry-to-stop distance.
- No ORB window, breakout threshold, volume threshold, trend/VWAP/GEX rule,
  ranking, permission, session rule, stop, target, or R multiple may change.

`bar_ts` is the bar-open timestamp. The signal becomes available only after
that 15-minute bar completes. The order is constructed after completion using
that completed bar's close as the contemporaneous replay market observation.

## Exact inversion transform

Only directional exposure changes.

For each approved original order:

1. Preserve date, signal bar, instrument, strategy, session, planned entry,
   contracts, notes, qualification, execution mode, and all original absolute
   price distances.
2. Flip LONG to SHORT and SHORT to LONG.
3. Let `S = abs(planned_entry - original_stop)`.
4. Let `T = abs(original_target - planned_entry)`.
5. Inverse LONG: `stop = planned_entry - S`,
   `target = planned_entry + T`.
6. Inverse SHORT: `stop = planned_entry + S`,
   `target = planned_entry - T`.

The fixed population must contain one contract on every captured source
order. The run aborts if #358 produces any ORB Breakout source attempt with a
different size. The chronological inverse uses the same frozen sizing engine
with a one-contract hard invariant; it aborts rather than silently changing
size.

If the exact mirror violates bracket geometry or cannot be passed through the
same marketable-limit fill model, the study stops without promotion evidence.

## Frozen marketable-limit execution

This is exactly #358's `marketable_limit` replay arm:

- `PaperBroker.entry_fill_model = "ioc_limit"`.
- Maximum marketability is 8 ticks for both MNQ and MES.
- MNQ tick size is 0.25, so the eight-tick bound is 2.00 points.
- Current market is the completed decision bar's close.
- Inverse LONG limit: `planned_entry + 8 ticks`.
  - Cancel if current market is above the limit.
  - Otherwise fill at `min(limit, current_market + adverse_entry_slippage)`.
- Inverse SHORT limit: `planned_entry - 8 ticks`.
  - Cancel if current market is below the limit.
  - Otherwise fill at `max(limit, current_market - adverse_entry_slippage)`.
- The IOC either fills immediately at/inside the bound or cancels. It never
  rests for a later bar.
- A limit fill requires the contemporaneous market to be tradable inside the
  directional cap. No structural-plan fantasy fill is allowed.

The completed signal bar is used only to form the decision and current quote.
Stop/target resolution begins strictly on the next same-instrument bar. No
future part of the signal bar is used after order construction.

## Bracket, exit, and ambiguity rules

- Preserve the mirrored static stop and target; no trailing or altered exit.
- Target is a resting limit and fills clean at the target.
- Stop is a market exit and receives adverse stop slippage.
- If a later bar touches both the original stop and target and intrabar order
  is unknowable, the stop wins.
- Gap handling is not optimistic: marketable entry is capped by its limit;
  stop exits receive adverse slippage; no target is awarded from the decision
  bar.
- A filled position resolves chronologically against later same-instrument
  bars. An unfilled IOC is `ENTRY_NOT_FILLED`.
- Any still-open end-of-corpus position remains open and is not assigned a
  synthetic outcome.

## Sessions, duplicates, roll, and costs

- Frozen sessions: Asian, London, and New York, with the exact #358 session
  mapping and no added session window.
- Duplicate attempts are rejected by stable identity. Duplicate outcomes for
  one paper-order identity abort.
- Corpus rows are MNQ/MES continuous front-contract roots generated under the
  repository's quarterly front-contract convention: roll eight calendar days
  before the third-Friday expiry. The byte-identical corpus is controlling.
  It does not contain dated-contract provenance, so this pass can establish
  replay roll consistency but not broker-symbol parity.
- Baseline commission: $1.48 per resolved round trip.
- Baseline slippage: one adverse tick on entry and on a stop exit; targets fill
  clean, exactly as #358.
- Frozen stress tiers are baseline plus one, two, three, and four additional
  adverse ticks: total PaperBroker slippage of 2, 3, 4, and 5 ticks. Each tier
  is fully re-resolved; no post-hoc winner relabeling.

## Required analysis A — fixed-population inverse

1. Re-run the exact #358 marketable-limit arm once to capture approved order
   geometry and reconcile the stable 111-attempt ORB Breakout identity set.
2. Independently mirror those exact 111 attempts.
3. Resolve each against actual later bars without letting its counterfactual
   result alter later candidate, position, or breaker availability.

This measures direction plus the fill-selection consequences of reversing the
same approved attempts.

## Required analysis B — chronological system-path inverse

Re-run the full frozen marketable-limit engine chronologically, leaving every
non-ORB-Breakout strategy in its original direction. Mirror only approved
`orb_breakout` orders immediately before PaperBroker execution.

Inverted fills and outcomes update the normal account and 20% maximum-
drawdown breaker. Existing-position and ordinary account gates remain active.
The resulting ORB Breakout attempt set may therefore differ. Report stable
attempts retained, removed, and added versus the 111-attempt source set, plus
breaker dates and reasons.

This is a selective ORB Breakout intervention, not a system-wide inversion.

## Original-versus-inverse attribution

Report three distinct effects:

- **Directional effect:** original versus inverse P&L on stable attempts that
  fill and resolve in both directions.
- **Fill effect:** counts and P&L associated with stable attempts whose
  fill/resolution status changes solely because the IOC side is reversed.
- **Breaker/path effect:** chronological inverse versus fixed-population
  inverse, including retained/removed/added attempt identities. This comparison
  is not assumed to be dollar-additive because the populations differ.

## Robustness and decision contract

For fixed-population and system-path results, report overall attempts, fills,
resolved, gross, net, expectancy, PF, win rate, max drawdown, longest losing
streak, recovery duration, H1/H2, instrument, session, inverse LONG/SHORT,
calendar year, equal-count chronological quarters, latest 25% of resolved
trades, rolling three-month, rolling six-month, cost tiers, and top-one/five/
ten concentration with removal results.

Classification must be exactly one of:

`VALIDATED`, `PROMISING BUT UNPROVEN`, `BROKEN`, `OVERFIT`, `UNSAFE`, `WAIT`.

Final decision must be exactly one of:

`PROMOTE TO PAPER-BUILD CANDIDATE`, `KEEP RESEARCHING`, `REJECT`.

Promotion requires positive baseline results, positive H1/H2, evidence not
dependent on one instrument/session/inverse direction, reasonable slippage
survival, no catastrophic recent decay, acceptable drawdown, no severe winner
concentration, causal execution, and no material fixed/system-path
contradiction.

The source population already contains only MNQ. Therefore this pass cannot
claim cross-instrument validation even if profitable.

If the candidate fails, reject it. Do not tune marketability ticks, stops,
targets, filters, sessions, breaker rules, or any rescue variant.

Only a promoted candidate may receive a minimal paper-build plan. No
implementation is authorized here.
