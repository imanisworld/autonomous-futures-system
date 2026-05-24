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
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    TrendData,
    VWAPData,
    VolumeData,
)
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger
from replay.candle_loader import ReplayCandle, ReplayCandleLoader
from replay.manifest import ReplayManifest
from replay.replay_report import MultiDayReplayReport, ReplayReport
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy.signal_engine import DecisionEngine


class ReplayEngine:
    """Runs local candle files through the paper system without live data."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        log_dir: str = "logs/replay",
    ):
        self.config = config or load_config()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

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
        broker = PaperBroker()
        daily_state = DailyState(date=run_date)

        stopped_reason: str | None = None
        candles_processed = 0

        for idx, candle in enumerate(candles):
            candles_processed += 1

            if daily_state.trade_count >= self.config.max_trades_per_day:
                stopped_reason = "max_trades_per_day"
                break
            if daily_state.consecutive_losses >= self.config.max_consecutive_losses:
                stopped_reason = "max_consecutive_losses"
                break

            state = self._market_state_from_candle(candle)
            decision = decision_engine.evaluate(state, daily_state)
            risk_result_dict = None

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
                )
                risk_result = risk_engine.validate(trade_setup, daily_state)
                risk_result_dict = {
                    "result": risk_result.result,
                    "failed_rule": risk_result.failed_rule,
                    "reason": risk_result.reason,
                }

                journal.log_decision(decision.to_dict(), risk_result_dict, for_date=journal_date)

                if risk_result.approved:
                    order = BracketOrder(
                        instrument=state.instrument,
                        direction=decision.setup.direction,
                        entry=decision.setup.entry,
                        stop=decision.setup.stop,
                        target=decision.setup.target,
                        rr_ratio=decision.setup.rr_ratio,
                        strategy=decision.setup.strategy,
                        notes=decision.setup.notes,
                    )
                    broker.execute_bracket(order)
                    next_candle = candles[idx + 1] if idx + 1 < len(candles) else candle
                    fill = broker.resolve_position(NextBarOHLC(high=next_candle.high, low=next_candle.low))
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
                            for_date=journal_date,
                        )
                        daily_state.trade_count += 1
                        if fill.result == "LOSS":
                            daily_state.consecutive_losses += 1
                        elif fill.result == "WIN":
                            daily_state.consecutive_losses = 0
                        daily_state.has_open_position = False
                    else:
                        daily_state.trade_count += 1
                        daily_state.has_open_position = True
                continue

            journal.log_decision(decision.to_dict(), risk_result_dict, for_date=journal_date)

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

    def run_many(self, candle_paths: list[str | Path]) -> MultiDayReplayReport:
        reports: list[ReplayReport] = []
        for path in candle_paths:
            reports.append(self.run(path))
        return self._aggregate_reports(reports, candle_paths)

    def run_manifest(self, manifest_path: str | Path) -> MultiDayReplayReport:
        manifest = ReplayManifest.load(manifest_path)
        reports: list[ReplayReport] = []
        for entry in manifest.entries:
            reports.append(
                self.run(
                    entry.path,
                    allow_mixed_instruments=entry.allow_mixed_instruments,
                )
            )
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

    def _market_state_from_candle(self, candle: ReplayCandle) -> MarketState:
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
                reclaimed=candle.price_vs_vwap == "above",
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
            raw=None,
        )

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
