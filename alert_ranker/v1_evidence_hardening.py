"""Options Paper V1 evidence hardening.

This layer is deliberately advisory/paper-only.  It adds the evidence needed to
answer the operator's post-sample decomposition questions without weakening the
existing trade gate:

* mechanically valid signals rejected by filters are tracked as COUNTERFACTUAL
  observations, never alerts and never aggregate-risk consumers;
* 1H and session-anchored 4H Strat observations are collected from the same
  causal 30m source bars so timeframe comparisons are possible;
* exact-contract outcomes keep explicit snapshot-path ambiguity telemetry;
* the existing active 30m/Daily V1 path remains authoritative for alerts.

No broker/order capability is imported or added here.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from options_manager.levels import LevelFinderInputs, find_targets

from .causal_bars import Bar, completed_bars, session_bars
from .contract_marks import record_contract_mark
from .daily_strat import evaluate_daily_setup
from .lifecycle import classify_candidate, open_candidate_fields
from .multisetup_scanner import _candidate_identity
from .paper_v1 import (
    POLICY_ID,
    build_v1_contract_fields,
    choose_contract,
    choose_expiration,
    data_invalid,
    episode_key,
)
from .scanner_legacy import (
    ScanOutcome,
    _clear_external_contract_fields,
    _float_or_none,
    _mid_from_raw,
    _provider_snapshot,
    _selected_contract,
    _shadow_setup_inputs,
)

COUNTERFACTUAL_LANE = "COUNTERFACTUAL"
ACTIVE_LANE = "ACTIVE"
TIMEFRAME_1H = "1H"
TIMEFRAME_4H = "4H_RTH"


def _long_short(direction: str | None) -> str | None:
    if direction == "CALL":
        return "LONG"
    if direction == "PUT":
        return "SHORT"
    return None


def _aggregate(members: Sequence[Bar], start: datetime) -> Bar | None:
    ordered = sorted(members, key=lambda bar: bar.start_utc)
    if not ordered:
        return None
    return Bar(
        start=start,
        open=ordered[0].open,
        high=max(bar.high for bar in ordered),
        low=min(bar.low for bar in ordered),
        close=ordered[-1].close,
        volume=sum(bar.volume for bar in ordered),
        vwap=None,
    )


def _timeframe_series(
    *,
    bars: Sequence[Bar],
    source_timeframe,
    sessions,
    current_session,
    cutoff: datetime,
    span: timedelta,
) -> tuple[list[Bar], Bar | None]:
    """Build completed prior candles plus the current causal partial candle.

    Groups are anchored to each regular-session open.  On completed sessions the
    final shortened RTH group is retained (e.g. 13:30-16:00 for a 4H block), and
    on the current session only source intervals already closed by ``cutoff``
    participate.  This definition is explicit so 4H evidence is never confused
    with a vendor/native 4H candle whose session construction has not been
    validated.
    """
    closed = completed_bars(bars, source_timeframe, cutoff)
    prior: list[Bar] = []
    current_partial: Bar | None = None

    for session in sessions:
        kept = session_bars(
            closed,
            source_timeframe,
            session.open,
            session.close,
        )
        by_start = {bar.start_utc: bar for bar in kept}
        anchor = session.open.astimezone(timezone.utc)
        session_close = session.close.astimezone(timezone.utc)
        is_current = session.date == current_session.date

        while anchor < session_close:
            group_end = min(anchor + span, session_close)
            expected_starts: list[datetime] = []
            cursor = anchor
            while cursor + source_timeframe.delta <= group_end:
                expected_starts.append(cursor)
                cursor += source_timeframe.delta

            available = [by_start[start] for start in expected_starts if start in by_start]
            group_complete = bool(expected_starts) and len(available) == len(expected_starts)
            end_known = (not is_current) or group_end <= cutoff.astimezone(timezone.utc)

            if group_complete and end_known:
                candle = _aggregate(available, anchor)
                if candle is not None:
                    prior.append(candle)
            elif is_current and anchor < cutoff.astimezone(timezone.utc) < group_end:
                partial_starts = [
                    start
                    for start in expected_starts
                    if start + source_timeframe.delta <= cutoff.astimezone(timezone.utc)
                ]
                partial_members = [by_start[start] for start in partial_starts if start in by_start]
                if partial_members:
                    current_partial = _aggregate(partial_members, anchor)
            anchor += span

    return prior, current_partial


def _renamed_setup_type(setup_type: str | None, prefix: str) -> str | None:
    if not setup_type:
        return None
    if setup_type.startswith("DAILY_"):
        return prefix + setup_type[len("DAILY_") :]
    return f"{prefix}{setup_type}"


def _observer_candidate(
    *,
    prior: Sequence[Bar],
    current: Bar | None,
    timeframe: str,
    prefix: str,
    filter_reason: str,
) -> dict[str, Any] | None:
    verdict = evaluate_daily_setup(prior, current)
    setup_type = _renamed_setup_type(verdict.setup_type, prefix)
    if verdict.watching:
        return {
            "paper_candidate_id": f"{timeframe}:{setup_type or prefix + '32_DEVELOPING'}",
            "setup_type": setup_type or prefix + "32_DEVELOPING",
            "setup_timeframe": timeframe,
            "timeframe": timeframe,
            "pattern": verdict.sequence or "strat_32_developing",
            "strat_sequence": verdict.sequence or "strat_32_developing",
            "setup_status": "WATCH",
            "setup_reason_code": verdict.reason_code,
            "counterfactual_observer": True,
            "paper_evidence_lane": COUNTERFACTUAL_LANE,
            "counterfactual_filter_reason": filter_reason,
            "daily_previous_high": verdict.previous_high,
            "daily_previous_low": verdict.previous_low,
            "daily_two_back_type": verdict.two_back_type,
            "daily_previous_type": verdict.previous_type,
            "daily_current_type": verdict.current_type,
        }
    if not verdict.triggered or not setup_type:
        return None

    target_1 = target_2 = rr_1 = rr_2 = None
    target_reason = "observer_targets_unresolved"
    resistance = tuple(sorted({bar.high for bar in prior}))
    support = tuple(sorted({bar.low for bar in prior}, reverse=True))
    if verdict.direction in {"CALL", "PUT"} and verdict.entry_trigger is not None and verdict.invalidation is not None:
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

    return {
        "paper_candidate_id": f"{timeframe}:{setup_type}:COUNTERFACTUAL",
        "setup_type": setup_type,
        "setup_timeframe": timeframe,
        "timeframe": timeframe,
        "pattern": verdict.sequence,
        "strat_sequence": verdict.sequence,
        "direction": _long_short(verdict.direction),
        "setup_direction": verdict.direction,
        "setup_entry_trigger": verdict.entry_trigger,
        "underlying_invalidation": verdict.invalidation,
        "stop": verdict.invalidation,
        "target": target_1,
        "target_1": target_1,
        "target_2": target_2,
        "setup_rr_1": rr_1,
        "setup_rr_2": rr_2,
        "setup_target_reason": target_reason,
        "setup_status": "OBSERVE",
        "mechanical_signal_status": "TRIGGERED",
        "setup_proof_status": "MECHANICAL_ONLY",
        "setup_market_status": "OBSERVE_ONLY",
        "trade_proof_status": "OBSERVE_ONLY",
        "counterfactual_observer": True,
        "paper_evidence_lane": COUNTERFACTUAL_LANE,
        "counterfactual_filter_reason": filter_reason,
        "daily_previous_high": verdict.previous_high,
        "daily_previous_low": verdict.previous_low,
        "daily_two_back_type": verdict.two_back_type,
        "daily_previous_type": verdict.previous_type,
        "daily_current_type": verdict.current_type,
    }


def _target_hit(direction: str, target: float | None, price: float | None) -> bool:
    if target is None or price is None:
        return False
    side = str(direction or "").upper()
    if side == "LONG":
        return price >= target
    if side == "SHORT":
        return price <= target
    return False


def build_v1_evidence_hardening(base_cls):
    """Wrap the existing multi-setup scanner with evidence-only additions."""

    class V1EvidenceHardenedScanner(base_cls):
        async def _fetch_bar_context(self, ticker: str, now: datetime) -> dict[str, Any]:
            fields = await super()._fetch_bar_context(ticker, now)
            candidates = list(fields.get("paper_setup_candidates") or [])

            # A mechanically triggered Daily signal that failed market/target
            # promotion must remain observable so later filter analysis is not
            # survivor-biased. It is never upgraded to a trade candidate here.
            rewritten: list[dict[str, Any]] = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                item = dict(candidate)
                if item.get("setup_status") == "INVALID" and item.get("setup_type"):
                    original_reason = str(
                        item.get("setup_suppression_reason")
                        or item.get("setup_reason_code")
                        or "daily_filter_rejected"
                    )
                    item.update(
                        {
                            "setup_status": "OBSERVE",
                            "mechanical_signal_status": "TRIGGERED",
                            "counterfactual_observer": True,
                            "paper_evidence_lane": COUNTERFACTUAL_LANE,
                            "counterfactual_filter_reason": original_reason,
                            "setup_suppression_reason": "counterfactual_observer_only",
                        }
                    )
                rewritten.append(item)
            candidates = rewritten

            # The original 30m authority also exposes a confirmed sequence before
            # market promotion. Preserve a counterfactual row when that proof is
            # filtered out, instead of losing the rejected population entirely.
            if (
                fields.get("setup_sequence_confirmed")
                and str(fields.get("setup_status") or "").upper() != "TRIGGERED"
                and fields.get("setup_direction") in {"CALL", "PUT"}
            ):
                direction = str(fields.get("setup_direction"))
                candidates.append(
                    {
                        "paper_candidate_id": "30m:STRAT_212_CONTINUATION:COUNTERFACTUAL",
                        "setup_type": "STRAT_212_CONTINUATION",
                        "setup_timeframe": "30m",
                        "timeframe": "30m",
                        "pattern": fields.get("strat_sequence") or "strat_212",
                        "strat_sequence": fields.get("strat_sequence") or "strat_212",
                        "direction": _long_short(direction),
                        "setup_direction": direction,
                        "setup_entry_trigger": fields.get("setup_entry_trigger"),
                        "underlying_invalidation": fields.get("underlying_invalidation"),
                        "stop": fields.get("underlying_invalidation"),
                        "target": fields.get("target_1"),
                        "target_1": fields.get("target_1"),
                        "target_2": fields.get("target_2"),
                        "setup_rr_1": fields.get("setup_rr_1"),
                        "setup_rr_2": fields.get("setup_rr_2"),
                        "setup_target_reason": fields.get("setup_target_reason"),
                        "setup_status": "OBSERVE",
                        "mechanical_signal_status": "TRIGGERED",
                        "setup_proof_status": "MECHANICAL_ONLY",
                        "setup_market_status": fields.get("setup_market_status") or "OBSERVE_ONLY",
                        "counterfactual_observer": True,
                        "paper_evidence_lane": COUNTERFACTUAL_LANE,
                        "counterfactual_filter_reason": fields.get("setup_suppression_reason")
                        or "30m_market_filter_rejected",
                    }
                )

            # 1H and session-anchored 4H are evidence-only populations. Fetching
            # the symbol bars a second time is intentionally isolated from the
            # authoritative active path: any failure here leaves the original
            # 30m/Daily decision untouched and is recorded as telemetry.
            if self._causal_lane_active() and fields.get("bar_context_available") and self.bar_context is not None:
                builder = self.bar_context
                try:
                    cutoff = now.astimezone(timezone.utc) - builder.delay_buffer
                    session = await builder._session_for(cutoff)
                    if session is not None:
                        start = cutoff - timedelta(days=builder.lookback_days)
                        required = await builder._sessions_in_window(start, session)
                        raw = await builder.provider.fetch_bars(
                            [(ticker or "").strip().upper()],
                            builder.timeframe,
                            start,
                            cutoff,
                        )
                        symbol_bars = raw.get((ticker or "").strip().upper(), [])
                        for timeframe, prefix, span in (
                            (TIMEFRAME_1H, "H1_", timedelta(hours=1)),
                            (TIMEFRAME_4H, "H4_", timedelta(hours=4)),
                        ):
                            prior, current = _timeframe_series(
                                bars=symbol_bars,
                                source_timeframe=builder.timeframe,
                                sessions=required,
                                current_session=session,
                                cutoff=cutoff,
                                span=span,
                            )
                            candidate = _observer_candidate(
                                prior=prior,
                                current=current,
                                timeframe=timeframe,
                                prefix=prefix,
                                filter_reason="timeframe_observation_only",
                            )
                            if candidate is not None:
                                candidates.append(candidate)
                except Exception as exc:  # noqa: BLE001 - observer failure cannot alter active lane
                    fields["timeframe_observer_error"] = f"{type(exc).__name__}"

            fields["paper_setup_candidates"] = candidates
            fields["paper_setup_candidate_count"] = len(candidates)
            return fields

        async def scan_ticker(
            self,
            ticker: str,
            *,
            source: str,
            context: dict[str, Any] | None = None,
            now=None,
        ):
            """Preserve primary API semantics while labeling extra populations honestly."""
            now = now or datetime.now(ZoneInfo(self.config.timezone))
            normalized = await self._build_normalized_data(ticker, context or {}, now)
            extra_candidates = normalized.get("paper_setup_candidates")
            primary = await self._process_normalized_candidate(
                ticker,
                {key: value for key, value in normalized.items() if key != "paper_setup_candidates"},
                source=source,
                now=now,
            )
            if isinstance(extra_candidates, list):
                for index, candidate in enumerate(extra_candidates, start=1):
                    if not isinstance(candidate, dict):
                        continue
                    data = self._overlay_candidate(normalized, candidate)
                    data["paper_setup_candidate_index"] = index
                    data["paper_setup_candidate_count"] = len(extra_candidates)
                    suffix = str(candidate.get("setup_timeframe") or "evidence").lower()
                    await self._process_normalized_candidate(
                        ticker,
                        data,
                        source=f"{source}:{suffix}",
                        now=now,
                    )
            return primary

        async def _apply_paper_v1_contract(self, ticker, normalized, direction, now):
            if not normalized.get("counterfactual_observer"):
                data = await super()._apply_paper_v1_contract(ticker, normalized, direction, now)
                if data.get("paper_policy_status") == "VALID":
                    data.setdefault("paper_evidence_lane", ACTIVE_LANE)
                    data.setdefault("risk_budget_consumed", True)
                return data

            data = _clear_external_contract_fields(normalized)
            data["paper_policy_id"] = POLICY_ID
            data["paper_evidence_lane"] = COUNTERFACTUAL_LANE
            data["risk_budget_consumed"] = False

            setup_direction = str(normalized.get("setup_direction") or "").upper()
            effective_direction = "LONG" if setup_direction == "CALL" else "SHORT" if setup_direction == "PUT" else direction
            if effective_direction not in {"LONG", "SHORT"}:
                data.update(data_invalid("direction_unknown"))
                return data

            invalidation = (
                normalized.get("underlying_invalidation")
                or normalized.get("invalidation")
                or normalized.get("stop")
                or normalized.get("stop_level")
            )
            target_1 = normalized.get("target_1") or normalized.get("target")
            if invalidation in (None, ""):
                data.update(data_invalid("underlying_invalidation_missing"))
                return data
            if target_1 in (None, ""):
                data.update(data_invalid("target_missing"))
                return data

            fetch_expirations = getattr(self.market_data, "fetch_option_expirations", None)
            fetch_chain = getattr(self.market_data, "fetch_option_chain", None)
            if not callable(fetch_expirations) or not callable(fetch_chain):
                data.update(data_invalid("option_chain_provider_unavailable"))
                return data
            try:
                expirations = await fetch_expirations(ticker)
            except Exception as exc:  # noqa: BLE001
                data.update(data_invalid(f"expiration_fetch_error:{type(exc).__name__}"))
                return data
            expiry_decision = choose_expiration(expirations, now)
            if not expiry_decision.valid or expiry_decision.expiry is None:
                data.update(data_invalid(expiry_decision.reason or "expiration_invalid"))
                return data
            try:
                chain = await fetch_chain(ticker, expiry_decision.expiry.expiration)
            except Exception as exc:  # noqa: BLE001
                data.update(data_invalid(f"chain_fetch_error:{type(exc).__name__}"))
                return data
            if getattr(chain, "error", None):
                data.update(data_invalid(f"chain_error:{chain.error}"))
                return data
            if str(getattr(chain, "expiration", "") or "")[:10] != expiry_decision.expiry.expiration:
                data.update(data_invalid("chain_expiration_mismatch"))
                return data

            side = "CALL" if effective_direction == "LONG" else "PUT"
            contracts = getattr(chain, "calls", ()) if side == "CALL" else getattr(chain, "puts", ())
            contract_decision = choose_contract(
                contracts,
                option_type=side,
                underlying_price=_float_or_none(normalized.get("price")),
            )
            if not contract_decision.valid or contract_decision.contract is None:
                data.update(data_invalid(contract_decision.reason or "contract_invalid"))
                return data

            # Counterfactuals keep the same per-trade contract/risk quality but
            # are intentionally evaluated outside the active $1,000 aggregate
            # budget.  They represent "what the filter rejected", not positions.
            fields, reason = build_v1_contract_fields(
                expiry=expiry_decision.expiry,
                contract=contract_decision.contract,
                underlying_invalidation=invalidation,
                target_1=target_1,
                aggregate_open_risk=0.0,
            )
            if fields is None:
                data.update(data_invalid(reason or "risk_invalid"))
                return data
            fields["aggregate_open_planned_risk_before"] = None
            fields["projected_aggregate_open_planned_risk"] = None
            fields["paper_evidence_lane"] = COUNTERFACTUAL_LANE
            fields["risk_budget_consumed"] = False
            data.update(fields)
            data["stop"] = _float_or_none(invalidation)
            data["target"] = _float_or_none(target_1)
            data["target_1"] = _float_or_none(target_1)
            return data

        async def _process_normalized_candidate(self, ticker, normalized, *, source, now):
            outcome = await super()._process_normalized_candidate(
                ticker, normalized, source=source, now=now
            )
            if not normalized.get("counterfactual_observer") or outcome.shadow_id:
                return outcome

            result = outcome.result
            classification = classify_candidate({**result.raw, "direction": result.direction})
            if (
                not classification.is_open_eligible
                or result.raw.get("paper_policy_id") != POLICY_ID
                or result.raw.get("paper_policy_status") != "VALID"
            ):
                return outcome

            base_key = _candidate_identity(classification.contract_key, result.raw)
            candidate_key = f"{base_key}|{COUNTERFACTUAL_LANE}"
            episode = episode_key(
                candidate_key, result.raw.get("setup_timeframe"), now
            )
            duplicate = self.storage.find_open_duplicate(result.ticker, candidate_key)
            if duplicate is not None:
                return ScanOutcome(
                    result,
                    outcome.alert_sent,
                    outcome.alert_suppression_reason,
                    outcome.storage_id,
                    0,
                    f"counterfactual_duplicate_open:{duplicate}",
                )
            episode_duplicate = self.storage.find_episode_duplicate(
                result.ticker, episode
            )
            if episode_duplicate is not None:
                return ScanOutcome(
                    result,
                    outcome.alert_sent,
                    outcome.alert_suppression_reason,
                    outcome.storage_id,
                    0,
                    f"counterfactual_duplicate_episode:{episode_duplicate}",
                )

            selected = _selected_contract(result.raw)
            selected.update(open_candidate_fields(result.raw, classification.contract_key))
            selected["option_contract_key"] = classification.contract_key
            selected["contract_key"] = candidate_key
            selected["candidate_key"] = candidate_key
            selected["episode_key"] = episode
            selected["paper_evidence_lane"] = COUNTERFACTUAL_LANE
            selected["risk_budget_consumed"] = False
            selected["setup_type"] = result.raw.get("setup_type")
            selected["setup_timeframe"] = result.raw.get("setup_timeframe")
            selected["counterfactual_filter_reason"] = result.raw.get(
                "counterfactual_filter_reason"
            )
            shadow_id = self.storage.record_shadow_setup(
                result,
                scan_id=outcome.storage_id,
                setup_inputs=_shadow_setup_inputs(result.raw),
                provider_snapshot=_provider_snapshot(result.raw),
                selected_contract=selected,
                timestamp=now,
            )
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
                    "paper_evidence_lane": COUNTERFACTUAL_LANE,
                    "risk_budget_consumed": False,
                    "setup_type": result.raw.get("setup_type"),
                    "setup_timeframe": result.raw.get("setup_timeframe"),
                    "candidate_key": candidate_key,
                    "counterfactual_filter_reason": result.raw.get(
                        "counterfactual_filter_reason"
                    ),
                },
            )
            return ScanOutcome(
                result,
                outcome.alert_sent,
                outcome.alert_suppression_reason,
                outcome.storage_id,
                shadow_id,
                "counterfactual_open",
            )

        async def _resolve_v1_candidate(self, setup, underlying_price, now, chain_cache):
            resolution = await super()._resolve_v1_candidate(
                setup, underlying_price, now, chain_cache
            )
            if resolution is None:
                return None
            status, outcome = resolution
            enriched = dict(outcome)
            enriched["resolution_sampling"] = "scheduled_snapshot"
            enriched["intra_interval_path_known"] = False

            contract = setup.selected_contract or {}
            bid = _float_or_none(enriched.get("option_bid_at_resolution"))
            premium_stop = _float_or_none(contract.get("premium_stop"))
            target = _float_or_none(contract.get("target"))
            same_snapshot_both = bool(
                bid is not None
                and premium_stop is not None
                and bid <= premium_stop
                and _target_hit(setup.direction, target, underlying_price)
            )
            if same_snapshot_both:
                # Pessimistic accounting is retained, but the row can no longer
                # masquerade as an unambiguous loss. Later studies can include
                # or exclude these rows explicitly.
                status = "LOSS"
                enriched.update(
                    {
                        "resolution_ambiguity": "AMBIGUOUS",
                        "ambiguity_reason": "premium_stop_and_underlying_target_true_same_snapshot",
                        "pessimistic_status": "LOSS",
                        "pessimistic_resolution_used": True,
                        "closed_reason": "ambiguous_same_snapshot_pessimistic_loss",
                    }
                )
            else:
                enriched.setdefault("resolution_ambiguity", "PATH_UNOBSERVED_BETWEEN_SNAPSHOTS")
                enriched.setdefault("pessimistic_resolution_used", False)
            return status, enriched

    V1EvidenceHardenedScanner.__name__ = base_cls.__name__
    V1EvidenceHardenedScanner.__qualname__ = base_cls.__qualname__
    return V1EvidenceHardenedScanner
