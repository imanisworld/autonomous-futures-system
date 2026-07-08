# Options Forward Proof Packet (Increment 25I)

Every fixture candidate reconciled so far in this validation lane --
FITB, BAC, ORCL Packets A-D, HOOD, EBAY, AMD, NOK, ADP, ARM, QCOM -- was
reconstructed *after the fact* from broker records and candles, and every
one of them hit the same wall: no pre-trade source ever existed for the
claimed setup, trigger, invalidation, or target. That's why none of them
could clear the bar for `FixtureStatus.CLEAN_COMPLETE_FIXTURE` -- a
plausible reconstruction is not the same claim as a documented plan.

This template exists to stop that from happening to the *next* real
trade. It is filled out **before or at entry**, not reconstructed from
memory afterward. A future scanner-identification proof fixture needs a
packet like this, captured in real time, to promote from.

The matching dataclass and validator live in
`options_manager/validation/proof_packet.py`
(`ProofPacket` / `ProofPacketStatus` / `validate_proof_packet()`).
Like the rest of this validation package, it performs no I/O of any
kind -- filling out a packet is a manual, human act, not something a
scanner run or broker call produces automatically.

## Required pre-trade fields

| Field | Description |
|---|---|
| `ticker` | Underlying symbol |
| `created_at` | Timestamp the packet itself was created -- must predate any fill; not machine-verified, a human discipline |
| `direction` | `CALL` or `PUT` |
| `setup_type` | Named setup pattern (e.g. "support-hold continuation", "PDL reclaim") |
| `timeframe` | Chart timeframe the setup is read on |
| `entry_trigger` | The exact condition that turns a watch into a trigger |
| `underlying_invalidation` | The underlying price level that invalidates the thesis |
| `premium_stop` | The option-premium-based stop, if different from the underlying invalidation |
| `target_1` | First profit target |
| `target_2` | Second profit target |
| `expiration` | Contract expiration date |
| `strike` | Contract strike |
| `premium` | Quoted premium at packet creation |
| `bid` | Contract bid at packet creation |
| `ask` | Contract ask at packet creation |
| `spread_percent` | Bid/ask spread as a percent of mid |
| `volume` | Contract volume at packet creation |
| `open_interest` | Contract open interest at packet creation |
| `max_contracts` | Position-sizing cap for this trade |
| `max_dollar_risk` | Dollar risk cap for this trade |
| `spy_context` | SPY context at packet creation |
| `qqq_context` | QQQ context at packet creation |
| `gex_context` | GEX context at packet creation, if used |
| `signa_context` | Signa context at packet creation, if used |
| `source_references` | Screenshot, alert log, or dated note references -- the pre-trade source itself |
| `status` | One of `WATCHING` / `TRIGGERED` / `INVALIDATED` / `ACTIVE` / `EXITED` / `EXPIRED` |

## Optional post-trade outcome fields

Filled in only after the trade resolves -- never used to invent or
backfill any of the required pre-trade fields above:

`actual_entry_time`, `actual_entry_premium`, `actual_exit_time`,
`actual_exit_premium`, `realized_pnl_dollars`, `realized_pnl_percent`,
`outcome_notes`.

## Rules

- **No entry trigger, no valid packet.** A watch with no trigger
  condition is not a plan.
- **No underlying invalidation, no valid packet.** Every setup needs a
  level that proves it wrong.
- **No premium stop, no valid packet.**
- **Fewer than both profit targets, no valid packet.**
- **Missing contract-liquidity fields (bid, ask, spread, volume, open
  interest), no valid packet.** Illiquid contracts are a different risk
  question, not a missing-data gap to skip.
- **Missing risk fields (max contracts, max dollar risk), no valid
  packet.**
- **No source reference, no valid packet.** A screenshot, alert log, or
  dated note is the entire point of capturing this forward instead of
  reconstructing it later.
- **No post-hoc promotion to `CLEAN_COMPLETE_FIXTURE`.** A valid,
  complete `ProofPacket` is a pre-trade record, not a fixture. Promotion
  to `FixtureStatus.CLEAN_COMPLETE_FIXTURE` -- if it ever happens for a
  future trade -- is a separate human call made in `fixture_status.py`
  itself, after the real outcome is reconciled against this packet's
  pre-trade claims, exactly like every other status in that module.
- **Trade outcome can never invent a missing pre-trade field.** A
  favorable outcome does not retroactively supply a trigger,
  invalidation, or target that was never written down before the trade.
  This is the same discipline this entire validation lane was built to
  enforce after finding it missing in FITB, BAC, and every ORCL packet.

## Scope note

This is a template and a structural validator, not a scanner input and
not a fixture. `validate_proof_packet()` performs no I/O and has no
clock access -- it cannot verify that `created_at` actually predates a
fill, only that the packet's required fields are all present. Nothing in
`options_manager/validation/proof_packet.py` is imported by
`options_manager.scanner`, `execution`, or `webhook`, and it imports
none of them.
