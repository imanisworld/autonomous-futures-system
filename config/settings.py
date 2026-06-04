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
from typing import List, Optional

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


@dataclass(frozen=True)
class PositionSizingRule:
    min_balance: float
    max_balance: Optional[float]
    instrument: str
    max_contracts: int


@dataclass(frozen=True)
class PositionSizingConfig:
    starting_balance: float = 5000.0
    enabled: bool = False
    aggressive_rounding: bool = True
    rounding_threshold_percent: float = 10.0
    sizing_rules: List[PositionSizingRule] = field(default_factory=list)


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
    max_daily_loss: float
    max_drawdown_percent: float
    circuit_breaker_losses: int
    circuit_breaker_pause_minutes: int
    conservative_mode: bool

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

    # Fine-grained session windows (optional — no window gate if session absent)
    session_windows: dict = field(default_factory=dict)
    # Per-session trade limits (optional — no limit applied if session not present)
    per_session_limits: dict = field(default_factory=dict)
    # Time-of-day cutoffs per session (HH:MM ET strings)
    session_cutoffs: dict = field(default_factory=dict)
    # Minimum target distance in instrument points
    min_target_points: dict = field(default_factory=dict)
    # Maximum stop distance in ticks per instrument (rejects setups with oversized risk)
    max_stop_ticks: dict = field(default_factory=dict)
    # Quality gates: require trend.strength == STRONG per instrument
    require_strong_trend: dict = field(default_factory=dict)
    # Quality gates: minimum signal-bar relative volume per instrument
    min_signal_bar_volume: dict = field(default_factory=dict)
    # Quality gates: require explicit daily/4H FTFC alignment when HTF data is present
    require_htf_alignment: dict = field(default_factory=dict)
    # Minimum confluence grade required for ordinary futures entries; blank disables.
    min_confluence_grade: str = ""
    # Per-instrument strategy exclusions — overrides enabled_concepts for that instrument
    disabled_concepts_per_instrument: dict = field(default_factory=dict)
    # Bonus trades after normal daily max; RiskEngine requires confluence grade.
    bonus_trades_after_max: int = 0
    bonus_min_confluence_grade: str = "A"
    # Early-session loss floor — blocks entries if down more than N dollars
    # within the first `early_session_minutes` of a session.
    early_session_loss_floor: float = 0.0   # 0 = disabled
    early_session_minutes: int = 30
    # Win-streak bonus contracts — after N consecutive wins allow +1c on A/A+ setups.
    win_streak_bonus_after: int = 0          # 0 = disabled
    win_streak_bonus_contracts: int = 1
    win_streak_bonus_min_grade: str = "A"
    # High-impact news/FOMC controls. Dates are YYYY-MM-DD decision days.
    news_blackout_dates: List[str] = field(default_factory=list)
    news_blackout_mode: str = "off"  # off | block | reduced
    news_blackout_max_trades: int = 1
    news_blackout_cutoff_et: str = "13:30"

    # Future broker/capital planning (inactive while live trading is blocked)
    broker_priority: List[str] = field(default_factory=lambda: ["paper", "tradovate"])
    starting_capital_default: float = 1000.0
    minimum_starting_capital: float = 500.0
    max_account_risk_per_trade_percent: float = 1.0
    max_daily_loss_percent: float = 3.0
    require_margin_check: bool = True
    max_contracts_per_instrument: dict = field(default_factory=dict)
    position_sizing: PositionSizingConfig = field(default_factory=PositionSizingConfig)

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
    signa_base_url: str = "https://app.getsigna.ai"
    signa_timeout_seconds: float = 3.0
    signa_symbol_map: dict = field(default_factory=dict)


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
    quality = rules.get("quality_gates", {})
    capital = rules.get("capital_guardrails", {})
    sizing = rules.get("position_sizing", {})

    config = SystemConfig(
        live_trading_enabled=False,  # Always false — enforced above
        paper_mode=_env_bool("PAPER_MODE", trading.get("paper_mode", True)),

        allowed_instruments=instruments.get("allowed", []),
        allowed_sessions=sessions.get("allowed", []),
        disabled_sessions=sessions.get("disabled", []),
        session_hours=session_hours,
        session_windows=rules.get("session_windows", {}) or {},

        max_trades_per_day=daily.get("max_trades_per_day", 3),
        max_consecutive_losses=daily.get("max_consecutive_losses", 2),
        max_daily_loss=float(daily.get("max_daily_loss", 0) or 0),
        max_drawdown_percent=float(daily.get("max_drawdown_percent", 0) or 0),
        circuit_breaker_losses=int(daily.get("circuit_breaker_losses", 0) or 0),
        circuit_breaker_pause_minutes=int(daily.get("circuit_breaker_pause_minutes", 30) or 30),
        conservative_mode=bool(daily.get("conservative_mode", False)),
        bonus_trades_after_max=int(daily.get("bonus_trades_after_max", 0) or 0),
        bonus_min_confluence_grade=str(daily.get("bonus_min_confluence_grade", "A") or "A"),
        early_session_loss_floor=float(daily.get("early_session_loss_floor", 0) or 0),
        early_session_minutes=int(daily.get("early_session_minutes", 30) or 30),
        win_streak_bonus_after=int(daily.get("win_streak_bonus_after", 0) or 0),
        win_streak_bonus_contracts=int(daily.get("win_streak_bonus_contracts", 1) or 1),
        win_streak_bonus_min_grade=str(daily.get("win_streak_bonus_min_grade", "A") or "A"),
        news_blackout_dates=[str(value) for value in daily.get("news_blackout_dates", []) or []],
        news_blackout_mode=str(daily.get("news_blackout_mode", "off") or "off").lower(),
        news_blackout_max_trades=int(daily.get("news_blackout_max_trades", 1) or 1),
        news_blackout_cutoff_et=str(daily.get("news_blackout_cutoff_et", "13:30") or "13:30"),
        per_session_limits=daily.get("per_session_limits", {}),
        session_cutoffs=daily.get("session_cutoffs_et", {}),
        min_target_points=daily.get("min_target_points", {}),
        max_stop_ticks=daily.get("max_stop_ticks", {}),
        require_strong_trend=quality.get("require_strong_trend", {}),
        min_signal_bar_volume=quality.get("min_signal_bar_volume", {}),
        require_htf_alignment=quality.get("require_htf_alignment", {}),
        min_confluence_grade=str(quality.get("min_confluence_grade", "") or "").upper(),

        max_open_positions=position.get("max_open_positions", 1),
        averaging_down_allowed=position.get("averaging_down", False),
        max_contracts_per_instrument=position.get("max_contracts_per_instrument", {}),
        position_sizing=_parse_position_sizing(sizing),

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
        disabled_concepts_per_instrument=strategy.get("disabled_concepts_per_instrument", {}),

        broker_priority=broker.get("broker_priority", ["paper", "tradovate"]),
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
        signa_base_url=os.getenv("SIGNA_BASE_URL", "https://app.getsigna.ai").strip().rstrip("/"),
        signa_timeout_seconds=float(os.getenv("SIGNA_TIMEOUT_SECONDS", "3") or 3),
        signa_symbol_map=_env_symbol_map(
            "SIGNA_SYMBOL_MAP",
            {"MES": "SPY", "ES": "SPY", "MNQ": "QQQ", "NQ": "QQQ"},
        ),
    )

    _validate_config(config)
    return config


def _parse_position_sizing(raw: dict) -> PositionSizingConfig:
    raw = dict(raw or {})
    if os.getenv("STARTING_BALANCE"):
        raw["starting_balance"] = os.getenv("STARTING_BALANCE")
    rules = []
    for item in raw.get("sizing_rules", []) or []:
        rules.append(
            PositionSizingRule(
                min_balance=float(item.get("min_balance", 0)),
                max_balance=(
                    float(item["max_balance"])
                    if item.get("max_balance") is not None
                    else None
                ),
                instrument=str(item.get("instrument", "")).upper(),
                max_contracts=int(item.get("max_contracts", 0)),
            )
        )
    rules.sort(key=lambda rule: rule.min_balance)
    return PositionSizingConfig(
        starting_balance=float(raw.get("starting_balance", 5000)),
        enabled=bool(raw.get("position_sizing_enabled", False)),
        aggressive_rounding=bool(raw.get("aggressive_rounding", True)),
        rounding_threshold_percent=float(raw.get("rounding_threshold_percent", 10)),
        sizing_rules=rules,
    )


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
    if config.bonus_trades_after_max < 0:
        raise ConfigError("bonus_trades_after_max must be >= 0.")
    if config.news_blackout_mode not in {"off", "block", "reduced"}:
        raise ConfigError("news_blackout_mode must be one of: off, block, reduced.")
    if config.news_blackout_max_trades < 0:
        raise ConfigError("news_blackout_max_trades must be >= 0.")
    if config.min_confluence_grade and config.min_confluence_grade not in {"A+", "A", "B", "C", "WEAK", "F"}:
        raise ConfigError("min_confluence_grade must be one of: A+, A, B, C, WEAK, F.")
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
    if config.position_sizing.starting_balance <= 0:
        raise ConfigError("position_sizing.starting_balance must be > 0.")
    if not (0 <= config.position_sizing.rounding_threshold_percent <= 100):
        raise ConfigError("position_sizing.rounding_threshold_percent must be between 0 and 100.")
    for rule in config.position_sizing.sizing_rules:
        if rule.min_balance < 0:
            raise ConfigError("position sizing min_balance must be >= 0.")
        if rule.max_balance is not None and rule.max_balance <= rule.min_balance:
            raise ConfigError("position sizing max_balance must be greater than min_balance.")
        if not rule.instrument:
            raise ConfigError("position sizing rule instrument is required.")
        if rule.max_contracts < 1:
            raise ConfigError("position sizing max_contracts must be >= 1.")
    if config.signa_timeout_seconds <= 0:
        raise ConfigError("signa_timeout_seconds must be > 0.")
    if config.live_trading_enabled:
        # This should never be reached, but belt-and-suspenders
        raise LiveTradingBlockedError(source="post-parse validation")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def _env_symbol_map(name: str, default: dict) -> dict:
    raw = os.getenv(name, "").strip()
    if not raw:
        return dict(default)
    mapping = dict(default)
    for item in raw.split(","):
        if not item.strip():
            continue
        if ":" not in item:
            raise ConfigError(f"{name} entries must look like MES:SPY")
        key, value = item.split(":", 1)
        key = key.strip().upper()
        value = value.strip().upper()
        if key and value:
            mapping[key] = value
    return mapping


def _env_csv(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]
