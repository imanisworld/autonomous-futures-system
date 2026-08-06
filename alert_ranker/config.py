"""Environment settings for the advisory options scanner."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


def _split_watchlist(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float) -> float:
    try:
        parsed = float(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _symbol_map(value: str | None) -> dict[str, str]:
    mapping = {
        "SPXW": "SPY",
        "SPX": "SPY",
        "SPY": "SPY",
        "QQQ": "QQQ",
        "MES": "SPY",
        "ES": "SPY",
        "MNQ": "QQQ",
        "NQ": "QQQ",
    }
    raw = (value or "").strip()
    if not raw:
        return mapping
    for item in raw.split(","):
        if not item.strip() or ":" not in item:
            continue
        key, val = item.split(":", 1)
        key = key.strip().upper()
        val = val.strip().upper()
        if key and val:
            mapping[key] = val
    return mapping


def _as_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class ScannerConfig:
    market_data_provider: str
    tastytrade_username: str
    tastytrade_password: str
    tastytrade_base_url: str
    public_api_key_configured: bool
    public_base_url: str
    alpaca_api_key_configured: bool
    alpaca_secret_key_configured: bool
    alpaca_paper: bool
    alpaca_data_base_url: str
    port: int
    discord_webhook_url: str
    watchlist: list[str]
    interval_minutes: int
    sqlite_path: Path
    timezone: str = "America/New_York"
    public_account_id: str = ""
    public_token_validity_minutes: int = 1440
    public_stale_quote_seconds: float = 900.0
    alert_threshold: int = 7
    duplicate_window_minutes: int = 30
    signa_api_enabled: bool = False
    signa_api_key_configured: bool = False
    signa_base_url: str = "https://app.getsigna.ai"
    signa_timeout_seconds: float = 3.0
    signa_symbol_map: dict[str, str] = field(default_factory=dict)
    rh_bearer_token: str = ""
    rh_refresh_token: str = ""
    rh_auto_check_interval_minutes: int = 15

    @property
    def rh_configured(self) -> bool:
        return bool(self.rh_bearer_token)

    @property
    def tastytrade_configured(self) -> bool:
        return bool(self.tastytrade_username and self.tastytrade_password)

    @property
    def market_data_configured(self) -> bool:
        provider = self.market_data_provider.lower()
        if provider == "tastytrade":
            return self.tastytrade_configured
        if provider == "public":
            return self.public_api_key_configured and bool(self.public_account_id)
        if provider == "alpaca":
            return self.alpaca_api_key_configured and self.alpaca_secret_key_configured
        return False


def load_config(environ: Iterable[tuple[str, str]] | None = None) -> ScannerConfig:
    load_dotenv()
    env = dict(environ) if environ is not None else os.environ
    return ScannerConfig(
        market_data_provider=env.get("OPTIONS_MARKET_DATA_PROVIDER", "public").strip().lower(),
        tastytrade_username=env.get("TASTYTRADE_USERNAME", ""),
        tastytrade_password=env.get("TASTYTRADE_PASSWORD", ""),
        tastytrade_base_url=env.get("TASTYTRADE_BASE_URL", "https://api.tastyworks.com"),
        public_api_key_configured=bool(
            env.get("PUBLIC_API_SECRET_KEY", "").strip() or env.get("PUBLIC_API_KEY", "").strip()
        ),
        public_base_url=env.get("PUBLIC_BASE_URL", "https://api.public.com").strip().rstrip("/"),
        public_account_id=env.get("PUBLIC_ACCOUNT_ID", "").strip(),
        public_token_validity_minutes=_as_int(env.get("PUBLIC_TOKEN_VALIDITY_MINUTES"), 1440),
        public_stale_quote_seconds=_as_float(env.get("PUBLIC_STALE_QUOTE_SECONDS"), 900.0),
        alpaca_api_key_configured=bool(env.get("ALPACA_API_KEY", "").strip()),
        alpaca_secret_key_configured=bool(env.get("ALPACA_SECRET_KEY", "").strip()),
        alpaca_paper=_as_bool(env.get("ALPACA_PAPER"), True),
        alpaca_data_base_url=env.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").strip().rstrip("/"),
        port=_as_int(env.get("OPTIONS_SCANNER_PORT"), 8010),
        discord_webhook_url=env.get("OPTIONS_SCANNER_DISCORD_WEBHOOK_URL", ""),
        watchlist=_split_watchlist(
            env.get("OPTIONS_SCANNER_WATCHLIST", "AAPL,MSFT,NVDA,TSLA,SPY,QQQ")
        ),
        interval_minutes=_as_int(env.get("OPTIONS_SCANNER_INTERVAL_MINUTES"), 5),
        sqlite_path=Path(env.get("OPTIONS_SCANNER_SQLITE_PATH", "logs/options_scanner.sqlite")),
        signa_api_enabled=_as_bool(env.get("SIGNA_API_ENABLED"), False),
        signa_api_key_configured=bool(env.get("SIGNA_API_KEY", "").strip()),
        signa_base_url=env.get("SIGNA_BASE_URL", "https://app.getsigna.ai").strip().rstrip("/"),
        signa_timeout_seconds=_as_float(env.get("SIGNA_TIMEOUT_SECONDS"), 3.0),
        signa_symbol_map=_symbol_map(env.get("SIGNA_SYMBOL_MAP")),
        rh_bearer_token=env.get("RH_BEARER_TOKEN", "").strip(),
        rh_refresh_token=env.get("RH_REFRESH_TOKEN", "").strip(),
        rh_auto_check_interval_minutes=_as_int(env.get("RH_AUTO_CHECK_INTERVAL_MINUTES"), 15),
    )
