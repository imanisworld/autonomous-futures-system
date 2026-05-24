"""
main.py

Autonomous Futures Paper-Trading System — Main Loop

Usage:
    python main.py --market-state data/sample_market_state.json
    python main.py --market-state data/sample_market_state.json --dry-run

Flow:
    1. Load config (hard fail if LIVE_TRADING_ENABLED=true)
    2. Load and validate market state
    3. Reconstruct daily state from journal
    4. Run DecisionEngine → DecisionOutput
    5. Run RiskEngine (if TRADE decision)
    6. Execute via PaperBroker (if APPROVED)
    7. Log everything to journal
    8. Print summary

Paper-only. No real orders. No broker connections.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from config.settings import load_config, LiveTradingBlockedError, ConfigError
from context.market_context import MarketStateLoader, DataQualityError, StaleDataError
from strategy.signal_engine import DecisionEngine
from risk.risk_engine import RiskEngine, TradeSetup, DailyState
from execution.paper_broker import NextBarOHLC, PaperBroker
from journal.journal_logger import JournalLogger


# ─── Logging Setup ────────────────────────────────────────────────────────────

def setup_logging(log_dir: str, level: str = "INFO") -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"{log_dir}/system.log"),
        ],
    )


log = logging.getLogger("main")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous Futures Paper-Trading Engine"
    )
    parser.add_argument(
        "--market-state",
        required=True,
        help="Path to market state JSON file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and market state only. No decision, no order.",
    )
    parser.add_argument(
        "--risk-rules",
        default="risk_rules.yaml",
        help="Path to risk_rules.yaml (default: risk_rules.yaml)",
    )
    parser.add_argument(
        "--next-bar",
        help=(
            "Optional JSON file with next-bar OHLC fields {high, low}. "
            "When provided, an approved paper trade is immediately resolved "
            "and the outcome is journaled."
        ),
    )
    args = parser.parse_args()

    # ── 1. Load Config ────────────────────────────────────────────────────────
    try:
        config = load_config(args.risk_rules)
    except LiveTradingBlockedError as e:
        print(f"\n{'='*60}")
        print(f"  FATAL: LIVE TRADING IS BLOCKED IN PHASE 1")
        print(f"  {e}")
        print(f"{'='*60}\n")
        return 1
    except ConfigError as e:
        print(f"FATAL: Configuration error — {e}")
        return 1

    setup_logging(config.log_dir, config.log_level)
    log.info("=" * 60)
    log.info("Autonomous Futures Paper-Trading System — Starting")
    log.info(f"Mode: {'DRY RUN' if args.dry_run else 'PAPER'}")
    log.info(f"Live trading enabled: {config.live_trading_enabled}")
    log.info("=" * 60)

    journal = JournalLogger(log_dir=config.log_dir)

    # ── 2. Load Market State ──────────────────────────────────────────────────
    loader = MarketStateLoader(config=config)
    try:
        state = loader.load(args.market_state)
        log.info(
            f"Market state loaded: {state.instrument} | {state.session} | "
            f"last={state.price.last} | condition={state.market_condition}"
        )
    except FileNotFoundError as e:
        log.error(f"Market state file not found: {e}")
        return 1
    except StaleDataError as e:
        log.warning(f"Stale data — NO_TRADE: {e}")
        journal.log_decision({
            "ts": datetime.now(timezone.utc).isoformat(),
            "instrument": "UNKNOWN",
            "session": "UNKNOWN",
            "decision": "NO_TRADE",
            "reason": f"Stale data: {e}",
            "market_condition": None,
            "setup": None,
        })
        _print_result("NO_TRADE", f"Stale data: {e}")
        return 0
    except DataQualityError as e:
        log.warning(f"Data quality error — NO_TRADE: {e}")
        journal.log_decision({
            "ts": datetime.now(timezone.utc).isoformat(),
            "instrument": "UNKNOWN",
            "session": "UNKNOWN",
            "decision": "NO_TRADE",
            "reason": f"Data quality error: {e}",
            "market_condition": None,
            "setup": None,
        })
        _print_result("NO_TRADE", f"Data quality: {e}")
        return 0

    if args.dry_run:
        log.info("DRY RUN complete — config and market state are valid.")
        print("\n✓ Dry run passed. Config and market state are valid.")
        return 0

    # ── 3. Reconstruct Daily State ────────────────────────────────────────────
    daily_state: DailyState = journal.get_daily_state()
    log.info(
        f"Daily state: trades={daily_state.trade_count}, "
        f"losses={daily_state.consecutive_losses}, "
        f"open_position={daily_state.has_open_position}"
    )

    # ── 4. DecisionEngine ─────────────────────────────────────────────────────
    decision_engine = DecisionEngine(config=config)
    decision = decision_engine.evaluate(state, daily_state)
    log.info(f"Decision: {decision.decision} — {decision.reason}")

    # ── 5. Risk Check (only if TRADE) ─────────────────────────────────────────
    risk_result_dict = None
    decision_logged = False

    if decision.decision == "TRADE" and decision.setup is not None:
        risk_engine = RiskEngine(config=config)
        setup = decision.setup

        trade_setup = TradeSetup(
            direction=setup.direction,
            entry=setup.entry,
            stop=setup.stop,
            target=setup.target,
            rr_ratio=setup.rr_ratio,
            strategy=setup.strategy,
            instrument=state.instrument,
            session=state.session,
            notes=setup.notes,
        )

        risk_result = risk_engine.validate(trade_setup, daily_state)
        risk_result_dict = {
            "result": risk_result.result,
            "failed_rule": risk_result.failed_rule,
            "reason": risk_result.reason,
        }
        log.info(f"Risk check: {risk_result.result}")
        if risk_result.rejected:
            log.warning(
                f"Risk rejected: [{risk_result.failed_rule}] {risk_result.reason}"
            )

        # ── 6. Execute (if APPROVED) ──────────────────────────────────────────
        if risk_result.approved:
            journal.log_decision(decision.to_dict(), risk_result_dict)
            decision_logged = True
            broker = PaperBroker()
            from execution.broker_interface import BracketOrder
            order = BracketOrder(
                instrument=state.instrument,
                direction=setup.direction,
                entry=setup.entry,
                stop=setup.stop,
                target=setup.target,
                rr_ratio=setup.rr_ratio,
                strategy=setup.strategy,
                notes=setup.notes,
            )
            fill = broker.execute_bracket(order)
            log.info(
                f"Order submitted to PaperBroker: {fill.direction} {fill.instrument} "
                f"@ {fill.entry_price} | result={fill.result}"
            )
            if args.next_bar:
                next_bar = load_next_bar(args.next_bar)
                outcome = broker.resolve_position(next_bar)
                if outcome is not None:
                    journal.log_outcome(
                        instrument=outcome.instrument,
                        session=state.session,
                        result=outcome.result,
                        entry_price=outcome.entry_price,
                        exit_price=outcome.exit_price,
                        exit_reason=outcome.exit_reason,
                        pnl_ticks=outcome.pnl_ticks,
                        pnl_dollars=outcome.pnl_dollars,
                    )
                    log.info(
                        f"Paper position resolved: {outcome.result} "
                        f"exit={outcome.exit_price} pnl=${outcome.pnl_dollars}"
                    )
                else:
                    log.info("Paper position remains OPEN after next-bar resolution.")

    # ── 7. Log ────────────────────────────────────────────────────────────────
    if not decision_logged:
        journal.log_decision(decision.to_dict(), risk_result_dict)

    # ── 8. Summary ────────────────────────────────────────────────────────────
    _print_result(decision.decision, decision.reason, decision.setup, risk_result_dict)
    summary = journal.get_summary()
    print(
        f"\nDaily summary: {summary['trades']} trade(s), "
        f"{summary['no_trades']} no-trade(s), "
        f"{summary['wins']} win(s), {summary['losses']} loss(es)"
    )
    print(f"Journal: {summary['journal_path']}\n")

    return 0


def load_next_bar(path: str) -> NextBarOHLC:
    """Load minimal next-bar OHLC data for fake paper resolution."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    try:
        return NextBarOHLC(high=float(raw["high"]), low=float(raw["low"]))
    except KeyError as e:
        raise ValueError(f"Next-bar file missing required field: {e.args[0]}") from e


def _print_result(decision: str, reason: str, setup=None, risk=None) -> None:
    line = "─" * 60
    print(f"\n{line}")
    print(f"  DECISION: {decision}")
    print(f"  Reason:   {reason}")
    if setup:
        print(f"  Setup:    {setup.direction} @ {setup.entry}")
        print(f"            stop={setup.stop}  target={setup.target}  R:R={setup.rr_ratio:.2f}")
        print(f"            strategy={setup.strategy}")
    if risk:
        print(f"  Risk:     {risk['result']}", end="")
        if risk.get("failed_rule"):
            print(f" [{risk['failed_rule']}] {risk['reason']}", end="")
        print()
    print(f"{line}\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        journal = JournalLogger()
        journal.log_error("Unhandled exception in main", exc=e)
        print(f"\nFATAL: Unhandled exception — {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
