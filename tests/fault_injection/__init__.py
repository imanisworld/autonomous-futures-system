"""Fault-injection suite, phase 1: broker position truth (#950 audit gaps 1-4).

Every test drives the real ``TradovateBroker`` / runner code against an
in-memory fake order book (``_harness.FakeBook``). There is no network, box or
Tradovate contact.

Marker policy (preregistered):
- A known-defect test asserts the SAFE behaviour at full strength and carries
  ``@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KNOWN DEFECT FI-n")``.
  When a fix lands, the test XPASSes, strict mode turns CI red, and the fix PR
  must delete the marker.
- Setup mistakes raise ``FaultSetupError``, which is not an AssertionError, so a
  broken harness fails CI instead of hiding behind the xfail.
- No test asserts the unsafe behaviour as correct.
"""
