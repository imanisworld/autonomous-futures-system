from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "audit322",
    Path(__file__).parents[1] / "scripts" / "322_trigger_timing_ab_2026_09_18.py",
)
m = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(m)


def test_classification_survives_only_when_both_halves_positive():
    assert m.classify({"net": 10, "h1_net": 5, "h2_net": 5}) == (
        "TIMING EDGE SURVIVES / PROMISING BUT UNPROVEN"
    )
    assert m.classify({"net": 10, "h1_net": -1, "h2_net": 11}) == (
        "TIMING RESULT UNSTABLE / WAIT"
    )
    assert m.classify({"net": 0, "h1_net": 1, "h2_net": -1}) == (
        "TIMING EDGE DOES NOT SURVIVE"
    )


def test_reproduction_constants_match_erratum_2026_09_24():
    # Original sealed nets stay in the prereg and in the harness comment:
    # plan 2532.66 / H1 1383.34, IOC 1859.40 / H1 1068.68.
    assert m.EXPECTED_N == 34
    assert m.IOC_TOLERANCE == 32.0
    assert m.EXPECTED_PLAN == {
        "filled": 33, "net": 2293.64, "h1_net": 1144.32, "h2_net": 1149.32,
    }
    assert m.EXPECTED_IOC == {
        "filled": 20, "net": 1622.38, "h1_net": 831.66, "h2_net": 790.72,
    }


def test_reproduction_gate_accepts_frozen_values():
    m.check_repro(dict(m.EXPECTED_PLAN), dict(m.EXPECTED_IOC))


def test_reproduction_gate_rejects_drift():
    bad = dict(m.EXPECTED_IOC)
    bad["filled"] = 19
    try:
        m.check_repro(dict(m.EXPECTED_PLAN), bad)
    except RuntimeError as exc:
        assert "ioc filled" in str(exc)
    else:
        raise AssertionError("drift must fail closed")
