"""Phase 6 — human-confirmed order prep.

Pure, deterministic verification of a caller-supplied ConfirmationRecord
against a Phase 5 DryRunReviewResult. No broker calls, no order calls, no
HTTP, no Discord, no file writes, no storage of any kind — this module
performs no I/O of any kind. It only reads a review result, a confirmation
record, and a config object, and returns a result.

This module does NOT place orders, preview orders, or execute anything. It
also does NOT store, persist, or mutate a ConfirmationRecord — it only
verifies one the caller already has. Marking a record's used_at (to prevent
replay) is the caller's responsibility in a future storage layer; this
module never writes to any record it's given.

No live-options lock bypass exists here because no order path exists in this
phase — there is nothing for a lock to gate. This module never reads or
mutates LIVE_OPTIONS_TRADING_ENABLED.

Independent of risk/risk_engine.py (futures) and risk/options_risk_engine.py
(reference only, not imported) — this is options_manager's own verifier.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .dry_run_review import DryRunReviewResult, OptionOrderIntent


@dataclass(kw_only=True)
class ConfirmationRequest:
    intent_id: str
    reviewer: str
    approval_text: str
    created_at: datetime
    expires_at: datetime
    nonce: str


@dataclass(kw_only=True)
class ConfirmationRecord:
    confirmation_id: str
    intent_id: str
    reviewer: str
    approved: bool
    approval_text: str
    created_at: datetime
    expires_at: datetime
    used_at: Optional[datetime]
    nonce: str


@dataclass
class HumanConfirmedOrderPrep:
    approved_for_order_prep: bool
    status: Literal["CONFIRMED", "REJECTED", "EXPIRED", "USED", "DATA_BLOCKED"]
    failed_stage: Optional[str] = None
    reason: str = ""
    order_intent: Optional[OptionOrderIntent] = None
    confirmation_id: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def compute_intent_id(order_intent: OptionOrderIntent) -> str:
    """Deterministic id derived from the intent's own content-defining fields.

    Deliberately excludes nonce/reviewer/timestamps — the same intent content
    always produces the same id, so a confirmation only matches an intent
    whose material terms haven't changed since the confirmation was issued.
    """
    raw = "|".join(
        [
            order_intent.ticker,
            order_intent.direction,
            order_intent.order_action,
            str(order_intent.quantity),
            f"{order_intent.contract_strike:.4f}",
            order_intent.contract_expiry.isoformat(),
            f"{order_intent.max_premium:.4f}",
            f"{order_intent.estimated_limit_price:.4f}",
            f"{order_intent.estimated_notional:.4f}",
            order_intent.account_tag,
            order_intent.source,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_confirmation_request(
    review_result: DryRunReviewResult,
    config: OptionsManagerConfig,
    *,
    reviewer: str,
    approval_text: str,
    now: datetime,
    nonce: str,
) -> ConfirmationRequest:
    """Pure function of its explicit inputs -> ConfirmationRequest.

    Never generates a nonce or reads the clock internally — reviewer,
    approval_text, now, and nonce are all caller-supplied so this stays
    deterministic and testable. Requires review_result to be REVIEW_READY
    with an order_intent; anything else is a programming error, not a
    reviewable state, so it raises rather than returning a placeholder.
    """
    if review_result.status != "REVIEW_READY" or review_result.order_intent is None:
        raise ValueError(
            "build_confirmation_request requires a REVIEW_READY review_result "
            "with an order_intent; got "
            f"status={review_result.status!r} order_intent={review_result.order_intent!r}"
        )
    cfg = config
    intent_id = compute_intent_id(review_result.order_intent)
    expires_at = now + timedelta(seconds=cfg.human_confirm_ttl_seconds)
    return ConfirmationRequest(
        intent_id=intent_id,
        reviewer=reviewer,
        approval_text=approval_text,
        created_at=now,
        expires_at=expires_at,
        nonce=nonce,
    )


def _confirmed(order_intent: OptionOrderIntent, confirmation_id: str) -> HumanConfirmedOrderPrep:
    return HumanConfirmedOrderPrep(
        approved_for_order_prep=True,
        status="CONFIRMED",
        failed_stage=None,
        reason="",
        order_intent=order_intent,
        confirmation_id=confirmation_id,
        warnings=[],
    )


def _rejected(failed_stage: str, reason: str) -> HumanConfirmedOrderPrep:
    return HumanConfirmedOrderPrep(
        approved_for_order_prep=False,
        status="REJECTED",
        failed_stage=failed_stage,
        reason=reason,
    )


def _data_blocked(failed_stage: str, reason: str) -> HumanConfirmedOrderPrep:
    return HumanConfirmedOrderPrep(
        approved_for_order_prep=False,
        status="DATA_BLOCKED",
        failed_stage=failed_stage,
        reason=reason,
    )


def _expired(reason: str) -> HumanConfirmedOrderPrep:
    return HumanConfirmedOrderPrep(
        approved_for_order_prep=False,
        status="EXPIRED",
        failed_stage="expired",
        reason=reason,
    )


def _used(reason: str) -> HumanConfirmedOrderPrep:
    return HumanConfirmedOrderPrep(
        approved_for_order_prep=False,
        status="USED",
        failed_stage="used",
        reason=reason,
    )


def confirm_order_intent(
    review_result: DryRunReviewResult,
    confirmation_record: ConfirmationRecord,
    config: OptionsManagerConfig,
) -> HumanConfirmedOrderPrep:
    """Pure function of (review_result, confirmation_record, config) -> HumanConfirmedOrderPrep.

    config is required and must be passed explicitly by the caller — this
    function itself must never read env vars, .env files, or any other
    external mutable state (other than the wall clock, for the expiry
    comparison), or it stops being deterministic. It never calls a broker,
    never places or previews a real order, and never writes/mutates anything
    — including confirmation_record itself, which it only reads.
    """
    cfg = config

    # 0. Subsystem-level kill switch for human-confirmed order prep.
    if not cfg.human_confirm_enabled:
        return _rejected(
            "human_confirm_disabled",
            "human_confirm_enabled is False; no order prep object created",
        )

    # 1. Dry-run review must be REVIEW_READY.
    if review_result.status == "REJECTED":
        return _rejected(
            "dry_run_review",
            f"dry_run_review rejected: failed_stage={review_result.failed_stage!r}, "
            f"reason={review_result.reason!r}",
        )
    if review_result.status == "DATA_BLOCKED":
        return _data_blocked(
            "dry_run_review",
            f"dry_run_review data_blocked: failed_stage={review_result.failed_stage!r}, "
            f"reason={review_result.reason!r}",
        )

    # 2. Order intent required.
    order_intent = review_result.order_intent
    if order_intent is None:
        return _data_blocked("order_intent", "review_result.order_intent is missing")

    # 3. dry_run_only must still be True — defensive re-check, same pattern
    # Phase 5 used on itself.
    if order_intent.dry_run_only is not True:
        return _rejected(
            "dry_run_only", "order_intent.dry_run_only is not True; refusing to confirm"
        )

    # 4. Reviewer required.
    if cfg.human_confirm_require_reviewer and not confirmation_record.reviewer.strip():
        return _rejected("reviewer", "confirmation_record.reviewer is empty")

    # 5. Nonce required.
    if cfg.human_confirm_require_nonce and not confirmation_record.nonce.strip():
        return _rejected("nonce", "confirmation_record.nonce is empty")

    # 6. Timezone-aware timestamps required.
    if confirmation_record.created_at.tzinfo is None:
        return _data_blocked(
            "timestamp", "confirmation_record.created_at has no timezone info"
        )
    if confirmation_record.expires_at.tzinfo is None:
        return _data_blocked(
            "timestamp", "confirmation_record.expires_at has no timezone info"
        )

    # 7. Confirmation must match exactly one intent.
    expected_intent_id = compute_intent_id(order_intent)
    if confirmation_record.intent_id != expected_intent_id:
        return _rejected(
            "intent_mismatch",
            f"confirmation_record.intent_id {confirmation_record.intent_id!r} does not "
            f"match computed intent_id {expected_intent_id!r}",
        )

    # 8. Confirmation must not be expired.
    now = datetime.now(timezone.utc)
    if now > confirmation_record.expires_at:
        return _expired(
            f"confirmation expired at {confirmation_record.expires_at.isoformat()} "
            f"(now={now.isoformat()})"
        )

    # 9. Confirmation must not be reused.
    if confirmation_record.used_at is not None:
        return _used(
            f"confirmation already used at {confirmation_record.used_at.isoformat()}"
        )

    # 10. Confirmation must be explicit: approved and an exact phrase match.
    phrase = cfg.human_confirm_required_phrase
    approval_text = confirmation_record.approval_text
    if not cfg.human_confirm_case_sensitive:
        phrase = phrase.lower()
        approval_text = approval_text.lower()
    if not confirmation_record.approved or approval_text != phrase:
        return _rejected(
            "approval_text",
            "confirmation_record.approved is False or approval_text does not "
            f"exactly match the required phrase {cfg.human_confirm_required_phrase!r}",
        )

    return _confirmed(order_intent, confirmation_record.confirmation_id)
