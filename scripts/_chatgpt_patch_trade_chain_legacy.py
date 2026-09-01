from pathlib import Path

p = Path("ops/project_check/trade_chain.py")
text = p.read_text(encoding="utf-8")
old = '''    unmatched_outcomes_all = unmatched_confirmed_outcomes + unmatched_intent_cancellations
'''
new = '''    confirmed_outcome_ids = {id(row) for row in outcome_by_attempt.values()}
    unmatched_intent_cancellations = [
        row for row in unmatched_intent_cancellations
        if id(row) not in confirmed_outcome_ids
    ]
    unmatched_outcomes_all = unmatched_confirmed_outcomes + unmatched_intent_cancellations
'''
if text.count(old) != 1:
    raise SystemExit("expected one unmatched-outcome merge point")
p.write_text(text.replace(old, new, 1), encoding="utf-8")

# The compatibility fixture must use a reason the existing no-fill taxonomy
# classifies as CANCELLED/no-fill, otherwise the test is asking the checker to
# reinterpret an intentionally unknown legacy outcome.
tp = Path("tests/test_project_check_trade_chain.py")
t = tp.read_text(encoding="utf-8")
old_reason = 'exit_reason="legacy cancel"'
if t.count(old_reason) != 1:
    raise SystemExit("expected one legacy cancellation fixture")
tp.write_text(t.replace(old_reason, 'exit_reason="IOC limit expired"', 1), encoding="utf-8")

Path(__file__).unlink()
