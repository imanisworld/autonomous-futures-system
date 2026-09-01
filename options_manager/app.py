"""options_manager FastAPI process — Phase 1 advisory only.

Canonical nested payloads are evaluated through the proof/contract/portfolio
advisory stack, journaled, and optionally sent to Discord. The legacy flat
packet shape remains temporarily accepted for compatibility but is explicitly
non-canonical. No broker calls or order calls exist here.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets as secrets_module

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import OptionsManagerConfig
from .journal import log_advisory_decision
from .live_lock import assert_live_options_trading_disabled
from .notify import notify_advisory_decision
from .packet_builder import build_packet
from .validation.advisory_decision import check_advisory_decision_intake

logger = logging.getLogger(__name__)

assert_live_options_trading_disabled()

app = FastAPI(title="options_manager", version="0.2.0-advisory")

SECRET_HEADER = "X-Options-Manager-Secret"
GENERIC_INVALID_PACKET_DETAIL = "missing or malformed packet field"
_CANONICAL_SECTION_KEYS = frozenset(("proof_packet", "contract_quality", "portfolio_risk"))


def _auth_ok(request: Request, config: OptionsManagerConfig) -> bool:
    """If the dedicated ingest credential is unset, auth is disabled for local/dev use."""
    if not config.ingest_secret:
        return True
    provided = request.headers.get(SECRET_HEADER, "")
    return secrets_module.compare_digest(provided, config.ingest_secret)


def _is_canonical_payload(raw_input: object) -> bool:
    return isinstance(raw_input, dict) and bool(_CANONICAL_SECTION_KEYS & set(raw_input))


def _assert_safe_bind(bind_host: str, ingest_secret: str) -> None:
    """Fail closed if the advisory API is exposed off-box without its secret."""
    host = (bind_host or "").strip()
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A hostname other than localhost may resolve off-box. Require auth.
        if not ingest_secret:
            raise RuntimeError(
                "OPTIONS_MANAGER_INGEST_SECRET is required for non-loopback bind host"
            )
        return
    if not address.is_loopback and not ingest_secret:
        raise RuntimeError(
            "OPTIONS_MANAGER_INGEST_SECRET is required for non-loopback bind host"
        )


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

    if _is_canonical_payload(raw_input):
        result = check_advisory_decision_intake(
            raw_input,
            require_portfolio_risk=True,
            # No default budget lives below this line. Unset here means the
            # portfolio gate blocks and the verdict cannot be TAKE.
            max_aggregate_open_risk_dollars=config.max_aggregate_open_risk_dollars,
        )
        log_advisory_decision(
            request_payload=raw_input,
            result=result,
            journal_dir=config.journal_dir,
        )
        notified = notify_advisory_decision(
            result,
            raw_input.get("proof_packet") if isinstance(raw_input, dict) else None,
            config=config,
        )
        if not notified:
            logger.info("options_manager: advisory Discord notification not sent; verdict unaffected")

        return JSONResponse(
            status_code=200,
            content={
                "verdict": result.verdict.value.upper(),
                "proof_valid": result.proof_valid,
                "contract_verdict": result.contract_verdict.value,
                "portfolio_verdict": result.portfolio_verdict.value,
                "blocking_reasons": list(result.blocking_reasons),
                "warnings": list(result.warnings),
                "no_trade_reasons": [reason.value for reason in result.no_trade_reasons],
                "next_required_action": result.next_required_action,
                "actionable": result.verdict.value == "take",
            },
        )

    # Temporary compatibility lane for the old flat OptionTradePacket shape.
    # It is not the canonical advisory authority and cannot produce TAKE/WAIT.
    try:
        packet = build_packet(raw_input)
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("options_manager: rejected malformed legacy packet input: %r", exc)
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
            "legacy_compatibility": True,
            "actionable": False,
        },
    )


def run() -> None:
    import uvicorn

    config = OptionsManagerConfig.from_env()
    bind_host = os.getenv("OPTIONS_MANAGER_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
    _assert_safe_bind(bind_host, config.ingest_secret)
    uvicorn.run("options_manager.app:app", host=bind_host, port=config.port)


if __name__ == "__main__":
    run()
