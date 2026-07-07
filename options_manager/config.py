"""Own configuration for options_manager.

Never imports config/settings.py — this module's env vars are entirely
independent of the futures system's configuration.
"""

from __future__ import annotations

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
    risk_reject_empty_gex_regime: bool = True
    risk_warn_unknown_gex_regime: bool = True

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
                default=True,
            ),
            risk_warn_unknown_gex_regime=_as_bool(
                os.getenv("OPTIONS_MANAGER_RISK_WARN_UNKNOWN_GEX_REGIME"),
                default=True,
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


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_tuple(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None or value.strip() == "":
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())
