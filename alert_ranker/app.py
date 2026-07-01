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

import asyncio
import os
import json
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import ScannerConfig, load_config
from .discord import DiscordAlerter
from .market_data import build_provider_capabilities, create_market_data_client
from .rh_client import RHClient
from .rh_options import (
    _parse_rh_inputs,
    auto_check_positions,
    check_open_positions,
    evaluate_messy_rh_options_text,
    evaluate_rh_options,
    kill_switch,
    manage_rh_options_position,
    morning_check,
    rank_option_contracts,
    sample_rh_options_payload,
    sample_rh_options_text,
)
from .scanner import OptionsScanner
from .storage import ScanStorage


SHADOW_OUTCOME_STATUSES = {"OPEN", "WIN", "LOSS", "BREAKEVEN", "CANCELLED", "EXPIRED"}

def create_app(config: ScannerConfig | None = None, scanner: OptionsScanner | None = None) -> FastAPI:
    cfg = config or load_config()
    app_state: dict[str, Any] = {"scheduler": None}
    rh_client = RHClient(cfg.rh_bearer_token, cfg.rh_refresh_token) if cfg.rh_configured else None

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
            if rh_client and rh_client.configured:
                def _auto_check_job():
                    auto_check_positions(storage, cfg.discord_webhook_url, rh_client)

                scheduler.add_job(
                    _auto_check_job,
                    "interval",
                    minutes=cfg.rh_auto_check_interval_minutes,
                    id="rh-auto-check-positions",
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

    @app.get("/shadow-journal/{shadow_id}")
    async def get_shadow_setup(shadow_id: int) -> dict[str, Any]:
        entry = get_scanner().storage.get_shadow_setup(shadow_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="shadow_setup_not_found")
        return {"advisory_only": True, "item": entry.__dict__}

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

    @app.post("/rh-options/evaluate")
    async def rh_options_evaluate(request: Request) -> dict[str, Any]:
        body = await request.json()
        try:
            inputs = _parse_rh_inputs(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return evaluate_rh_options(inputs, storage=get_scanner().storage)

    @app.post("/rh-options/evaluate-text")
    async def rh_options_evaluate_text(request: Request) -> dict[str, Any]:
        body = await request.json()
        text = body.get("text") if isinstance(body, dict) else None
        try:
            return evaluate_messy_rh_options_text(str(text or ""), storage=get_scanner().storage)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/rh-options/evaluate-auto")
    async def rh_options_evaluate_auto(request: Request) -> dict[str, Any]:
        import asyncio
        from sources.signa_client import SignaClient
        body = await request.json()
        ticker = str(body.get("ticker") or "").strip().upper()
        if not ticker:
            raise HTTPException(status_code=422, detail="ticker is required")

        signa_enrichment: dict[str, Any] = {}
        api_key = os.getenv("SIGNA_API_KEY", "").strip()
        if api_key:
            scanner = get_scanner()
            signa_symbol = scanner._signa_symbol_for(ticker)
            client = SignaClient(
                api_key=api_key,
                base_url=scanner.config.signa_base_url,
                timeout=scanner.config.signa_timeout_seconds,
            )
            signal = await asyncio.to_thread(client.fetch_signal, signa_symbol)
            if signal.ok:
                signa_enrichment = {
                    "signa_grade": signal.grade,
                    "signa_score": signal.score,
                    "signa_daily_direction": signal.daily_direction,
                    "signa_weekly_direction": signal.weekly_direction,
                    "current_price": signal.price,
                    "flow_score": signal.flow_score,
                    "regime_class": signal.regime_class,
                    "trend_strength": signal.trend_strength,
                    "_pivot_s1": signal.pivot_s1,
                    "_pivot_r1": signal.pivot_r1,
                }
            else:
                signa_enrichment = {"signa_error": signal.error}

        # Merge: Signa fills defaults; body values override
        merged: dict[str, Any] = {}
        if signa_enrichment.get("signa_grade"):
            merged["signa_grade"] = signa_enrichment["signa_grade"]
        if signa_enrichment.get("signa_score") is not None:
            merged["signa_score"] = signa_enrichment["signa_score"]
        if signa_enrichment.get("signa_daily_direction"):
            merged["signa_daily_direction"] = signa_enrichment["signa_daily_direction"]
        if signa_enrichment.get("signa_weekly_direction"):
            merged["signa_weekly_direction"] = signa_enrichment["signa_weekly_direction"]
        if signa_enrichment.get("current_price") is not None:
            merged["current_price"] = signa_enrichment["current_price"]
        # Pivot points as GEX wall fallback only if user didn't supply walls
        if not body.get("gex_support_wall") and signa_enrichment.get("_pivot_s1"):
            merged["gex_support_wall"] = signa_enrichment["_pivot_s1"]
        if not body.get("gex_resistance_wall") and signa_enrichment.get("_pivot_r1"):
            merged["gex_resistance_wall"] = signa_enrichment["_pivot_r1"]
        # User-supplied values always win
        merged.update({k: v for k, v in body.items() if v is not None})

        try:
            inputs = _parse_rh_inputs(merged)
        except ValueError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "signa_enriched": {k: v for k, v in signa_enrichment.items() if not k.startswith("_")},
                "merged_inputs": merged,
            }

        result = evaluate_rh_options(inputs, storage=get_scanner().storage)
        result["signa_enriched"] = {k: v for k, v in signa_enrichment.items() if not k.startswith("_")}
        return result

    @app.post("/rh-options/rank-and-evaluate")
    async def rh_options_rank_and_evaluate(request: Request) -> dict[str, Any]:
        import asyncio
        from sources.signa_client import SignaClient
        body = await request.json()
        ticker = str(body.get("ticker") or "").strip().upper()
        if not ticker:
            raise HTTPException(status_code=422, detail="ticker is required")
        candidates = list(body.get("candidates") or [])
        if not candidates:
            raise HTTPException(status_code=422, detail="candidates list is required")

        top_n = max(1, min(int(body.get("top_n") or 3), 10))
        direction = str(body.get("direction") or "LONG")
        gex_regime = str(body.get("gex_regime") or "LOW_PINNING").upper().strip()
        gex_support_wall = _optional_request_float(body.get("gex_support_wall"))
        gex_resistance_wall = _optional_request_float(body.get("gex_resistance_wall"))
        nine_ma = _optional_request_float(body.get("nine_ma"))
        max_premium = float(body.get("max_premium_per_contract") or 250.0)

        # Auto-fetch Signa
        signa_enrichment: dict[str, Any] = {}
        api_key = os.getenv("SIGNA_API_KEY", "").strip()
        if api_key:
            scanner = get_scanner()
            signa_symbol = scanner._signa_symbol_for(ticker)
            client = SignaClient(
                api_key=api_key,
                base_url=scanner.config.signa_base_url,
                timeout=scanner.config.signa_timeout_seconds,
            )
            signal = await asyncio.to_thread(client.fetch_signal, signa_symbol)
            if signal.ok:
                signa_enrichment = {
                    "signa_grade": signal.grade,
                    "signa_score": signal.score,
                    "signa_daily_direction": signal.daily_direction,
                    "signa_weekly_direction": signal.weekly_direction,
                    "current_price": signal.price,
                }
            else:
                signa_enrichment = {"signa_error": signal.error}

        current_price = signa_enrichment.get("current_price") or _optional_request_float(body.get("current_price"))
        if not current_price:
            raise HTTPException(status_code=422, detail="current_price required (Signa did not return price)")

        ranked = rank_option_contracts(
            candidates,
            direction=direction,
            current_price=current_price,
            gex_resistance_wall=gex_resistance_wall,
            gex_support_wall=gex_support_wall,
            max_premium_per_contract=max_premium,
        )

        evaluated = []
        for contract in ranked[:top_n]:
            merged = {
                "ticker": ticker,
                "direction": direction,
                "contract_type": "CALL" if "LONG" in direction.upper() else "PUT",
                "gex_regime": gex_regime,
                "gex_support_wall": gex_support_wall,
                "gex_resistance_wall": gex_resistance_wall,
                "current_price": current_price,
                "nine_ma": nine_ma,
                **signa_enrichment,
                **contract,
            }
            try:
                inputs = _parse_rh_inputs(merged)
                ev = evaluate_rh_options(inputs, storage=get_scanner().storage)
                ev["contract"] = {k: contract[k] for k in ("strike", "expiry_date", "dte", "premium", "option_volume", "open_interest", "rr", "rank", "dollar_gain", "dollar_risk") if k in contract}
                evaluated.append(ev)
            except (ValueError, KeyError) as exc:
                evaluated.append({"error": str(exc), "contract": contract})

        return {
            "advisory_only": True,
            "ticker": ticker,
            "direction": direction,
            "gex_resistance_wall": gex_resistance_wall,
            "gex_support_wall": gex_support_wall,
            "candidates_received": len(candidates),
            "contracts_ranked": len(ranked),
            "evaluated": evaluated,
            "signa_enriched": {k: v for k, v in signa_enrichment.items() if not k.startswith("_")},
            "all_ranked": ranked,
        }

    @app.get("/rh-options/sample")
    async def rh_options_sample() -> dict[str, Any]:
        return sample_rh_options_payload()

    @app.get("/rh-options/sample-text")
    async def rh_options_sample_text() -> dict[str, str]:
        return {"text": sample_rh_options_text()}

    @app.get("/rh-options/recent")
    async def rh_options_recent(limit: int = 10) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 100))
        return {
            "advisory_only": True,
            "items": [
                item.__dict__
                for item in get_scanner().storage.latest_rh_option_setups(limit=bounded_limit)
            ],
        }

    @app.post("/rh-options/morning-check")
    async def rh_options_morning_check() -> dict[str, Any]:
        return morning_check(get_scanner().storage, cfg.discord_webhook_url)

    @app.post("/rh-options/kill-switch")
    async def rh_options_kill_switch() -> dict[str, Any]:
        return kill_switch(get_scanner().storage, cfg.discord_webhook_url)

    @app.post("/rh-options/check-positions")
    async def rh_options_check_positions(request: Request) -> dict[str, Any]:
        body = await request.json()
        raw_marks = body.get("marks") or {}
        marks: dict[str, float] = {}
        for k, v in raw_marks.items():
            try:
                marks[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        return check_open_positions(get_scanner().storage, cfg.discord_webhook_url, marks or None)

    @app.post("/rh-options/check-positions-auto")
    async def rh_options_check_positions_auto() -> dict[str, Any]:
        if not rh_client or not rh_client.configured:
            raise HTTPException(status_code=503, detail="RH_BEARER_TOKEN not configured")
        return await asyncio.to_thread(
            auto_check_positions, get_scanner().storage, cfg.discord_webhook_url, rh_client
        )

    @app.post("/rh-options/manage")
    async def rh_options_manage(request: Request) -> dict[str, Any]:
        body = await request.json()
        try:
            shadow_id = int(body.get("shadow_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="shadow_id is required") from exc
        setup = get_scanner().storage.get_shadow_setup(shadow_id)
        if setup is None:
            raise HTTPException(status_code=404, detail="shadow_setup_not_found")
        return manage_rh_options_position(
            setup,
            current_price=_optional_request_float(body.get("current_price")),
            current_premium=_optional_request_float(body.get("current_premium")),
        )

    @app.get("/rh-options", response_class=HTMLResponse)
    async def rh_options_terminal() -> HTMLResponse:
        return HTMLResponse(_render_rh_options_terminal(sample_rh_options_payload(), sample_rh_options_text()))

    return app


def _optional_request_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _render_rh_options_terminal(sample_payload: dict[str, Any], sample_text: str) -> str:
    sample_json = json.dumps(sample_payload, indent=2, sort_keys=True).replace("<", "\\u003c")
    sample_note_json = json.dumps(sample_text).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RH Options Scout</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #101314; color: #eef4ef; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 40px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: end; margin-bottom: 20px; }}
    h1 {{ margin: 0; font-size: 26px; letter-spacing: 0; }}
    .sub {{ margin: 6px 0 0; color: #9da9a3; font-size: 13px; }}
    .pill {{ border: 1px solid #33413a; color: #9ed8b1; background: #17211b; border-radius: 999px; padding: 7px 10px; font-size: 12px; white-space: nowrap; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, 0.85fr); gap: 16px; align-items: stretch; }}
    .panel {{ border: 1px solid #27332e; background: #171b1c; border-radius: 8px; padding: 14px; min-width: 0; }}
    .panel h2 {{ margin: 0 0 12px; font-size: 14px; color: #cdd8d1; }}
    textarea {{ width: 100%; min-height: 560px; resize: vertical; border: 1px solid #30403a; border-radius: 6px; padding: 12px; background: #0d1011; color: #e9f0ec; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    button {{ border: 0; border-radius: 6px; padding: 10px 13px; color: #07100b; background: #8ee6a8; font-weight: 700; cursor: pointer; }}
    button.secondary {{ color: #dbe8df; background: #26322d; }}
    button.ghost {{ color: #cbd7d1; background: transparent; border: 1px solid #33413a; }}
    .actions {{ display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; }}
    .status {{ display: inline-flex; align-items: center; min-height: 34px; padding: 7px 10px; border-radius: 6px; background: #20282a; color: #cbd7d1; font-size: 13px; }}
    .status.trade {{ background: #12351f; color: #9ef0b6; }}
    .status.watch {{ background: #3a3114; color: #f4d37b; }}
    .status.no_trade {{ background: #3a1919; color: #ffaaa3; }}
    pre {{ overflow: auto; margin: 0; white-space: pre-wrap; word-break: break-word; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #dce6e0; }}
    dl {{ display: grid; grid-template-columns: 132px 1fr; gap: 8px 10px; margin: 0 0 14px; }}
    dt {{ color: #7f8e86; font-size: 12px; }}
    dd {{ margin: 0; color: #edf5ef; font-size: 13px; }}
    .recent {{ margin-top: 16px; }}
    .recent-list {{ display: grid; gap: 8px; }}
    .recent-item {{ display: grid; grid-template-columns: 76px 1fr auto; gap: 10px; align-items: center; padding: 10px; border: 1px solid #26332e; border-radius: 6px; background: #121617; cursor: pointer; transition: background 0.15s; }}
    .recent-item:hover {{ background: #1a1f21; }}
    .recent-main {{ min-width: 0; }}
    .recent-title {{ color: #eef4ef; font-size: 13px; font-weight: 700; }}
    .recent-meta {{ color: #829189; font-size: 11px; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .mini {{ display: inline-flex; justify-content: center; border-radius: 999px; padding: 5px 8px; font-size: 11px; font-weight: 700; background: #222b2d; color: #cdd8d1; }}
    .mini.open {{ background: #19321f; color: #9ef0b6; }}
    .empty {{ color: #83918a; font-size: 13px; padding: 10px; border: 1px dashed #30403a; border-radius: 6px; }}
    .manage-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) auto; gap: 10px; align-items: end; }}
    .outcome-grid {{ display: grid; grid-template-columns: 80px 160px 110px 1fr auto; gap: 10px; align-items: end; }}
    label {{ display: grid; gap: 5px; color: #829189; font-size: 12px; }}
    input {{ width: 100%; border: 1px solid #30403a; border-radius: 6px; padding: 9px 10px; background: #0d1011; color: #e9f0ec; font: 13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    select {{ width: 100%; border: 1px solid #30403a; border-radius: 6px; padding: 9px 10px; background: #0d1011; color: #e9f0ec; font: 13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; appearance: none; }}
    .mini.win {{ background: #12351f; color: #9ef0b6; }}
    .mini.loss {{ background: #3a1919; color: #ffaaa3; }}
    .mini.breakeven {{ background: #1a2a3a; color: #8fccf0; }}
    .mini.cancelled, .mini.expired {{ background: #222b2d; color: #9da9a3; }}
    .stats-strip {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
    .stat-card {{ border: 1px solid #27332e; background: #171b1c; border-radius: 8px; padding: 12px 16px; min-width: 110px; }}
    .stat-label {{ color: #7f8e86; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }}
    .stat-value {{ color: #eef4ef; font-size: 22px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }}
    .stat-value.positive {{ color: #9ef0b6; }}
    .stat-value.negative {{ color: #ffaaa3; }}
    @media (max-width: 860px) {{
      header {{ align-items: start; flex-direction: column; }}
      .grid {{ grid-template-columns: 1fr; }}
      textarea {{ min-height: 420px; }}
      .recent-item {{ grid-template-columns: 1fr; }}
      .manage-grid {{ grid-template-columns: 1fr; }}
      .outcome-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>RH Options Scout</h1>
        <p class="sub">Paste structured Signa, GEX, and contract context. This is advisory-only and never submits Robinhood orders.</p>
      </div>
      <span class="pill">ADVISORY ONLY</span>
    </header>
    <div id="stats-strip" class="stats-strip" style="display:none">
      <div class="stat-card"><div class="stat-label">Open</div><div class="stat-value" id="stat-open">—</div></div>
      <div class="stat-card"><div class="stat-label">Closed</div><div class="stat-value" id="stat-closed">—</div></div>
      <div class="stat-card"><div class="stat-label">Win Rate</div><div class="stat-value" id="stat-winrate">—</div></div>
      <div class="stat-card"><div class="stat-label">W / L</div><div class="stat-value" id="stat-wl">—</div></div>
      <div class="stat-card"><div class="stat-label">Total P&amp;L</div><div class="stat-value" id="stat-pnl">—</div></div>
    </div>
    <section class="grid">
      <div class="panel">
        <h2>Setup Notes or JSON</h2>
        <textarea id="payload" spellcheck="false"></textarea>
        <div class="actions">
          <button id="evaluate">Evaluate</button>
          <button class="secondary" id="notes-sample">Load Notes</button>
          <button class="ghost" id="json-sample">Load JSON</button>
          <span id="status" class="status">Ready</span>
        </div>
      </div>
      <div class="panel">
        <h2>Decision</h2>
        <dl>
          <dt>Decision</dt><dd id="decision">-</dd>
          <dt>Score</dt><dd id="score">-</dd>
          <dt>Shadow ID</dt><dd id="shadow">-</dd>
          <dt>Broker</dt><dd id="broker">-</dd>
        </dl>
        <h2>Result JSON</h2>
        <pre id="result">Click Evaluate to run the scout.</pre>
      </div>
    </section>
    <section class="panel recent">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px;">
        <h2 style="margin:0;">Recent RH Evaluations</h2>
        <button class="ghost" id="refresh-recent">Refresh</button>
      </div>
      <div id="recent-list" class="recent-list"><div class="empty">No RH evaluations yet.</div></div>
    </section>
    <section class="panel recent">
      <h2>Manage Open Idea</h2>
      <div class="manage-grid">
        <label>Shadow ID<input id="manage-shadow" placeholder="e.g. 12"></label>
        <label>Current Price<input id="manage-price" placeholder="e.g. 501.25"></label>
        <label>Current Premium<input id="manage-premium" placeholder="e.g. 3.80"></label>
        <button id="manage">Manage</button>
      </div>
      <pre id="manage-result" style="margin-top:12px;">Pick a recent shadow id and enter current price or premium.</pre>
    </section>
    <section class="panel recent">
      <h2>Update Outcome</h2>
      <div class="outcome-grid">
        <label>Shadow ID<input id="outcome-shadow" placeholder="e.g. 12"></label>
        <label>Status<select id="outcome-status">
          <option value="">— pick —</option>
          <option value="WIN">WIN</option>
          <option value="LOSS">LOSS</option>
          <option value="BREAKEVEN">BREAKEVEN</option>
          <option value="CANCELLED">CANCELLED</option>
          <option value="EXPIRED">EXPIRED</option>
        </select></label>
        <label>Exit Premium<input id="outcome-exit" type="number" step="0.01" min="0" placeholder="e.g. 4.50"></label>
        <label>Notes<input id="outcome-reason" placeholder="target hit, stopped out…"></label>
        <button id="update-outcome">Save</button>
      </div>
      <pre id="outcome-result" style="margin-top:12px;">Click a recent evaluation or enter a shadow ID, then pick a status to close the idea.</pre>
    </section>
  </main>
  <script>
    const sample = {sample_json};
    const sampleText = {sample_note_json};
    const payload = document.getElementById('payload');
    const result = document.getElementById('result');
    const status = document.getElementById('status');
    const decision = document.getElementById('decision');
    const score = document.getElementById('score');
    const shadow = document.getElementById('shadow');
    const broker = document.getElementById('broker');
    const recentList = document.getElementById('recent-list');
    const manageShadow = document.getElementById('manage-shadow');
    const manageResult = document.getElementById('manage-result');
    const outcomeShadow = document.getElementById('outcome-shadow');
    const outcomeStatus = document.getElementById('outcome-status');
    const outcomeExit = document.getElementById('outcome-exit');
    const outcomeReason = document.getElementById('outcome-reason');
    const outcomeResult = document.getElementById('outcome-result');

    function setStatus(text, cls) {{
      status.className = 'status' + (cls ? ' ' + cls : '');
      status.textContent = text;
    }}

    function renderResult(data, ok) {{
      result.textContent = JSON.stringify(data, null, 2);
      decision.textContent = data.decision || '-';
      score.textContent = data.score ?? '-';
      shadow.textContent = data.shadow_id ?? '-';
      broker.textContent = data.broker_preview?.status || '-';
      const cls = data.decision === 'TRADE' ? 'trade' : (data.decision === 'WATCH' ? 'watch' : 'no_trade');
      setStatus(ok ? (data.decision || 'Done') : 'Error', ok ? cls : 'no_trade');
      loadRecent();
    }}

    function esc(value) {{
      return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}

    function renderRecent(items) {{
      if (!items || !items.length) {{
        recentList.innerHTML = '<div class="empty">No RH evaluations yet.</div>';
        return;
      }}
      recentList.innerHTML = items.map(item => {{
        const ticket = item.selected_contract || {{}};
        const strike = ticket.strike ? ' ' + ticket.strike + ' ' + (ticket.contract_type || '') : '';
        const premium = ticket.limit_debit ? ' @ ' + ticket.limit_debit : '';
        const expiry = ticket.expiry ? ' exp ' + ticket.expiry : '';
        const st = String(item.status || 'OPEN').toLowerCase();
        return '<div class="recent-item" data-id="' + esc(item.id) + '">' +
          '<span class="mini ' + esc(st) + '">' + esc(item.status || 'OPEN') + '</span>' +
          '<div class="recent-main"><div class="recent-title">#' + esc(item.id) + ' ' + esc(item.ticker) + strike + premium + '</div>' +
          '<div class="recent-meta">' + esc(item.direction) + ' · score ' + esc(item.score) + expiry + '</div></div>' +
          '<span class="mini">id ' + esc(item.id) + '</span>' +
        '</div>';
      }}).join('');
      recentList.querySelectorAll('.recent-item').forEach(function(el) {{
        el.addEventListener('click', function() {{
          const id = el.getAttribute('data-id');
          manageShadow.value = id;
          outcomeShadow.value = id;
        }});
      }});
    }}

    async function loadSummary() {{
      try {{
        const response = await fetch('/shadow-journal/summary');
        const data = await response.json();
        const s = data.summary || {{}};
        if (!s.total) return;
        document.getElementById('stats-strip').style.display = '';
        document.getElementById('stat-open').textContent = s.open ?? '—';
        document.getElementById('stat-closed').textContent = s.closed ?? '—';
        const wr = s.win_rate_percent;
        document.getElementById('stat-winrate').textContent = wr != null ? wr.toFixed(0) + '%' : '—';
        document.getElementById('stat-wl').textContent = (s.wins ?? 0) + ' / ' + (s.losses ?? 0);
        const pnl = s.total_pnl_dollars ?? 0;
        const pnlEl = document.getElementById('stat-pnl');
        pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
        pnlEl.className = 'stat-value' + (pnl > 0 ? ' positive' : pnl < 0 ? ' negative' : '');
      }} catch (_) {{}}
    }}

    async function loadRecent() {{
      try {{
        const response = await fetch('/rh-options/recent?limit=8');
        const data = await response.json();
        renderRecent(data.items || []);
      }} catch (err) {{
        recentList.innerHTML = '<div class="empty">Recent evaluations unavailable.</div>';
      }}
      loadSummary();
    }}

    document.getElementById('notes-sample').addEventListener('click', () => {{
      payload.value = sampleText;
      setStatus('Notes sample loaded', '');
    }});

    document.getElementById('json-sample').addEventListener('click', () => {{
      payload.value = JSON.stringify(sample, null, 2);
      setStatus('JSON sample loaded', '');
    }});

    document.getElementById('evaluate').addEventListener('click', async () => {{
      let body;
      let url = '/rh-options/evaluate-text';
      let raw = payload.value.trim();
      if (!raw) {{
        setStatus('Paste setup first', 'no_trade');
        return;
      }}
      try {{
        if (raw.startsWith('{{')) {{
          body = JSON.parse(raw);
          url = '/rh-options/evaluate';
        }} else {{
          body = {{ text: raw }};
        }}
      }} catch (err) {{
        setStatus('Invalid JSON', 'no_trade');
        result.textContent = String(err);
        return;
      }}
      setStatus('Evaluating...', '');
      try {{
        const response = await fetch(url, {{
          method: 'POST',
          headers: {{ 'content-type': 'application/json' }},
          body: JSON.stringify(body)
        }});
        const data = await response.json();
        renderResult(data, response.ok);
      }} catch (err) {{
        setStatus('Request failed', 'no_trade');
        result.textContent = String(err);
      }}
    }});
    document.getElementById('refresh-recent').addEventListener('click', loadRecent);
    document.getElementById('manage').addEventListener('click', async () => {{
      const body = {{
        shadow_id: manageShadow.value,
        current_price: document.getElementById('manage-price').value,
        current_premium: document.getElementById('manage-premium').value
      }};
      try {{
        const response = await fetch('/rh-options/manage', {{
          method: 'POST',
          headers: {{ 'content-type': 'application/json' }},
          body: JSON.stringify(body)
        }});
        const data = await response.json();
        manageResult.textContent = JSON.stringify(data, null, 2);
      }} catch (err) {{
        manageResult.textContent = String(err);
      }}
    }});
    document.getElementById('update-outcome').addEventListener('click', async function() {{
      const id = outcomeShadow.value.trim();
      const st = outcomeStatus.value;
      if (!id || !st) {{
        outcomeResult.textContent = 'Shadow ID and status are required.';
        return;
      }}
      const body = {{ status: st }};
      const exit = parseFloat(outcomeExit.value);
      if (!isNaN(exit)) body.exit_mark = exit;
      const reason = outcomeReason.value.trim();
      if (reason) body.closed_reason = reason;
      try {{
        const response = await fetch('/shadow-journal/' + id + '/outcome', {{
          method: 'PATCH',
          headers: {{ 'content-type': 'application/json' }},
          body: JSON.stringify(body)
        }});
        const data = await response.json();
        outcomeResult.textContent = JSON.stringify(data, null, 2);
        if (response.ok) {{
          outcomeStatus.value = '';
          outcomeExit.value = '';
          outcomeReason.value = '';
          loadRecent();
        }}
      }} catch (err) {{
        outcomeResult.textContent = String(err);
      }}
    }});
    payload.value = sampleText;
    loadSummary();
    loadRecent();
  </script>
</body>
</html>"""


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
