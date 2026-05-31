"""
replay/replay_engine.py

Offline replay of historical or synthetic candles through the existing
decision, risk, paper broker, journal, and review path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from agent.daily_summary import DailySummaryAgent
from config.settings import SystemConfig, load_config
from context.market_context import (
    MarketState,
    GEXContext,
    HTFContext,
    ICCContext,
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
from execution.paper_broker import NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger
from context.htf_loader import HTFLookup
from replay.candle_loader import ReplayCandle, ReplayCandleLoader
from replay.manifest import ReplayManifest
from replay.replay_report import MultiDayReplayReport, ReplayReport
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
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
            )
        )
        daily_state = DailyState(
            date=run_date,
            account_balance=broker.get_account_balance(),
        )

        stopped_reason: str | None = None
        candles_processed = 0
        skip_to = 0  # index of first bar available after an open position resolves
        prev_candle: Optional[ReplayCandle] = None

        for idx, candle in enumerate(candles):
            candles_processed += 1
            if idx < skip_to:
                prev_candle = candle
                continue

            if daily_state.consecutive_losses >= self.config.max_consecutive_losses:
                stopped_reason = "max_consecutive_losses"
                break
            if daily_state.trade_count >= self.config.max_trades_per_day:
                stopped_reason = "max_trades_per_day"
                break

            state = self._market_state_from_candle(candle, prev_candle)
            decision = decision_engine.evaluate(state, daily_state)
            risk_result_dict = None

            prev_candle = candle

            if decision.decision == "TRADE" and decision.setup is not None:
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
                    entry_time=_parse_timestamp(candle.timestamp),
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

                journal.log_decision(decision.to_dict(), risk_result_dict, for_date=journal_date)

                if risk_result.approved:
                    contracts = trade_setup.contracts
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
                    )
                    broker.execute_bracket(order)
                    fill = None
                    for future_idx in range(idx + 1, len(candles)):
                        fc = candles[future_idx]
                        fill = broker.resolve_position(
                            NextBarOHLC(high=fc.high, low=fc.low)
                        )
                        if fill is not None:
                            skip_to = future_idx + 1
                            break
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
                        daily_state.trade_count += 1
                        if fill.result == "LOSS":
                            daily_state.consecutive_losses += 1
                        elif fill.result in ("WIN", "BREAKEVEN"):
                            daily_state.consecutive_losses = 0
                        daily_state.has_open_position = False
                        daily_state.account_balance = broker.get_account_balance()
                    else:
                        daily_state.trade_count += 1
                        daily_state.has_open_position = True
                continue

            journal.log_decision(decision.to_dict(), risk_result_dict, for_date=journal_date)

        if stopped_reason is None and daily_state.trade_count >= self.config.max_trades_per_day:
            stopped_reason = "max_trades_per_day"

        summary = journal.get_summary(_date_to_date(run_date))
        review = DailySummaryAgent(self.config)
        review.log_dir = self.log_dir
        review.risk_reviewer.config.log_dir = str(self.log_dir)
        review.trade_grader.config.log_dir = str(self.log_dir)
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
    ) -> MarketState:
        strat = self._strat_context_from_candle(candle)
        # True VWAP cross: previous bar was not above, current bar is above
        vwap_reclaimed = (
            prev_candle is not None
            and prev_candle.price_vs_vwap != "above"
            and candle.price_vs_vwap == "above"
        )
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
            ),
            orb=ORBData(
                high=candle.orb_high,
                low=candle.orb_low,
                timeframe_minutes=15,
                status=candle.orb_status,
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
            ),
            strat=strat,
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
            raw=None,
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
    def _strat_context_from_candle(candle: ReplayCandle) -> Optional[StratContext]:
        """
        Build StratContext from a ReplayCandle.

        Priority:
        1. Explicit Pine-classified fields (current_bar_type, strat_sequence, etc.)
        2. Auto-classify from bar OHLC history (previous_bar_high/low, two_bars_back_high/low)
        3. None — no strat context available (Phase 1 proxy strategies still fire)
        """
        # If Pine provided explicit classification, use it directly.
        if candle.current_bar_type is not None:
            return StratContext(
                current_bar_type=candle.current_bar_type,
                previous_bar_type=candle.previous_bar_type,
                two_bars_back_type=candle.two_bars_back_type,
                strat_sequence=candle.strat_sequence,
                strat_trigger=candle.strat_trigger,
                strat_direction=candle.strat_direction,
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
