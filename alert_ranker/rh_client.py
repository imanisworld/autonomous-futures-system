"""Lightweight Robinhood API client — advisory mark-fetching only.

Never places, cancels, or modifies orders. Used solely to get current option
mark prices for open shadow positions so check_open_positions can fire Discord
alerts without manual marks being passed in.

Token lifecycle:
  1. Caller provides RH_BEARER_TOKEN (access token) from env.
  2. On 401, the client tries RH_REFRESH_TOKEN once to get a new bearer.
  3. If refresh also fails, the request returns None (fail-soft — advisory).
  4. The refreshed bearer is stored in-memory for the process lifetime; it is
     NOT written back to .env (user handles token rotation externally).
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from .storage import StoredShadowSetup

_RH_BASE = "https://api.robinhood.com"
# Robinhood's public OAuth2 client_id (same for all unofficial integrations)
_RH_CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"


class RHClient:
    """Advisory-only Robinhood client: fetches option marks, refreshes tokens."""

    def __init__(self, bearer_token: str, refresh_token: str = ""):
        self._bearer = bearer_token.strip()
        self._refresh_token = refresh_token.strip()
        self._lock = threading.Lock()
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self._bearer)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bearer}"}

    def _try_refresh(self) -> bool:
        """Exchange refresh_token for a new bearer. Returns True on success."""
        if not self._refresh_token:
            return False
        try:
            resp = httpx.post(
                f"{_RH_BASE}/oauth2/token/",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": _RH_CLIENT_ID,
                    "expires_in": 86400,
                    "scope": "internal",
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                payload = resp.json()
                self._bearer = payload["access_token"]
                if payload.get("refresh_token"):
                    self._refresh_token = payload["refresh_token"]
                self.last_error = None
                return True
            self.last_error = f"refresh_failed:{resp.status_code}"
        except Exception as exc:
            self.last_error = f"refresh_error:{exc}"
        return False

    def _get(self, url: str, params: dict | None = None, *, _retried: bool = False) -> dict[str, Any] | None:
        """GET with one automatic token refresh on 401."""
        try:
            resp = httpx.get(url, headers=self._auth_headers(), params=params, timeout=10.0)
            if resp.status_code == 401 and not _retried:
                with self._lock:
                    if self._try_refresh():
                        return self._get(url, params, _retried=True)
                self.last_error = "bearer_expired_refresh_failed"
                return None
            if resp.status_code == 200:
                return resp.json()
            self.last_error = f"http_{resp.status_code}"
        except Exception as exc:
            self.last_error = f"request_error:{exc}"
        return None

    def get_option_instrument_url(
        self, ticker: str, strike: float, expiry: str, contract_type: str
    ) -> str | None:
        """Look up the Robinhood instrument URL for a specific contract."""
        data = self._get(
            f"{_RH_BASE}/options/instruments/",
            params={
                "chain_symbol": ticker.upper(),
                "expiration_dates": expiry,
                "strike_price": f"{strike:.4f}",
                "type": contract_type.lower(),
                "state": "active",
            },
        )
        results = (data or {}).get("results", [])
        return results[0].get("url") if results else None

    def get_option_mark(self, instrument_url: str) -> float | None:
        """Fetch current mark price for a single option contract."""
        data = self._get(
            f"{_RH_BASE}/marketdata/options/",
            params={"instruments[]": instrument_url},
        )
        results = (data or {}).get("results", [])
        if not results:
            return None
        try:
            return float(results[0]["mark_price"])
        except (KeyError, TypeError, ValueError):
            return None

    def fetch_marks_for_positions(
        self, positions: list[StoredShadowSetup]
    ) -> dict[str, float]:
        """Return {str(shadow_id): mark_price} for every position with contract data.

        Skips positions missing ticker / strike / expiry / type — logs nothing,
        returns partial results (fail-soft).
        """
        marks: dict[str, float] = {}
        for pos in positions:
            sc = pos.selected_contract or {}
            ticker = pos.ticker
            strike_raw = sc.get("strike")
            expiry = sc.get("expiry") or sc.get("expiration")
            contract_type = sc.get("option_type") or sc.get("contract_type")

            if not all([ticker, strike_raw, expiry, contract_type]):
                continue
            try:
                strike = float(strike_raw)
            except (TypeError, ValueError):
                continue

            # Use stored instrument URL if available (set by rank-and-evaluate),
            # otherwise do a live lookup (two API calls instead of one).
            url = sc.get("rh_instrument_url") or self.get_option_instrument_url(
                ticker, strike, expiry, contract_type
            )
            if not url:
                continue

            mark = self.get_option_mark(url)
            if mark is not None:
                marks[str(pos.id)] = mark

        return marks

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "refresh_token_set": bool(self._refresh_token),
            "last_error": self.last_error,
        }
