# Inverse ORB DEMO wiring review

**DRAFT / HOLD — DO NOT MERGE OR DEPLOY.** This completes the implementation
started in draft #500, pending resolution of the evidence mismatch below.
No runtime configuration or deployment was changed.

## What is implemented

- Both active modes require an offset-aware accounting epoch and exclude the
  other breakout proof mode. `live` remains invalid.
- `paper_sim` retains its isolated PaperBroker. `tradovate_demo` requires
  explicit `BROKER=tradovate`, `TRADOVATE_ENV=demo`,
  `LIVE_TRADING_ENABLED=false`, `PAPER_MODE=false`, and a positive numeric
  `TRADOVATE_EXPECTED_ACCOUNT_ID`. The factory and broker both enforce the
  route; the resolved account must match the pin.
- Only the inverse order uses the frozen one-contract, eight-tick IOC limit.
  It ignores global entry tolerance/entry-mode/runner settings and preserves
  both static children. Off-grid or malformed inverse brackets are rejected.
  Other order types retain the existing R:R-preserving entry clamp.
- Source-order post-fill protection remains enabled for DEMO. No risk floor,
  stop cap, signal qualification, inverse direction, or static distance changed.
- Account position/order reads must be well-formed and positively clear before
  submission. A persistent exclusive submit latch blocks overlapping workers,
  uncertain results, and retries after restart. The latch is outside the epoch.
- Confirmed journal ownership and order IDs are read back before releasing the
  latch. Later resolution uses the persisted account, contract and exit-order
  identities, with no price-only fallback. Global runner settings cannot move
  an inverse stop or remove its target.
- The account pin participates in the existing proof-critical environment drift
  checks and release-manifest fingerprint. Audit rows specify the $1.48
  round-trip commission convention for comparisons; broker accounting is not
  overwritten with that convention.

## Reproducible offline evidence

Run `python3 -m scripts.inverse_orb_demo_parity --output /tmp/inverse-demo-parity.json`.
The script exercises the production Tradovate payload builder with all network
boundaries stubbed, then compares limit eligibility with the actual PaperBroker
and every arm in the existing canonical JSON. It intentionally injects conflicting
32-tick, market-entry and runner defaults to check per-order isolation.

| Check | Result |
|---|---:|
| Canonical arms | 63 |
| IOC-eligible / not eligible | 57 / 6 |
| Entry eligibility mismatches | 0 |
| Static bracket / quantity mismatches | 0 |
| Fill differences with the legacy R:R entry clamp | 1 |
| Modeled fills rejected by retained post-fill checks | 38 of 57 |
| Rejections involving wrong-side static stop | 37 |
| Rejections involving minimum actual R:R | 38 |

Baseline file SHA-256:
`033e6a4d186b3fe9862ac219bfa359486cd6e9166f4c5d229d22d1dce4f149ba`.
Population fingerprint:
`f32b1b1d2fd5f5860d476b7c44f479d28abb8dc0445fccc21f8c7f27519e2da2`.
The baseline is unchanged. The formerly documented 111-arm population is still
unreproduced and is not substituted for the available 63 arms.

This proves deterministic **entry eligibility**, not actual Tradovate liquidity,
IOC/OSO acceptance, fill prices, or equivalent strategy outcomes. In particular,
a favorable price can move past the static stop before submission; the paper
model accepts such an entry while external protection correctly rejects it.
Removing that protection to reproduce the paper outcome is not an acceptable
implementation shortcut. The reported paper profitability is not DEMO evidence.

## Release blockers and activation procedure

1. Resolve the baseline's incompatible fill geometry and independently review
   what forward DEMO evidence can validly be compared with it. Keep the eight-tick
   contract and risk checks intact unless a separate strategy decision authorizes
   a change. No automatic promotion follows the offline entry-parity result.
2. Complete exact-revision CI and independent review. Confirm the existing
   release proof pins include the account pin without publishing account data.
3. Before any authorized activation, verify the exact DEMO account, no open
   position, no working order, and no unresolved prior submission from the
   existing execution history. The new latch cannot attest to submissions made
   before this code existed. Preserve the canonical baseline and start a new
   DEMO epoch at activation, separate from the prior paper epoch.
4. Keep the existing forward review checkpoint. Normalize comparison results
   using actual completed broker fills and $1.48 per round trip; include safety
   liquidations and distinguish them from IOC no-fills. Do not reuse baseline
   outcomes for forward DEMO results.

A transport error, unconfirmed cancellation, missing journal identity, or safety
liquidation leaves `inverse_demo_submit_pending.json` in the runtime journal
directory and stops further inverse submissions. This is intentional, including
for a confirmed post-fill safety liquidation: review is required before another
attempt. Never delete that file merely to resume trading or roll an epoch. First
reconcile its client order ID against broker history, confirm flatness and no
working orders, and preserve the reconciliation evidence. There is no automatic
latch reset or automated release in this change.
