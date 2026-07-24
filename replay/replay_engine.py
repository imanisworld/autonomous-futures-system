"""
replay/replay_engine.py

Offline replay of historical or synthetic candles through the existing
decision, risk, paper broker, journal, and review path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from agent.daily_summary import DailySummaryAgent
from config.settings import SystemConfig, load_config
from context.bar_history import BarHistory
from context.market_context import (
    MarketState,
    GEXContext,
    HTFContext,
    ICCContext,
    KeyLevels,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    SignaContext,
    SupplyDemandData,
    TrendData,
    VWAPData,
    VolumeData,
)
from execution.broker_interface import BracketOrder
from execution.day_only_exit import (
    EOD_BAR_MISSING,
    is_after_eod_close,
    is_exact_eod_bar,
    resolve_paper_eod,
    strategy_is_day_only,
)
from execution.paper_broker import NextBarOHLC, PaperBroker
from execution.post_fill_validation import TICK_SIZE as EXEC_TICK_SIZE, TICK_VALUE as EXEC_TICK_VALUE
from journal.journal_logger import JournalLogger
from context.htf_loader import HTFLookup
from context.live_direction import apply_live_direction
from context.trend import moderate_subtype
from replay.candle_loader import ReplayCandle, ReplayCandleLoader
from replay.manifest import ReplayManifest
from replay.replay_report import MultiDayReplayReport, ReplayReport
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy.confluence_scorer import score_setup as _score_setup
from strategy.stop_sizing import apply_stop_multiplier
from strategy.strat_212_122 import STRAT_122, STRAT_212
from strategy.shadow_setups import evaluate_shadow_setups, resolve_shadow_candidate
from strategy.signal_engine import DecisionEngine
from strategy.strat_classifier import StratContext, classify_from_ohlc

_DEFAULT_HTF_FILES = {
    "1D": "data/htf/CME_MINI_MNQ1!_1D.jsonl",
    "4H": "data/htf/CME_MINI_MNQ1!_240_(1).jsonl",
}


class ReplayEngine:
    """Runs local candle files through the paper system without live data."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        log_dir: str = "logs/replay",
        htf_lookup: Optional[HTFLookup] = None,
    ):
        self.config = config or load_config()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.htf = htf_lookup or self._load_default_htf()
        self._rolling_balance: float | None = None
        # Rolling per-instrument 15m history for htf_direction_source=live —
        # persists across days in run_many/run_manifest so the first bars of a
        # day still have a prior 4h window (like BarHistory's lookback live).
        self._live_dir_bars: dict[str, deque] = {}
        self._research_bars: dict[str, deque] = {}
        # Canonical 4HR input history.  Kept across run_many day boundaries so
        # Monday can see Sunday/Friday references exactly as live BarHistory can.
        self._four_hr_bars: dict[str, deque] = {}

    @staticmethod
    def _load_default_htf() -> HTFLookup:
        lookup = HTFLookup()
        for tf, path in _DEFAULT_HTF_FILES.items():
            p = Path(path)
            if p.exists():
                lookup.load(p, timeframe=tf)
        return lookup

    def run(
        self,
        candle_path: str | Path,
        review_date: Optional[str] = None,
        *,
        allow_mixed_instruments: bool = False,
    ) -> ReplayReport:
        candles = ReplayCandleLoader().load_jsonl(
            candle_path,
            allow_mixed_instruments=allow_mixed_instruments,
        )
        if not candles:
            return self._empty_report(candle_path, review_date)

        run_date = review_date or _date_from_timestamp(candles[0].timestamp)
        # Continuation observers are intraday studies. Never let the previous
        # replay file/day seed the next day's impulse or consolidation.
        self._research_bars.clear()
        self._reset_run_outputs(run_date)
        journal = JournalLogger(log_dir=str(self.log_dir))
        journal_date = _date_to_date(run_date)
        decision_engine = DecisionEngine(config=self.config)
        risk_engine = RiskEngine(config=self.config)
        broker = PaperBroker(
            starting_balance=(
                self._rolling_balance
                if self._rolling_balance is not None
                else self.config.position_sizing.starting_balance
            ),
            slippage_ticks=float(getattr(self.config, "fill_slippage_ticks", 0.0) or 0.0),
            pessimistic_both_hit=bool(getattr(self.config, "fill_pessimistic_both_hit", False)),
            breakeven_at_1r=bool(getattr(self.config, "breakeven_at_1r", True)),
            runner_mode=bool(getattr(self.config, "runner_mode", False)),
            runner_activation_r=float(getattr(self.config, "runner_activation_r", 1.0) or 1.0),
            runner_trail_r=float(getattr(self.config, "runner_trail_r", 0.5) or 0.5),
            entry_fill_model=str(getattr(self.config, "entry_fill_model", "market") or "market"),
            entry_tolerance_ticks_by_root=dict(
                getattr(self.config, "entry_tolerance_ticks_by_root", {}) or {}
            ),
        )
        daily_state = DailyState(
            date=run_date,
            account_balance=broker.get_account_balance(),
        )

        stopped_reason: str | None = None
        candles_processed = 0
        skip_to = 0  # index of first bar available after an open position resolves
        # Keyed by (instrument, timeframe), not a single global pair: run()
        # supports allow_mixed_instruments=True, where candles from different
        # instruments can interleave in one sequence. A global prev_candle/
        # prev_prev_candle would let one instrument's bars leak into
        # another's vwap_reclaimed/failed_reclaim derivation (e.g. an MNQ
        # reclaim immediately preceding an unrelated MES bar in the merged
        # stream would falsely arm MES's failed_reclaim). Each key's history
        # only ever sees that instrument+timeframe's own authoritative bars.
        prev_candle_by_key: dict[tuple[str, str], ReplayCandle] = {}
        prev_prev_candle_by_key: dict[tuple[str, str], ReplayCandle] = {}

        for idx, candle in enumerate(candles):
            candles_processed += 1
            # Live-direction history sees EVERY candle (including skipped
            # ones), mirroring BarHistory recording every ingested bar live.
            if getattr(self.config, "htf_direction_source", "payload") == "live":
                self._live_dir_bars.setdefault(
                    candle.instrument, deque(maxlen=120)
                ).append(
                    {
                        "ts": candle.timestamp,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                    }
                )
            self._research_bars.setdefault(
                candle.instrument, deque(maxlen=8)
            ).append(
                {
                    "ts": candle.timestamp,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
            )
            if str(candle.timeframe).strip().lower() in {
                "5", "5m", "5min", "5minute", "5minutes"
            }:
                self._four_hr_bars.setdefault(
                    candle.instrument, deque(maxlen=5000)
                ).append(
                    {
                        "ts": candle.timestamp,
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                        "timeframe": candle.timeframe,
                    }
                )
            candle_key = (candle.instrument, candle.timeframe)
            if idx < skip_to:
                prev_prev_candle_by_key[candle_key] = prev_candle_by_key.get(candle_key)
                prev_candle_by_key[candle_key] = candle
                continue

            if daily_state.consecutive_losses >= self.config.max_consecutive_losses:
                stopped_reason = "max_consecutive_losses"
                break
            total_daily_capacity = (
                self.config.max_trades_per_day
                + int(getattr(self.config, "bonus_trades_after_max", 0) or 0)
            )
            if daily_state.trade_count >= total_daily_capacity:
                stopped_reason = "max_trades_per_day"
                break

            state = self._market_state_from_candle(
                candle,
                prev_candle_by_key.get(candle_key),
                prev_prev_candle_by_key.get(candle_key),
            )
            state.bar_history_5m = list(
                self._four_hr_bars.get(candle.instrument, ())
            )
            # Effective-parity: live (webhook/runner.py) sets this from the
            # last 6 bars of its persisted BarHistory right after recording
            # the current bar — a continuous-data directional read that lets
            # DecisionEngine veto a CHOPPY label into RANGE_BOUND even when
            # Pine's own strat/trend fields don't independently confirm it
            # (see DecisionEngine._has_directional_structure). Replay has no
            # persisted BarHistory, so it uses _research_bars — the same
            # per-instrument rolling window already fed every candle for
            # shadow-setup evaluation below, and already cleared per run()
            # call for the same "don't let a prior day seed this one" reason
            # BarHistory.recent's 3-day lookback does not need to honor here.
            state.window_direction = BarHistory.window_direction(
                list(self._research_bars.get(candle.instrument, ()))[-6:]
            )
            if getattr(self.config, "htf_direction_source", "payload") == "live":
                apply_live_direction(
                    state, self._live_dir_bars.get(candle.instrument, ())
                )
            # Shadow setups: audit-only observation (read-only, never trades).
            # Each candidate is resolved against the remaining bars of the day so
            # the journal records WIN/LOSS/NO_FILL — turning observation into
            # measurable edge — without touching the broker or daily_state.
            try:
                forward_bars = [(c.high, c.low) for c in candles[idx + 1:]]
                shadow_candidates = []
                for cand in evaluate_shadow_setups(
                    state, list(self._research_bars.get(candle.instrument, ()))
                ):
                    record = cand.to_dict()
                    record["outcome"] = resolve_shadow_candidate(
                        cand, forward_bars, instrument=state.instrument
                    ).to_dict()
                    shadow_candidates.append(record)
            except Exception:
                shadow_candidates = []
            decision = decision_engine.evaluate(state, daily_state)
            risk_result_dict = None

            prev_prev_candle_by_key[candle_key] = prev_candle_by_key.get(candle_key)
            prev_candle_by_key[candle_key] = candle

            if decision.decision == "TRADE" and decision.setup is not None:
                # Per-instrument stop-width multiplier — shared with webhook.runner
                # via strategy.stop_sizing so live and replay can never diverge.
                apply_stop_multiplier(
                    decision.setup, state.instrument,
                    getattr(self.config, "stop_multiplier_per_instrument", None),
                )
                confluence = _score_setup(state, decision.setup)
                journal_entry = decision.to_dict()
                journal_entry["strategy_state"] = {
                    "strat_4hr_retrigger": dict(
                        daily_state.four_hr_retrigger_state
                    ),
                    "strat_212_122": dict(daily_state.strat_212_122_state),
                }
                # Persist the historical candle time (the record's own `ts` is the
                # wall-clock replay-run time) so downstream analysis — e.g. the MFE
                # study — can locate the trade's real price window.
                journal_entry["bar_ts"] = candle.timestamp
                if shadow_candidates:
                    journal_entry["shadow_candidates"] = shadow_candidates
                journal_entry["confluence"] = {
                    "score": confluence.score,
                    "grade": confluence.grade,
                    "factors": confluence.factors,
                    "penalties": confluence.penalties,
                }
                trade_setup = TradeSetup(
                    direction=decision.setup.direction,
                    entry=decision.setup.entry,
                    stop=decision.setup.stop,
                    target=decision.setup.target,
                    rr_ratio=decision.setup.rr_ratio,
                    strategy=decision.setup.strategy,
                    instrument=state.instrument,
                    session=state.session,
                    notes=decision.setup.notes,
                    entry_time=(
                        decision.setup.entry_time
                        or _parse_timestamp(candle.timestamp)
                    ),
                    confluence_grade=confluence.grade,
                )
                daily_state.account_balance = broker.get_account_balance()
                trade_setup.contracts = risk_engine.recommended_contracts(
                    state.instrument, daily_state.account_balance
                )
                risk_result = risk_engine.validate(trade_setup, daily_state)
                risk_result_dict = {
                    "result": risk_result.result,
                    "failed_rule": risk_result.failed_rule,
                    "reason": risk_result.reason,
                }
                if not risk_result.approved:
                    journal_entry["decision"] = "RISK_REJECTED"
                    journal_entry["reason"] = (
                        risk_result.reason or journal_entry.get("reason")
                    )
                    failed_gates = list(journal_entry.get("failed_gates") or [])
                    if (
                        risk_result.failed_rule
                        and risk_result.failed_rule not in failed_gates
                    ):
                        journal_entry["failed_gates"] = (
                            failed_gates + [risk_result.failed_rule]
                        )

                journal.log_decision(journal_entry, risk_result_dict, for_date=journal_date)

                if risk_result.approved and decision.setup.pre_resolved is not None:
                    # strat_212/122 same-bar RESOLVED case (see
                    # strategy/strat_212_122.py): the armed boundary AND
                    # either the target or the opposite (stop) boundary were
                    # both reached on the same watched bar, so this already
                    # resolved (WIN or pessimistic LOSS) before this decision
                    # was even evaluated. Never submit it as an order —
                    # replay is always PaperBroker, so this is always the
                    # evidence-journaling path (see webhook/runner.py for the
                    # live/Tradovate refusal branch of the same case).
                    _pre = decision.setup.pre_resolved
                    contracts = trade_setup.contracts
                    broker.restore_position(
                        instrument=state.instrument,
                        direction=decision.setup.direction,
                        entry=decision.setup.entry,
                        stop=decision.setup.stop,
                        target=decision.setup.target,
                        contracts=contracts,
                    )
                    resolved_fill = broker.force_resolve(
                        _pre["result"], float(_pre["exit_price"])
                    )
                    resolved_fill.exit_reason = _pre["exit_reason"]
                    journal.log_outcome(
                        instrument=resolved_fill.instrument,
                        session=state.session,
                        result=resolved_fill.result,
                        entry_price=resolved_fill.entry_price,
                        exit_price=resolved_fill.exit_price,
                        exit_reason=resolved_fill.exit_reason,
                        pnl_ticks=resolved_fill.pnl_ticks,
                        pnl_dollars=resolved_fill.pnl_dollars,
                        contracts=resolved_fill.contracts,
                        for_date=journal_date,
                        strategy=decision.setup.strategy,
                        execution_audit={
                            "source": "strat_212_122_same_bar_resolution"
                        },
                    )
                    daily_state.trade_count += 1
                    daily_state.account_balance = broker.get_account_balance()
                    daily_state.realized_pnl_dollars += float(
                        resolved_fill.pnl_dollars or 0.0
                    )
                    if resolved_fill.result == "LOSS":
                        daily_state.consecutive_losses += 1
                        daily_state.last_loss_at = _parse_timestamp(candle.timestamp)
                    elif resolved_fill.result in ("WIN", "BREAKEVEN"):
                        daily_state.consecutive_losses = 0
                    continue

                if risk_result.approved:
                    contracts = trade_setup.contracts
                    # strategy/strat_212_122.py's causal resolver only ever
                    # hands an "OPEN" (non-pre_resolved) candidate back once
                    # the watched bar has already shown entry triggering at
                    # its causal fill price (decision.setup.entry —
                    # gap-through-at-open or the exact boundary, never the
                    # naive structural level chased at post-close price).
                    # The position already exists as of this bar's close:
                    # restore it directly rather than submitting a fresh
                    # order through the normal fill-model path, then join
                    # the SAME future-bar resolution every other strategy
                    # uses below. Never submitted on a live broker at all —
                    # see webhook/runner.py's refusal branch for that case
                    # (replay is always PaperBroker, so this path always
                    # applies here when it's this pair of strategies).
                    if decision.setup.strategy in (STRAT_212, STRAT_122):
                        broker.restore_position(
                            instrument=state.instrument,
                            direction=decision.setup.direction,
                            entry=decision.setup.entry,
                            stop=decision.setup.stop,
                            target=decision.setup.target,
                            contracts=contracts,
                        )
                        entry_fill = None
                    else:
                        order = BracketOrder(
                            instrument=state.instrument,
                            direction=decision.setup.direction,
                            entry=decision.setup.entry,
                            stop=decision.setup.stop,
                            target=decision.setup.target,
                            rr_ratio=decision.setup.rr_ratio,
                            strategy=decision.setup.strategy,
                            notes=decision.setup.notes,
                            contracts=contracts,
                            min_rr_ratio=float(getattr(self.config, "min_rr_ratio", 2.0)),
                            max_dollar_risk=(
                                (
                                    abs(float(decision.setup.entry) - float(decision.setup.stop))
                                    / EXEC_TICK_SIZE.get(state.instrument, 0.25)
                                    + float(
                                        (getattr(self.config, "entry_tolerance_ticks_by_root", {}) or {}).get(
                                            state.instrument, 0
                                        ) or 0
                                    )
                                )
                                * EXEC_TICK_VALUE.get(state.instrument, 1.25)
                                * contracts
                            ),
                            max_stop_ticks=float(
                                (getattr(self.config, "max_stop_ticks", {}) or {}).get(state.instrument, 0) or 0
                            ) or None,
                            max_slippage_ticks=float(
                                (getattr(self.config, "entry_tolerance_ticks_by_root", {}) or {}).get(state.instrument, 0) or 0
                            ) or None,
                            post_fill_validation_required=False,
                        )
                        entry_fill = broker.execute_bracket(order, market_price=candle.close)
                    if entry_fill is not None and entry_fill.result == "CANCELLED":
                        # IOC-faithful baseline: the entry self-cancelled (market
                        # beyond entry ± tolerance at decision time). Book it the
                        # way live does — CANCELLED/ENTRY_NOT_FILLED outcome, NO
                        # trade counted, no session budget consumed, no position.
                        journal.log_outcome(
                            instrument=entry_fill.instrument,
                            session=state.session,
                            result=entry_fill.result,
                            entry_price=entry_fill.entry_price,
                            exit_price=None,
                            exit_reason=entry_fill.exit_reason,
                            pnl_ticks=0.0,
                            pnl_dollars=0.0,
                            contracts=entry_fill.contracts,
                            for_date=journal_date,
                        )
                        continue
                    fill = None
                    day_only_trade = strategy_is_day_only(decision.setup.strategy)
                    trade_date = _date_from_timestamp(candle.timestamp)
                    for future_idx in range(idx + 1, len(candles)):
                        fc = candles[future_idx]
                        if day_only_trade and _date_from_timestamp(fc.timestamp) != trade_date:
                            break
                        if day_only_trade and is_after_eod_close(fc.timestamp):
                            # The exact closing bar was absent. Never let a later
                            # bar stand in as either a day-only exit or a bracket
                            # resolution after the mandated flat time.
                            break
                        fill = broker.resolve_position(
                            NextBarOHLC(open=fc.open, high=fc.high, low=fc.low)
                        )
                        if fill is not None:
                            if fill.result != "CANCELLED":
                                skip_to = future_idx + 1
                            break
                        if day_only_trade and is_exact_eod_bar(
                            fc.timestamp, fc.timeframe
                        ):
                            # Stop/target above has precedence on this same bar.
                            fill = resolve_paper_eod(
                                broker,
                                {
                                    "instrument": state.instrument,
                                    "direction": decision.setup.direction,
                                    "entry": entry_fill.entry_price,
                                    "contracts": contracts,
                                    "strategy": decision.setup.strategy,
                                },
                                timestamp=fc.timestamp,
                                timeframe=fc.timeframe,
                                close=fc.close,
                            )
                            if fill is not None:
                                skip_to = future_idx + 1
                            break
                    if fill is None and broker.has_pending_entry():
                        fill = broker.cancel_pending_entry("ENTRY_NO_NEXT_BAR")
                    if fill is not None:
                        journal.log_outcome(
                            instrument=fill.instrument,
                            session=state.session,
                            result=fill.result,
                            entry_price=fill.entry_price,
                            exit_price=fill.exit_price,
                            exit_reason=fill.exit_reason,
                            pnl_ticks=fill.pnl_ticks,
                            pnl_dollars=fill.pnl_dollars,
                            contracts=fill.contracts,
                            for_date=journal_date,
                        )
                        if fill.result == "CANCELLED":
                            continue
                        session_key = state.session or ""
                        if session_key:
                            daily_state.session_trade_counts[session_key] = (
                                daily_state.session_trade_counts.get(session_key, 0) + 1
                            )
                        daily_state.trade_count += 1
                        daily_state.realized_pnl_dollars += float(fill.pnl_dollars or 0.0)
                        if fill.result == "LOSS":
                            daily_state.consecutive_losses += 1
                            daily_state.last_loss_at = _parse_timestamp(fc.timestamp)
                        elif fill.result in ("WIN", "BREAKEVEN"):
                            daily_state.consecutive_losses = 0
                        daily_state.has_open_position = False
                        daily_state.account_balance = broker.get_account_balance()
                    else:
                        daily_state.trade_count += 1
                        daily_state.has_open_position = True
                        if day_only_trade:
                            journal.log_day_only_exit_issue(
                                instrument=state.instrument,
                                strategy=decision.setup.strategy,
                                reason=EOD_BAR_MISSING,
                                for_date=journal_date,
                            )
                continue

            journal_entry = decision.to_dict()
            journal_entry["strategy_state"] = {
                "strat_4hr_retrigger": dict(
                    daily_state.four_hr_retrigger_state
                ),
                "strat_212_122": dict(daily_state.strat_212_122_state),
            }
            # Persist the historical candle time (the record's own `ts` is the
            # wall-clock replay-run time) so shadow candidates can be re-resolved
            # offline — e.g. a runner-exit A/B that needs each setup's real bars.
            journal_entry["bar_ts"] = candle.timestamp
            if shadow_candidates:
                journal_entry["shadow_candidates"] = shadow_candidates
            journal.log_decision(journal_entry, risk_result_dict, for_date=journal_date)

        total_daily_capacity = (
            self.config.max_trades_per_day
            + int(getattr(self.config, "bonus_trades_after_max", 0) or 0)
        )
        if stopped_reason is None and daily_state.trade_count >= total_daily_capacity:
            stopped_reason = "max_trades_per_day"

        summary = journal.get_summary(_date_to_date(run_date))
        # Construct the review stack with the isolated replay directory from
        # the start. Reassigning log_dir after construction is too late because
        # DailySummaryAgent.__init__ creates config.log_dir immediately (and a
        # production /app/logs path is not writable in local/offline replay).
        review_config = replace(self.config, log_dir=str(self.log_dir))
        review = DailySummaryAgent(review_config)
        review_payload = review.eod(run_date)
        report = ReplayReport(
            source_path=str(candle_path),
            candles_processed=candles_processed,
            decisions=summary.get("trades", 0) + summary.get("no_trades", 0),
            approved_trades=summary.get("trades", 0),
            no_trades=summary.get("no_trades", 0),
            wins=summary.get("wins", 0),
            losses=summary.get("losses", 0),
            open_trades=review_payload["risk_review"]["open_trades"],
            realized_pnl_dollars=self._realized_pnl(journal, run_date),
            **self._metrics(journal, run_date, days=1),
            stopped_reason=stopped_reason,
            journal_path=summary.get("journal_path", ""),
            review_path=str(self.log_dir / f"daily_review_{run_date}.md"),
        )
        self._rolling_balance = broker.get_account_balance()
        report.write_markdown(self.log_dir / f"replay_report_{run_date}.md")
        return report

    def _reset_run_outputs(self, run_date: str) -> None:
        """Remove prior generated replay artifacts for a deterministic run."""
        for filename in (
            f"journal_{run_date}.jsonl",
            f"review_{run_date}.json",
            f"trade_grades_{run_date}.csv",
            f"daily_review_{run_date}.md",
            f"replay_report_{run_date}.md",
        ):
            path = self.log_dir / filename
            if path.exists():
                path.unlink()

    def run_many(
        self,
        candle_paths: list[str | Path],
        *,
        allow_mixed_instruments: bool = False,
    ) -> MultiDayReplayReport:
        reports: list[ReplayReport] = []
        previous_balance = self._rolling_balance
        self._rolling_balance = self.config.position_sizing.starting_balance
        try:
            for path in candle_paths:
                reports.append(self.run(path, allow_mixed_instruments=allow_mixed_instruments))
        finally:
            self._rolling_balance = previous_balance
        return self._aggregate_reports(reports, candle_paths)

    def run_manifest(self, manifest_path: str | Path) -> MultiDayReplayReport:
        manifest = ReplayManifest.load(manifest_path)
        reports: list[ReplayReport] = []
        previous_balance = self._rolling_balance
        self._rolling_balance = self.config.position_sizing.starting_balance
        try:
            for entry in manifest.entries:
                reports.append(
                    self.run(
                        entry.path,
                        allow_mixed_instruments=entry.allow_mixed_instruments,
                    )
                )
        finally:
            self._rolling_balance = previous_balance
        return self._aggregate_reports(reports, manifest.paths)

    def _aggregate_reports(
        self,
        reports: list[ReplayReport],
        candle_paths: list[str | Path],
    ) -> MultiDayReplayReport:
        failure_reasons: list[str] = []
        open_trades = sum(report.open_trades for report in reports)
        if open_trades:
            failure_reasons.append("open_trades_after_replay")

        for report in reports:
            if report.approved_trades > self.config.max_trades_per_day:
                failure_reasons.append("daily_trade_limit_violation")
            if report.losses >= self.config.max_consecutive_losses and report.stopped_reason not in (
                "max_consecutive_losses",
                "max_trades_per_day",  # trade cap is a valid stop — not a lockout violation
                None,
            ):
                failure_reasons.append("loss_lockout_violation")

        aggregate = MultiDayReplayReport(
            source_paths=[str(path) for path in candle_paths],
            days=len(reports),
            candles_processed=sum(report.candles_processed for report in reports),
            approved_trades=sum(report.approved_trades for report in reports),
            no_trades=sum(report.no_trades for report in reports),
            wins=sum(report.wins for report in reports),
            losses=sum(report.losses for report in reports),
            open_trades=open_trades,
            realized_pnl_dollars=round(sum(report.realized_pnl_dollars for report in reports), 2),
            expectancy=self._aggregate_expectancy(reports),
            win_rate=self._aggregate_win_rate(reports),
            average_win=self._aggregate_average_win(reports),
            average_loss=self._aggregate_average_loss(reports),
            profit_factor=self._aggregate_profit_factor(reports),
            max_drawdown=self._aggregate_max_drawdown(reports),
            trades_per_day=(
                sum(report.approved_trades for report in reports) / len(reports)
                if reports else 0.0
            ),
            stopped_days=sum(1 for report in reports if report.stopped_reason is not None),
            survival_passed=not failure_reasons,
            failure_reasons=sorted(set(failure_reasons)),
        )
        aggregate.write_markdown(self.log_dir / "multi_day_replay_report.md")
        return aggregate

    def _market_state_from_candle(
        self,
        candle: ReplayCandle,
        prev_candle: Optional[ReplayCandle] = None,
        prev_prev_candle: Optional[ReplayCandle] = None,
    ) -> MarketState:
        strat = self._strat_context_from_candle(candle)
        # True VWAP cross: previous bar was not above, current bar is above
        vwap_reclaimed = (
            prev_candle is not None
            and prev_candle.price_vs_vwap != "above"
            and candle.price_vs_vwap == "above"
        )
        # Failed reclaim (rejection): the PRIOR bar was itself a genuine
        # reclaim (same crossover test, shifted one bar) and THIS bar closes
        # back below VWAP. Derived entirely from the candle sequence itself
        # (prev_candle/prev_prev_candle, threaded unconditionally through
        # every iteration of the run() loop, including skipped bars) —
        # deliberately NOT via DailyState/DecisionEngine, since bars that get
        # blocked before evaluate() runs (max-trades/loss-lockout/open-
        # position) would otherwise desync a backend-persisted "previous bar"
        # memory from the true immediately-preceding market bar. Mirrors how
        # live gets this from Pine directly (Pine advances its own crossover
        # state on every bar regardless of backend gating).
        prev_bar_was_reclaimed = (
            prev_candle is not None
            and prev_prev_candle is not None
            and prev_prev_candle.price_vs_vwap != "above"
            and prev_candle.price_vs_vwap == "above"
        )
        vwap_failed_reclaim = (
            prev_bar_was_reclaimed and candle.price_vs_vwap == "below"
        )
        if candle.session == "london" and candle.london_orb_high is not None:
            orb_high = candle.london_orb_high
            orb_low = candle.london_orb_low
            orb_status = candle.london_orb_status
        elif candle.session == "london":
            # Legacy replay candles have no London ORB fields. Fail closed
            # instead of substituting the persisted NY range during London.
            orb_high = candle.high
            orb_low = candle.low
            orb_status = "undefined"
        else:
            orb_high = candle.orb_high
            orb_low = candle.orb_low
            orb_status = candle.orb_status
        return MarketState(
            timestamp=_parse_timestamp(candle.timestamp),
            instrument=candle.instrument,
            session=candle.session,
            price=PriceData(last=candle.close, bid=candle.close, ask=candle.close),
            ohlc=OHLCData(
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                timeframe=candle.timeframe,
                bar_start=candle.timestamp,
            ),
            vwap=VWAPData(
                value=candle.vwap,
                price_vs_vwap=candle.price_vs_vwap,
                reclaimed=vwap_reclaimed,
                holding=candle.price_vs_vwap in ("above", "below"),
                failed_reclaim=vwap_failed_reclaim,
            ),
            orb=ORBData(
                high=orb_high,
                low=orb_low,
                timeframe_minutes=15,
                status=orb_status,
            ),
            previous_day=PreviousDayData(
                high=candle.previous_day_high,
                low=candle.previous_day_low,
                close=candle.previous_day_close,
                price_vs_pdh=candle.price_vs_pdh,
                price_vs_pdl=candle.price_vs_pdl,
            ),
            volume=VolumeData(
                current_bar=candle.volume,
                avg_bar=max(candle.avg_volume, 1),
                relative=candle.volume / max(candle.avg_volume, 1),
            ),
            market_condition=candle.market_condition,
            trend=TrendData(
                direction=candle.trend_direction,
                strength=candle.trend_strength,
                moderate_kind=moderate_subtype(
                    candle.close, candle.ema_9, candle.ema_21, candle.ema_55
                ),
            ),
            strat=strat,
            key_levels=self._key_levels_from_candle(candle),
            gex=GEXContext(
                gex_flip=candle.gex_flip,
                call_wall=candle.call_wall,
                put_wall=candle.put_wall,
                hvl=candle.hvl,
                max_pain=candle.max_pain,
                ghost=candle.ghost,
                mid_upper=candle.mid_upper,
                mid_lower=candle.mid_lower,
                vol_trigger_up=candle.vol_trigger_up,
                vol_trigger_down=candle.vol_trigger_down,
                gex_regime=candle.gex_regime,
                delta_bias=candle.delta_bias,
            ),
            signa=SignaContext(
                grade=candle.signa_grade,
                score=candle.signa_score,
                daily_direction=candle.signa_daily_direction,
                weekly_direction=candle.signa_weekly_direction,
            ),
            icc=ICCContext(
                phase=candle.icc_phase,
                entry_signal=candle.icc_entry_signal,
                indication_type=candle.icc_indication_type,
                indication_level=candle.icc_indication_level,
                last_swing_high=candle.icc_last_swing_high,
                last_swing_low=candle.icc_last_swing_low,
                correction_high=candle.icc_correction_high,
                correction_low=candle.icc_correction_low,
                stop_loss=candle.icc_stop_loss,
                tp1=candle.icc_tp1,
                tp2=candle.icc_tp2,
                htf_phase=candle.icc_htf_phase,
            ),
            htf=self._htf_context_for(candle),
            sd=SupplyDemandData(
                supply_top=candle.supply_top,
                supply_bottom=candle.supply_bottom,
                supply_wavg=candle.supply_wavg,
                demand_top=candle.demand_top,
                demand_bottom=candle.demand_bottom,
                demand_wavg=candle.demand_wavg,
            ) if any(v is not None for v in (
                candle.supply_top, candle.supply_bottom,
                candle.demand_top, candle.demand_bottom,
            )) else None,
            raw=candle.source,
        )

    def _htf_context_for(self, candle: ReplayCandle) -> Optional[HTFContext]:
        """
        Return HTFContext for this candle, preferring the HTFLookup (real data)
        over candle-embedded fields, falling back to candle fields if lookup
        has nothing loaded.
        """
        # Prefer real HTF data from the lookup
        if self.htf and self.htf.loaded_timeframes():
            ts = _parse_timestamp(candle.timestamp)
            return self.htf.get_context(ts)

        # Fall back to whatever the candle carries (may all be None)
        if any(v is not None for v in (
            candle.daily_bar_type, candle.daily_direction,
            candle.four_hour_bar_type, candle.four_hour_direction,
            candle.ftfc_direction, candle.ftfc_aligned,
        )):
            return HTFContext(
                daily_bar_type=candle.daily_bar_type,
                daily_direction=candle.daily_direction,
                four_hour_bar_type=candle.four_hour_bar_type,
                four_hour_direction=candle.four_hour_direction,
                one_hour_bar_type=candle.one_hour_bar_type,
                one_hour_direction=candle.one_hour_direction,
                ftfc_direction=candle.ftfc_direction,
                ftfc_aligned=candle.ftfc_aligned,
            )
        return None

    @staticmethod
    @staticmethod
    def _key_levels_from_candle(candle: ReplayCandle) -> Optional[KeyLevels]:
        """Build KeyLevels from candle EMA/HOD/LOD — mirrors the live
        webhook/state_builder._build_key_levels so replay scores confluence the
        same way as production. Returns None when no level data is present."""
        if not any(v is not None for v in (
            candle.hod, candle.lod,
            candle.ema_9, candle.ema_21, candle.ema_55, candle.ema_200,
        )):
            return None
        ema_9_above_21 = (
            candle.ema_9 > candle.ema_21
            if candle.ema_9 is not None and candle.ema_21 is not None
            else None
        )
        price_above_ema_55 = (
            candle.close > candle.ema_55 if candle.ema_55 is not None else None
        )
        price_above_ema_200 = (
            candle.close > candle.ema_200 if candle.ema_200 is not None else None
        )
        return KeyLevels(
            hod=candle.hod,
            lod=candle.lod,
            ema_9=candle.ema_9,
            ema_21=candle.ema_21,
            ema_55=candle.ema_55,
            ema_200=candle.ema_200,
            ema_9_above_21=ema_9_above_21,
            price_above_ema_55=price_above_ema_55,
            price_above_ema_200=price_above_ema_200,
        )

    @staticmethod
    def _strat_context_from_candle(candle: ReplayCandle) -> Optional[StratContext]:
        """
        Build StratContext from a ReplayCandle.

        Priority:
        1. Explicit Pine-classified fields (current_bar_type, strat_sequence, etc.)
        2. Auto-classify from bar OHLC history (previous_bar_high/low, two_bars_back_high/low)
        3. None — no strat context available (Phase 1 proxy strategies still fire)
        """
        # If Pine provided explicit classification, use it — but when the
        # SEQUENCE is missing (bar types present, sequence absent — the common
        # CSV case), classify it from bar history so the +3 strat confluence
        # bonus and strat_* setups aren't silently disabled (mirrors live).
        if candle.current_bar_type is not None:
            classified = None
            if candle.strat_sequence is None and candle.previous_bar_high is not None:
                classified = classify_from_ohlc(
                    current_high=candle.high,
                    current_low=candle.low,
                    previous_high=candle.previous_bar_high,
                    previous_low=candle.previous_bar_low,
                    two_bars_back_high=candle.two_bars_back_high,
                    two_bars_back_low=candle.two_bars_back_low,
                    two_bars_back_type=candle.two_bars_back_type,
                )
            return StratContext(
                current_bar_type=candle.current_bar_type,
                previous_bar_type=candle.previous_bar_type,
                two_bars_back_type=candle.two_bars_back_type,
                strat_sequence=candle.strat_sequence or (classified.strat_sequence if classified else None),
                strat_trigger=candle.strat_trigger or (classified.strat_trigger if classified else None),
                strat_direction=candle.strat_direction or (classified.strat_direction if classified else None),
            )

        # Auto-classify from OHLC history when available.
        if candle.previous_bar_high is not None and candle.previous_bar_low is not None:
            return classify_from_ohlc(
                current_high=candle.high,
                current_low=candle.low,
                previous_high=candle.previous_bar_high,
                previous_low=candle.previous_bar_low,
                two_bars_back_high=candle.two_bars_back_high,
                two_bars_back_low=candle.two_bars_back_low,
                two_bars_back_type=candle.two_bars_back_type,
            )

        return None

    def _empty_report(self, candle_path: str | Path, review_date: Optional[str]) -> ReplayReport:
        run_date = review_date or date.today().isoformat()
        return ReplayReport(
            source_path=str(candle_path),
            candles_processed=0,
            decisions=0,
            approved_trades=0,
            no_trades=0,
            wins=0,
            losses=0,
            open_trades=0,
            realized_pnl_dollars=0.0,
            expectancy=0.0,
            win_rate=0.0,
            average_win=0.0,
            average_loss=0.0,
            profit_factor=None,
            max_drawdown=0.0,
            trades_per_day=0.0,
            stopped_reason="no_candles",
            journal_path=str(self.log_dir / f"journal_{run_date}.jsonl"),
            review_path=None,
        )

    @staticmethod
    def _realized_pnl(journal: JournalLogger, run_date: str) -> float:
        path = journal._journal_path(_date_to_date(run_date))
        total = 0.0
        for entry in journal._read_entries(path):
            outcome = entry.get("outcome") or {}
            if isinstance(outcome.get("pnl_dollars"), (int, float)):
                total += float(outcome["pnl_dollars"])
        return round(total, 2)

    @staticmethod
    def _metrics(journal: JournalLogger, run_date: str, days: int) -> dict:
        path = journal._journal_path(_date_to_date(run_date))
        pnl_values: list[float] = []
        for entry in journal._read_entries(path):
            outcome = entry.get("outcome") or {}
            if isinstance(outcome.get("pnl_dollars"), (int, float)):
                pnl_values.append(float(outcome["pnl_dollars"]))

        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "expectancy": round(sum(pnl_values) / len(pnl_values), 2) if pnl_values else 0.0,
            "win_rate": round(len(wins) / len(pnl_values), 4) if pnl_values else 0.0,
            "average_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "average_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else None,
            "max_drawdown": ReplayEngine._max_drawdown(pnl_values),
            "trades_per_day": round(len(pnl_values) / days, 4) if days else 0.0,
        }

    @staticmethod
    def _max_drawdown(pnl_values: list[float]) -> float:
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for value in pnl_values:
            equity += value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return round(max_drawdown, 2)

    @staticmethod
    def _aggregate_expectancy(reports: list[ReplayReport]) -> float:
        trades = sum(report.approved_trades for report in reports)
        pnl = sum(report.realized_pnl_dollars for report in reports)
        return round(pnl / trades, 2) if trades else 0.0

    @staticmethod
    def _aggregate_win_rate(reports: list[ReplayReport]) -> float:
        trades = sum(report.approved_trades for report in reports)
        wins = sum(report.wins for report in reports)
        return round(wins / trades, 4) if trades else 0.0

    @staticmethod
    def _aggregate_average_win(reports: list[ReplayReport]) -> float:
        # Weighted by win count so a day with 3 wins doesn't count the same as 1.
        total_pnl = sum(report.average_win * report.wins for report in reports)
        total_wins = sum(report.wins for report in reports)
        return round(total_pnl / total_wins, 2) if total_wins else 0.0

    @staticmethod
    def _aggregate_average_loss(reports: list[ReplayReport]) -> float:
        # Weighted by loss count.
        total_pnl = sum(report.average_loss * report.losses for report in reports)
        total_losses = sum(report.losses for report in reports)
        return round(total_pnl / total_losses, 2) if total_losses else 0.0

    @staticmethod
    def _aggregate_profit_factor(reports: list[ReplayReport]) -> float | None:
        gross_win = sum(report.average_win * report.wins for report in reports)
        gross_loss = sum(report.average_loss * report.losses for report in reports)
        return round(gross_win / gross_loss, 4) if gross_loss else None

    @staticmethod
    def _aggregate_max_drawdown(reports: list[ReplayReport]) -> float:
        """Drawdown on the cumulative day-level equity curve across all days."""
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for report in reports:
            equity += report.realized_pnl_dollars
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return round(max_dd, 2)


def _parse_timestamp(value: str):
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _date_from_timestamp(value: str) -> str:
    return _parse_timestamp(value).date().isoformat()


def _date_to_date(value: str) -> date:
    return date.fromisoformat(value)
