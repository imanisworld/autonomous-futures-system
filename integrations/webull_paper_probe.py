"""Webull paper adapter Phase 1 read-only sandbox account probe.

Safety contract:
- fixed Webull sandbox Trading API endpoint only;
- Phase 0 config must explicitly allow API calls;
- account-list GET only;
- no broad trading client or execution wiring;
- no account identifiers or secret values are returned or logged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from typing import Any, Callable, Mapping, Protocol

from integrations.webull_paper_config import (
    WEBULL_SANDBOX_HOST,
    load_webull_paper_config,
)

WEBULL_SANDBOX_TRADING_ENDPOINT = WEBULL_SANDBOX_HOST
WEBULL_REGION = "us"


class _ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any: ...
class _AccountClientLike(Protocol):
    def get_account_list(self) -> _ResponseLike: ...


AccountClientFactory = Callable[[str, str], _AccountClientLike]


@dataclass(frozen=True)
class WebullPaperProbeResult:
    provider: str
    mode: str
    endpoint: str
    status: str
    reachable: bool
    paper_context_verified: bool
    account_count: int | None
    observed_at: str
    reason: str | None = None

    def to_safe_dict(self) -> dict[str, object]:
        """Return only non-secret, non-account-identifying probe metadata."""
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _blocked(reason: str, observed_at: datetime) -> WebullPaperProbeResult:
    return WebullPaperProbeResult(
        provider="webull",
        mode="paper",
        endpoint=WEBULL_SANDBOX_TRADING_ENDPOINT,
        status="BLOCKED",
        reachable=False,
        paper_context_verified=False,
        account_count=None,
        observed_at=_format_utc(observed_at),
        reason=reason,
    )


def _extract_account_count(payload: Any) -> int | None:
    """Count accounts without retaining account identifiers or row contents."""
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None
    for key in ("accounts", "account_list", "accountList"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)

    data = payload.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("accounts", "account_list", "accountList", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def _official_account_client(app_key: str, app_secret: str) -> _AccountClientLike:
    """Create the narrow official SDK account client for the sandbox host."""
    from webull.core.client import ApiClient
    from webull.trade.trade.v2.account_info_v2 import AccountV2

    api_client = ApiClient(app_key, app_secret, WEBULL_REGION)
    api_client.add_endpoint(WEBULL_REGION, WEBULL_SANDBOX_TRADING_ENDPOINT)
    return AccountV2(api_client)
def probe_webull_sandbox_accounts(
    env: Mapping[str, str] | None = None,
    *,
    account_client_factory: AccountClientFactory | None = None,
    observed_at: datetime | None = None,
) -> WebullPaperProbeResult:
    """Perform one read-only account-list call against Webull sandbox.

    Refuses before SDK import/client creation unless Phase 0 config is
    paper-only safe and WEBULL_SANDBOX_API_ENABLED=true.
    """
    source = os.environ if env is None else env
    now = observed_at or _utc_now()
    config = load_webull_paper_config(source)

    if not config.paper_only_safe:
        return _blocked("phase0_config_invalid", now)
    if not config.sandbox_api_enabled:
        return _blocked("api_disabled", now)
    if not config.network_calls_allowed:
        return _blocked("network_not_allowed", now)

    app_key = str(source.get("WEBULL_SANDBOX_APP_KEY", "") or "").strip()
    app_secret = str(source.get("WEBULL_SANDBOX_APP_SECRET", "") or "").strip()
    factory = account_client_factory or _official_account_client
    try:
        account_client = factory(app_key, app_secret)
    except ImportError:
        return _blocked("sdk_unavailable", now)
    except Exception as exc:
        return _blocked(f"client_init_failed:{type(exc).__name__}", now)

    try:
        response = account_client.get_account_list()
    except Exception as exc:
        return WebullPaperProbeResult(
            provider="webull",
            mode="paper",
            endpoint=WEBULL_SANDBOX_TRADING_ENDPOINT,
            status="ERROR",
            reachable=False,
            paper_context_verified=False,
            account_count=None,
            observed_at=_format_utc(now),
            reason=f"request_failed:{type(exc).__name__}",
        )

    try:
        status_code = int(response.status_code)
    except (TypeError, ValueError, AttributeError):
        status_code = 0
    if status_code != 200:
        return WebullPaperProbeResult(
            provider="webull",
            mode="paper",
            endpoint=WEBULL_SANDBOX_TRADING_ENDPOINT,
            status="ERROR",
            reachable=True,
            paper_context_verified=True,
            account_count=None,
            observed_at=_format_utc(now),
            reason=f"http_{status_code or 'unknown'}",
        )

    try:
        payload = response.json()
    except Exception as exc:
        return WebullPaperProbeResult(
            provider="webull",
            mode="paper",
            endpoint=WEBULL_SANDBOX_TRADING_ENDPOINT,
            status="ERROR",
            reachable=True,
            paper_context_verified=True,
            account_count=None,
            observed_at=_format_utc(now),
            reason=f"invalid_json:{type(exc).__name__}",
        )
    account_count = _extract_account_count(payload)
    if account_count is None:
        return WebullPaperProbeResult(
            provider="webull",
            mode="paper",
            endpoint=WEBULL_SANDBOX_TRADING_ENDPOINT,
            status="ERROR",
            reachable=True,
            paper_context_verified=True,
            account_count=None,
            observed_at=_format_utc(now),
            reason="account_schema_unrecognized",
        )

    if account_count == 0:
        return WebullPaperProbeResult(
            provider="webull",
            mode="paper",
            endpoint=WEBULL_SANDBOX_TRADING_ENDPOINT,
            status="WAIT",
            reachable=True,
            paper_context_verified=True,
            account_count=0,
            observed_at=_format_utc(now),
            reason="no_sandbox_accounts",
        )
    return WebullPaperProbeResult(
        provider="webull",
        mode="paper",
        endpoint=WEBULL_SANDBOX_TRADING_ENDPOINT,
        status="OK",
        reachable=True,
        paper_context_verified=True,
        account_count=account_count,
        observed_at=_format_utc(now),
        reason=None,
    )
