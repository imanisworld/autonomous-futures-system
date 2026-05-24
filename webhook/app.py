"""
webhook/app.py

FastAPI application — receives TradingView bar-close alerts and routes them
through the paper-trading pipeline.

Endpoints:
    POST /webhook/alert   — main inbound alert handler
    GET  /health          — liveness check
    GET  /status/today    — today's daily state (trade count, losses, open pos)

Start locally:
    python -m webhook              (port 8000)
    uvicorn webhook.app:app --reload --port 8000

Expose to TradingView via ngrok:
    ngrok http 8000
    → use https://<id>.ngrok-free.app/webhook/alert as the alert URL
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from config.settings import load_config
from journal.journal_logger import JournalLogger
from webhook.payload import AlertPayload
from webhook.runner import process_alert

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Paper Trading Webhook",
    description="TradingView → paper engine → JSONL journal. No live trading.",
    version="1.0.0",
)

# Load config once at startup; fail loudly if LIVE_TRADING_ENABLED=true.
_config = load_config()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/webhook/alert")
async def receive_alert(
    payload: AlertPayload,
    secret: str | None = Query(default=None),
) -> JSONResponse:
    """
    Accept a bar-close alert from TradingView, run it through the
    paper-trading pipeline, and return a structured decision result.
    """
    _verify_webhook_secret(secret)
    try:
        result = process_alert(payload, config=_config)
        return JSONResponse(content={"ok": True, **result})
    except Exception as exc:
        logger.exception("Error processing alert: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
async def health() -> dict:
    """Liveness check. Always returns 200 if the server is running."""
    return {
        "ok": True,
        "live_trading_enabled": _config.live_trading_enabled,
        "paper_mode": _config.paper_mode,
        "webhook_secret_required": bool(_configured_webhook_secret()),
    }


@app.get("/status/today")
async def status_today() -> dict:
    """Return today's reconstructed daily state from the journal."""
    journal = JournalLogger(log_dir=_config.log_dir)
    daily_state = journal.get_daily_state(date.today())
    return {
        "date": daily_state.date,
        "trade_count": daily_state.trade_count,
        "consecutive_losses": daily_state.consecutive_losses,
        "has_open_position": daily_state.has_open_position,
    }


# ─── Error handlers ───────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": str(exc)},
    )


def _configured_webhook_secret() -> str:
    return os.getenv("WEBHOOK_SECRET", "").strip()


def _verify_webhook_secret(provided: str | None) -> None:
    expected = _configured_webhook_secret()
    if not expected:
        return
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
