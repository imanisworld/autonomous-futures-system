from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Durable but inert final order-suppression audit rows.
marker = '''    def last_reconcile_ts(self, for_date: Optional[date] = None) -> Optional[str]:
'''
method = '''    def log_order_suppression(
        self,
        *,
        instrument: str,
        session: str,
        final_decision: str,
        gate_reason: str,
        strategy: Optional[str] = None,
        signal_timestamp: Optional[str] = None,
        client_order_id: Optional[str] = None,
        for_date: Optional[date] = None,
    ) -> None:
        """Append the final reason an approved intent did not reach the broker.

        Audit-only and inert to daily state: the record carries no `decision`
        field and is not an OUTCOME/TRADE/ORDER_IDS row. It therefore cannot
        create, close, count, or otherwise mutate a position; it only makes the
        terminal suppression durable after the earlier TRADE_INTENT row.
        """
        try:
            self._append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "ORDER_SUPPRESSION",
                    "instrument": instrument,
                    "session": session,
                    "final_decision": final_decision,
                    "gate_reason": gate_reason,
                    "strategy": strategy,
                    "signal_timestamp": signal_timestamp,
                    "client_order_id": client_order_id,
                },
                for_date,
            )
        except Exception as exc:  # noqa: BLE001 — visibility must never raise
            logger.debug("log_order_suppression failed: %s", exc)

    def last_reconcile_ts(self, for_date: Optional[date] = None) -> Optional[str]:
'''
replace_once("journal/journal_logger.py", marker, method)

# 2) Capacity locks already had strategy-context evidence; expose the same reason
# in the returned result so Discord can actually explain the configured alerts.
replace_once(
    "webhook/runner.py",
    '''    if daily_state.trade_count >= total_daily_capacity:
        result["decision"] = "BLOCKED_MAX_TRADES"
        _observe_strategy_context_once(
            _capacity_observation(
                "BLOCKED_MAX_TRADES",
                "Daily trade capacity reached before strategy evaluation.",
            )
        )
        return result
''',
    '''    if daily_state.trade_count >= total_daily_capacity:
        result["decision"] = "BLOCKED_MAX_TRADES"
        result["reason"] = "Daily trade capacity reached before strategy evaluation."
        _observe_strategy_context_once(
            _capacity_observation(
                "BLOCKED_MAX_TRADES",
                result["reason"],
            )
        )
        return result
''',
)
replace_once(
    "webhook/runner.py",
    '''    if daily_state.consecutive_losses >= cfg.max_consecutive_losses:
        result["decision"] = "BLOCKED_LOSS_LOCKOUT"
        _observe_strategy_context_once(
            _capacity_observation(
                "BLOCKED_LOSS_LOCKOUT",
                "Maximum consecutive-loss limit reached before strategy evaluation.",
            )
        )
        return result
''',
    '''    if daily_state.consecutive_losses >= cfg.max_consecutive_losses:
        result["decision"] = "BLOCKED_LOSS_LOCKOUT"
        result["reason"] = "Maximum consecutive-loss limit reached before strategy evaluation."
        _observe_strategy_context_once(
            _capacity_observation(
                "BLOCKED_LOSS_LOCKOUT",
                result["reason"],
            )
        )
        return result
''',
)

# 3) Persist the final schedule/working-order suppression after TRADE_INTENT.
replace_once(
    "webhook/runner.py",
    '''        _record_candidate_lifecycle(
            opportunity_candidate_ids,
            log_dir,
            today,
            "ORDER_SUPPRESSED",
            broker_result="NOT_SENT",
            gate_reason=_gate_reason,
        )
        return result
''',
    '''        _record_candidate_lifecycle(
            opportunity_candidate_ids,
            log_dir,
            today,
            "ORDER_SUPPRESSED",
            broker_result="NOT_SENT",
            gate_reason=_gate_reason,
        )
        journal.log_order_suppression(
            instrument=state.instrument,
            session=state.session,
            final_decision="SHADOW_NO_ORDER",
            gate_reason=_gate_reason,
            strategy=order.strategy,
            signal_timestamp=state.timestamp.isoformat() if state.timestamp else None,
            client_order_id=order.client_order_id,
            for_date=today,
        )
        return result
''',
)
replace_once(
    "webhook/runner.py",
    '''            _record_candidate_lifecycle(
                opportunity_candidate_ids,
                log_dir,
                today,
                "ORDER_SUPPRESSED",
                broker_result="NOT_SENT",
                gate_reason=_wo_reason,
            )
            return result
''',
    '''            _record_candidate_lifecycle(
                opportunity_candidate_ids,
                log_dir,
                today,
                "ORDER_SUPPRESSED",
                broker_result="NOT_SENT",
                gate_reason=_wo_reason,
            )
            journal.log_order_suppression(
                instrument=state.instrument,
                session=state.session,
                final_decision="ORDER_SUPPRESSED",
                gate_reason=_wo_reason,
                strategy=order.strategy,
                signal_timestamp=state.timestamp.isoformat() if state.timestamp else None,
                client_order_id=order.client_order_id,
                for_date=today,
            )
            return result
''',
)

# 4) Discord: surface existing terminal reason fields, without inventing logic.
helper_marker = '''def _format_message(payload: AlertPayload, result: dict) -> str:
'''
helper = '''def _decision_reason_line(result: dict) -> Optional[str]:
    reason = result.get("gate_reason") or result.get("reason")
    if not reason:
        failed = result.get("failed_gates") or []
        if isinstance(failed, str):
            failed = [failed]
        if failed:
            reason = ", ".join(str(item) for item in failed)
    return f"Why: {reason}" if reason else None


def _format_message(payload: AlertPayload, result: dict) -> str:
'''
replace_once("notifications/discord_notifier.py", helper_marker, helper)
replace_once(
    "notifications/discord_notifier.py",
    '''        if resolution:
            lines.append(f"Resolution: {resolution}")
        if risk:
            lines.append(_risk_line(risk))
''',
    '''        if resolution:
            lines.append(f"Resolution: {resolution}")
        reason_line = _decision_reason_line(result)
        if reason_line:
            lines.append(reason_line)
        if risk:
            lines.append(_risk_line(risk))
''',
)

# 5) Existing why-no-trade diagnostic: point it at the evidence that actually exists.
replace_once(
    ".claude/commands/futures-why-no-trade.md",
    '''- Decision engine produced a candidate? — did `DecisionEngine.evaluate` return TRADE with a setup, or NO_TRADE with a reason
- Risk engine rejected? — if TRADE, which specific `RiskEngine.validate()` check fired (`failed_rule`), including the alert-freshness gate (`alert_timestamp_missing` / `alert_timestamp_future` / `stale_alert`)
- Schedule gate suppressed? — `adaptive.execution_gate.order_placement_allowed` / `SHADOW_NO_ORDER`
- Working-order recheck suppressed? — `ORDER_SUPPRESSED` with `gate_reason` of `working_order_conflict` or `order_state_unreadable`
''',
    '''- Capacity/loss lock fired before strategy evaluation? — for `BLOCKED_MAX_TRADES` / `BLOCKED_LOSS_LOCKOUT`, consult `strategy_context_observations.jsonl`; these early-return reasons are preserved there even when the primary decision journal has no normal decision row
- Decision engine produced a candidate? — did `DecisionEngine.evaluate` return TRADE with a setup, or NO_TRADE with a reason
- Risk engine rejected? — if TRADE, which specific `RiskEngine.validate()` check fired (`failed_rule`), including the alert-freshness gate (`alert_timestamp_missing` / `alert_timestamp_future` / `stale_alert`)
- Schedule gate suppressed? — `adaptive.execution_gate.order_placement_allowed` / `SHADOW_NO_ORDER`; confirm the inert `ORDER_SUPPRESSION` journal row carries the final `gate_reason`
- Working-order recheck suppressed? — `ORDER_SUPPRESSED` with `gate_reason` of `working_order_conflict` or `order_state_unreadable`; confirm the inert `ORDER_SUPPRESSION` journal row matches the earlier `TRADE_INTENT`
''',
)

# ---- regression tests ----
block_tests = Path("tests/test_block_visibility.py")
block_tests.write_text(
    block_tests.read_text(encoding="utf-8")
    + '''\n\ndef test_order_suppression_record_is_inert_to_daily_state(tmp_path):
    d = date(2026, 7, 21)
    j = JournalLogger(log_dir=str(tmp_path))
    _seed_open(j, d)
    before = j.get_daily_state(d)
    before_open = j.get_open_position(d)

    j.log_order_suppression(
        instrument="MES",
        session="new_york",
        final_decision="ORDER_SUPPRESSED",
        gate_reason="working_order_conflict: 1 working order(s) on account",
        strategy="orb_breakout",
        signal_timestamp="2026-07-21T18:00:00+00:00",
        client_order_id="AFS-test",
        for_date=d,
    )

    after = j.get_daily_state(d)
    assert after.trade_count == before.trade_count
    assert after.has_open_position == before.has_open_position
    assert after.realized_pnl_dollars == before.realized_pnl_dollars
    assert j.get_open_position(d) == before_open
    rows = j._read_entries(j._journal_path(d))
    rec = next(row for row in rows if row.get("type") == "ORDER_SUPPRESSION")
    assert rec["final_decision"] == "ORDER_SUPPRESSED"
    assert rec["gate_reason"].startswith("working_order_conflict")
    assert rec["client_order_id"] == "AFS-test"
    assert "decision" not in rec
''',
    encoding="utf-8",
)

discord_tests = Path("tests/test_discord_notifier.py")
discord_tests.write_text(
    discord_tests.read_text(encoding="utf-8")
    + '''\n\ndef test_non_trade_alert_surfaces_general_reason():
    from notifications.discord_notifier import _format_message
    result = _result("BLOCKED_MAX_TRADES")
    result["risk"] = None
    result["reason"] = "Daily trade capacity reached before strategy evaluation."
    msg = _format_message(_payload(), result)
    assert "Why: Daily trade capacity reached before strategy evaluation." in msg


def test_non_trade_alert_prefers_gate_reason():
    from notifications.discord_notifier import _format_message
    result = _result("ORDER_SUPPRESSED")
    result["risk"] = None
    result["reason"] = "generic"
    result["gate_reason"] = "working_order_conflict: 1 working order(s) on account"
    msg = _format_message(_payload(), result)
    assert "Why: working_order_conflict: 1 working order(s) on account" in msg
    assert "Why: generic" not in msg


def test_non_trade_alert_falls_back_to_failed_gates():
    from notifications.discord_notifier import _format_message
    result = _result("NO_TRADE")
    result["risk"] = None
    result["failed_gates"] = ["ENTRY_DETACHED_FROM_PRICE"]
    msg = _format_message(_payload(), result)
    assert "Why: ENTRY_DETACHED_FROM_PRICE" in msg
''',
    encoding="utf-8",
)

# Strengthen existing runner capacity-lock tests: returned result must carry the reason Discord uses.
webhook_tests = Path("tests/test_webhook.py")
text = webhook_tests.read_text(encoding="utf-8")
text = text.replace(
    '''        if r["decision"] == "BLOCKED_MAX_TRADES":
            return  # ✓ limit enforced
''',
    '''        if r["decision"] == "BLOCKED_MAX_TRADES":
            assert r["reason"] == "Daily trade capacity reached before strategy evaluation."
            return  # ✓ limit enforced
''',
    1,
)
text = text.replace(
    '''    assert result["decision"] == "BLOCKED_LOSS_LOCKOUT"
''',
    '''    assert result["decision"] == "BLOCKED_LOSS_LOCKOUT"
    assert result["reason"] == "Maximum consecutive-loss limit reached before strategy evaluation."
''',
    1,
)
webhook_tests.write_text(text, encoding="utf-8")

for raw in (
    "scripts/_chatgpt_apply_no_trade_visibility.py",
    ".github/workflows/chatgpt-apply-no-trade-visibility.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
