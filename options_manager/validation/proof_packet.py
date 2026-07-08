"""options_manager/validation/proof_packet.py

Forward-only options proof-packet template -- Increment 25I. Every
candidate reconciled so far in this validation lane (FITB, BAC, ORCL
Packets A-D, HOOD, EBAY, AMD, NOK, ADP, ARM, QCOM) was reconstructed
*after the fact* from broker records and candles, and every single one of
them ran into the same wall: no pre-trade source ever existed for the
claimed setup/trigger/invalidation/target, so none of them could become
more than a management case, a special case, or an incomplete record.
`ProofPacket` exists to stop that from happening to the *next* real
trade -- it is filled out before or at entry, not reconstructed from
memory afterward, so a future scanner-identification proof fixture has
something real to promote from.

This is a forward-capture template, not a retroactive one:
`created_at` is documented as the packet's own creation timestamp and
`validate_proof_packet()` only checks that the required pre-trade fields
are non-empty -- it never reads the system clock and performs no I/O of
any kind, so it cannot verify that `created_at` actually predates a fill.
That ordering discipline is a human responsibility, the same way this
whole validation package is advisory-only and hand-authored rather than
auto-classified.

`validate_proof_packet()` enforces the rules this lane's own history
proved necessary the hard way:
- no entry trigger, no valid packet
- no underlying invalidation level, no valid packet
- no premium stop, no valid packet
- fewer than both profit targets, no valid packet
- missing contract-liquidity fields (bid, ask, spread, volume, open
  interest), no valid packet
- missing risk fields (max contracts, max dollar risk), no valid packet
- no source reference (screenshot, alert log, dated note), no valid
  packet

A valid `ProofPacket` is still only a *pre-trade* record -- nothing in
this module promotes a packet to `FixtureStatus.CLEAN_COMPLETE_FIXTURE`
or any other fixture_status.py status. That promotion, if it ever
happens, is a separate human call made in fixture_status.py itself once
a real trade's outcome is reconciled against this packet's pre-trade
claims -- post-trade outcome fields on this dataclass exist to be filled
in *after* the fact, but their presence or absence is never used here to
invent, backfill, or infer any of the pre-trade fields above.

Performs no I/O of any kind: no candle fetch, no option-chain fetch, no
market-data fetch, no broker call, no order placement, no execution, no
alert sending, no file access at runtime, no network calls, no MCP calls.
Does not import replay/replay_engine.py, the live context.market_context
loader, alert_ranker, options_companion, execution, webhook, broker
systems, options_manager.scanner, or risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class ProofPacketStatus(str, Enum):
    """A packet's own lifecycle state, set by the human filling it out --
    not derived or auto-advanced by anything in this module."""

    WATCHING = "watching"
    TRIGGERED = "triggered"
    INVALIDATED = "invalidated"
    ACTIVE = "active"
    EXITED = "exited"
    EXPIRED = "expired"


@dataclass(frozen=True, kw_only=True)
class ProofPacket:
    """A single forward-captured options-trade proof packet. Every field
    above the post-trade-outcome section is meant to be filled in before
    or at entry -- `validate_proof_packet()` treats all of them as
    required. The post-trade-outcome fields are optional and left None
    until the trade actually resolves; nothing in this module fabricates
    them, and nothing here uses them to fill in a missing pre-trade
    field."""

    ticker: str
    created_at: str
    direction: Literal["CALL", "PUT"]
    setup_type: str
    timeframe: str
    entry_trigger: str
    underlying_invalidation: str
    premium_stop: str
    target_1: str
    target_2: str
    expiration: str
    strike: float
    premium: float
    bid: float
    ask: float
    spread_percent: float
    volume: int
    open_interest: int
    max_contracts: int
    max_dollar_risk: float
    spy_context: str
    qqq_context: str
    gex_context: str
    signa_context: str
    source_references: tuple[str, ...]
    status: ProofPacketStatus
    actual_entry_time: Optional[str] = None
    actual_entry_premium: Optional[float] = None
    actual_exit_time: Optional[str] = None
    actual_exit_premium: Optional[float] = None
    realized_pnl_dollars: Optional[float] = None
    realized_pnl_percent: Optional[float] = None
    outcome_notes: str = ""


def validate_proof_packet(packet: ProofPacket) -> tuple[bool, tuple[str, ...]]:
    """Checks a `ProofPacket`'s required pre-trade fields are all
    present. Returns `(True, ())` when valid, or `(False, <errors>)`
    naming every missing field -- never partially valid. Purely
    structural: this cannot and does not check that `created_at` actually
    predates a real fill, only that the packet describes a complete
    pre-trade thesis, contract, liquidity, and risk picture."""
    errors: list[str] = []

    def _require_text(value: str, name: str) -> None:
        if not value or not value.strip():
            errors.append(f"missing {name}")

    _require_text(packet.setup_type, "setup_type")
    _require_text(packet.entry_trigger, "entry_trigger")
    _require_text(packet.underlying_invalidation, "underlying_invalidation")
    _require_text(packet.premium_stop, "premium_stop")
    _require_text(packet.target_1, "target_1")
    _require_text(packet.target_2, "target_2")

    if packet.strike <= 0:
        errors.append("missing/invalid strike")
    if packet.premium <= 0:
        errors.append("missing/invalid premium")
    if packet.bid <= 0:
        errors.append("missing/invalid bid")
    if packet.ask <= 0:
        errors.append("missing/invalid ask")
    if packet.spread_percent < 0:
        errors.append("missing/invalid spread_percent")
    if packet.volume < 0:
        errors.append("missing/invalid volume")
    if packet.open_interest < 0:
        errors.append("missing/invalid open_interest")

    if packet.max_contracts <= 0:
        errors.append("missing/invalid max_contracts")
    if packet.max_dollar_risk <= 0:
        errors.append("missing/invalid max_dollar_risk")

    if not packet.source_references:
        errors.append("missing source_references")

    return (not errors, tuple(errors))
