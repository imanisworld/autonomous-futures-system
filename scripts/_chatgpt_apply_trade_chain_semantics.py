from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


marker = '''def build_trade_chain_report(
'''
helper = '''def _signal_identity_key(entry: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return the current runner's exact signal identity fields when present.

    TRADE_INTENT/TRADE rows carry instrument + setup.strategy + decision-bar ts.
    Current no-fill OUTCOME rows carry the same instrument plus outcome.strategy
    and outcome.signal_timestamp. This is stronger than same-instrument FIFO and
    is available on current-format rows without changing the journal schema.
    """
    instrument = str(entry.get("instrument") or "").strip()
    if entry.get("type") == "OUTCOME":
        outcome = entry.get("outcome") or {}
        strategy = str(outcome.get("strategy") or "").strip()
        signal_ts = outcome.get("signal_timestamp")
    else:
        setup = entry.get("setup") or {}
        strategy = str(setup.get("strategy") or "").strip()
        signal_ts = entry.get("ts")
    parsed = parse_proof_ts(signal_ts)
    if not instrument or not strategy or parsed is None:
        return None
    return instrument, strategy, parsed.isoformat()


def _pair_cancelled_outcomes_to_intents(
    intents: list[dict[str, Any]],
    confirmed_trades: list[dict[str, Any]],
    cancelled_outcomes: list[dict[str, Any]],
    all_in_order: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Join current-format no-fill OUTCOMEs to TRADE_INTENT rows.

    Exact signal identity is authoritative when available. Historical rows that
    predate strategy/signal_timestamp diagnostics fall back to a latest-pending
    same-instrument intent queue. Confirmed TRADE rows consume their own intent
    first so a later cancellation can never be FIFO-guessed onto an older fill.
    """
    intent_ids = {id(row) for row in intents}
    confirmed_ids = {id(row) for row in confirmed_trades}
    cancelled_ids = {id(row) for row in cancelled_outcomes}

    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for intent in intents:
        key = _signal_identity_key(intent)
        if key is not None:
            by_key[key].append(intent)

    paired: dict[int, dict[str, Any]] = {}
    consumed_intents: set[int] = set()
    consumed_outcomes: set[int] = set()

    # A confirmed TRADE is the same current-format attempt as its prior intent,
    # not a second attempt. Mark the matching intent consumed before pairing
    # no-fill outcomes.
    for trade in confirmed_trades:
        key = _signal_identity_key(trade)
        candidates = [row for row in by_key.get(key, []) if id(row) not in consumed_intents] if key else []
        if len(candidates) == 1:
            consumed_intents.add(id(candidates[0]))

    # Current-format cancellation: exact signal identity first. If an identity
    # key is duplicated/ambiguous, fail closed by leaving the OUTCOME unmatched.
    for outcome in cancelled_outcomes:
        key = _signal_identity_key(outcome)
        candidates = [row for row in by_key.get(key, []) if id(row) not in consumed_intents] if key else []
        if len(candidates) == 1:
            intent = candidates[0]
            consumed_intents.add(id(intent))
            consumed_outcomes.add(id(outcome))
            paired[id(intent)] = outcome

    # Legacy fallback only: rows lacking exact signal identity. Use the most
    # recent still-pending intent for that instrument. This mirrors the actual
    # serialized runner flow better than oldest-first FIFO while keeping all
    # identity-bearing rows out of guesswork entirely.
    pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in all_in_order:
        instrument = str(entry.get("instrument") or "")
        if id(entry) in intent_ids:
            if id(entry) not in consumed_intents and _signal_identity_key(entry) is None:
                pending[instrument].append(entry)
        elif id(entry) in confirmed_ids:
            if _signal_identity_key(entry) is None and pending[instrument]:
                consumed_intents.add(id(pending[instrument].pop()))
        elif id(entry) in cancelled_ids:
            if id(entry) in consumed_outcomes or _signal_identity_key(entry) is not None:
                continue
            if pending[instrument]:
                intent = pending[instrument].pop()
                consumed_intents.add(id(intent))
                consumed_outcomes.add(id(entry))
                paired[id(intent)] = entry

    unmatched = [row for row in cancelled_outcomes if id(row) not in consumed_outcomes]
    return paired, unmatched


def build_trade_chain_report(
'''
replace_once("ops/project_check/trade_chain.py", marker, helper)

old_pairing = '''    attempts_all = [
        e for e in entries_no_errors
        if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "APPROVED"
    ]
    outcomes_all = [e for e in entries_no_errors if e.get("type") == "OUTCOME"]
    order_id_events_all = [e for e in entries_no_errors if e.get("type") == "ORDER_IDS"]
    risk_rejected = [e for e in entries_no_errors if e.get("decision") == "RISK_REJECTED" and is_new(e)]
    config_blocked = [e for e in entries_no_errors if e.get("decision") == "CONFIG_BLOCKED" and is_new(e)]

    outcome_by_attempt, unmatched_outcomes_all = _pair_fifo_by_instrument(
        attempts_all, outcomes_all, entries_no_errors
    )
    orderids_by_attempt, unmatched_order_ids_all = _pair_fifo_by_instrument(
        attempts_all, order_id_events_all, entries_no_errors
    )
'''
new_pairing = '''    attempts_all = [
        e for e in entries_no_errors
        if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "APPROVED"
    ]
    intents_all = [
        e for e in entries_no_errors
        if e.get("decision") == "TRADE_INTENT" and (e.get("risk_check") or {}).get("result") == "APPROVED"
    ]
    outcomes_all = [e for e in entries_no_errors if e.get("type") == "OUTCOME"]
    cancelled_outcomes_all = [
        e for e in outcomes_all
        if classify_outcome(e.get("outcome") or {}) == "cancelled_nofill"
    ]
    order_id_events_all = [e for e in entries_no_errors if e.get("type") == "ORDER_IDS"]
    risk_rejected = [e for e in entries_no_errors if e.get("decision") == "RISK_REJECTED" and is_new(e)]
    config_blocked = [e for e in entries_no_errors if e.get("decision") == "CONFIG_BLOCKED" and is_new(e)]

    cancelled_by_intent, unmatched_intent_cancellations = _pair_cancelled_outcomes_to_intents(
        intents_all, attempts_all, cancelled_outcomes_all, entries_no_errors
    )
    intent_cancel_outcome_ids = {id(row) for row in cancelled_by_intent.values()}
    outcomes_for_confirmed = [row for row in outcomes_all if id(row) not in intent_cancel_outcome_ids]
    outcome_by_attempt, unmatched_confirmed_outcomes = _pair_fifo_by_instrument(
        attempts_all, outcomes_for_confirmed, entries_no_errors
    )
    unmatched_outcomes_all = unmatched_confirmed_outcomes + unmatched_intent_cancellations
    orderids_by_attempt, unmatched_order_ids_all = _pair_fifo_by_instrument(
        attempts_all, order_id_events_all, entries_no_errors
    )
'''
replace_once("ops/project_check/trade_chain.py", old_pairing, new_pairing)

replace_once(
    "ops/project_check/trade_chain.py",
    '    attempts = [a for a in attempts_all if is_new(a)]\n\n    resolved_fills',
    '    confirmed_attempts = [a for a in attempts_all if is_new(a)]\n    cancellation_intents = [a for a in intents_all if id(a) in cancelled_by_intent and is_new(a)]\n    attempts = confirmed_attempts + cancellation_intents\n\n    resolved_fills',
)

old_loop = '''    for attempt in attempts:
        outcome = outcome_by_attempt.get(id(attempt))
        classified = _classify_row(attempt, outcome)
'''
new_loop = '''    for attempt in attempts:
        outcome = cancelled_by_intent.get(id(attempt)) or outcome_by_attempt.get(id(attempt))
        classified = _classify_row(attempt, outcome)
'''
replace_once("ops/project_check/trade_chain.py", old_loop, new_loop)

old_carry = '''    for attempt in attempts_all:
        if is_new(attempt):
            continue  # already handled above as a "new" attempt
        outcome = outcome_by_attempt.get(id(attempt))
        if outcome is None or not is_new(outcome):
            continue  # still unresolved, or was already resolved before the checkpoint
        classified = _classify_row(attempt, outcome)
        setup = classified.pop("_setup")
        category = classified.pop("_category")
        classified.pop("_bucket")
        classified["fills_this_run"] = category in ("filled_win_loss", "breakeven")
        carryover_resolutions.append(classified)
'''
new_carry = '''    for attempt in attempts_all + intents_all:
        if is_new(attempt):
            continue  # already handled above as a "new" attempt when applicable
        outcome = cancelled_by_intent.get(id(attempt)) or outcome_by_attempt.get(id(attempt))
        if outcome is None or not is_new(outcome):
            continue  # still unresolved/suppressed, or was already resolved before checkpoint
        classified = _classify_row(attempt, outcome)
        classified.pop("_setup")
        category = classified.pop("_category")
        classified.pop("_bucket")
        classified["fills_this_run"] = category in ("filled_win_loss", "breakeven")
        carryover_resolutions.append(classified)
'''
replace_once("ops/project_check/trade_chain.py", old_carry, new_carry)

replace_once(
    "ops/project_check/trade_chain.py",
    '            "attempts": len(attempts),\n',
    '            "attempts": len(attempts),\n            "confirmed_trade_attempts": len(confirmed_attempts),\n            "no_fill_intent_attempts": len(cancellation_intents),\n',
)
replace_once(
    "ops/project_check/trade_chain.py",
    '                "attempts = fills + cancellations + needs_broker_verification + "\n',
    '                "attempts (confirmed TRADEs + matched no-fill TRADE_INTENTs) = "\n                "fills + cancellations + needs_broker_verification + "\n',
)

# ---- tests ----
replace_once(
    "tests/test_project_check_trade_chain.py",
    '''def _outcome(ts: str, *, instrument: str = "MNQ", result: str = "WIN", exit_reason: str = "target hit", pnl=25.0) -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "type": "OUTCOME",
        "outcome": {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl},
    }
''',
    '''def _intent(ts: str, *, instrument: str = "MNQ", strategy: str = "orb_breakout") -> dict:
    row = _trade(ts, instrument=instrument, strategy=strategy)
    row["decision"] = "TRADE_INTENT"
    return row


def _outcome(
    ts: str,
    *,
    instrument: str = "MNQ",
    result: str = "WIN",
    exit_reason: str = "target hit",
    pnl=25.0,
    strategy: str | None = None,
    signal_timestamp: str | None = None,
) -> dict:
    outcome = {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl}
    if strategy is not None:
        outcome["strategy"] = strategy
    if signal_timestamp is not None:
        outcome["signal_timestamp"] = signal_timestamp
    return {"ts": ts, "instrument": instrument, "type": "OUTCOME", "outcome": outcome}
''',
)

old_clean = '''            _trade("2026-07-01T14:00:00Z"),
            _outcome("2026-07-01T14:30:00Z", result="WIN", pnl=25.0),
            _trade("2026-07-01T15:00:00Z"),
            _outcome("2026-07-01T15:10:00Z", result="CANCELLED", exit_reason="IOC limit expired"),
'''
new_clean = '''            _trade("2026-07-01T14:00:00Z"),
            _outcome("2026-07-01T14:30:00Z", result="WIN", pnl=25.0),
            _intent("2026-07-01T15:00:00Z"),
            _outcome(
                "2026-07-01T15:10:00Z",
                result="CANCELLED",
                exit_reason="IOC limit expired",
                strategy="orb_breakout",
                signal_timestamp="2026-07-01T15:00:00Z",
            ),
'''
replace_once("tests/test_project_check_trade_chain.py", old_clean, new_clean)
replace_once(
    "tests/test_project_check_trade_chain.py",
    '    assert s["attempts"] == 2\n    assert s["fills"] == 1\n',
    '    assert s["attempts"] == 2\n    assert s["confirmed_trade_attempts"] == 1\n    assert s["no_fill_intent_attempts"] == 1\n    assert s["fills"] == 1\n',
)

append = '''\n\ndef test_current_format_cancel_does_not_pair_to_older_confirmed_trade(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            _outcome("2026-07-01T14:30:00Z", result="WIN"),
            # A suppressed intent may exist with no terminal OUTCOME. It must
            # not steal the later broker-attempt cancellation.
            _intent("2026-07-01T14:45:00Z"),
            _intent("2026-07-01T15:00:00Z"),
            _outcome(
                "2026-07-01T15:00:05Z",
                result="CANCELLED",
                exit_reason="execution_failed:CANCELLED",
                strategy="orb_breakout",
                signal_timestamp="2026-07-01T15:00:00Z",
            ),
        ],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    assert report["summary"]["fills"] == 1
    assert report["summary"]["cancellations"] == 1
    assert report["summary"]["unmatched_outcomes"] == 0
    assert report["summary"]["orphans"] == 0


def test_legacy_trade_cancel_still_reconciles(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            _outcome("2026-07-01T14:05:00Z", result="CANCELLED", exit_reason="legacy cancel"),
        ],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    assert report["summary"]["attempts"] == 1
    assert report["summary"]["cancellations"] == 1
    assert report["summary"]["unmatched_outcomes"] == 0


def test_cancelled_intent_before_checkpoint_resolves_as_carryover(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _intent("2026-07-01T10:00:00Z"),
            _outcome(
                "2026-07-01T13:00:00Z",
                result="CANCELLED",
                strategy="orb_breakout",
                signal_timestamp="2026-07-01T10:00:00Z",
            ),
        ],
    )
    report = build_trade_chain_report(
        journal_dir=journal_dir,
        repo_root=tmp_path,
        since_ts="2026-07-01T12:00:00Z",
        use_checkpoint=False,
    )
    assert report["status"] == "PASS"
    assert report["summary"]["attempts"] == 0
    assert report["summary"]["carryover_resolutions"] == 1
    assert report["detail"]["carryover_resolutions"][0]["category"] == "cancelled_nofill"
'''
p = Path("tests/test_project_check_trade_chain.py")
p.write_text(p.read_text(encoding="utf-8") + append, encoding="utf-8")

for raw in (
    "scripts/_chatgpt_apply_trade_chain_semantics.py",
    ".github/workflows/chatgpt-apply-trade-chain-semantics.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
