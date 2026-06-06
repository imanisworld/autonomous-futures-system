"""FastAPI app and scheduler lifecycle for the options scanner.

TEST-ONLY. This advisory options scanner is NOT part of the live futures
deployment (the production service runs ``uvicorn webhook.app:app`` and never
imports this module). It is advisory-only — it can place no broker orders — and
its endpoints are unauthenticated, so it must not be exposed in production. The
launch path is gated behind ``OPTIONS_SCANNER_ENABLED=true`` (default off): the
served ``app`` is inert and ``run()`` refuses unless the flag is set in a test
environment.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request

from .config import ScannerConfig, load_config
from .discord import DiscordAlerter
from .market_data import build_provider_capabilities, create_market_data_client
from .scanner import OptionsScanner
from .storage import ScanStorage


SHADOW_OUTCOME_STATUSES = {"OPEN", "WIN", "LOSS", "BREAKEVEN", "CANCELLED", "EXPIRED"}

def create_app(config: ScannerConfig | None = None, scanner: OptionsScanner | None = None) -> FastAPI:
    cfg = config or load_config()
    app_state: dict[str, Any] = {"scheduler": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal scanner
        storage = ScanStorage(cfg.sqlite_path)
        async with create_market_data_client(cfg) as market_data, DiscordAlerter(cfg, storage) as discord:
            scanner = scanner or OptionsScanner(cfg, market_data, storage, discord)
            app.state.scanner = scanner
            scheduler = AsyncIOScheduler(timezone=cfg.timezone)
            scheduler.add_job(
                scanner.scan_watchlist,
                "interval",
                minutes=cfg.interval_minutes,
                kwargs={"source": "scheduled"},
                id="options-watchlist-scan",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            app_state["scheduler"] = scheduler
            try:
                yield
            finally:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="Advisory Options Scanner", lifespan=lifespan)
    if scanner is not None:
        app.state.scanner = scanner

    def get_scanner() -> OptionsScanner:
        return app.state.scanner

    @app.get("/health")
    async def health() -> dict[str, Any]:
        provider_profile = build_provider_capabilities(
            cfg,
            last_error=getattr(getattr(app.state, "scanner", None), "market_data", None).last_error
            if getattr(app.state, "scanner", None) is not None
            else None,
        ).to_dict()
        return {
            "status": "healthy",
            "service": "options-scanner",
            "advisory_only": True,
            "port": cfg.port,
            "database": str(cfg.sqlite_path),
            "scheduler_running": bool(app_state["scheduler"] and app_state["scheduler"].running),
            "market_data_provider": cfg.market_data_provider,
            "market_data_configured": cfg.market_data_configured,
            "provider_profile": provider_profile,
            "tastytrade_configured": cfg.tastytrade_configured,
        }

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return get_scanner().status()

    @app.get("/watchlist")
    async def watchlist() -> dict[str, Any]:
        return {"watchlist": cfg.watchlist}

    @app.get("/terminal")
    async def terminal() -> dict[str, Any]:
        return get_scanner().terminal_state()

    @app.get("/shadow-journal")
    async def shadow_journal(
        limit: int = 25,
        ticker: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 100))
        normalized_status = status.upper() if status else None
        if normalized_status and normalized_status not in SHADOW_OUTCOME_STATUSES:
            raise HTTPException(status_code=400, detail="unsupported_shadow_status")
        return {
            "advisory_only": True,
            "items": [
                item.__dict__
                for item in get_scanner().storage.latest_shadow_setups(
                    limit=bounded_limit,
                    ticker=ticker,
                    status=normalized_status,
                )
            ],
        }

    @app.get("/shadow-journal/summary")
    async def shadow_journal_summary(ticker: str | None = None) -> dict[str, Any]:
        return {
            "advisory_only": True,
            "ticker": ticker.upper() if ticker else None,
            "summary": get_scanner().storage.shadow_summary(ticker=ticker).__dict__,
        }

    @app.patch("/shadow-journal/{shadow_id}/outcome")
    async def update_shadow_outcome(shadow_id: int, request: Request) -> dict[str, Any]:
        payload = await request.json()
        status = str(payload.get("status") or "OPEN").upper()
        if status not in SHADOW_OUTCOME_STATUSES:
            raise HTTPException(status_code=400, detail="unsupported_shadow_status")
        outcome = payload.get("outcome")
        if outcome is None:
            outcome = {key: value for key, value in payload.items() if key != "status"}
        if not isinstance(outcome, dict):
            raise HTTPException(status_code=400, detail="shadow_outcome_must_be_object")
        updated = get_scanner().storage.update_shadow_outcome(
            shadow_id,
            status=status,
            outcome=outcome,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="shadow_setup_not_found")
        return {
            "updated": True,
            "advisory_only": True,
            "item": updated.__dict__,
        }

    @app.post("/webhook/alert")
    async def webhook_alert(request: Request) -> dict[str, Any]:
        payload = await request.json()
        ticker = str(payload.get("ticker") or payload.get("symbol") or "").upper()
        scanner_obj = get_scanner()
        if ticker:
            outcomes = [
                await scanner_obj.scan_ticker(ticker, source="webhook", context=payload)
            ]
        else:
            outcomes = await scanner_obj.scan_watchlist(source="webhook", context=payload)
        return {
            "accepted": True,
            "advisory_only": True,
            "results": [
                {
                    "ticker": outcome.result.ticker,
                    "direction": outcome.result.direction,
                    "score": outcome.result.score,
                    "pattern": outcome.result.pattern,
                    "alert_sent": outcome.alert_sent,
                    "alert_suppression_reason": outcome.alert_suppression_reason,
                    "storage_id": outcome.storage_id,
                    "shadow_id": outcome.shadow_id,
                }
                for outcome in outcomes
            ],
        }

    return app


def options_scanner_enabled() -> bool:
    """The scanner only runs when explicitly opted in (test environments)."""
    return os.getenv("OPTIONS_SCANNER_ENABLED", "false").strip().lower() in {"true", "1", "yes"}


def _disabled_app() -> FastAPI:
    """Inert stand-in served when the scanner is not enabled, so that
    ``uvicorn alert_ranker.app:app`` in production is safe and 403s everything
    instead of starting the advisory scanner + scheduler."""
    disabled = FastAPI(title="Advisory Options Scanner (disabled)")

    @disabled.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    async def _blocked(path: str) -> Any:
        raise HTTPException(
            status_code=403,
            detail="Options scanner is disabled. It is a test-only advisory module, "
            "not part of the live deployment. Set OPTIONS_SCANNER_ENABLED=true to run it.",
        )

    return disabled


# Served by ``uvicorn alert_ranker.app:app`` — inert unless explicitly enabled.
app = create_app() if options_scanner_enabled() else _disabled_app()


def run() -> None:
    if not options_scanner_enabled():
        raise SystemExit(
            "Options scanner is disabled. It is a TEST-ONLY advisory module and is not "
            "part of the live futures deployment. Set OPTIONS_SCANNER_ENABLED=true in a "
            "test environment to run it."
        )
    cfg = load_config()
    uvicorn.run("alert_ranker.app:app", host="0.0.0.0", port=cfg.port)
