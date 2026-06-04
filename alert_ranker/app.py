"""FastAPI app and scheduler lifecycle for the options scanner."""

from __future__ import annotations

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


app = create_app()


def run() -> None:
    cfg = load_config()
    uvicorn.run("alert_ranker.app:app", host="0.0.0.0", port=cfg.port)
