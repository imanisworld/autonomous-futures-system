"""Offline #1184 replay tests. HMAC keys are fixture bytes, NOT production secrets."""
from __future__ import annotations
import copy
import hashlib
import hmac
import json
from dataclasses import replace

import pytest

from options_evidence import authority_history as ah
from options_evidence.fitness import EVALUATOR_ACTOR

STRATEGY, EPOCH, PERSON = "options_122", "122-IEX-E1", "operator:example"
EVENT_KEY = b"test-only-event-key"
HEAD_KEY = b"test-only-head-key"
APPROVAL_KEY = b"test-only-approval-key"


def mac(key, value):
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def approval_token(row):
    return mac(APPROVAL_KEY, "|".join(
        (row["strategy"], row["epoch"], row["actor"],
         row["action"], row["approval_ref"], row["digest"])))


def row(seq, prev, action="GRANT", actor=PERSON, *, when=None, approval_ref=None):
    if action in ("GRANT", "RESTORE", "RETIRE") and approval_ref is None:
        approval_ref = f"APPROVAL-{seq}"
    r = dict(schema=ah.EVENT_SCHEMA, strategy=STRATEGY, epoch=EPOCH,
             seq=seq, event_id=f"{STRATEGY}/{EPOCH}/{seq}",
             at=when or f"2026-10-08T12:00:{seq:02d}+00:00",
             actor=actor, action=action, approval_ref=approval_ref,
             prev_digest=prev, digest="", event_proof="", approval_proof=None)
    r["digest"] = ah._event_digest(r)
    r["event_proof"] = mac(EVENT_KEY, r["digest"])
    if approval_ref is not None:
        r["approval_proof"] = approval_token(r)
    return r


def chain(actions):
    out = []
    for index, action in enumerate(actions, 1):
        actor = EVALUATOR_ACTOR if action == "REVOKE" else PERSON
        out.append(row(index, out[-1]["digest"] if out else ah.GENESIS_DIGEST,
                       action, actor))
    return out


def head_for(rows):
    h = dict(schema=ah.HEAD_SCHEMA, strategy=STRATEGY, epoch=EPOCH,
             last_seq=len(rows),
             last_digest=rows[-1]["digest"] if rows else ah.GENESIS_DIGEST,
             head_proof="")
    h["head_proof"] = mac(
        HEAD_KEY, f'{STRATEGY}|{EPOCH}|{h["last_seq"]}|{h["last_digest"]}')
    return h


def trust_for(rows, *, allow=frozenset({PERSON}), override=None):
    # Mock latest-head storage is DISTINCT from serialized rows in these tests.
    # A caller-chosen trust instance is NOT production authority.
    current = head_for(rows)
    def head_loader(strategy, epoch):
        assert (strategy, epoch) == (STRATEGY, EPOCH)
        return copy.deepcopy(override if override is not None else current)
    def verify_head(h):
        return h == current and hmac.compare_digest(
            h["head_proof"], mac(
                HEAD_KEY, f'{STRATEGY}|{EPOCH}|{h["last_seq"]}|{h["last_digest"]}'))
    return ah.ReplayTrust(
        load_latest_head=head_loader, verify_head=verify_head,
        verify_event=lambda r: hmac.compare_digest(
            r["event_proof"], mac(EVENT_KEY, r["digest"])),
        verify_approval=lambda r: hmac.compare_digest(
            r["approval_proof"], approval_token(r)),
        approved_humans=allow)


def replay(rows, *, trust=None):
    return ah.replay_history(
        rows, strategy=STRATEGY, epoch=EPOCH,
        trust=trust if trust is not None else trust_for(rows))


def refused(rows, reason, *, trust=None):
    with pytest.raises(ah.AuthorityHistoryError, match=reason):
        replay(rows, trust=trust)


def test_fixed_evaluator_principal_matches_existing_research_evaluator():
    assert ah.EVALUATOR == EVALUATOR_ACTOR


def test_no_trust_or_no_approved_human_cannot_grant():
    rows = chain(["GRANT"])
    with pytest.raises(ah.AuthorityHistoryError, match="independent_trust_not_configured"):
        ah.replay_history(rows, strategy=STRATEGY, epoch=EPOCH)
    refused(rows, "human_principal_not_authorized",
            trust=trust_for(rows, allow=frozenset()))


def test_grant_is_diagnostic_never_execution_authority():
    rows = chain(["GRANT"])
    out = replay(rows)
    assert out.grant_observed and out.checked_seq == 1
    assert not out.execution_authority and out.observer_enabled
    with pytest.raises(ah.AuthorityHistoryError, match="diagnostic cannot carry"):
        replace(out, execution_authority=True)


@pytest.mark.parametrize("actions,would_grant,retired", [
    (["GRANT", "REVOKE"], False, False),
    (["GRANT", "REVOKE", "RESTORE"], True, False),
    (["GRANT", "REVOKE", "RESTORE", "RETIRE"], False, True),
])
def test_legitimate_replay_does_not_grant_execution(actions, would_grant, retired):
    out = replay(chain(actions))
    assert out.grant_observed is would_grant
    assert out.retired is retired and out.observer_enabled
    assert out.execution_authority is False


@pytest.mark.parametrize("actions,why", [
    (["RESTORE"], "restore_without_previous_grant"),
    (["REVOKE", "GRANT"], "grant_not_permitted"),
    (["GRANT", "GRANT"], "grant_not_permitted"),
    (["GRANT", "RESTORE"], "restore_without_previous_grant"),
    (["GRANT", "REVOKE", "RESTORE", "RESTORE"], "restore_without_previous_grant"),
    (["GRANT", "RETIRE", "RESTORE"], "retired_epoch_is_final"),
])
def test_illegal_action_sequences_fail(actions, why):
    refused(chain(actions), why)


def test_inserted_forged_grant_rejected_despite_rehashed_chain():
    rows = chain(["GRANT", "REVOKE"])
    fake = row(2, rows[0]["digest"], "RESTORE")
    fake["event_proof"] = "0" * 64
    modified = [rows[0], fake]
    refused(modified, "event_not_authenticated", trust=trust_for(modified))


def test_truncated_revocation_and_replayed_head_detected():
    committed = chain(["GRANT", "REVOKE"])
    refused(committed[:1], "trusted_head_replay_or_truncation",
            trust=trust_for(committed))
    refused(committed[:1], "trusted_head_unverified",
            trust=trust_for(committed, override=head_for(committed[:1])))
    extra = chain(["GRANT", "REVOKE", "RESTORE"])
    refused(extra, "unanchored_extra_event", trust=trust_for(committed))


@pytest.mark.parametrize("field,value,reason", [
    ("seq", True, "event_seq_gap_or_reorder"),
    ("seq", 4, "event_seq_gap_or_reorder"),
    ("event_id", "other", "event_id_invalid"),
    ("prev_digest", "0" * 64, "event_chain_broken"),
    ("at", "2026-10-08T12:00:02", "event_timestamp_not_canonical"),
    ("at", "2026-10-08T12:00:02Z", "event_timestamp_not_canonical"),
    ("at", "2026-10-08T12:00:02+04:00", "event_timestamp_not_canonical"),
    ("at", "2026-10-08T12:00:02.000000+00:00", "event_timestamp_not_canonical"),
    ("action", "GRANT", "event_payload_modified"),
])
def test_tampered_fields_cannot_pass(field, value, reason):
    valid = chain(["GRANT", "REVOKE"])
    broken = copy.deepcopy(valid)
    broken[1][field] = value
    refused(broken, reason, trust=trust_for(valid))


def test_reordered_or_same_timestamp_fails_even_with_valid_signatures():
    valid = chain(["GRANT", "REVOKE"])
    broken = copy.deepcopy(valid)
    broken.reverse()
    refused(broken, "event_seq_gap_or_reorder", trust=trust_for(valid))
    same_time = [
        valid[0],
        row(2, valid[0]["digest"], "REVOKE", EVALUATOR_ACTOR, when=valid[0]["at"]),
    ]
    refused(same_time, "nonmonotonic_event_time")


@pytest.mark.parametrize("actor", [
    "Fitness_Evaluator", "fitness_evaluator\u200b", "operator:Admin",
    "OPERATOR:admin", "operator:admіn", "operator:admin\u200b",
    "operator:аdmin", "operator:a\u0301", "operator:admın",
])
def test_noncanonical_human_and_unicode_confusables_rejected(actor):
    events = [row(1, ah.GENESIS_DIGEST, actor=actor)]
    refused(events, "actor_not_canonical_human")


@pytest.mark.parametrize("action", ["GRANT", "RESTORE", "RETIRE"])
def test_evaluator_cannot_perform_human_actions(action):
    events = [row(1, ah.GENESIS_DIGEST, action, EVALUATOR_ACTOR)]
    refused(events, "evaluator_may_only_revoke")


def test_approval_reference_and_signature_are_verified():
    rows = chain(["GRANT"])
    rows[0]["approval_proof"] = "bad"
    refused(rows, "approval_not_authenticated")
    malformed = [row(1, ah.GENESIS_DIGEST, approval_ref="APPROVAL-\u200b1")]
    refused(malformed, "approval_ref_invalid")


def test_revoke_cannot_smuggle_approval_or_rewrite_signed_payload():
    rows = chain(["GRANT", "REVOKE"])
    rows[1]["approval_ref"] = "APPROVAL-2"
    refused(rows, "event_payload_modified")


def test_missing_invalid_or_unverified_head_fails_closed():
    rows = chain(["GRANT"])
    unavailable = replace(
        trust_for(rows), load_latest_head=lambda *_: (_ for _ in ()).throw(
            RuntimeError("offline")))
    refused(rows, "trusted_head_unavailable", trust=unavailable)
    refused(rows, "trusted_head_unverified",
            trust=replace(trust_for(rows), verify_head=lambda _: False))
    refused(rows, "trusted_head_invalid",
            trust=replace(trust_for(rows), load_latest_head=lambda *_: {}))


def test_serialized_reload_rejects_duplicate_keys_torn_tail_and_missing_fields(tmp_path):
    rows = chain(["GRANT", "REVOKE", "RESTORE"])
    path = tmp_path / "authority-events.jsonl"
    contents = b"".join(
        (json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for r in rows)
    path.write_bytes(contents)
    assert replay(ah.read_history_jsonl(path)).execution_authority is False
    assert path.read_bytes() == contents
    path.write_bytes(contents + b'{"incomplete":')
    with pytest.raises(ah.AuthorityHistoryError, match="truncated_line"):
        ah.read_history_jsonl(path)
    path.write_bytes(b'{"seq":1,"seq":2}\n')
    with pytest.raises(ah.AuthorityHistoryError, match="duplicate_json_key"):
        ah.read_history_jsonl(path)
    incomplete = copy.deepcopy(rows)
    incomplete[0].pop("event_proof")
    refused(incomplete, "event_fields_invalid", trust=trust_for(rows))


def test_empty_anchored_history_cannot_be_execution_authority():
    result = replay([])
    assert result.checked_seq == 0 and not result.grant_observed
    assert result.observer_enabled and not result.execution_authority


def test_no_runtime_or_order_integration_imports():
    import inspect
    source = inspect.getsource(ah)
    for banned in (
        "from execution", "import execution", "from risk", "from broker",
        "from options_manager", "from tradovate", "import requests",
        "subprocess.run", "os.system",
    ):
        assert banned not in source
