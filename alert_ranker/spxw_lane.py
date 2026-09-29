"""Scheduled SPX → SPXW paper lane (isolated from equity watchlist scanner).

Paper/advisory only. Never writes equity ``options_shadow_journal`` rows, never
submits broker orders, and never adds SPX/SPXW to the 66-symbol universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from alert_ranker import paper_spxw_v1 as spxw
from alert_ranker.session_calendar import us_equity_rth_state
from alert_ranker.spxw_storage import SpxwStorage

EQUITY_LANE_FORBIDDEN = frozenset({spxw.SIGNAL_UNDERLYING, spxw.CONTRACT_ROOT})


@dataclass(frozen=True)
class SpxwLaneResult:
    status: str
    reason: str
    dte_cohort: str | None
    selected_contract: dict[str, Any] | None
    journal_id: int | None = None
    signal_underlying: str = spxw.SIGNAL_UNDERLYING
    contract_root: str = spxw.CONTRACT_ROOT
    paper_policy_id: str = spxw.POLICY_ID


def equity_universe_contains_spx_or_spxw(watchlist: Sequence[str]) -> bool:
    return any(str(item).strip().upper() in EQUITY_LANE_FORBIDDEN for item in watchlist)


def assert_isolated_from_equity_universe(watchlist: Sequence[str]) -> None:
    if equity_universe_contains_spx_or_spxw(watchlist):
        raise RuntimeError(
            "spxw_lane_isolation_violation:SPX/SPXW must not appear on equity watchlist"
        )


def _option_side(direction: str) -> str:
    return "CALL" if direction == "LONG" else "PUT"


async def build_spxw_paper_candidate(
    *,
    market_data: Any,
    setup: dict[str, Any],
    now: datetime,
    aggregate_open_risk: float = 0.0,
    max_quote_age_seconds: float = 900.0,
    min_remaining_rr: float = spxw.DEFAULT_MIN_REMAINING_RR,
) -> SpxwLaneResult:
    """Route a valid SPX setup into an SPXW paper contract decision.

    ``setup`` must already carry Strat TRIGGERED status and levels. Contract
    discovery uses **SPXW**, not the equity ticker path.
    """
    signal = str(setup.get("ticker") or setup.get("signal_underlying") or "").upper()
    if signal != spxw.SIGNAL_UNDERLYING:
        return SpxwLaneResult("REJECTED", "signal_underlying_not_spx", None, None)

    gate = spxw.evaluate_entry_gates(
        setup_status=str(setup.get("setup_status") or ""),
        direction=str(setup.get("direction") or "").upper(),
        price=_float(setup.get("price")),
        invalidation=setup.get("underlying_invalidation")
        or setup.get("invalidation")
        or setup.get("stop"),
        target_1=setup.get("target_1") or setup.get("target"),
        min_remaining_rr=min_remaining_rr,
    )
    if gate is not None:
        return SpxwLaneResult(
            str(gate.get("paper_policy_status") or "DATA_INVALID"),
            str(gate.get("paper_policy_reason") or "entry_gate"),
            None,
            gate,
        )

    direction = str(setup["direction"]).upper()
    fetch_expirations = getattr(market_data, "fetch_option_expirations", None)
    fetch_chain = getattr(market_data, "fetch_option_chain", None)
    if not callable(fetch_expirations) or not callable(fetch_chain):
        invalid = spxw.data_invalid("option_chain_provider_unavailable")
        return SpxwLaneResult("DATA_INVALID", invalid["paper_policy_reason"], None, invalid)

    try:
        expirations = await fetch_expirations(spxw.CONTRACT_ROOT)
    except Exception as exc:  # noqa: BLE001 - fail closed
        invalid = spxw.data_invalid(f"expiration_fetch_error:{type(exc).__name__}")
        return SpxwLaneResult("DATA_INVALID", invalid["paper_policy_reason"], None, invalid)

    eligible = spxw.list_eligible_expirations(expirations, now)
    if not eligible:
        invalid = spxw.data_invalid("no_expiration_in_spxw_dte_range")
        return SpxwLaneResult("DATA_INVALID", invalid["paper_policy_reason"], None, invalid)

    # Fair selection: evaluate nearest expiries first; keep best liquid contract
    # by the same rank key used inside choose_contract (no 0DTE preference).
    best: tuple[float, float, float, spxw.ExpiryChoice, spxw.ContractChoice] | None = None
    last_reject = "no_liquid_contract"
    side = _option_side(direction)
    spot = _float(setup.get("price"))

    for expiry in eligible:
        try:
            chain = await fetch_chain(spxw.CONTRACT_ROOT, expiry.expiration)
        except Exception as exc:  # noqa: BLE001
            last_reject = f"chain_fetch_error:{type(exc).__name__}"
            continue
        if getattr(chain, "error", None):
            last_reject = f"chain_error:{chain.error}"
            continue
        if str(getattr(chain, "expiration", "") or "")[:10] != expiry.expiration:
            last_reject = "chain_expiration_mismatch"
            continue
        legs = getattr(chain, "calls", ()) if side == "CALL" else getattr(chain, "puts", ())
        decision = spxw.choose_contract(
            legs,
            option_type=side,
            underlying_price=spot,
            max_quote_age_seconds=max_quote_age_seconds,
            now=now,
        )
        if not decision.valid or decision.contract is None:
            last_reject = decision.reason or "no_liquid_contract"
            continue
        contract = decision.contract
        delta_rank = (
            abs(abs(contract.delta) - spxw.DELTA_TARGET)
            if contract.delta is not None
            else 9.0
        )
        if spot is None:
            strike_rank = 0.0
        elif side == "CALL":
            strike_rank = abs(contract.strike - spot) + (
                0.0 if contract.strike >= spot else 10_000.0
            )
        else:
            strike_rank = abs(contract.strike - spot) + (
                0.0 if contract.strike <= spot else 10_000.0
            )
        key = (delta_rank, contract.spread_percent, strike_rank)
        if best is None or key < best[:3]:
            best = (*key, expiry, contract)

    if best is None:
        invalid = spxw.data_invalid(last_reject)
        return SpxwLaneResult("DATA_INVALID", last_reject, None, invalid)

    _delta_rank, _spread, _strike, expiry, contract = best
    fields, reason = spxw.build_spxw_contract_fields(
        expiry=expiry,
        contract=contract,
        underlying_invalidation=setup.get("underlying_invalidation")
        or setup.get("invalidation")
        or setup.get("stop"),
        target_1=setup.get("target_1") or setup.get("target"),
        aggregate_open_risk=aggregate_open_risk,
    )
    if fields is None:
        invalid = spxw.data_invalid(reason)
        return SpxwLaneResult("DATA_INVALID", reason, expiry.cohort, invalid)

    fields["setup_status"] = "TRIGGERED"
    fields["pattern"] = str(setup.get("pattern") or setup.get("setup_type") or "")
    fields["direction"] = direction
    fields["signal_price"] = spot
    return SpxwLaneResult("OPEN", "", expiry.cohort, fields)


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _direction_from_setup_fields(fields: dict[str, Any]) -> str:
    raw = str(fields.get("direction") or fields.get("setup_direction") or "").upper()
    if raw in {"LONG", "SHORT"}:
        return raw
    if raw == "CALL":
        return "LONG"
    if raw == "PUT":
        return "SHORT"
    return ""


class SpxwPaperLane:
    """Separate scheduled lane: SPX signal authority → SPXW paper contracts."""

    def __init__(
        self,
        *,
        config: Any,
        market_data: Any,
        storage: SpxwStorage,
        equity_watchlist: Sequence[str],
        bar_context: Any | None = None,
    ):
        assert_isolated_from_equity_universe(equity_watchlist)
        self.config = config
        self.market_data = market_data
        self.storage = storage
        self.equity_watchlist = list(equity_watchlist)
        self.bar_context = bar_context
        self.last_skip_reason: str | None = None
        self.last_result: SpxwLaneResult | None = None

    def is_market_hours(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(ZoneInfo(getattr(self.config, "timezone", "America/New_York")))
        return us_equity_rth_state(current).is_open

    async def run_scheduled_scan(self, *, now: datetime | None = None) -> SpxwLaneResult:
        """Discover an SPX setup via bar context and route it into SPXW paper.

        Fail-closed when the lane is disabled, outside RTH, or bar context cannot
        prove a TRIGGERED SPX setup. Never touches the equity watchlist path.
        """
        now = now or datetime.now(ZoneInfo(getattr(self.config, "timezone", "America/New_York")))
        assert_isolated_from_equity_universe(self.equity_watchlist)
        if not bool(getattr(self.config, "spxw_paper_lane_enabled", False)):
            self.last_skip_reason = "spxw_lane_disabled"
            result = SpxwLaneResult("SKIPPED", "spxw_lane_disabled", None, None)
            self.last_result = result
            return result
        if not self.is_market_hours(now):
            self.last_skip_reason = "outside_market_hours"
            result = SpxwLaneResult("SKIPPED", "outside_market_hours", None, None)
            self.last_result = result
            return result
        if self.bar_context is None:
            self.last_skip_reason = "bar_context_unavailable"
            result = SpxwLaneResult("SKIPPED", "bar_context_unavailable", None, None)
            self.last_result = result
            return result

        try:
            market_context = await self.bar_context.build(spxw.SIGNAL_UNDERLYING, now)
        except Exception as exc:  # noqa: BLE001 - fail closed
            self.last_skip_reason = f"bar_context_error:{type(exc).__name__}"
            result = SpxwLaneResult("SKIPPED", self.last_skip_reason, None, None)
            self.last_result = result
            return result

        fields = market_context.to_scanner_fields()
        setup_status = str(fields.get("setup_status") or "").upper()
        if setup_status != "TRIGGERED":
            reason = str(
                fields.get("setup_suppression_reason")
                or fields.get("bar_context_reason")
                or f"setup_not_triggered:{setup_status or 'missing'}"
            )
            self.last_skip_reason = reason
            result = SpxwLaneResult("SKIPPED", reason, None, None)
            self.last_result = result
            return result

        direction = _direction_from_setup_fields(fields)
        if not direction:
            self.last_skip_reason = "direction_unknown"
            result = SpxwLaneResult("SKIPPED", "direction_unknown", None, None)
            self.last_result = result
            return result

        price = _float(fields.get("price"))
        if price is None:
            try:
                snap = await self.market_data.fetch_market_snapshot(spxw.SIGNAL_UNDERLYING)
                price = _float(getattr(snap, "price", None))
            except Exception:  # noqa: BLE001
                price = None

        setup = {
            "ticker": spxw.SIGNAL_UNDERLYING,
            "signal_underlying": spxw.SIGNAL_UNDERLYING,
            "setup_status": "TRIGGERED",
            "direction": direction,
            "price": price,
            "pattern": fields.get("pattern") or fields.get("strat_sequence") or "2-1-2",
            "setup_type": fields.get("setup_type") or fields.get("pattern") or "2-1-2",
            "underlying_invalidation": fields.get("underlying_invalidation")
            or fields.get("setup_invalidation")
            or fields.get("stop"),
            "target_1": fields.get("target_1") or fields.get("target"),
            "setup_entry_trigger": fields.get("setup_entry_trigger"),
            "setup_timeframe": fields.get("timeframe") or fields.get("setup_timeframe"),
        }
        result = await self.scan_spx_setup(setup, now=now, source="scheduled")
        self.last_result = result
        return result

    async def scan_spx_setup(
        self,
        setup: dict[str, Any],
        *,
        now: datetime | None = None,
        source: str = "scheduled",
    ) -> SpxwLaneResult:
        """Process one SPX setup packet into the SPXW paper journal."""
        del source  # reserved for journal provenance; no Discord/broker side effects
        assert_isolated_from_equity_universe(self.equity_watchlist)
        now = now or datetime.now(ZoneInfo(getattr(self.config, "timezone", "America/New_York")))
        if not bool(getattr(self.config, "spxw_paper_lane_enabled", False)):
            self.last_skip_reason = "spxw_lane_disabled"
            return SpxwLaneResult("SKIPPED", "spxw_lane_disabled", None, None)
        if not self.is_market_hours(now):
            self.last_skip_reason = "outside_market_hours"
            return SpxwLaneResult("SKIPPED", "outside_market_hours", None, None)

        result = await build_spxw_paper_candidate(
            market_data=self.market_data,
            setup=setup,
            now=now,
            aggregate_open_risk=self.storage.open_planned_risk(),
            max_quote_age_seconds=float(
                getattr(self.config, "public_stale_quote_seconds", 900.0) or 900.0
            ),
            min_remaining_rr=float(
                getattr(self.config, "paper_v1_min_remaining_rr", spxw.DEFAULT_MIN_REMAINING_RR)
            ),
        )
        pattern = str(setup.get("pattern") or setup.get("setup_type") or "unknown")
        setup_type = str(setup.get("setup_type") or pattern)
        direction = str(setup.get("direction") or "UNKNOWN").upper()
        if result.status == "OPEN" and result.selected_contract:
            journal_id = self.storage.record(
                direction=direction,
                pattern=pattern,
                setup_type=setup_type,
                status="OPEN",
                dte_cohort=str(result.dte_cohort or ""),
                dte=result.selected_contract.get("dte"),
                rejection_reason="",
                setup_inputs=dict(setup),
                selected_contract=result.selected_contract,
                timestamp=now,
            )
            self.last_skip_reason = None
            return SpxwLaneResult(
                result.status,
                result.reason,
                result.dte_cohort,
                result.selected_contract,
                journal_id=journal_id,
            )

        cohort = result.dte_cohort or ""
        dte = None
        if result.selected_contract:
            dte = result.selected_contract.get("dte")
        journal_id = self.storage.record(
            direction=direction,
            pattern=pattern,
            setup_type=setup_type,
            status=result.status,
            dte_cohort=cohort,
            dte=dte if isinstance(dte, int) else None,
            rejection_reason=result.reason,
            setup_inputs=dict(setup),
            selected_contract=result.selected_contract or {},
            timestamp=now,
        )
        self.last_skip_reason = result.reason or result.status
        return SpxwLaneResult(
            result.status,
            result.reason,
            result.dte_cohort,
            result.selected_contract,
            journal_id=journal_id,
        )

    def status(self) -> dict[str, Any]:
        return {
            "lane": "SPX_SPXW_PAPER",
            "enabled": bool(getattr(self.config, "spxw_paper_lane_enabled", False)),
            "signal_underlying": spxw.SIGNAL_UNDERLYING,
            "contract_root": spxw.CONTRACT_ROOT,
            "paper_policy_id": spxw.POLICY_ID,
            "equity_watchlist_contains_spx_spxw": equity_universe_contains_spx_or_spxw(
                self.equity_watchlist
            ),
            "live_execution": False,
            "broker_order_path": False,
            "open_planned_risk": self.storage.open_planned_risk(),
            "last_skip_reason": self.last_skip_reason,
            "last_status": None if self.last_result is None else self.last_result.status,
            "interval_minutes": int(
                getattr(self.config, "spxw_interval_minutes", 5) or 5
            ),
            "sqlite_path": str(getattr(self.config, "spxw_sqlite_path", "")),
        }
