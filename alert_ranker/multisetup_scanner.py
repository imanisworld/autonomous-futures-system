"""Multi-setup paper-evidence orchestration for the options scanner.

This options-only extension preserves the existing 30m 2-1-2 lane and adds
Daily 2-1-2 continuation, 2-2-2 continuation/reversal, 3-2 developing WATCH,
and 3-2-2 continuation/reversal as separate evidence populations.

The builder fetches causal bars once per ticker, then every independent setup
candidate flows through the exact same frozen OPTIONS_PAPER_V1 contract/risk
policy.  No broker/order capability is added.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from options_manager.levels import LevelFinderInputs, find_targets
from strategy.strat_classifier import TWO_DOWN, TWO_UP

from .bar_context import CANONICAL_TIMEFRAME, MarketContext
from .bar_provider import CONSOLIDATED_FEED, BarProviderError
from .causal_bars import build_session_candle, completed_bars, session_bars
from .contract_marks import record_contract_mark
from .daily_strat import DAILY_TIMEFRAME, evaluate_daily_setup
from .discord import AlertDecision
from .lifecycle import classify_candidate, open_candidate_fields
from .paper_v1 import POLICY_ID
from .scorer import score_setup
from .session_calendar import SessionCalendarError


_CANDIDATE_SPECIFIC_KEYS = {
    "pattern",
    "direction",
    "setup_type",
    "setup_timeframe",
    "paper_candidate_id",
    "setup_status",
    "setup_reason_code",
    "setup_suppression_reason",
    "setup_direction",
    "setup_entry_trigger",
    "setup_invalidation",
    "underlying_invalidation",
    "stop",
    "stop_level",
    "invalidation",
    "target",
    "target_1",
    "target_2",
    "setup_rr_1",
    "setup_rr_2",
    "setup_target_reason",
    "setup_market_status",
    "setup_market_reason",
    "setup_proof_status",
    "trade_proof_status",
    "trade_proof_reason",
    "strat_sequence",
    "timeframe",
}

_EXTRA_CONTEXT_KEYS = _CANDIDATE_SPECIFIC_KEYS | {
    "daily_previous_high",
    "daily_previous_low",
    "daily_two_back_type",
    "daily_previous_type",
    "daily_current_type",
}


def _candidate_identity(contract_key: str, raw: dict[str, Any]) -> str:
    setup_type = str(raw.get("setup_type") or "UNSPECIFIED").upper()
    timeframe = str(raw.get("setup_timeframe") or raw.get("timeframe") or "UNSPECIFIED").upper()
    return f"{contract_key}|{timeframe}|{setup_type}"


def _long_short(direction: str | None) -> str | None:
    if direction == "CALL":
        return "LONG"
    if direction == "PUT":
        return "SHORT"
    return None


def _paper_candidate_id(setup_type: str, timeframe: str) -> str:
    return f"{timeframe}:{setup_type}"


def build_multisetup_scanner(base_cls):
    """Return an OptionsScanner subclass that persists all emitted candidates."""
    # Imported here after scanner.py has defined these helpers. scanner.py
    # installs this subclass at module end, so there is no circular read of a
    # half-defined OptionsScanner.
    from .scanner import (
        ScanOutcome,
        _float_or_none,
        _mid_from_raw,
        _provider_snapshot,
        _selected_contract,
        _shadow_setup_inputs,
    )

    class MultiSetupOptionsScanner(base_cls):
        async def _build_normalized_data(self, ticker, context, now):
            data = await super()._build_normalized_data(ticker, context, now)
            for key in _EXTRA_CONTEXT_KEYS:
                if key in context:
                    data[key] = context[key]
            return data

        async def _fetch_bar_context(self, ticker: str, now: datetime) -> dict[str, Any]:
            """Build original 30m proof plus Daily candidates from one bar fetch."""
            if not self._causal_lane_active():
                return {}
            if self.bar_context is None:
                return {
                    "bar_context_available": False,
                    "bar_context_reason": "bar_context_unconfigured",
                }

            builder = self.bar_context
            symbol = (ticker or "").strip().upper()
            try:
                cutoff = now.astimezone(timezone.utc) - builder.delay_buffer
                base = {
                    "feed": getattr(builder.provider, "feed", ""),
                    "requested_as_of": now.astimezone(timezone.utc).isoformat(),
                    "information_cutoff": cutoff.isoformat(),
                    "delay_buffer_seconds": int(builder.delay_buffer.total_seconds()),
                    "timeframe": builder.timeframe.name,
                }
                if base["feed"] != CONSOLIDATED_FEED:
                    return {
                        "bar_context_available": False,
                        "bar_context_reason": "feed_not_consolidated",
                        "bar_context_feed": base["feed"],
                    }
                if builder.timeframe.name != CANONICAL_TIMEFRAME.name:
                    return {
                        "bar_context_available": False,
                        "bar_context_reason": "unsupported_timeframe",
                        "bar_context_feed": base["feed"],
                    }
                if not symbol:
                    return {
                        "bar_context_available": False,
                        "bar_context_reason": "missing_symbol",
                        "bar_context_feed": base["feed"],
                    }

                wanted = [symbol]
                if builder.require_index_context:
                    wanted.extend(name for name in builder.index_symbols if name != symbol)

                session = await builder._session_for(cutoff)
                if session is None:
                    return {
                        "bar_context_available": False,
                        "bar_context_reason": "no_session",
                        "bar_context_feed": base["feed"],
                    }
                if cutoff < session.open:
                    return {
                        "bar_context_available": False,
                        "bar_context_reason": "session_not_started",
                        "bar_context_feed": base["feed"],
                    }
                start = cutoff - timedelta(days=builder.lookback_days)
                required = await builder._sessions_in_window(start, session)
                raw = await builder.provider.fetch_bars(
                    wanted, builder.timeframe, start, cutoff
                )

                contexts = {
                    name: builder._symbol_context(
                        name, raw.get(name, []), session, required, cutoff
                    )
                    for name in wanted
                }
                ticker_context = contexts[symbol]
                spy = contexts.get("SPY")
                qqq = contexts.get("QQQ")
                reason = ""
                if not ticker_context.available:
                    reason = ticker_context.reason
                elif builder.require_index_context and spy is not None and not spy.available:
                    reason = "missing_context:spy"
                elif builder.require_index_context and qqq is not None and not qqq.available:
                    reason = "missing_context:qqq"
                if not reason:
                    ticker_context = builder._promote_paper_evidence_setup(
                        ticker_context, spy, qqq
                    )

                market_context = MarketContext(
                    available=not reason,
                    reason=reason,
                    ticker=ticker_context,
                    spy=spy,
                    qqq=qqq,
                    **base,
                )
                fields = market_context.to_scanner_fields()
                candidates: list[dict[str, Any]] = []
                if not reason:
                    thirty = self._thirty_minute_candidate(ticker_context)
                    if thirty is not None:
                        candidates.append(thirty)
                    daily = self._daily_candidate_from_raw(
                        builder,
                        raw.get(symbol, []),
                        session,
                        required,
                        cutoff,
                        ticker_context,
                        spy,
                        qqq,
                    )
                    if daily is not None:
                        candidates.append(daily)
                fields["paper_setup_candidates"] = candidates
                fields["paper_setup_candidate_count"] = len(candidates)
                return fields
            except SessionCalendarError as exc:
                return {
                    "bar_context_available": False,
                    "bar_context_reason": exc.reason,
                }
            except BarProviderError as exc:
                reason = exc.reason
                if reason == "missing_symbol" and exc.detail:
                    reason = f"missing_symbol:{exc.detail.lower()}"
                return {
                    "bar_context_available": False,
                    "bar_context_reason": reason,
                }
            except Exception as exc:  # noqa: BLE001 - preserve fail-closed scanner
                return {
                    "bar_context_available": False,
                    "bar_context_reason": f"bar_context_error:{type(exc).__name__}",
                }

        @staticmethod
        def _thirty_minute_candidate(ticker_context) -> dict[str, Any] | None:
            if not (
                ticker_context.setup_status == "TRIGGERED"
                and ticker_context.setup_proof_status == "VALID"
                and ticker_context.setup_direction in {"CALL", "PUT"}
            ):
                return None
            return {
                "paper_candidate_id": _paper_candidate_id(
                    "STRAT_212_CONTINUATION", "30m"
                ),
                "setup_type": "STRAT_212_CONTINUATION",
                "setup_timeframe": "30m",
                "timeframe": "30m",
                "pattern": ticker_context.strat_sequence or "strat_212",
                "strat_sequence": ticker_context.strat_sequence or "strat_212",
                "direction": _long_short(ticker_context.setup_direction),
                "setup_status": "TRIGGERED",
                "setup_reason_code": ticker_context.setup_reason_code,
                "setup_direction": ticker_context.setup_direction,
                "setup_entry_trigger": ticker_context.setup_entry_trigger,
                "underlying_invalidation": ticker_context.setup_invalidation,
                "stop": ticker_context.setup_invalidation,
                "target": ticker_context.setup_target_1,
                "target_1": ticker_context.setup_target_1,
                "target_2": ticker_context.setup_target_2,
                "setup_rr_1": ticker_context.setup_rr_1,
                "setup_rr_2": ticker_context.setup_rr_2,
                "setup_market_status": ticker_context.setup_market_status,
                "setup_market_reason": ticker_context.setup_market_reason,
                "setup_proof_status": ticker_context.setup_proof_status,
                "trade_proof_status": ticker_context.trade_proof_status,
                "trade_proof_reason": ticker_context.trade_proof_reason,
                "setup_suppression_reason": None,
            }

        def _daily_candidate_from_raw(
            self,
            builder,
            bars,
            session,
            required,
            cutoff,
            ticker_context,
            spy,
            qqq,
        ) -> dict[str, Any] | None:
            closed = completed_bars(bars, builder.timeframe, cutoff)
            tz = ZoneInfo(builder.exchange_timezone)
            by_day: dict[date, list[Any]] = {}
            for bar in closed:
                by_day.setdefault(bar.start_utc.astimezone(tz).date(), []).append(bar)

            prior_daily = []
            current_daily = None
            for day_session in required:
                kept = session_bars(
                    by_day.get(day_session.date, []),
                    builder.timeframe,
                    day_session.open,
                    day_session.close,
                )
                candle = build_session_candle(kept)
                if day_session.date == session.date:
                    current_daily = candle
                elif candle is not None:
                    prior_daily.append(candle)

            verdict = evaluate_daily_setup(prior_daily, current_daily)
            if verdict.watching:
                return {
                    "paper_candidate_id": _paper_candidate_id(
                        verdict.setup_type or "DAILY_32_DEVELOPING", DAILY_TIMEFRAME
                    ),
                    "setup_type": verdict.setup_type or "DAILY_32_DEVELOPING",
                    "setup_timeframe": DAILY_TIMEFRAME,
                    "timeframe": DAILY_TIMEFRAME,
                    "pattern": verdict.sequence or "strat_32_developing",
                    "strat_sequence": verdict.sequence or "strat_32_developing",
                    "setup_status": "WATCH",
                    "setup_reason_code": verdict.reason_code,
                    "setup_suppression_reason": "setup_forming:daily_32",
                    "daily_previous_high": verdict.previous_high,
                    "daily_previous_low": verdict.previous_low,
                    "daily_two_back_type": verdict.two_back_type,
                    "daily_previous_type": verdict.previous_type,
                    "daily_current_type": verdict.current_type,
                }
            if not verdict.triggered or not verdict.setup_type:
                return None

            target_1 = target_2 = rr_1 = rr_2 = None
            target_reason = "daily_targets_unresolved"
            resistance = tuple(sorted({candle.high for candle in prior_daily}))
            support = tuple(sorted({candle.low for candle in prior_daily}, reverse=True))
            if (
                verdict.direction in {"CALL", "PUT"}
                and verdict.entry_trigger is not None
                and verdict.invalidation is not None
            ):
                target_result = find_targets(
                    LevelFinderInputs(
                        direction=verdict.direction,
                        entry=verdict.entry_trigger,
                        underlying_invalidation=verdict.invalidation,
                        resistance_levels=resistance,
                        support_levels=support,
                    )
                )
                target_reason = target_result.reason_code
                if target_result.status == "VALID":
                    target_1 = target_result.target_1
                    target_2 = target_result.target_2
                    rr_1 = target_result.rr_1
                    rr_2 = target_result.rr_2

            direction = verdict.direction
            desired_trend = "bullish" if direction == "CALL" else "bearish"
            desired_candle = TWO_UP if direction == "CALL" else TWO_DOWN
            spy_trend = builder._trend(spy)
            qqq_trend = builder._trend(qqq)
            market_reason = (
                f"spy={spy_trend or 'missing'};qqq={qqq_trend or 'missing'};"
                f"hourly={ticker_context.hourly_candle_type or 'missing'}"
            )
            targets_valid = target_1 is not None and target_2 is not None
            market_aligned = (
                spy_trend == desired_trend
                and qqq_trend == desired_trend
                and ticker_context.hourly_candle_type == desired_candle
            )

            common = {
                "paper_candidate_id": _paper_candidate_id(
                    verdict.setup_type, DAILY_TIMEFRAME
                ),
                "setup_type": verdict.setup_type,
                "setup_timeframe": DAILY_TIMEFRAME,
                "timeframe": DAILY_TIMEFRAME,
                "pattern": verdict.sequence,
                "strat_sequence": verdict.sequence,
                "direction": _long_short(direction),
                "setup_direction": direction,
                "setup_entry_trigger": verdict.entry_trigger,
                "underlying_invalidation": verdict.invalidation,
                "stop": verdict.invalidation,
                "target": target_1,
                "target_1": target_1,
                "target_2": target_2,
                "setup_rr_1": rr_1,
                "setup_rr_2": rr_2,
                "setup_target_reason": target_reason,
                "setup_market_reason": market_reason,
                "daily_previous_high": verdict.previous_high,
                "daily_previous_low": verdict.previous_low,
                "daily_two_back_type": verdict.two_back_type,
                "daily_previous_type": verdict.previous_type,
                "daily_current_type": verdict.current_type,
            }
            if not targets_valid:
                return {
                    **common,
                    "setup_status": "INVALID",
                    "setup_reason_code": "daily_targets_incomplete",
                    "setup_proof_status": "INCOMPLETE",
                    "setup_market_status": "UNRESOLVED",
                    "setup_suppression_reason": "setup_proof_incomplete:daily_targets",
                }
            if not market_aligned:
                return {
                    **common,
                    "setup_status": "INVALID",
                    "setup_reason_code": "daily_market_not_aligned",
                    "setup_proof_status": "INCOMPLETE",
                    "setup_market_status": "NOT_ALIGNED",
                    "setup_suppression_reason": "setup_proof_incomplete:market_not_aligned",
                }
            return {
                **common,
                "setup_status": "TRIGGERED",
                "setup_reason_code": "daily_paper_evidence_setup_proven",
                "setup_proof_status": "VALID",
                "setup_market_status": "VALID",
                "trade_proof_status": "INCOMPLETE",
                "trade_proof_reason": (
                    "weekly_monthly_context_unavailable;"
                    "event_risk_unavailable;flip_context_unavailable"
                ),
                "setup_suppression_reason": None,
            }

        async def scan_ticker(
            self,
            ticker: str,
            *,
            source: str,
            context: dict[str, Any] | None = None,
            now=None,
        ):
            now = now or datetime.now(ZoneInfo(self.config.timezone))
            normalized = await self._build_normalized_data(ticker, context or {}, now)
            raw_candidates = normalized.get("paper_setup_candidates")
            candidates = (
                [item for item in raw_candidates if isinstance(item, dict)]
                if isinstance(raw_candidates, list)
                else []
            )
            if not candidates:
                return await self._process_normalized_candidate(
                    ticker, normalized, source=source, now=now
                )

            outcomes = []
            total = len(candidates)
            for index, candidate in enumerate(candidates, start=1):
                data = self._overlay_candidate(normalized, candidate)
                data["paper_setup_candidate_index"] = index
                data["paper_setup_candidate_count"] = total
                outcomes.append(
                    await self._process_normalized_candidate(
                        ticker, data, source=source, now=now
                    )
                )
            for outcome in outcomes:
                if str(outcome.result.raw.get("setup_status") or "").upper() == "TRIGGERED":
                    return outcome
            return outcomes[0]

        @staticmethod
        def _overlay_candidate(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
            data = {
                key: value
                for key, value in base.items()
                if key not in _CANDIDATE_SPECIFIC_KEYS and key != "paper_setup_candidates"
            }
            data.update(candidate)
            return data

        async def _process_normalized_candidate(self, ticker, normalized, *, source, now):
            preliminary = score_setup(normalized, now=now)
            normalized = await self._apply_paper_v1_contract(
                ticker, normalized, preliminary.direction, now
            )
            result = score_setup(normalized, now=now)

            gate = self._structural_gate(normalized)
            if gate:
                decision = AlertDecision(False, gate)
            else:
                decision = await self.discord.send_if_eligible(result, now=now)
            suppression_reason = decision.reason
            storage_id = self.storage.record_scan(
                result,
                source=source,
                alert_sent=decision.sent,
                alert_suppression_reason=suppression_reason,
                timestamp=now,
            )

            classification = classify_candidate({**result.raw, "direction": result.direction})
            shadow_id = 0
            shadow_reason = classification.reason or "candidate"
            if classification.is_open_eligible and gate:
                shadow_reason = f"suppressed:{gate}"
            elif classification.is_open_eligible:
                candidate_key = _candidate_identity(classification.contract_key, result.raw)
                duplicate = self.storage.find_open_duplicate(result.ticker, candidate_key)
                if duplicate is not None:
                    shadow_reason = f"duplicate_open:{duplicate}"
                else:
                    selected = _selected_contract(result.raw)
                    selected.update(open_candidate_fields(result.raw, classification.contract_key))
                    selected["option_contract_key"] = classification.contract_key
                    selected["contract_key"] = candidate_key
                    selected["candidate_key"] = candidate_key
                    selected["setup_type"] = result.raw.get("setup_type")
                    selected["setup_timeframe"] = result.raw.get("setup_timeframe")
                    shadow_id = self.storage.record_shadow_setup(
                        result,
                        scan_id=storage_id,
                        setup_inputs=_shadow_setup_inputs(result.raw),
                        provider_snapshot=_provider_snapshot(result.raw),
                        selected_contract=selected,
                        timestamp=now,
                    )
                    if result.raw.get("paper_policy_id") == POLICY_ID:
                        record_contract_mark(
                            self.storage,
                            shadow_id=shadow_id,
                            option_symbol=str(result.raw.get("contract") or ""),
                            timestamp=now,
                            bid=_float_or_none(result.raw.get("option_bid")),
                            ask=_float_or_none(result.raw.get("option_ask")),
                            mid=_mid_from_raw(result.raw),
                            volume=_float_or_none(result.raw.get("option_volume")),
                            open_interest=_float_or_none(result.raw.get("open_interest")),
                            delta=_float_or_none(result.raw.get("delta")),
                            gamma=_float_or_none(result.raw.get("gamma")),
                            theta=_float_or_none(result.raw.get("theta")),
                            implied_volatility=_float_or_none(result.raw.get("implied_volatility")),
                            quote_timestamp=(
                                str(result.raw.get("option_quote_timestamp"))
                                if result.raw.get("option_quote_timestamp")
                                else None
                            ),
                            raw={
                                "event": "ENTRY",
                                "basis": "ASK",
                                "policy_id": POLICY_ID,
                                "setup_type": result.raw.get("setup_type"),
                                "setup_timeframe": result.raw.get("setup_timeframe"),
                                "candidate_key": candidate_key,
                            },
                        )
            elif not classification.reason.startswith("provider_error"):
                shadow_reason = "not_a_candidate:" + ",".join(classification.missing)

            return ScanOutcome(
                result,
                decision.sent,
                suppression_reason,
                storage_id,
                shadow_id,
                shadow_reason,
            )

    MultiSetupOptionsScanner.__name__ = base_cls.__name__
    MultiSetupOptionsScanner.__qualname__ = base_cls.__qualname__
    return MultiSetupOptionsScanner
