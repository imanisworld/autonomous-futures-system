"""Phase 15 — inert read-only HTTP status API.

A standalone, read-only FastAPI surface over the Phase 12 storage layer's two
existing pure-read functions (`has_confirmation_consumed`,
`has_ticket_for_confirmation`), plus a narrow reporting endpoint over the
static fixture-candidate inventory in
`options_manager.validation.fixture_status`. This module performs no writes
of any kind — it never calls `append_confirmation_consumed_event`,
`append_ticket_created_event`, `confirm_order_intent`, `build_order_ticket`,
`build_preview_request`, or `validate_preview_boundary`. It never calls a
broker, never places, cancels, or previews an order, and creates no order
queue.

Deliberately NOT mounted into options_manager/app.py — this is a separate
FastAPI app built by `create_options_status_app(config, *, db_path)`, so
Phase 1's existing `/options/packet` ingest app is completely unaffected by
this phase.

Auth uses its own dedicated header/secret (X-Options-Status-Secret /
OPTIONS_MANAGER_HTTP_STATUS_SECRET) — never OPTIONS_MANAGER_INGEST_SECRET,
never WEBHOOK_SECRET. Per the Phase 14 audit decision, the secret is
required unconditionally: if none is configured, every request is refused
with 503 rather than silently allowing unauthenticated access (the Phase 1
ingest endpoint's "auth disabled if unset" convenience is deliberately NOT
repeated here).

Every confirmation/ticket response is built from an explicit allowlist — never
`dataclasses.asdict(StorageReadResult)`, never the raw `record` dict, never
a raw SQLite row. `submitted`, `executable`, `broker`, and `broker_order_id`
are always forced to their inert values (False/False/None/None) in every
response, regardless of what storage returns. `approval_text`, `nonce`, and
`reviewer` are never read or exposed anywhere in this module — they are not
even present on the StorageReadResult.record shapes this module reads.

The fixture-candidate endpoint is also explicitly allowlisted and exposes
only the six requested labels (HOOD, EBAY, AMD, ORCL, FITB, BAC) plus the
inventory's deterministic summary counts. It imports fixture_status directly,
not the scanner, broker, execution, webhook, or broader validation package.

No live-options lock bypass exists here because no order path exists in
this phase — there is nothing for a lock to gate. This module never reads
or mutates LIVE_OPTIONS_TRADING_ENABLED, and never imports live_lock.

Independent of webhook/app.py and execution/ — neither is imported here.
"""

from __future__ import annotations

import secrets as secrets_module

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import OptionsManagerConfig
from .storage import has_confirmation_consumed, has_ticket_for_confirmation
from .validation.fixture_status import (
    build_fixture_candidate_inventory,
    summarize_fixture_candidate_inventory,
)

STATUS_SECRET_HEADER = "X-Options-Status-Secret"

_ALLOWED_RESPONSE_FIELDS = frozenset(
    {
        "status",
        "found",
        "reason",
        "failed_stage",
        "confirmation_id",
        "ticket_id",
        "submitted",
        "executable",
        "broker",
        "broker_order_id",
        "warnings",
    }
)

_FIXTURE_CANDIDATE_TICKERS = ("HOOD", "EBAY", "AMD", "ORCL", "FITB", "BAC")

_ALLOWED_FIXTURE_RESPONSE_FIELDS = frozenset(
    {
        "status",
        "found",
        "source",
        "summary",
        "candidates",
        "submitted",
        "executable",
        "broker",
        "broker_order_id",
        "warnings",
    }
)

_ALLOWED_FIXTURE_CANDIDATE_FIELDS = frozenset(
    {
        "ticker",
        "window",
        "fixture_status",
        "best_future_use",
        "proof_confirmed",
        "proof_missing",
        "reason_not_first_proof",
        "promotion_requirements",
        "notes",
    }
)


def _build_status_response(
    read_result, *, confirmation_id: str, ticket_id: str | None
) -> dict:
    """Build a response strictly from the explicit allowlist above.

    Never touches read_result.record directly and never uses
    dataclasses.asdict — every field here is named individually. submitted,
    executable, broker, and broker_order_id are always forced to their inert
    values regardless of what storage returns, so this module can never leak
    a future storage field it wasn't updated to know about.
    """
    response = {
        "status": read_result.status,
        "found": read_result.found,
        "reason": read_result.reason,
        "failed_stage": read_result.failed_stage,
        "confirmation_id": confirmation_id,
        "ticket_id": ticket_id,
        "submitted": False,
        "executable": False,
        "broker": None,
        "broker_order_id": None,
        "warnings": list(read_result.warnings),
    }
    assert set(response.keys()) == _ALLOWED_RESPONSE_FIELDS
    return response


def _auth_ok(request: Request, config: OptionsManagerConfig) -> bool:
    provided = request.headers.get(STATUS_SECRET_HEADER, "")
    return secrets_module.compare_digest(provided, config.http_status_secret)


def _service_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "status_secret_not_configured",
            "detail": "HTTP status API is not configured",
        },
    )


def _unauthorized_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "error": "unauthorized",
            "detail": f"missing or invalid {STATUS_SECRET_HEADER} header",
        },
    )


def _fixture_candidate_response() -> dict:
    inventory = build_fixture_candidate_inventory()
    summary = summarize_fixture_candidate_inventory(inventory)

    candidates = {}
    for ticker in _FIXTURE_CANDIDATE_TICKERS:
        candidate = inventory[ticker]
        item = {
            "ticker": candidate.ticker,
            "window": candidate.window,
            "fixture_status": candidate.status.value,
            "best_future_use": candidate.best_future_use,
            "proof_confirmed": list(candidate.proof_confirmed),
            "proof_missing": list(candidate.proof_missing),
            "reason_not_first_proof": candidate.reason_not_first_proof,
            "promotion_requirements": list(candidate.promotion_requirements),
            "notes": candidate.notes,
        }
        assert set(item.keys()) == _ALLOWED_FIXTURE_CANDIDATE_FIELDS
        candidates[ticker] = item

    response = {
        "status": "FOUND",
        "found": True,
        "source": "options_manager.validation.fixture_status",
        "summary": {
            "total_candidates": summary.total_candidates,
            "counts_by_status": dict(summary.counts_by_status),
        },
        "candidates": candidates,
        "submitted": False,
        "executable": False,
        "broker": None,
        "broker_order_id": None,
        "warnings": [],
    }
    assert set(response.keys()) == _ALLOWED_FIXTURE_RESPONSE_FIELDS
    return response


def create_options_status_app(
    config: OptionsManagerConfig, *, db_path: str
) -> FastAPI:
    """Build a standalone, read-only FastAPI app over Phase 12 storage.

    config and db_path are both caller-supplied — this factory never reads
    env vars or hardcodes a production database path itself, and never
    calls init_options_storage; the caller owns creating/initializing the
    database before mounting this app.
    """
    app = FastAPI(title="options_manager_status", version="0.1.0-phase15")

    @app.get("/options/status/confirmation/{confirmation_id}")
    async def confirmation_status(confirmation_id: str, request: Request) -> JSONResponse:
        if not config.http_status_enabled or not config.http_status_secret:
            return _service_unavailable_response()
        if not _auth_ok(request, config):
            return _unauthorized_response()

        read_result = has_confirmation_consumed(db_path, confirmation_id, config)
        ticket_id = None
        if read_result.found and read_result.record is not None:
            ticket_id = read_result.record.get("ticket_id")
        return JSONResponse(
            status_code=200,
            content=_build_status_response(
                read_result, confirmation_id=confirmation_id, ticket_id=ticket_id
            ),
        )

    @app.get("/options/status/ticket/{confirmation_id}")
    async def ticket_status(confirmation_id: str, request: Request) -> JSONResponse:
        if not config.http_status_enabled or not config.http_status_secret:
            return _service_unavailable_response()
        if not _auth_ok(request, config):
            return _unauthorized_response()

        read_result = has_ticket_for_confirmation(db_path, confirmation_id, config)
        ticket_id = None
        if read_result.found and read_result.record is not None:
            ticket_id = read_result.record.get("ticket_id")
        return JSONResponse(
            status_code=200,
            content=_build_status_response(
                read_result, confirmation_id=confirmation_id, ticket_id=ticket_id
            ),
        )

    @app.get("/options/status/fixture-candidates")
    async def fixture_candidates_status(request: Request) -> JSONResponse:
        if not config.http_status_enabled or not config.http_status_secret:
            return _service_unavailable_response()
        if not _auth_ok(request, config):
            return _unauthorized_response()

        return JSONResponse(status_code=200, content=_fixture_candidate_response())

    return app
