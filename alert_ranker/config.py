"""Environment settings for the advisory options scanner."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


DEFAULT_SIGNA_CONTEXT_PULL_INCLUDE: tuple[str, ...] = (
    "scan",
    "action_card",
    "options_flow",
    "market_tide",
    "signal_index",
)


def _split_watchlist(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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


def _as_non_negative_float(value: str | None, default: float) -> float:
    """Like _as_float, but an explicit 0 is a valid value (it means OFF)."""
    try:
        parsed = float(value if value not in (None, "") else "x")
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


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
    # Legacy /api/v1/signal TTL cache. The grade/score come from Signa's nightly
    # engine, so re-fetching every 5-minute scan only burns the shared
    # 1,000/day quota (it ran out by ~16:05Z on 2026-09-21..23). 0 disables.
    signa_cache_ttl_seconds: float = 3600.0
    signa_symbol_map: dict[str, str] = field(default_factory=dict)
    signa_context_pull_enabled: bool = False
    signa_context_pull_interval_minutes: int = 15
    signa_context_pull_timeframe: str = "1d"
    signa_context_pull_symbols: list[str] = field(default_factory=list)
    signa_context_pull_include: list[str] = field(
        default_factory=lambda: list(DEFAULT_SIGNA_CONTEXT_PULL_INCLUDE)
    )
    signa_context_pull_include_shared_proxies: bool = True
    signa_context_pull_symbol_limit: int = 50
    rh_bearer_token: str = ""
    rh_refresh_token: str = ""
    rh_auto_check_interval_minutes: int = 15
    # Causal bar context (PR C). Default OFF: enabling it changes what the
    # scheduled scanner sees, so it is an explicit operator decision rather
    # than something a deploy turns on by itself.
    bar_context_enabled: bool = False
    bar_context_feed: str = "sip"
    # 30Min is the only canonical source timeframe. Any other value is
    # refused fail-closed at build time rather than silently corrected --
    # native hourly bars are not valid regular-session Strat candles, and an
    # hourly candle is rebuilt from session-aligned 30m pairs instead.
    bar_context_timeframe: str = "30Min"
    bar_context_lookback_days: int = 10
    # Measured entitlement boundary for consolidated bars is exactly 15
    # minutes, and a request inside it fails the whole call. The default
    # carries one minute of margin for clock skew.
    sip_delay_buffer_seconds: int = 960
    alpaca_trading_base_url: str = "https://paper-api.alpaca.markets"
    # Late-entry guard for the ACTIVE paper lane (2026-09-14): minimum reward
    # still available from the live price per unit of risk. Price already past
    # the target or the stop is always refused; 0 disables only the ratio floor.
    paper_v1_min_remaining_rr: float = 1.0
    # Daily paper lane (2026-09-14 operator rulings):
    # - target floor: prior-day levels closer than this many R from the trigger
    #   are skipped when choosing target_1/target_2 (0 or negative = off);
    # - the session-hourly 2U/2D confirmation is no longer required for the
    #   Daily lane (it cannot exist before ~11:46 ET, which is what made every
    #   gap-through day a late entry). SPY/QQQ trend alignment still is. Set the
    #   flag to restore the old hourly requirement.
    paper_v1_daily_min_target_rr: float = 1.0
    paper_v1_daily_require_hourly_alignment: bool = False

    @property
    def rh_configured(self) -> bool:
        return bool(self.rh_bearer_token)

    @property
    def bar_context_configured(self) -> bool:
        return (
            self.bar_context_enabled
            and self.alpaca_api_key_configured
            and self.alpaca_secret_key_configured
        )

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


# The box stores Alpaca credentials as ALPACA_KEY / ALPACA_SECRET, while this
# module originally read only ALPACA_API_KEY / ALPACA_SECRET_KEY.  The mismatch
# made a fully-credentialed provider report `credentials_missing`, which is
# indistinguishable from having no key at all.  Accept both spellings -- the
# same fallback PUBLIC_API_SECRET_KEY/PUBLIC_API_KEY already uses.
ALPACA_KEY_ENV: tuple[str, ...] = ("ALPACA_API_KEY", "ALPACA_KEY")
ALPACA_SECRET_ENV: tuple[str, ...] = ("ALPACA_SECRET_KEY", "ALPACA_SECRET")


def _first_env(env: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = env.get(name, "").strip()
        if value:
            return value
    return ""


def resolve_alpaca_credentials(env: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (key, secret) accepting either supported spelling of each."""
    source = dict(os.environ) if env is None else env
    return _first_env(source, ALPACA_KEY_ENV), _first_env(source, ALPACA_SECRET_ENV)


def misnamed_alpaca_env(env: dict[str, str] | None = None) -> list[str]:
    """Names holding a credential that the canonical variable is missing.

    Reported so a misnamed key surfaces as a config problem instead of
    silently degrading to "unconfigured".
    """
    source = dict(os.environ) if env is None else env
    flagged: list[str] = []
    for names in (ALPACA_KEY_ENV, ALPACA_SECRET_ENV):
        canonical, *aliases = names
        if source.get(canonical, "").strip():
            continue
        flagged.extend(a for a in aliases if source.get(a, "").strip())
    return flagged


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
        alpaca_api_key_configured=bool(_first_env(env, ALPACA_KEY_ENV)),
        alpaca_secret_key_configured=bool(_first_env(env, ALPACA_SECRET_ENV)),
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
        signa_cache_ttl_seconds=_as_non_negative_float(env.get("OPTIONS_SIGNA_CACHE_TTL_SECONDS"), 3600.0),
        signa_symbol_map=_symbol_map(env.get("SIGNA_SYMBOL_MAP")),
        signa_context_pull_enabled=_as_bool(env.get("OPTIONS_SIGNA_CONTEXT_PULL_ENABLED"), False),
        signa_context_pull_interval_minutes=_as_int(env.get("OPTIONS_SIGNA_CONTEXT_PULL_INTERVAL_MINUTES"), 15),
        signa_context_pull_timeframe=env.get("OPTIONS_SIGNA_CONTEXT_PULL_TIMEFRAME", "1d").strip() or "1d",
        signa_context_pull_symbols=_split_watchlist(env.get("OPTIONS_SIGNA_CONTEXT_PULL_SYMBOLS", "")),
        signa_context_pull_include=_split_csv(env.get(
            "OPTIONS_SIGNA_CONTEXT_PULL_INCLUDE",
            ",".join(DEFAULT_SIGNA_CONTEXT_PULL_INCLUDE),
        )),
        signa_context_pull_include_shared_proxies=_as_bool(
            env.get("OPTIONS_SIGNA_CONTEXT_PULL_INCLUDE_SHARED_PROXIES"), True
        ),
        signa_context_pull_symbol_limit=_as_int(env.get("OPTIONS_SIGNA_CONTEXT_PULL_SYMBOL_LIMIT"), 50),
        rh_bearer_token=env.get("RH_BEARER_TOKEN", "").strip(),
        rh_refresh_token=env.get("RH_REFRESH_TOKEN", "").strip(),
        rh_auto_check_interval_minutes=_as_int(env.get("RH_AUTO_CHECK_INTERVAL_MINUTES"), 15),
        bar_context_enabled=_as_bool(env.get("OPTIONS_BAR_CONTEXT_ENABLED"), False),
        bar_context_feed=env.get("OPTIONS_BAR_CONTEXT_FEED", "sip").strip().lower(),
        bar_context_timeframe=env.get("OPTIONS_BAR_CONTEXT_TIMEFRAME", "30Min").strip(),
        bar_context_lookback_days=_as_int(env.get("OPTIONS_BAR_CONTEXT_LOOKBACK_DAYS"), 10),
        sip_delay_buffer_seconds=_as_int(env.get("OPTIONS_SIP_DELAY_BUFFER_SECONDS"), 960),
        paper_v1_min_remaining_rr=_as_non_negative_float(
            env.get("OPTIONS_PAPER_V1_MIN_REMAINING_RR"), 1.0
        ),
        paper_v1_daily_min_target_rr=_as_non_negative_float(
            env.get("OPTIONS_PAPER_V1_DAILY_MIN_TARGET_RR"), 1.0
        ),
        paper_v1_daily_require_hourly_alignment=_as_bool(
            env.get("OPTIONS_PAPER_V1_DAILY_REQUIRE_HOURLY"), False
        ),
        alpaca_trading_base_url=env.get(
            "ALPACA_ENDPOINT", "https://paper-api.alpaca.markets"
        ).strip().rstrip("/"),
    )
