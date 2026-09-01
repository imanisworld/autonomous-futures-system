from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Journal schema: additive optional identity only.
replace_once(
    "journal/journal_logger.py",
    '''        paper_order_id: Optional[str] = None,
        execution_audit: Optional[dict] = None,
''',
    '''        paper_order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        execution_audit: Optional[dict] = None,
''',
)
replace_once(
    "journal/journal_logger.py",
    '''                "paper_order_id": paper_order_id,
                "execution_audit": execution_audit,
''',
    '''                "paper_order_id": paper_order_id,
                "client_order_id": client_order_id,
                "execution_audit": execution_audit,
''',
)
replace_once(
    "journal/journal_logger.py",
    '''        *,
        stop: Optional[float] = None,
        exit_mode: Optional[str] = None,
''',
    '''        *,
        stop: Optional[float] = None,
        exit_mode: Optional[str] = None,
        client_order_id: Optional[str] = None,
''',
)
replace_once(
    "journal/journal_logger.py",
    '''        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "ORDER_IDS",
            "instrument": instrument,
            "session": session,
            "order_ids": dict(order_ids or {}),
        }
''',
    '''        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "ORDER_IDS",
            "instrument": instrument,
            "session": session,
            "order_ids": dict(order_ids or {}),
        }
        if client_order_id:
            record["client_order_id"] = str(client_order_id)
''',
)
replace_once(
    "journal/journal_logger.py",
    '''                        "paper_order_id": entry.get("paper_order_id"),
                        "mnq_orb_reclaim_proof_audit": entry.get(
''',
    '''                        "paper_order_id": entry.get("paper_order_id"),
                        "client_order_id": entry.get("client_order_id"),
                        "mnq_orb_reclaim_proof_audit": entry.get(
''',
)

# Runner: mint the EXISTING deterministic id before TRADE_INTENT is persisted.
# Special strat_212/122 evidence paths do not submit the normal bracket and do
# not claim a broker client identity.
old_approved = '''    if not risk_result.approved:
        # Update journal entry decision before writing so the log reflects reality.
        journal_entry["decision"] = "RISK_REJECTED"
        journal_entry["reason"] = risk_result.reason or journal_entry.get("reason")
    else:
        # Confirmed-execution model (2026-07-10, EXECUTION_STATE_BUG fix): the
        # pre-broker row is an INTENT, not an open position. Log it as
        # decision="TRADE_INTENT" so NO reader (get_open_position /
        # _compute_daily_state / risk gates / reconciler / status) treats it as an
        # open, counted trade. The authoritative decision="TRADE" row — the only row
        # any reader treats as an open position — is written ONLY after the broker
        # confirms an OPEN position with order ids, further below.
        journal_entry["decision"] = "TRADE_INTENT"
    journal.log_decision(journal_entry, risk_dict, for_date=today)
'''
new_approved = '''    _client_order_id = None
    _execution_direction = None
    if not risk_result.approved:
        # Update journal entry decision before writing so the log reflects reality.
        journal_entry["decision"] = "RISK_REJECTED"
        journal_entry["reason"] = risk_result.reason or journal_entry.get("reason")
    else:
        # Mint the same deterministic identity the broker order already uses,
        # but do it before the intent is journaled so intent/order/outcome can be
        # joined exactly. strat_212/122 evidence is already decided by its watched
        # bar and never submits this normal bracket, so it deliberately carries no
        # broker client-order identity.
        if decision.setup.strategy not in (STRAT_212, STRAT_122):
            import hashlib as _hashlib
            _execution_direction = (
                mnq_breakout_inverse_audit["submitted_setup"]["direction"]
                if (
                    mnq_breakout_inverse_decision is not None
                    and mnq_breakout_inverse_decision.apply_override
                )
                else decision.setup.direction
            )
            _signal_identity = "|".join(
                str(part)
                for part in (
                    state.instrument,
                    decision.setup.strategy,
                    _execution_direction,
                    getattr(state, "timestamp", ""),
                )
            )
            _client_order_id = "AFS-" + _hashlib.sha1(_signal_identity.encode()).hexdigest()[:24]
            journal_entry["client_order_id"] = _client_order_id
        # Confirmed-execution model (2026-07-10, EXECUTION_STATE_BUG fix): the
        # pre-broker row is an INTENT, not an open position. Log it as
        # decision="TRADE_INTENT" so NO reader (get_open_position /
        # _compute_daily_state / risk gates / reconciler / status) treats it as an
        # open, counted trade. The authoritative decision="TRADE" row — the only row
        # any reader treats as an open position — is written ONLY after the broker
        # confirms an OPEN position with order ids, further below.
        journal_entry["decision"] = "TRADE_INTENT"
    journal.log_decision(journal_entry, risk_dict, for_date=today)
'''
replace_once("webhook/runner.py", old_approved, new_approved)

old_mint = '''    # Deterministic client order identity: the same logical signal (same
    # instrument/strategy/direction/decision-bar) always maps to the same id,
    # so a retry or recovery path can never create a second parent order at
    # the broker (TradovateBroker refuses a registered clOrdId; ambiguous
    # submissions must reconcile first). Derived, not random — restarts and
    # duplicate webhook deliveries produce the identical identity.
    import hashlib as _hashlib
    _execution_direction = (
        mnq_breakout_inverse_audit["submitted_setup"]["direction"]
        if (
            mnq_breakout_inverse_decision is not None
            and mnq_breakout_inverse_decision.apply_override
        )
        else decision.setup.direction
    )
    _signal_identity = "|".join(
        str(part)
        for part in (
            state.instrument,
            decision.setup.strategy,
            _execution_direction,
            getattr(state, "timestamp", ""),
        )
    )
    _client_order_id = "AFS-" + _hashlib.sha1(_signal_identity.encode()).hexdigest()[:24]

'''
new_mint = '''    # `_client_order_id` was minted before TRADE_INTENT so the append-only
    # journal and broker order share one identity. This fallback preserves the
    # pre-existing order behavior if a future control-flow change reaches Step 5
    # without the earlier persistence block.
    if _client_order_id is None:
        import hashlib as _hashlib
        _execution_direction = decision.setup.direction
        _signal_identity = "|".join(
            str(part)
            for part in (
                state.instrument,
                decision.setup.strategy,
                _execution_direction,
                getattr(state, "timestamp", ""),
            )
        )
        _client_order_id = "AFS-" + _hashlib.sha1(_signal_identity.encode()).hexdigest()[:24]

'''
replace_once("webhook/runner.py", old_mint, new_mint)

# Immediate no-fill and confirmation-missing outcomes get the same broker identity.
replace_once(
    "webhook/runner.py",
    '''            ticks_moved_from_entry=None,
            execution_audit=getattr(fill, "execution_audit", None),
''',
    '''            ticks_moved_from_entry=None,
            client_order_id=order.client_order_id,
            execution_audit=getattr(fill, "execution_audit", None),
''',
)
replace_once(
    "webhook/runner.py",
    '''            seconds_until_cancel=(_cancel_ts - _submit_ts).total_seconds(),
            requested_entry=order.entry,
        )
''',
    '''            seconds_until_cancel=(_cancel_ts - _submit_ts).total_seconds(),
            requested_entry=order.entry,
            client_order_id=order.client_order_id,
        )
''',
)

# Normal next-bar/exit resolution carries the confirmed trade identity forward.
replace_once(
    "webhook/runner.py",
    '''                    for_date=open_position_date,
                    paper_order_id=getattr(fill, "paper_order_id", None),
                )
''',
    '''                    for_date=open_position_date,
                    paper_order_id=getattr(fill, "paper_order_id", None),
                    client_order_id=open_pos.get("client_order_id"),
                )
''',
)

# Initial broker ORDER_IDS record and later trail updates carry the same identity.
replace_once(
    "webhook/runner.py",
    '''                stop=decision.setup.stop,
                exit_mode=getattr(cfg, "exit_mode", "static"),
            )
''',
    '''                stop=decision.setup.stop,
                exit_mode=getattr(cfg, "exit_mode", "static"),
                client_order_id=order.client_order_id,
            )
''',
)
replace_once(
    "webhook/runner.py",
    '''                            stop=float(_t["would_stop"]),
                            exit_mode="runner_live",
                        )
''',
    '''                            stop=float(_t["would_stop"]),
                            exit_mode="runner_live",
                            client_order_id=open_pos.get("client_order_id"),
                        )
''',
)

# Tests: strengthen the existing confirmed-execution fixtures rather than build
# a parallel harness.
tests = Path("tests/test_webhook.py")
text = tests.read_text(encoding="utf-8")
old_paper = '''    assert len(intents) == 1
    assert len(confirmed) == 1
    # The confirmed row carries the full payload readers depend on.
'''
new_paper = '''    assert len(intents) == 1
    assert len(confirmed) == 1
    assert intents[0]["client_order_id"].startswith("AFS-")
    assert confirmed[0]["client_order_id"] == intents[0]["client_order_id"]
    # The confirmed row carries the full payload readers depend on.
'''
if text.count(old_paper) != 1:
    raise SystemExit("paper confirmed-execution test anchor not found")
text = text.replace(old_paper, new_paper, 1)

old_real = '''    rows = _read_journal_rows(tmp_path)
    assert len([r for r in rows if r.get("decision") == "TRADE_INTENT"]) == 1
    assert len([r for r in rows if r.get("decision") == "TRADE"]) == 1
    assert any(r.get("type") == "ORDER_IDS" for r in rows)
'''
new_real = '''    rows = _read_journal_rows(tmp_path)
    intents = [r for r in rows if r.get("decision") == "TRADE_INTENT"]
    confirmed = [r for r in rows if r.get("decision") == "TRADE"]
    order_rows = [r for r in rows if r.get("type") == "ORDER_IDS"]
    assert len(intents) == 1
    assert len(confirmed) == 1
    assert len(order_rows) == 1
    assert intents[0]["client_order_id"].startswith("AFS-")
    assert confirmed[0]["client_order_id"] == intents[0]["client_order_id"]
    assert order_rows[0]["client_order_id"] == intents[0]["client_order_id"]
'''
if text.count(old_real) != 1:
    raise SystemExit("real confirmed-execution test anchor not found")
text = text.replace(old_real, new_real, 1)

old_missing = '''    assert result["decision"] == "BLOCKED_ORDER_CONFIRMATION_MISSING"
    rows = _read_journal_rows(tmp_path)
    assert [r for r in rows if r.get("decision") == "TRADE"] == []  # NO confirmed trade
    assert any(
        r.get("type") == "OUTCOME"
        and (r.get("outcome") or {}).get("result") == "CANCELLED"
        and (r.get("outcome") or {}).get("no_fill_reason") == "ORDER_CONFIRMATION_MISSING"
        for r in rows
    )
'''
new_missing = '''    assert result["decision"] == "BLOCKED_ORDER_CONFIRMATION_MISSING"
    rows = _read_journal_rows(tmp_path)
    assert [r for r in rows if r.get("decision") == "TRADE"] == []  # NO confirmed trade
    intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    cancelled = next(
        r for r in rows
        if r.get("type") == "OUTCOME"
        and (r.get("outcome") or {}).get("result") == "CANCELLED"
        and (r.get("outcome") or {}).get("no_fill_reason") == "ORDER_CONFIRMATION_MISSING"
    )
    assert intent["client_order_id"].startswith("AFS-")
    assert cancelled["outcome"]["client_order_id"] == intent["client_order_id"]
'''
if text.count(old_missing) != 1:
    raise SystemExit("confirmation-missing test anchor not found")
text = text.replace(old_missing, new_missing, 1)

old_cancel = '''    assert result["decision"] == "BLOCKED_EXECUTION_FAILED"
    rows = _read_journal_rows(tmp_path)
    assert [r for r in rows if r.get("decision") == "TRADE"] == []
    assert any(
        r.get("type") == "OUTCOME" and (r.get("outcome") or {}).get("result") == "CANCELLED"
        for r in rows
    )
'''
new_cancel = '''    assert result["decision"] == "BLOCKED_EXECUTION_FAILED"
    rows = _read_journal_rows(tmp_path)
    assert [r for r in rows if r.get("decision") == "TRADE"] == []
    intent = next(r for r in rows if r.get("decision") == "TRADE_INTENT")
    cancelled = next(
        r for r in rows
        if r.get("type") == "OUTCOME" and (r.get("outcome") or {}).get("result") == "CANCELLED"
    )
    assert intent["client_order_id"].startswith("AFS-")
    assert cancelled["outcome"]["client_order_id"] == intent["client_order_id"]
'''
if text.count(old_cancel) != 1:
    raise SystemExit("non-open cancellation test anchor not found")
text = text.replace(old_cancel, new_cancel, 1)
tests.write_text(text, encoding="utf-8")

# Journal-specific persistence/reconstruction tests.
p = Path("tests/test_order_id_persistence.py")
p.write_text(
    p.read_text(encoding="utf-8")
    + '''\n\ndef test_client_order_id_persists_on_outcome_and_open_position(tmp_path):
    from datetime import date
    from journal.journal_logger import JournalLogger

    d = date(2026, 9, 1)
    j = JournalLogger(log_dir=str(tmp_path))
    trade = _trade_record()
    trade["client_order_id"] = "AFS-identity-test"
    j.log_decision(trade, {"result": "APPROVED", "failed_rule": None, "reason": None}, for_date=d)
    open_pos = j.get_open_position(d)
    assert open_pos is not None
    assert open_pos["client_order_id"] == "AFS-identity-test"

    j.log_outcome(
        instrument="MNQ", session="new_york", result="CANCELLED",
        entry_price=30000.0, exit_price=None, exit_reason="ENTRY_NOT_FILLED",
        pnl_ticks=0.0, pnl_dollars=0.0, for_date=d,
        client_order_id="AFS-identity-test",
    )
    rows = j.read_day(d)
    outcome = next(row for row in rows if row.get("type") == "OUTCOME")
    assert outcome["outcome"]["client_order_id"] == "AFS-identity-test"


def test_client_order_id_persists_on_order_ids_record(tmp_path):
    from datetime import date
    from journal.journal_logger import JournalLogger

    d = date(2026, 9, 1)
    j = JournalLogger(log_dir=str(tmp_path))
    j.log_order_ids(
        instrument="MNQ", session="new_york",
        order_ids={"entry": "E1", "stop": "S1", "target": "T1"},
        client_order_id="AFS-identity-test", for_date=d,
    )
    row = next(row for row in j.read_day(d) if row.get("type") == "ORDER_IDS")
    assert row["client_order_id"] == "AFS-identity-test"
''',
    encoding="utf-8",
)

for raw in (
    "scripts/_chatgpt_apply_client_order_id.py",
    ".github/workflows/chatgpt-apply-client-order-id.yml",
):
    q = Path(raw)
    if q.exists():
        q.unlink()
