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
from replay.replay_report import ReplayReport
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

    def run(self, candle_path: str | Path, review_date: Optional[str] = None) -> ReplayReport:
        candles = ReplayCandleLoader().load_jsonl(candle_path)
        if not candles:
            return self._empty_report(candle_path, review_date)

        run_date = review_date or _date_from_timestamp(candles[0].timestamp)
        journal = JournalLogger(log_dir=str(self.log_dir))
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

                journal.log_decision(decision.to_dict(), risk_result_dict)

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

            journal.log_decision(decision.to_dict(), risk_result_dict)

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
            stopped_reason=stopped_reason,
            journal_path=summary.get("journal_path", ""),
            review_path=str(self.log_dir / f"daily_review_{run_date}.md"),
        )
        report.write_markdown(self.log_dir / f"replay_report_{run_date}.md")
        return report

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
