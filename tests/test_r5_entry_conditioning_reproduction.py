from scripts.r5_entry_conditioning_reproduction import barrier_result, current_bracket, touches


def _bar(low, high):
    return {"low": low, "high": high}


def _candidate(direction="LONG", entry=100.0, stop=99.0, target=102.0):
    return {
        "candidate_key": "test",
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
    }


def test_fill_requires_entry_inside_bar_range_not_gap_over():
    assert touches(_bar(100.0, 101.0), 100.0)
    assert not touches(_bar(101.0, 102.0), 100.0)


def test_same_bar_symmetric_ambiguity_is_bad_first():
    assert barrier_result([_bar(98.0, 102.0)], "LONG", 101.0, 99.0) == "bad"
    assert barrier_result([_bar(98.0, 102.0)], "SHORT", 99.0, 101.0) == "bad"


def test_current_bracket_gets_new_horizon_from_fill_bar():
    bars = [
        _bar(95.0, 96.0),
        _bar(95.0, 96.0),  # signal index
        *[_bar(103.0, 104.0) for _ in range(15)],
        _bar(99.5, 100.5),  # fills on 16th search bar
        _bar(101.0, 102.5),  # target after fill, outside signal+16 window
    ]
    gross, fill_index, outcome = current_bracket(_candidate(), bars, 1)
    assert fill_index == 17
    assert outcome == "target"
    assert gross == 2.0


def test_current_bracket_stop_first_on_fill_bar():
    bars = [_bar(100.0, 100.0), _bar(98.0, 103.0)]
    gross, fill_index, outcome = current_bracket(_candidate(), bars, 0)
    assert fill_index == 1
    assert outcome == "stop"
    assert gross == -1.0
