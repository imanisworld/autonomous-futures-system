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

    # Paths
    log_dir: str = "logs"
    log_level: str = "INFO"
    risk_rules_path: str = "risk_rules.yaml"


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

    config = SystemConfig(
        live_trading_enabled=False,  # Always false — enforced above
        paper_mode=trading.get("paper_mode", True),

        allowed_instruments=instruments.get("allowed", []),
        allowed_sessions=sessions.get("allowed", []),
        disabled_sessions=sessions.get("disabled", []),
        session_hours=session_hours,

        max_trades_per_day=daily.get("max_trades_per_day", 3),
        max_consecutive_losses=daily.get("max_consecutive_losses", 2),

        max_open_positions=position.get("max_open_positions", 1),
        averaging_down_allowed=position.get("averaging_down", False),

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

        log_dir=os.getenv("LOG_DIR", "logs"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        risk_rules_path=risk_rules_path,
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
    if config.live_trading_enabled:
        # This should never be reached, but belt-and-suspenders
        raise LiveTradingBlockedError(source="post-parse validation")
