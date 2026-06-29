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

    # ── Schedule mode (Phase 3, default preserves current behavior) ──────────
    # "current"          : existing schedule/session gates enforced (DEFAULT).
    # "always_on_shadow" : evaluate all sessions, never submit orders (shadow).
    # "always_on_paper"  : evaluate all sessions, paper orders only (live rejects).
    schedule_mode: str = "current"
    # Sessions eligible for paper orders under always_on_paper. session_gap and
    # off_hours stay shadow-only until they have sufficient evidence.
    paper_eligible_sessions: List[str] = field(
        default_factory=lambda: ["asian", "london", "new_york"]
    )

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
    # EXPERIMENT (off by default): when require_strong_trend is on, still admit a
    # MODERATE-PULLBACK bar (full EMA stack intact, price pulled back below ema9 —
    # a dip inside a confirmed trend, NOT an early/unconfirmed move). Per instrument.
    # Relaxes BOTH the strength gate AND the pre-setup EMA-stack-alignment gate.
    allow_moderate_pullback: dict = field(default_factory=dict)
    # EXPERIMENT (off by default): also admit MODERATE-EARLY bars (trend forming,
    # ema55 not yet flipped). With allow_moderate_pullback this means "trade any
    # MODERATE trend, not only STRONG." Per instrument.
    allow_moderate_early: dict = field(default_factory=dict)
    # When allow_moderate_pullback is on, optionally require price on the trend side
    # of VWAP for the admitted pullback (extra confluence). Per instrument bool.
    moderate_pullback_require_vwap_align: dict = field(default_factory=dict)
    # Quality gates: minimum signal-bar relative volume per instrument
    min_signal_bar_volume: dict = field(default_factory=dict)
    # Quality gates: require explicit daily/4H FTFC alignment when HTF data is present
    require_htf_alignment: dict = field(default_factory=dict)
    # Minimum confluence grade required for ordinary futures entries; blank disables.
    min_confluence_grade: str = ""
    # Required chart timeframe (minutes). Live TradingView alerts MUST be created
    # on this timeframe — the system is tuned/validated on 15m. A webhook arriving
    # on any other timeframe is a misconfigured alert, not a tradeable bar, and is
    # rejected as CONFIG_BLOCKED / TIMEFRAME_MISMATCH (never evaluated as NO_TRADE).
    expected_timeframe_minutes: int = 15
    # Required instruments that MUST be present in allowed_instruments. If any are
    # missing the dashboard surfaces a CONFIG ERROR — guards against a stale
    # in-memory universe silently dropping an instrument (e.g. MNQ).
    required_instruments: List[str] = field(default_factory=lambda: ["MES", "MNQ"])
    # Paper fill realism — applied by PaperBroker in live paper mode AND replay so
    # the simulated WR reflects reality instead of optimistic next-bar fills.
    #   fill_slippage_ticks: adverse slippage on market fills (entry + stop exit).
    #   fill_pessimistic_both_hit: a bar that straddles stop AND target resolves
    #       as the STOP (worst case) rather than the target.
    fill_slippage_ticks: float = 1.0
    fill_pessimistic_both_hit: bool = True
    # 1R→breakeven stop trail (1-contract paper/replay only). DEFAULT False so
    # the simulation matches the live box, which executes static Tradovate OSO
    # brackets with no breakeven trail — trades run to the original stop/target.
    # (Backtests with the trail ON understated strategies ~11% by scratching
    # winners the box actually lets run.) Set True to model a future trailing
    # stop. The live box never uses PaperBroker, so this flag has no live impact.
    breakeven_at_1r: bool = False
    # Runner exit (1-contract paper/replay only): once price reaches
    # runner_activation_r * R in our favour, DROP the fixed target and trail the
    # stop runner_trail_r * R behind the favourable extreme. Validated on the
    # 622-day replay (2026-06-29): beats a fixed target on BOTH instruments —
    # MES runner @1x stop = 74% WR, MNQ runner @2.5x stop = 57% WR, both higher
    # expectancy than fixed. DEFAULT False (matches the static-bracket live box);
    # enable behind a config-freeze + shadow-verify before trusting it live.
    runner_mode: bool = False
    runner_activation_r: float = 1.0
    runner_trail_r: float = 0.5
    # Quality gate (#1): when True, only a TRENDING market condition may trade —
    # RANGE_BOUND / CHOPPY / DEAD all reject (MARKET_CONDITION_NOT_TRENDING). The
    # 555-day replay never took a single trade in a non-TRENDING condition (0/1274),
    # so this is zero-regression on the validated edge while blocking the live
    # out-of-distribution false-breakout entries (e.g. RANGE_BOUND orb_breakout).
    require_trending_condition: bool = True
    # Quality gate: max distance (in ticks) the live close may sit from VWAP for a
    # VWAP-anchored setup (vwap_hold / vwap_reclaim / vwap_rejection) to fire. Those
    # setups place the entry AT VWAP (a retest play); on a strong trend day price
    # runs far from VWAP and never retests, so the entry rests off-market and the
    # live IOC/limit order cancels unfilled — and, being higher-priority, it blocks
    # the momentum setups below it from taking the bar. 0 = disabled (no gate).
    vwap_entry_max_distance_ticks: float = 0.0
    # Momentum entry re-anchor: the OTHER answer to the trend-day no-fill. Instead of
    # BLOCKING a level setup whose price has run away (vwap_entry_max_distance_ticks),
    # this RE-ANCHORS the entry to the live close and recomputes stop/target preserving
    # the original risk/reward, so the trade is taken at market on the move it would
    # otherwise miss. LIMIT/level setups only (vwap_*/pdh_reclaim/pdl_reclaim), and ONLY
    # when price moved in the trade's favor but is still INSIDE the original bracket — so
    # it can never chase a feed-gap dislocation (those still hit the entry-detachment
    # guard). Default OFF; backtest-gate before enabling live. See
    # scripts/fill_realism_report.py for the measured ~54% limit-setup miss rate.
    momentum_entry_reanchor: bool = False
    # Quality gate (#3): ORB-anchored stop offset in ticks beyond the ORB boundary.
    # Per instrument; falls back to the legacy hard-coded 8 ticks when unset. The
    # legacy 8-ticks-past-ORB stop is only ~10 ticks from entry — noise-width — so a
    # good fill still gets shaken out on normal post-breakout wiggle. Widen here and
    # validate on replay before changing live.
    orb_stop_ticks: dict = field(default_factory=dict)
    # Per-instrument INITIAL stop-width multiplier applied to the setup's risk
    # (entry->stop) before bracketing. MNQ's tight stops get swept then reverse —
    # 81% of stopped MNQ trades later hit the original target (vs 43% MES) — so
    # widen MNQ (~2.5x) while keeping MES at 1x. {} or unset instrument = 1.0
    # (no change). Validated on the 622-day replay (2026-06-29). Re-derives the
    # fixed target to preserve R when runner_mode is off.
    stop_multiplier_per_instrument: dict = field(default_factory=dict)
    # Execution (#2) — entry-slippage cap (Limit-vs-Market entry) is PER-INSTRUMENT
    # and read by the live Tradovate broker straight from the environment, NOT from
    # this config (the broker only has TradovateConfig). Set it via env:
    #   ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES=4   (1.0 pt)
    #   ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ=16  (4.0 pt)
    #   ENTRY_SLIPPAGE_TOLERANCE_TICKS=<n>     (global fallback; 0 = Market, default)
    # No SystemConfig field so config can never silently disagree with live behavior.
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
    news_blackout_mode: str = "off"  # off | block | reduced | release_window
    news_blackout_max_trades: int = 1
    news_blackout_cutoff_et: str = "13:30"
    # release_window mode: block only a window (total minutes, centered) around
    # each date's release time; default release time used for untagged dates.
    news_blackout_release_window_minutes: int = 30
    news_blackout_release_default_et: str = "14:00"

    # Future broker/capital planning (inactive while live trading is blocked)
    broker_priority: List[str] = field(default_factory=lambda: ["paper", "tradovate"])
    starting_capital_default: float = 1000.0
    minimum_starting_capital: float = 500.0
    max_account_risk_per_trade_percent: float = 1.0
    max_daily_loss_percent: float = 3.0
    require_margin_check: bool = True
    max_contracts_per_instrument: dict = field(default_factory=dict)
    position_sizing: PositionSizingConfig = field(default_factory=PositionSizingConfig)
    # Hard ceiling applied AFTER dynamic sizing — caps contracts regardless of
    # account balance. None = no cap. Used to keep demo/live execution at 1
    # contract while the balance-tiered rules still scale paper sizing.
    max_contracts_hard_cap: Optional[int] = None
    # Opt-in ranked strategy selection. "first_match" = default live behavior.
    # "ranked" = score all candidates and pick the highest-ranked one.
    strategy_selection_mode: str = "first_match"
    # After daily P&L reaches this threshold, only allow a new trade if its
    # planned max loss (stop_distance * point_value * contracts) does not exceed
    # the current daily profit. 0 = disabled (default).
    daily_profit_protect_threshold: float = 0.0

    # Paths
    log_dir: str = "logs"
    log_level: str = "INFO"
    risk_rules_path: str = "risk_rules.yaml"

    # Read-only notifications
    discord_notifications_enabled: bool = False
    discord_webhook_url: str = ""
    discord_notify_decisions: List[str] = field(default_factory=lambda: ["TRADE", "RISK_REJECTED", "BLOCKED_MAX_TRADES", "BLOCKED_LOSS_LOCKOUT"])
    # Hourly "still alive" heartbeat to Discord (off by default). Only sends while
    # bars are actively arriving, so it stays quiet on weekends / the halt.
    discord_heartbeat_enabled: bool = False
    # Independent live index quote (ES=F/NQ=F via Yahoo) for the Discord display price.
    live_quote_enabled: bool = True

    # Manual execution controls (close-all / flatten).
    # Default OFF: the dashboard hides the controls and /webhook/manual rejects
    # requests before parsing or dispatching an action.
    enable_manual_execution_controls: bool = False

    # Future signal/data vendor planning; key value is never stored on config.
    signa_api_enabled: bool = False
    signa_api_key_configured: bool = False
    signa_base_url: str = "https://app.getsigna.ai"
    signa_timeout_seconds: float = 3.0
    signa_symbol_map: dict = field(default_factory=dict)
    # Shadow vs enforce: when False (default), Signa is fetched/journaled for
    # observation but a FAIL never blocks a trade. Flip to True only after the
    # accumulated shadow data proves Signa-aligned trades actually outperform.
    signa_gate_enforced: bool = False

    # Default-off status analysis over journaled observe-only GEX snapshots.
    # Read-only; never used by DecisionEngine/RiskEngine/gex_gate.
    gex_shadow_analysis_enabled: bool = False

    # Observe-only GEX producer (sources/gex_observer.py). Computes net GEX /
    # gamma-flip / walls in-house from the Public.com chain (gamma + OI) for the
    # instrument's tracking ETF and journals it as `gex_observed`. OBSERVE-ONLY —
    # never mutates state.gex or the gex_gate. Default off.
    gex_observe_enabled: bool = False
    gex_observe_max_dte: int = 7
    # Range observation: wall_context + range_state/range_signal journaled as
    # `wall_context`/`range_state`/`range_signal`/`shadow_range_signal`. OBSERVE-ONLY —
    # never affects decisions or risk. Default off; enable to start collecting.
    range_observe_enabled: bool = False
    gex_observe_symbol_map: dict = field(default_factory=dict)

    # ── Companion options paper lane (options_companion/) ──────────────────────
    # When a futures trade is fully approved + opened, derive an INTERNAL paper
    # options trade (long-premium call/put on the matching ETF) and track it in a
    # separate SQLite ledger. v1 places NO live or broker-paper options orders.
    # Default OFF: a disabled lane changes nothing.
    options_companion_enabled: bool = False
    options_companion_mode: str = "paper"
    options_companion_sqlite_path: str = "logs/options_companion.sqlite"
    # Strict (default) = only grade A/B + daily-aligned candidates record an OPEN.
    # Set OPTIONS_COMPANION_STRICT_SIGNA=false to run "loose" (demo observe): the
    # Signa verdict is still recorded but non-blocking, so every directional candidate
    # logs a real paper OPEN. Still paper-ledger only — never places a broker order.
    options_companion_strict_signa: bool = True
    # Public is the strategic data target (read-only chains/quotes; never orders).
    # Account id scopes the market-data paths (/userapigateway/marketdata/{accountId}/...).
    public_base_url: str = "https://api.public.com"
    public_api_key_configured: bool = False
    public_account_id_configured: bool = False


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
    fill_model = rules.get("fill_model", {}) or {}
    schedule = rules.get("schedule", {}) or {}

    config = SystemConfig(
        live_trading_enabled=False,  # Always false — enforced above
        paper_mode=_env_bool("PAPER_MODE", trading.get("paper_mode", True)),

        allowed_instruments=instruments.get("allowed", []),
        allowed_sessions=sessions.get("allowed", []),
        disabled_sessions=sessions.get("disabled", []),
        session_hours=session_hours,
        schedule_mode=str(schedule.get("mode", "current")).strip().lower(),
        paper_eligible_sessions=schedule.get(
            "paper_eligible_sessions", ["asian", "london", "new_york"]
        ),
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
        news_blackout_release_window_minutes=int(daily.get("news_blackout_release_window_minutes", 30) or 30),
        news_blackout_release_default_et=str(daily.get("news_blackout_release_default_et", "14:00") or "14:00"),
        per_session_limits=daily.get("per_session_limits", {}),
        session_cutoffs=daily.get("session_cutoffs_et", {}),
        min_target_points=daily.get("min_target_points", {}),
        max_stop_ticks=daily.get("max_stop_ticks", {}),
        require_strong_trend=quality.get("require_strong_trend", {}),
        allow_moderate_pullback=quality.get("allow_moderate_pullback", {}),
        allow_moderate_early=quality.get("allow_moderate_early", {}),
        moderate_pullback_require_vwap_align=quality.get(
            "moderate_pullback_require_vwap_align", {}
        ),
        min_signal_bar_volume=quality.get("min_signal_bar_volume", {}),
        require_htf_alignment=quality.get("require_htf_alignment", {}),
        min_confluence_grade=str(quality.get("min_confluence_grade", "") or "").upper(),
        fill_slippage_ticks=float(
            os.getenv("FILL_SLIPPAGE_TICKS")
            or fill_model.get("slippage_ticks", 1.0)
            or 1.0
        ),
        fill_pessimistic_both_hit=_env_bool(
            "FILL_PESSIMISTIC_BOTH_HIT",
            bool(fill_model.get("pessimistic_both_hit", True)),
        ),
        breakeven_at_1r=_env_bool(
            "BREAKEVEN_AT_1R",
            bool(fill_model.get("breakeven_at_1r", False)),
        ),
        runner_mode=_env_bool(
            "RUNNER_MODE",
            bool(fill_model.get("runner_mode", False)),
        ),
        runner_activation_r=float(
            os.getenv("RUNNER_ACTIVATION_R", fill_model.get("runner_activation_r", 1.0)) or 1.0
        ),
        runner_trail_r=float(
            os.getenv("RUNNER_TRAIL_R", fill_model.get("runner_trail_r", 0.5)) or 0.5
        ),

        max_open_positions=position.get("max_open_positions", 1),
        averaging_down_allowed=position.get("averaging_down", False),
        max_contracts_per_instrument=position.get("max_contracts_per_instrument", {}),
        position_sizing=_parse_position_sizing(sizing),
        max_contracts_hard_cap=(
            int(os.getenv("MAX_CONTRACTS_HARD_CAP"))
            if (os.getenv("MAX_CONTRACTS_HARD_CAP") or "").strip().isdigit()
            else position.get("max_contracts_hard_cap")
        ),
        daily_profit_protect_threshold=float(
            daily.get("daily_profit_protect_threshold", 0.0) or 0.0
        ),

        require_entry=orders.get("require_entry", True),
        require_stop=orders.get("require_stop", True),
        require_target=orders.get("require_target", True),

        min_rr_ratio=float(rr.get("min_rr_ratio", 2.0)),

        max_staleness_seconds=data.get("max_staleness_seconds", 300),
        reject_null_required_fields=data.get("reject_null_required_fields", True),
        reject_contradictory_data=data.get("reject_contradictory_data", True),
        expected_timeframe_minutes=int(
            os.getenv("PRIMARY_DECISION_TF")
            or os.getenv("EXPECTED_TIMEFRAME_MINUTES")
            or data.get("expected_timeframe_minutes", 15)
            or 15
        ),
        required_instruments=[
            str(s).upper()
            for s in (instruments.get("required", ["MES", "MNQ"]) or ["MES", "MNQ"])
        ],

        tradable_states=condition.get("tradable_states", ["TRENDING", "RANGE_BOUND"]),
        non_tradable_states=condition.get("non_tradable_states", ["CHOPPY", "DEAD"]),
        require_trending_condition=_env_bool(
            "REQUIRE_TRENDING_CONDITION",
            bool(condition.get("require_trending", True)),
        ),
        vwap_entry_max_distance_ticks=float(
            os.getenv("VWAP_ENTRY_MAX_DISTANCE_TICKS")
            or strategy.get("vwap_entry_max_distance_ticks", 0.0)
            or 0.0
        ),
        momentum_entry_reanchor=_env_bool(
            "MOMENTUM_ENTRY_REANCHOR",
            bool(strategy.get("momentum_entry_reanchor", False)),
        ),

        enabled_concepts=strategy.get("enabled_concepts", []),
        orb_stop_ticks=strategy.get("orb_stop_ticks", {}),
        stop_multiplier_per_instrument=strategy.get("stop_multiplier_per_instrument", {}),
        strategy_selection_mode=str(
            strategy.get("selection_mode", "first_match") or "first_match"
        ).lower(),
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
        discord_heartbeat_enabled=_env_bool("DISCORD_HEARTBEAT_ENABLED", False),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        discord_notify_decisions=_env_csv(
            "DISCORD_NOTIFY_DECISIONS",
            ["TRADE", "RISK_REJECTED", "BLOCKED_MAX_TRADES", "BLOCKED_LOSS_LOCKOUT"],
        ),
        live_quote_enabled=_env_bool("LIVE_QUOTE_ENABLED", True),
        enable_manual_execution_controls=_env_bool("ENABLE_MANUAL_EXECUTION_CONTROLS", False),

        signa_api_enabled=_env_bool("SIGNA_API_ENABLED", False),
        signa_api_key_configured=bool(os.getenv("SIGNA_API_KEY", "").strip()),
        signa_base_url=os.getenv("SIGNA_BASE_URL", "https://app.getsigna.ai").strip().rstrip("/"),
        signa_timeout_seconds=float(os.getenv("SIGNA_TIMEOUT_SECONDS", "3") or 3),
        signa_symbol_map=_env_symbol_map(
            "SIGNA_SYMBOL_MAP",
            # MES→ES (S&P future is in Signa's universe); MNQ→QQQ (NASDAQ futures
            # NQ/NDX are NOT covered, QQQ ETF is the only working NASDAQ proxy).
            {"MES": "ES", "ES": "ES", "MNQ": "QQQ", "NQ": "QQQ"},
        ),
        signa_gate_enforced=_env_bool("SIGNA_GATE_ENFORCED", False),

        gex_shadow_analysis_enabled=_env_bool("GEX_SHADOW_ANALYSIS_ENABLED", False),
        gex_observe_enabled=_env_bool("GEX_OBSERVE_ENABLED", False),
        gex_observe_max_dte=int(os.getenv("GEX_OBSERVE_MAX_DTE", "7") or 7),
        range_observe_enabled=_env_bool("RANGE_OBSERVE_ENABLED", False),
        gex_observe_symbol_map=_env_symbol_map(
            # MNQ/NQ → QQQ, MES/ES → SPY (liquid ETF options on Public).
            "GEX_OBSERVE_SYMBOL_MAP",
            {"MNQ": "QQQ", "NQ": "QQQ", "MES": "SPY", "ES": "SPY"},
        ),

        options_companion_enabled=_env_bool("OPTIONS_COMPANION_ENABLED", False),
        options_companion_mode=os.getenv("OPTIONS_COMPANION_MODE", "paper").strip().lower(),
        options_companion_sqlite_path=os.getenv(
            "OPTIONS_COMPANION_SQLITE_PATH", "logs/options_companion.sqlite"
        ).strip(),
        options_companion_strict_signa=_env_bool("OPTIONS_COMPANION_STRICT_SIGNA", True),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "https://api.public.com").strip().rstrip("/"),
        public_api_key_configured=bool(os.getenv("PUBLIC_API_KEY", "").strip()),
        public_account_id_configured=bool(os.getenv("PUBLIC_ACCOUNT_ID", "").strip()),
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
    if config.schedule_mode not in {"current", "always_on_shadow", "always_on_paper"}:
        raise ConfigError(
            "schedule_mode must be one of: current, always_on_shadow, always_on_paper."
        )
    # Safety invariant: live execution may run ONLY the "current" schedule.
    # BOTH always-on modes are forbidden when live trading is enabled.
    if config.live_trading_enabled and config.schedule_mode != "current":
        raise ConfigError(
            f"schedule_mode '{config.schedule_mode}' is forbidden when "
            "live_trading_enabled is true — live execution must use 'current' "
            "(always-on must never place live orders)."
        )
    if config.max_trades_per_day < 1:
        raise ConfigError("max_trades_per_day must be >= 1.")
    if config.max_consecutive_losses < 1:
        raise ConfigError("max_consecutive_losses must be >= 1.")
    if config.bonus_trades_after_max < 0:
        raise ConfigError("bonus_trades_after_max must be >= 0.")
    if config.news_blackout_mode not in {"off", "block", "reduced", "release_window"}:
        raise ConfigError("news_blackout_mode must be one of: off, block, reduced, release_window.")
    if config.news_blackout_max_trades < 0:
        raise ConfigError("news_blackout_max_trades must be >= 0.")
    if config.news_blackout_release_window_minutes < 0:
        raise ConfigError("news_blackout_release_window_minutes must be >= 0.")
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
