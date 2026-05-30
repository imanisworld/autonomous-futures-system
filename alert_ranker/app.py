"""FastAPI app and scheduler lifecycle for the options scanner."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request

from .config import ScannerConfig, load_config
from .discord import DiscordAlerter
from .scanner import OptionsScanner
from .storage import ScanStorage
from .tastytrade_client import TastytradeClient


def create_app(config: ScannerConfig | None = None, scanner: OptionsScanner | None = None) -> FastAPI:
    cfg = config or load_config()
    app_state: dict[str, Any] = {"scheduler": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal scanner
        storage = ScanStorage(cfg.sqlite_path)
        async with TastytradeClient(cfg) as tastytrade, DiscordAlerter(cfg, storage) as discord:
            scanner = scanner or OptionsScanner(cfg, tastytrade, storage, discord)
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
        return {
            "status": "healthy",
            "service": "options-scanner",
            "advisory_only": True,
            "port": cfg.port,
            "database": str(cfg.sqlite_path),
            "scheduler_running": bool(app_state["scheduler"] and app_state["scheduler"].running),
            "tastytrade_configured": cfg.tastytrade_configured,
        }

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return get_scanner().status()

    @app.get("/watchlist")
    async def watchlist() -> dict[str, Any]:
        return {"watchlist": cfg.watchlist}

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
                }
                for outcome in outcomes
            ],
        }

    return app


app = create_app()


def run() -> None:
    cfg = load_config()
    uvicorn.run("alert_ranker.app:app", host="0.0.0.0", port=cfg.port)
