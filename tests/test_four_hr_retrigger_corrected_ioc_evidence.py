from scripts.four_hr_retrigger_corrected_ioc_evidence import classify, summarize


def _trade(pnl, direction="LONG"):
    return {
        "result": "WIN" if pnl > 0 else "LOSS",
        "gross_pnl": pnl + 1.48,
        "net_pnl": pnl,
        "direction": direction,
        "bar_ts": "2025-01-01T15:00:00+00:00",
    }


def _cell(net, resolved=40, top3_removed=1):
    return {
        "overall": {
            "net_pnl": net,
            "resolved": resolved,
            "net_after_removing_top3": top3_removed,
        },
        "first_half": {"net_pnl": 1},
        "second_half": {"net_pnl": 1},
    }


def test_summarize_reports_cost_drawdown_and_fat_tail():
    result = summarize([_trade(10), _trade(-4), _trade(8), _trade(-3)])
    assert result["resolved"] == 4
    assert result["net_pnl"] == 11
    assert result["gross_pnl"] == 16.92
    assert result["commission_total"] == 5.92
    assert result["profit_factor"] == 2.571
    assert result["max_drawdown"] == 4
    assert result["largest_loss"] == -4
    assert result["net_after_removing_top3"] == -7


def test_classify_promising_requires_every_robustness_gate():
    variants = {str(tick): _cell(10) for tick in (1, 2, 3, 4)}
    assert classify(variants)["verdict"] == "PROMISING BUT UNPROVEN"
    variants["2"]["second_half"]["net_pnl"] = -1
    assert classify(variants)["verdict"] == "OVERFIT"


def test_classify_wait_precedes_positive_headline():
    variants = {str(tick): _cell(10, resolved=12) for tick in (1, 2, 3, 4)}
    assert classify(variants)["verdict"] == "WAIT"


def test_classify_broken_on_negative_baseline():
    variants = {str(tick): _cell(10) for tick in (1, 2, 3, 4)}
    variants["2"]["overall"]["net_pnl"] = -1
    assert classify(variants)["verdict"] == "BROKEN"
