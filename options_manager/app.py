"""options_manager FastAPI process — Phase 1 only.

Accepts option trade packets, validates them, journals and notifies. No
broker calls, no order calls, no imports from execution/, webhook/, or
risk/risk_engine.py.
"""

from __future__ import annotations

import logging
import secrets as secrets_module

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import OptionsManagerConfig
from .live_lock import assert_live_options_trading_disabled
from .packet_builder import build_packet

logger = logging.getLogger(__name__)

# Checked at import/startup time — mirrors the futures system's fail-closed
# pattern. options_manager must not boot at all if this is set true.
assert_live_options_trading_disabled()

app = FastAPI(title="options_manager", version="0.1.0-phase1")

SECRET_HEADER = "X-Options-Manager-Secret"
GENERIC_INVALID_PACKET_DETAIL = "missing or malformed packet field"


def _auth_ok(request: Request, config: OptionsManagerConfig) -> bool:
    """If OPTIONS_MANAGER_INGEST_SECRET is unset, auth is disabled — local/dev
    use only. This is a distinct credential from the futures webhook's secret
    and must never reuse it."""
    if not config.ingest_secret:
        return True
    provided = request.headers.get(SECRET_HEADER, "")
    return secrets_module.compare_digest(provided, config.ingest_secret)


@app.post("/options/packet")
async def receive_packet(request: Request) -> JSONResponse:
    config = OptionsManagerConfig.from_env()

    if not _auth_ok(request, config):
        return JSONResponse(
            status_code=401,
            content={
                "error": "unauthorized",
                "detail": f"missing or invalid {SECRET_HEADER} header",
            },
        )

    try:
        raw_input = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "detail": "request body must be valid JSON"},
        )

    try:
        packet = build_packet(raw_input)
    except (KeyError, ValueError, TypeError) as exc:
        # Log the real exception server-side only — never return raw exception
        # text (field names, parse errors, internal values) to the caller.
        logger.warning("options_manager: rejected malformed packet input: %r", exc)
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_packet", "detail": GENERIC_INVALID_PACKET_DETAIL},
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": packet.status,
            "rejection_reason": packet.rejection_reason,
            "ticker": packet.ticker,
            "direction": packet.direction,
            "created_at": packet.created_at.isoformat(),
        },
    )


def run() -> None:
    import uvicorn

    config = OptionsManagerConfig.from_env()
    uvicorn.run("options_manager.app:app", host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    run()
