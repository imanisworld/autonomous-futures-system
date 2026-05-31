"""
config/settings.py

Loads and validates system configuration from risk_rules.yaml and environment.
Config is law — all modules read from here, never from raw files.

CRITICAL: If LIVE_TRADING_ENABLED is true, raises LiveTradingBlockedError.
This is a hard architectural block, not a soft toggle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv


# ─── Exceptions ──────────────────────────────────────────────────────────────

class LiveTradingBlockedError(RuntimeError):
    """Raised when any path attempts to enable live trading in Phase 1."""

    def __init__(self, source: str = "unknown"):
        super().__init__(
            f"LIVE TRADING IS BLOCKED IN PHASE 1. "
            f"Source: {source}. "
            f"Set LIVE_TRADING_ENABLED=false and paper_mode=true."
        )


class ConfigError(ValueError):
    """Raised when configuration is invalid or incomplete."""


# ─── Config Dataclass ────────────────────────────────────────────────────────

@dataclass
class SystemConfig:
    # Mode
    live_trading_enabled: bool
    paper_mode: bool

    # Universe
    allowed_instruments: List[str]
    allowed_sessions: List[str]
    disabled_sessions: List[str]
    session_hours: dict

    # Daily limits
    max_trades_per_day: int
    max_consecutive_losses: int

    # Position rules
    max_open_positions: int
    averaging_down_allowed: bool

    # Order rules
    require_entry: bool
    require_stop: bool
    require_target: bool

    # Risk/reward
    min_rr_ratio: float

    # Data quality
    max_staleness_seconds: int
    reject_null_required_fields: bool
    reject_contradictory_data: bool

    # Market condition
    tradable_states: List[str]
    non_tradable_states: List[str]

    # Strategy
    enabled_concepts: List[str]

    # Per-session trade limits (optional — no limit applied if session not present)
    per_session_limits: dict = field(default_factory=dict)
    # Time-of-day cutoffs per session (HH:MM ET strings)
    session_cutoffs: dict = field(default_factory=dict)
    # Minimum target distance in instrument points
    min_target_points: dict = field(default_factory=dict)
    # Maximum stop distance in ticks per instrument (rejects setups with oversized risk)
    max_stop_ticks: dict = field(default_factory=dict)

    # Future broker/capital planning (inactive while live trading is blocked)
    broker_priority: List[str] = field(default_factory=lambda: ["paper", "tradovate_sim", "ibkr_paper"])
    starting_capital_default: float = 1000.0
    minimum_starting_capital: float = 500.0
    max_account_risk_per_trade_percent: float = 1.0
    max_daily_loss_percent: float = 3.0
    require_margin_check: bool = True
    max_contracts_per_instrument: dict = field(default_factory=dict)

    # Paths
    log_dir: str = "logs"
    log_level: str = "INFO"
    risk_rules_path: str = "risk_rules.yaml"

    # Read-only notifications
    discord_notifications_enabled: bool = False
    discord_webhook_url: str = ""
    discord_notify_decisions: List[str] = field(default_factory=lambda: ["TRADE", "RISK_REJECTED", "BLOCKED_MAX_TRADES", "BLOCKED_LOSS_LOCKOUT"])

    # Future signal/data vendor planning; key value is never stored on config.
    signa_api_enabled: bool = False
    signa_api_key_configured: bool = False


# ─── Loader ──────────────────────────────────────────────────────────────────

def load_config(risk_rules_path: str = "risk_rules.yaml") -> SystemConfig:
    """
    Load and validate system configuration.

    Checks environment variables first (override), then risk_rules.yaml.
    Raises LiveTradingBlockedError if live trading is enabled by any source.
    """
    # Load .env if present
    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)

    # Load YAML
    rules_path = Path(risk_rules_path)
    if not rules_path.exists():
        raise ConfigError(f"risk_rules.yaml not found at: {rules_path.resolve()}")

    with open(rules_path) as f:
        rules = yaml.safe_load(f)

    # ── Live trading check (CRITICAL) ────────────────────────────────────────
    # Check YAML first
    yaml_live = rules.get("trading_mode", {}).get("live_trading_enabled", False)
    # Check environment override
    env_live_raw = os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower()
    env_live = env_live_raw in ("true", "1", "yes")

    if yaml_live:
        raise LiveTradingBlockedError(source=f"risk_rules.yaml → trading_mode.live_trading_enabled=true")
    if env_live:
        raise LiveTradingBlockedError(source=f"environment → LIVE_TRADING_ENABLED={env_live_raw}")

    # ── Parse remaining config ────────────────────────────────────────────────
    trading = rules.get("trading_mode", {})
    instruments = rules.get("instruments", {})
    sessions = rules.get("sessions", {})
    session_hours = rules.get("session_hours_et", {})
    daily = rules.get("daily_limits", {})
    position = rules.get("position_rules", {})
    orders = rules.get("order_rules", {})
    rr = rules.get("risk_reward", {})
    data = rules.get("data_quality", {})
    condition = rules.get("market_condition", {})
    strategy = rules.get("strategy", {})
    broker = rules.get("broker_roadmap", {})
    capital = rules.get("capital_guardrails", {})

    config = SystemConfig(
        live_trading_enabled=False,  # Always false — enforced above
        paper_mode=trading.get("paper_mode", True),

        allowed_instruments=instruments.get("allowed", []),
        allowed_sessions=sessions.get("allowed", []),
        disabled_sessions=sessions.get("disabled", []),
        session_hours=session_hours,

        max_trades_per_day=daily.get("max_trades_per_day", 3),
        max_consecutive_losses=daily.get("max_consecutive_losses", 2),
        per_session_limits=daily.get("per_session_limits", {}),
        session_cutoffs=daily.get("session_cutoffs_et", {}),
        min_target_points=daily.get("min_target_points", {}),
        max_stop_ticks=daily.get("max_stop_ticks", {}),

        max_open_positions=position.get("max_open_positions", 1),
        averaging_down_allowed=position.get("averaging_down", False),
        max_contracts_per_instrument=position.get("max_contracts_per_instrument", {}),

        require_entry=orders.get("require_entry", True),
        require_stop=orders.get("require_stop", True),
        require_target=orders.get("require_target", True),

        min_rr_ratio=float(rr.get("min_rr_ratio", 2.0)),

        max_staleness_seconds=data.get("max_staleness_seconds", 300),
        reject_null_required_fields=data.get("reject_null_required_fields", True),
        reject_contradictory_data=data.get("reject_contradictory_data", True),

        tradable_states=condition.get("tradable_states", ["TRENDING", "RANGE_BOUND"]),
        non_tradable_states=condition.get("non_tradable_states", ["CHOPPY", "DEAD"]),

        enabled_concepts=strategy.get("enabled_concepts", []),

        broker_priority=broker.get("broker_priority", ["paper", "tradovate_sim", "ibkr_paper"]),
        starting_capital_default=float(capital.get("starting_capital_default", 1000)),
        minimum_starting_capital=float(capital.get("minimum_starting_capital", 500)),
        max_account_risk_per_trade_percent=float(
            capital.get("max_account_risk_per_trade_percent", 1.0)
        ),
        max_daily_loss_percent=float(capital.get("max_daily_loss_percent", 3.0)),
        require_margin_check=capital.get("require_margin_check", True),

        log_dir=os.getenv("LOG_DIR", "logs"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        risk_rules_path=risk_rules_path,

        discord_notifications_enabled=_env_bool("DISCORD_NOTIFICATIONS_ENABLED", False),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        discord_notify_decisions=_env_csv(
            "DISCORD_NOTIFY_DECISIONS",
            ["TRADE", "RISK_REJECTED", "BLOCKED_MAX_TRADES", "BLOCKED_LOSS_LOCKOUT"],
        ),

        signa_api_enabled=_env_bool("SIGNA_API_ENABLED", False),
        signa_api_key_configured=bool(os.getenv("SIGNA_API_KEY", "").strip()),
    )

    _validate_config(config)
    return config


def _validate_config(config: SystemConfig) -> None:
    """Sanity-check the loaded configuration."""
    if not config.allowed_instruments:
        raise ConfigError("No allowed instruments configured.")
    if not config.allowed_sessions:
        raise ConfigError("No allowed sessions configured.")
    if config.max_trades_per_day < 1:
        raise ConfigError("max_trades_per_day must be >= 1.")
    if config.max_consecutive_losses < 1:
        raise ConfigError("max_consecutive_losses must be >= 1.")
    if config.min_rr_ratio < 1.0:
        raise ConfigError("min_rr_ratio must be >= 1.0.")
    if config.max_staleness_seconds < 1:
        raise ConfigError("max_staleness_seconds must be >= 1.")
    if config.minimum_starting_capital < 0:
        raise ConfigError("minimum_starting_capital must be >= 0.")
    if config.starting_capital_default < config.minimum_starting_capital:
        raise ConfigError("starting_capital_default must be >= minimum_starting_capital.")
    if not (0 < config.max_account_risk_per_trade_percent <= 100):
        raise ConfigError("max_account_risk_per_trade_percent must be between 0 and 100.")
    if not (0 < config.max_daily_loss_percent <= 100):
        raise ConfigError("max_daily_loss_percent must be between 0 and 100.")
    if config.live_trading_enabled:
        # This should never be reached, but belt-and-suspenders
        raise LiveTradingBlockedError(source="post-parse validation")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def _env_csv(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]
