from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "ops/project_check/trade_chain.py"
replace_once(
    path,
    '''    return paired, unmatched


def _signal_identity_key(entry: dict[str, Any]) -> tuple[str, str, str] | None:
''',
    '''    return paired, unmatched


def _client_order_id(entry: dict[str, Any]) -> str | None:
    """Return the persisted broker client identity when a row has one."""
    if entry.get("type") == "OUTCOME":
        raw = (entry.get("outcome") or {}).get("client_order_id")
    else:
        raw = entry.get("client_order_id")
    value = str(raw or "").strip()
    return value or None


def _pair_by_client_id_then_legacy_fifo(
    anchors: list[dict[str, Any]],
    events: list[dict[str, Any]],
    all_in_order: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Pair exact persisted client ids first; FIFO only when BOTH rows are legacy.

    A row that carries a client id is never guessed onto an identity-less row.
    Duplicate/ambiguous ids fail closed by remaining unmatched/unresolved.
    """
    anchors_by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    events_by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in anchors:
        cid = _client_order_id(row)
        if cid:
            anchors_by_client[cid].append(row)
    for row in events:
        cid = _client_order_id(row)
        if cid:
            events_by_client[cid].append(row)

    paired: dict[int, dict[str, Any]] = {}
    consumed_events: set[int] = set()
    for cid, anchor_rows in anchors_by_client.items():
        event_rows = events_by_client.get(cid, [])
        if len(anchor_rows) == 1 and len(event_rows) == 1:
            paired[id(anchor_rows[0])] = event_rows[0]
            consumed_events.add(id(event_rows[0]))

    # Historical fallback is deliberately narrower than the old behavior:
    # only rows with no persisted client identity on either side may FIFO-pair.
    legacy_anchors = [
        row for row in anchors
        if _client_order_id(row) is None and id(row) not in paired
    ]
    legacy_events = [
        row for row in events
        if _client_order_id(row) is None and id(row) not in consumed_events
    ]
    legacy_pairs, legacy_unmatched = _pair_fifo_by_instrument(
        legacy_anchors, legacy_events, all_in_order
    )
    paired.update(legacy_pairs)
    consumed_events.update(id(row) for row in legacy_pairs.values())

    unmatched = [row for row in events if id(row) not in consumed_events]
    # Preserve the legacy helper's ordering semantics for identity-less rows;
    # the comprehension above already includes those same unmatched rows once.
    assert {id(row) for row in legacy_unmatched} <= {id(row) for row in unmatched}
    return paired, unmatched


def _signal_identity_key(entry: dict[str, Any]) -> tuple[str, str, str] | None:
''',
)

replace_once(
    path,
    '''    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for intent in intents:
        key = _signal_identity_key(intent)
        if key is not None:
            by_key[key].append(intent)

    paired: dict[int, dict[str, Any]] = {}
''',
    '''    by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for intent in intents:
        client_id = _client_order_id(intent)
        if client_id is not None:
            by_client[client_id].append(intent)
        key = _signal_identity_key(intent)
        if key is not None:
            by_key[key].append(intent)

    paired: dict[int, dict[str, Any]] = {}
''',
)
replace_once(
    path,
    '''    for trade in confirmed_trades:
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
''',
    '''    for trade in confirmed_trades:
        client_id = _client_order_id(trade)
        if client_id is not None:
            candidates = [
                row for row in by_client.get(client_id, [])
                if id(row) not in consumed_intents
            ]
        else:
            key = _signal_identity_key(trade)
            candidates = [
                row for row in by_key.get(key, [])
                if id(row) not in consumed_intents and _client_order_id(row) is None
            ] if key else []
        if len(candidates) == 1:
            consumed_intents.add(id(candidates[0]))

    # Current-format cancellation: persisted broker identity wins. Signal
    # identity remains the exact fallback for pre-client-id current rows. An
    # identity-bearing row is never allowed into legacy/FIFO guesswork.
    for outcome in cancelled_outcomes:
        client_id = _client_order_id(outcome)
        if client_id is not None:
            candidates = [
                row for row in by_client.get(client_id, [])
                if id(row) not in consumed_intents
            ]
        else:
            key = _signal_identity_key(outcome)
            candidates = [
                row for row in by_key.get(key, [])
                if id(row) not in consumed_intents and _client_order_id(row) is None
            ] if key else []
        if len(candidates) == 1:
            intent = candidates[0]
            consumed_intents.add(id(intent))
            consumed_outcomes.add(id(outcome))
            paired[id(intent)] = outcome
''',
)
replace_once(
    path,
    '''        if id(entry) in intent_ids:
            if id(entry) not in consumed_intents and _signal_identity_key(entry) is None:
                pending[instrument].append(entry)
        elif id(entry) in confirmed_ids:
            if _signal_identity_key(entry) is None and pending[instrument]:
                consumed_intents.add(id(pending[instrument].pop()))
        elif id(entry) in cancelled_ids:
            if id(entry) in consumed_outcomes or _signal_identity_key(entry) is not None:
                continue
''',
    '''        if id(entry) in intent_ids:
            if (
                id(entry) not in consumed_intents
                and _client_order_id(entry) is None
                and _signal_identity_key(entry) is None
            ):
                pending[instrument].append(entry)
        elif id(entry) in confirmed_ids:
            if (
                _client_order_id(entry) is None
                and _signal_identity_key(entry) is None
                and pending[instrument]
            ):
                consumed_intents.add(id(pending[instrument].pop()))
        elif id(entry) in cancelled_ids:
            if (
                id(entry) in consumed_outcomes
                or _client_order_id(entry) is not None
                or _signal_identity_key(entry) is not None
            ):
                continue
''',
)
replace_once(
    path,
    '''    outcome_by_attempt, unmatched_confirmed_outcomes = _pair_fifo_by_instrument(
        attempts_all, outcomes_for_confirmed, entries_no_errors
    )
''',
    '''    outcome_by_attempt, unmatched_confirmed_outcomes = _pair_by_client_id_then_legacy_fifo(
        attempts_all, outcomes_for_confirmed, entries_no_errors
    )
''',
)
replace_once(
    path,
    '''    orderids_by_attempt, unmatched_order_ids_all = _pair_fifo_by_instrument(
        attempts_all, order_id_events_all, entries_no_errors
    )
''',
    '''    orderids_by_attempt, unmatched_order_ids_all = _pair_by_client_id_then_legacy_fifo(
        attempts_all, order_id_events_all, entries_no_errors
    )
''',
)

# Tests for exact-ID precedence and fail-closed transition behavior.
tp = Path("tests/test_project_check_trade_chain.py")
t = tp.read_text(encoding="utf-8")
t += '''\n\ndef test_client_order_id_prevents_confirmed_outcome_fifo_misattribution(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    first = _trade("2026-07-01T14:00:00Z")
    first["client_order_id"] = "AFS-A"
    second = _trade("2026-07-01T15:00:00Z")
    second["client_order_id"] = "AFS-B"
    outcome_b = _outcome("2026-07-01T15:30:00Z", result="WIN")
    outcome_b["outcome"]["client_order_id"] = "AFS-B"
    _write_jsonl(journal_dir / "journal_2026-07-01.jsonl", [first, second, outcome_b])

    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    assert report["summary"]["fills"] == 1
    assert report["summary"]["unverified_open_attempts"] == 1
    assert report["detail"]["resolved_fills"][0]["trade_ts"] == "2026-07-01T15:00:00Z"


def test_identity_bearing_outcome_never_falls_back_to_legacy_trade(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    legacy_trade = _trade("2026-07-01T14:00:00Z")
    identified_outcome = _outcome("2026-07-01T14:30:00Z", result="WIN")
    identified_outcome["outcome"]["client_order_id"] = "AFS-new-format"
    _write_jsonl(journal_dir / "journal_2026-07-01.jsonl", [legacy_trade, identified_outcome])

    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "FAIL"
    assert report["summary"]["unmatched_outcomes"] == 1
    assert report["summary"]["fills"] == 0


def test_client_order_id_disambiguates_current_no_fill_intents(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    a = _intent("2026-07-01T15:00:00Z")
    a["client_order_id"] = "AFS-A"
    b = _intent("2026-07-01T15:00:00Z")
    b["client_order_id"] = "AFS-B"
    cancelled = _outcome(
        "2026-07-01T15:00:05Z", result="CANCELLED",
        exit_reason="IOC limit expired", strategy="orb_breakout",
        signal_timestamp="2026-07-01T15:00:00Z",
    )
    cancelled["outcome"]["client_order_id"] = "AFS-B"
    _write_jsonl(journal_dir / "journal_2026-07-01.jsonl", [a, b, cancelled])

    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    assert report["summary"]["cancellations"] == 1
    assert report["summary"]["unmatched_outcomes"] == 0


def test_order_ids_pair_by_client_id_before_legacy_fifo() -> None:
    from ops.project_check.trade_chain import _pair_by_client_id_then_legacy_fifo

    a = _trade("2026-07-01T14:00:00Z")
    a["client_order_id"] = "AFS-A"
    b = _trade("2026-07-01T15:00:00Z")
    b["client_order_id"] = "AFS-B"
    order_b = _order_ids("2026-07-01T15:00:05Z", order_id="B-entry")
    order_b["client_order_id"] = "AFS-B"
    paired, unmatched = _pair_by_client_id_then_legacy_fifo([a, b], [order_b], [a, b, order_b])
    assert id(a) not in paired
    assert paired[id(b)] is order_b
    assert unmatched == []
'''
tp.write_text(t, encoding="utf-8")

for raw in (
    "scripts/_chatgpt_apply_trade_chain_client_id.py",
    ".github/workflows/chatgpt-apply-trade-chain-client-id.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
