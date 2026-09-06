"""Own configuration for options_manager.

Never imports config/settings.py — this module's env vars are entirely
independent of the futures system's configuration.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class OptionsManagerConfig:
    port: int = 8020
    discord_webhook_url: str = ""
    live_options_trading_enabled: bool = False
    journal_dir: str = "logs"
    # Shared secret for POST /options/packet, via OPTIONS_MANAGER_INGEST_SECRET.
    # This is intentionally independent of the futures webhook's secret — never
    # reuse it. If left unset, endpoint auth is disabled: fine for local/dev
    # use, but must be set before this endpoint is reachable from anywhere else.
    ingest_secret: str = ""

    # Phase 2 — options risk gate. Independent of risk_rules.yaml (the futures
    # config) by design; these gate options_manager's own packets only.
    risk_max_premium: float = 3.00
    risk_max_contracts: int = 2
    risk_max_total_premium_dollars: float = 300.00
    risk_min_dte_days: int = 14
    risk_min_signa_score: int = 30
    risk_allowed_grades: tuple[str, ...] = ("A", "B")
    risk_allowed_account_tags: tuple[str, ...] = ("agentic_micro_account",)
    # GEX is optional enrichment, not a required feed. Default is warn-and-proceed
    # so the lane does not depend on a vendor GEX subscription. Set the env var to
    # true only if a GEX source is present AND has earned the gate on evidence.
    risk_reject_empty_gex_regime: bool = False
    risk_warn_unknown_gex_regime: bool = True
    # Canonical portfolio-risk budget for the advisory decision path. NO
    # DEFAULT: the earlier hardcoded $1,000 was never approved policy. None
    # means unset (aggregate_risk_budget_missing); a supplied value that is
    # unparseable, non-finite, zero or negative is carried through and
    # rejected at the gate (aggregate_risk_budget_invalid). Either way the
    # canonical decision blocks and can never be TAKE. Set it only as a
    # deliberate operator decision.
    max_aggregate_open_risk_dollars: float | None = None

    # Phase 3 — contract quality / market data gate. Independent of
    # risk_rules.yaml; gates options_manager's own contract snapshots only.
    quality_max_spread_percent: float = 20.0
    quality_min_option_volume: int = 100
    quality_min_open_interest: int = 500
    quality_missing_greeks_blocks: bool = False
    quality_missing_quote_blocks: bool = True
    quality_missing_oi_volume_blocks: bool = True
    quality_max_quote_age_seconds: int = 900
    quality_require_quote_timestamp: bool = True
    quality_require_underlying_price: bool = True
    quality_underlying_price_max_diff_percent: float = 3.0
    quality_reject_underlying_price_mismatch: bool = False
    quality_min_abs_delta: float = 0.30
    quality_max_abs_delta: float = 0.70
    quality_high_iv_warning_threshold: float = 1.00
    quality_theta_warning_ratio: float = 0.10
    quality_allow_mock_provider: bool = True

    # Phase 4 — paper simulation (backtest/replay). Independent of
    # risk_rules.yaml; simulates options_manager's own packets against
    # supplied entry/exit snapshots only, no provider fetching.
    paper_sim_entry_fill: str = "ASK"
    paper_sim_exit_fill: str = "BID"
    paper_sim_slippage_percent: float = 0.0
    paper_sim_per_contract_fee: float = 0.0
    paper_sim_contract_multiplier: int = 100
    paper_sim_require_approved_risk: bool = True
    paper_sim_require_approved_quality: bool = True

    # Phase 5 — dry-run order review. Independent of risk_rules.yaml; builds a
    # local review object for options_manager's own packets only. No broker
    # calls, no order preview, no order placement.
    dry_run_enabled: bool = True
    dry_run_max_contracts: int = 2
    dry_run_max_notional: float = 300.00
    dry_run_allowed_account_tags: tuple[str, ...] = ("agentic_micro_account",)
    dry_run_order_action: str = "BUY_TO_OPEN"
    dry_run_require_paper_simulated: bool = True

    # Phase 6 — human-confirmed order prep. Independent of risk_rules.yaml;
    # verifies a caller-supplied confirmation record against a local dry-run
    # review only. No storage, no broker calls, no order placement.
    human_confirm_enabled: bool = True
    human_confirm_ttl_seconds: int = 300
    human_confirm_required_phrase: str = "CONFIRM DRY RUN ORDER PREP"
    human_confirm_case_sensitive: bool = True
    human_confirm_require_reviewer: bool = True
    human_confirm_require_nonce: bool = True

    # Phase 7 — controlled order ticket preparation. Independent of
    # risk_rules.yaml; builds a local, non-executable order ticket from a
    # Phase 6 confirmed order prep only. No storage, no broker calls, no
    # order placement.
    order_ticket_enabled: bool = True
    order_ticket_ttl_seconds: int = 120
    order_ticket_max_contracts: int = 2
    order_ticket_max_notional: float = 300.00
    order_ticket_max_limit_price: float = 3.00
    order_ticket_allowed_account_tags: tuple[str, ...] = ("agentic_micro_account",)

    # Phase 9 — inert broker boundary schema. Independent of risk_rules.yaml;
    # converts a Phase 7 order ticket into a local preview request and
    # re-validates it with its own independent safety caps only. No broker
    # calls, no real preview, no order placement, no storage.
    broker_boundary_enabled: bool = True
    broker_boundary_allow_real_preview: bool = False
    broker_boundary_max_contracts: int = 2
    broker_boundary_max_notional: float = 300.00
    broker_boundary_max_limit_price: float = 3.00
    broker_boundary_allowed_account_tags: tuple[str, ...] = ("agentic_micro_account",)

    # Phase 12 — inert append-only storage layer. Independent of
    # risk_rules.yaml; durably records confirmation-consumed and
    # ticket-created events for replay protection only. No broker calls,
    # no HTTP, no order queue, no update/delete of any stored row.
    storage_enabled: bool = True
    storage_backend: str = "sqlite"
    storage_require_append_only: bool = True
    storage_reject_order_queue_fields: bool = True

    # Phase 15 — inert read-only HTTP status API. Independent of
    # risk_rules.yaml and of OPTIONS_MANAGER_INGEST_SECRET; gates a
    # dedicated, read-only confirmation/ticket status surface only. No
    # writes, no broker calls, no order placement.
    http_status_enabled: bool = True
    http_status_secret: str = ""
    http_status_require_secret: bool = True

    @classmethod
    def from_env(cls) -> "OptionsManagerConfig":
        load_dotenv()
        return cls(
            port=_as_int(os.getenv("OPTIONS_MANAGER_PORT"), 8020),
            discord_webhook_url=os.getenv("OPTIONS_MANAGER_DISCORD_WEBHOOK_URL", ""),
            live_options_trading_enabled=_as_bool(
                os.getenv("LIVE_OPTIONS_TRADING_ENABLED")
            ),
            journal_dir=os.getenv("OPTIONS_MANAGER_JOURNAL_DIR", "logs"),
            ingest_secret=os.getenv("OPTIONS_MANAGER_INGEST_SECRET", ""),
            risk_max_premium=_as_float(
                os.getenv("OPTIONS_MANAGER_RISK_MAX_PREMIUM"), 3.00
            ),
            risk_max_contracts=_as_int(
                os.getenv("OPTIONS_MANAGER_RISK_MAX_CONTRACTS"), 2
            ),
            risk_max_total_premium_dollars=_as_float(
                os.getenv("OPTIONS_MANAGER_RISK_MAX_TOTAL_PREMIUM_DOLLARS"), 300.00
            ),
            risk_min_dte_days=_as_int(
                os.getenv("OPTIONS_MANAGER_RISK_MIN_DTE_DAYS"), 14
            ),
            risk_min_signa_score=_as_int(
                os.getenv("OPTIONS_MANAGER_RISK_MIN_SIGNA_SCORE"), 30
            ),
            risk_allowed_grades=_as_tuple(
                os.getenv("OPTIONS_MANAGER_RISK_ALLOWED_GRADES"), ("A", "B")
            ),
            risk_allowed_account_tags=_as_tuple(
                os.getenv("OPTIONS_MANAGER_RISK_ALLOWED_ACCOUNT_TAGS"),
                ("agentic_micro_account",),
            ),
            risk_reject_empty_gex_regime=_as_bool(
                os.getenv("OPTIONS_MANAGER_RISK_REJECT_EMPTY_GEX_REGIME"),
                default=False,
            ),
            max_aggregate_open_risk_dollars=_as_budget_float(
                os.getenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS")
            ),
            risk_warn_unknown_gex_regime=_as_bool(
                os.getenv("OPTIONS_MANAGER_RISK_WARN_UNKNOWN_GEX_REGIME"),
                default=True,
            ),
            quality_max_spread_percent=_as_float(
                os.getenv("OPTIONS_MANAGER_QUALITY_MAX_SPREAD_PERCENT"), 20.0
            ),
            quality_min_option_volume=_as_int(
                os.getenv("OPTIONS_MANAGER_QUALITY_MIN_OPTION_VOLUME"), 100
            ),
            quality_min_open_interest=_as_int(
                os.getenv("OPTIONS_MANAGER_QUALITY_MIN_OPEN_INTEREST"), 500
            ),
            quality_missing_greeks_blocks=_as_bool(
                os.getenv("OPTIONS_MANAGER_QUALITY_MISSING_GREEKS_BLOCKS"),
                default=False,
            ),
            quality_missing_quote_blocks=_as_bool(
                os.getenv("OPTIONS_MANAGER_QUALITY_MISSING_QUOTE_BLOCKS"),
                default=True,
            ),
            quality_missing_oi_volume_blocks=_as_bool(
                os.getenv("OPTIONS_MANAGER_QUALITY_MISSING_OI_VOLUME_BLOCKS"),
                default=True,
            ),
            quality_max_quote_age_seconds=_as_int(
                os.getenv("OPTIONS_MANAGER_QUALITY_MAX_QUOTE_AGE_SECONDS"), 900
            ),
            quality_require_quote_timestamp=_as_bool(
                os.getenv("OPTIONS_MANAGER_QUALITY_REQUIRE_QUOTE_TIMESTAMP"),
                default=True,
            ),
            quality_require_underlying_price=_as_bool(
                os.getenv("OPTIONS_MANAGER_QUALITY_REQUIRE_UNDERLYING_PRICE"),
                default=True,
            ),
            quality_underlying_price_max_diff_percent=_as_float(
                os.getenv("OPTIONS_MANAGER_QUALITY_UNDERLYING_PRICE_MAX_DIFF_PERCENT"),
                3.0,
            ),
            quality_reject_underlying_price_mismatch=_as_bool(
                os.getenv("OPTIONS_MANAGER_QUALITY_REJECT_UNDERLYING_PRICE_MISMATCH"),
                default=False,
            ),
            quality_min_abs_delta=_as_float(
                os.getenv("OPTIONS_MANAGER_QUALITY_MIN_ABS_DELTA"), 0.30
            ),
            quality_max_abs_delta=_as_float(
                os.getenv("OPTIONS_MANAGER_QUALITY_MAX_ABS_DELTA"), 0.70
            ),
            quality_high_iv_warning_threshold=_as_float(
                os.getenv("OPTIONS_MANAGER_QUALITY_HIGH_IV_WARNING_THRESHOLD"), 1.00
            ),
            quality_theta_warning_ratio=_as_float(
                os.getenv("OPTIONS_MANAGER_QUALITY_THETA_WARNING_RATIO"), 0.10
            ),
            quality_allow_mock_provider=_as_bool(
                os.getenv("OPTIONS_MANAGER_QUALITY_ALLOW_MOCK_PROVIDER"),
                default=True,
            ),
            paper_sim_entry_fill=os.getenv("OPTIONS_MANAGER_PAPER_SIM_ENTRY_FILL", "ASK"),
            paper_sim_exit_fill=os.getenv("OPTIONS_MANAGER_PAPER_SIM_EXIT_FILL", "BID"),
            paper_sim_slippage_percent=_as_float(
                os.getenv("OPTIONS_MANAGER_PAPER_SIM_SLIPPAGE_PERCENT"), 0.0
            ),
            paper_sim_per_contract_fee=_as_float(
                os.getenv("OPTIONS_MANAGER_PAPER_SIM_PER_CONTRACT_FEE"), 0.0
            ),
            paper_sim_contract_multiplier=_as_int(
                os.getenv("OPTIONS_MANAGER_PAPER_SIM_CONTRACT_MULTIPLIER"), 100
            ),
            paper_sim_require_approved_risk=_as_bool(
                os.getenv("OPTIONS_MANAGER_PAPER_SIM_REQUIRE_APPROVED_RISK"),
                default=True,
            ),
            paper_sim_require_approved_quality=_as_bool(
                os.getenv("OPTIONS_MANAGER_PAPER_SIM_REQUIRE_APPROVED_QUALITY"),
                default=True,
            ),
            dry_run_enabled=_as_bool(
                os.getenv("OPTIONS_MANAGER_DRY_RUN_ENABLED"), default=True
            ),
            dry_run_max_contracts=_as_int(
                os.getenv("OPTIONS_MANAGER_DRY_RUN_MAX_CONTRACTS"), 2
            ),
            dry_run_max_notional=_as_float(
                os.getenv("OPTIONS_MANAGER_DRY_RUN_MAX_NOTIONAL"), 300.00
            ),
            dry_run_allowed_account_tags=_as_tuple(
                os.getenv("OPTIONS_MANAGER_DRY_RUN_ALLOWED_ACCOUNT_TAGS"),
                ("agentic_micro_account",),
            ),
            dry_run_order_action=os.getenv(
                "OPTIONS_MANAGER_DRY_RUN_ORDER_ACTION", "BUY_TO_OPEN"
            ),
            dry_run_require_paper_simulated=_as_bool(
                os.getenv("OPTIONS_MANAGER_DRY_RUN_REQUIRE_PAPER_SIMULATED"),
                default=True,
            ),
            human_confirm_enabled=_as_bool(
                os.getenv("OPTIONS_MANAGER_HUMAN_CONFIRM_ENABLED"), default=True
            ),
            human_confirm_ttl_seconds=_as_int(
                os.getenv("OPTIONS_MANAGER_HUMAN_CONFIRM_TTL_SECONDS"), 300
            ),
            human_confirm_required_phrase=os.getenv(
                "OPTIONS_MANAGER_HUMAN_CONFIRM_REQUIRED_PHRASE",
                "CONFIRM DRY RUN ORDER PREP",
            ),
            human_confirm_case_sensitive=_as_bool(
                os.getenv("OPTIONS_MANAGER_HUMAN_CONFIRM_CASE_SENSITIVE"),
                default=True,
            ),
            human_confirm_require_reviewer=_as_bool(
                os.getenv("OPTIONS_MANAGER_HUMAN_CONFIRM_REQUIRE_REVIEWER"),
                default=True,
            ),
            human_confirm_require_nonce=_as_bool(
                os.getenv("OPTIONS_MANAGER_HUMAN_CONFIRM_REQUIRE_NONCE"),
                default=True,
            ),
            order_ticket_enabled=_as_bool(
                os.getenv("OPTIONS_MANAGER_ORDER_TICKET_ENABLED"), default=True
            ),
            order_ticket_ttl_seconds=_as_int(
                os.getenv("OPTIONS_MANAGER_ORDER_TICKET_TTL_SECONDS"), 120
            ),
            order_ticket_max_contracts=_as_int(
                os.getenv("OPTIONS_MANAGER_ORDER_TICKET_MAX_CONTRACTS"), 2
            ),
            order_ticket_max_notional=_as_float(
                os.getenv("OPTIONS_MANAGER_ORDER_TICKET_MAX_NOTIONAL"), 300.00
            ),
            order_ticket_max_limit_price=_as_float(
                os.getenv("OPTIONS_MANAGER_ORDER_TICKET_MAX_LIMIT_PRICE"), 3.00
            ),
            order_ticket_allowed_account_tags=_as_tuple(
                os.getenv("OPTIONS_MANAGER_ORDER_TICKET_ALLOWED_ACCOUNT_TAGS"),
                ("agentic_micro_account",),
            ),
            broker_boundary_enabled=_as_bool(
                os.getenv("OPTIONS_MANAGER_BROKER_BOUNDARY_ENABLED"), default=True
            ),
            broker_boundary_allow_real_preview=_as_bool(
                os.getenv("OPTIONS_MANAGER_BROKER_BOUNDARY_ALLOW_REAL_PREVIEW"),
                default=False,
            ),
            broker_boundary_max_contracts=_as_int(
                os.getenv("OPTIONS_MANAGER_BROKER_BOUNDARY_MAX_CONTRACTS"), 2
            ),
            broker_boundary_max_notional=_as_float(
                os.getenv("OPTIONS_MANAGER_BROKER_BOUNDARY_MAX_NOTIONAL"), 300.00
            ),
            broker_boundary_max_limit_price=_as_float(
                os.getenv("OPTIONS_MANAGER_BROKER_BOUNDARY_MAX_LIMIT_PRICE"), 3.00
            ),
            broker_boundary_allowed_account_tags=_as_tuple(
                os.getenv("OPTIONS_MANAGER_BROKER_BOUNDARY_ALLOWED_ACCOUNT_TAGS"),
                ("agentic_micro_account",),
            ),
            storage_enabled=_as_bool(
                os.getenv("OPTIONS_MANAGER_STORAGE_ENABLED"), default=True
            ),
            storage_backend=os.getenv("OPTIONS_MANAGER_STORAGE_BACKEND", "sqlite"),
            storage_require_append_only=_as_bool(
                os.getenv("OPTIONS_MANAGER_STORAGE_REQUIRE_APPEND_ONLY"), default=True
            ),
            storage_reject_order_queue_fields=_as_bool(
                os.getenv("OPTIONS_MANAGER_STORAGE_REJECT_ORDER_QUEUE_FIELDS"),
                default=True,
            ),
            http_status_enabled=_as_bool(
                os.getenv("OPTIONS_MANAGER_HTTP_STATUS_ENABLED"), default=True
            ),
            http_status_secret=os.getenv("OPTIONS_MANAGER_HTTP_STATUS_SECRET", ""),
            http_status_require_secret=_as_bool(
                os.getenv("OPTIONS_MANAGER_HTTP_STATUS_REQUIRE_SECRET"), default=True
            ),
        )


def _as_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_float(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _as_budget_float(value: str | None) -> float | None:
    """Parse an operator-supplied risk budget without losing its state.

    ``None`` means genuinely unset or blank -- the operator configured nothing.
    A value that was supplied but does not parse comes back as ``nan``: it is
    a *configured, invalid* budget, and the risk gate rejects every non-finite
    value under its own reason code. Collapsing it to ``None`` would tell the
    operator to set a variable they already set. Nothing here ever returns a
    number the operator did not type.
    """
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return math.nan


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_tuple(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None or value.strip() == "":
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())
