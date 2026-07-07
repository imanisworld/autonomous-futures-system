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
        )


def _as_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")
