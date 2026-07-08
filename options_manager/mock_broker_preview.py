"""Phase 17 — mock broker preview adapter.

A fully inert, deterministic, local-only mock adapter proving the future
broker-preview call contract: an existing `OptionsBrokerPreviewRequest` in,
an `OptionsBrokerPreviewResult` out, with no real broker, no credentials, no
account number, no routing destination, no HTTP/network, and no reachable
submit/cancel/replace path.

This module is a MOCK ONLY. It never calls Robinhood, IBKR, or Tradovate —
it never calls any broker at all. `preview_with_mock_broker` accepts only
the existing Phase 9 `OptionsBrokerPreviewRequest` type and returns the
existing Phase 9 `OptionsBrokerPreviewResult` type unchanged, reusing that
schema rather than inventing a parallel one. `broker` is always the literal
string "MOCK" — never a real broker name, and structurally distinct from
any real value `broker_boundary.py` or a future real adapter could ever
produce, so a caller cannot mistake this result for real broker acceptance.
`submitted` is always False, `executable` is always False, and
`broker_order_id` is always None.

This module accepts no credential, no account number, no routing
destination, and no network/client/session parameter of any kind — its only
parameter is the existing, already-inert `OptionsBrokerPreviewRequest`. It
performs no side effects: no storage write, no HTTP call, no file write, no
network call of any kind.

Per the Phase 16 audit decision, real broker preview remains blocked until a
dedicated, separately-approved phase resolves credentials, account-number
handling, and real-API staging-behavior verification. This module proves
only the calling shape — it proves nothing about whether real preview is
safe.

No live-options lock bypass exists here because no order path exists in
this phase — there is nothing for a lock to gate. This module never reads
or mutates LIVE_OPTIONS_TRADING_ENABLED, and never imports live_lock.

Independent of options_manager/http_api.py (never imported here — this
module has no HTTP exposure) and options_manager/storage.py (never imported
here — this module performs no storage writes).
"""

from __future__ import annotations

from .broker_boundary import OptionsBrokerPreviewRequest, OptionsBrokerPreviewResult

MOCK_BROKER_LABEL = "MOCK"


def _mock_preview_ready(ticket_id: str) -> OptionsBrokerPreviewResult:
    return OptionsBrokerPreviewResult(
        preview_ready=True,
        status="PREVIEW_READY",
        failed_stage=None,
        reason="",
        ticket_id=ticket_id,
        broker=MOCK_BROKER_LABEL,
        broker_order_id=None,
        executable=False,
        submitted=False,
        warnings=["mock_preview_only", "not_a_broker_order", "submitted_false"],
    )


def _mock_rejected(failed_stage: str, reason: str) -> OptionsBrokerPreviewResult:
    return OptionsBrokerPreviewResult(
        preview_ready=False,
        status="REJECTED",
        failed_stage=failed_stage,
        reason=reason,
        ticket_id=None,
        broker=None,
        broker_order_id=None,
        executable=False,
        submitted=False,
    )


def _mock_data_blocked(failed_stage: str, reason: str) -> OptionsBrokerPreviewResult:
    return OptionsBrokerPreviewResult(
        preview_ready=False,
        status="DATA_BLOCKED",
        failed_stage=failed_stage,
        reason=reason,
        ticket_id=None,
        broker=None,
        broker_order_id=None,
        executable=False,
        submitted=False,
    )


def preview_with_mock_broker(
    preview_request: OptionsBrokerPreviewRequest,
) -> OptionsBrokerPreviewResult:
    """Pure function of preview_request -> OptionsBrokerPreviewResult.

    Accepts only the existing, already-inert OptionsBrokerPreviewRequest —
    no credential, no account number, no routing destination, no
    network/client/session parameter. Performs no side effects: no storage
    write, no HTTP call, no network call of any kind. Never calls a real
    broker; this is a local, deterministic, canned mock only.

    Re-validates the same non-executable/dry-run-only invariants already
    proven in Phases 7 and 9 before returning a ready result — a mock
    adapter must not fabricate acceptance on top of a broken invariant any
    more than a real one would.
    """
    if not preview_request.ticket_id or not preview_request.ticket_id.strip():
        return _mock_data_blocked(
            "ticket_id", "preview_request.ticket_id is missing or empty"
        )
    if not preview_request.confirmation_id or not preview_request.confirmation_id.strip():
        return _mock_data_blocked(
            "confirmation_id", "preview_request.confirmation_id is missing or empty"
        )
    if preview_request.executable is not False:
        return _mock_rejected(
            "executable",
            "preview_request.executable is not False; refusing mock preview",
        )
    if preview_request.dry_run_only is not True:
        return _mock_rejected(
            "dry_run_only",
            "preview_request.dry_run_only is not True; refusing mock preview",
        )

    result = _mock_preview_ready(preview_request.ticket_id)

    # Defensive re-check: a mock adapter must never return a result that
    # isn't non-executable/non-submitted/broker-order-id-free, even though
    # the literals above guarantee it today.
    if result.executable is not False:
        return _mock_rejected(
            "executable", "result.executable is not False; refusing mock preview"
        )
    if result.submitted is not False:
        return _mock_rejected(
            "submitted", "result.submitted is not False; refusing mock preview"
        )
    if result.broker_order_id is not None:
        return _mock_rejected(
            "broker_order_id",
            "result.broker_order_id is not None; refusing mock preview",
        )
    if result.broker != MOCK_BROKER_LABEL:
        return _mock_rejected(
            "broker", "result.broker is not the mock label; refusing mock preview"
        )

    return result
