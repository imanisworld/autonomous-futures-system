from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


runner_old = '''def _paper_broker(starting_balance: float, cfg: Optional[SystemConfig]) -> PaperBroker:
    """PaperBroker wired with the configured fill-realism settings."""
    return PaperBroker(
        starting_balance=starting_balance,
        slippage_ticks=float(getattr(cfg, "fill_slippage_ticks", 0.0) or 0.0),
        pessimistic_both_hit=bool(getattr(cfg, "fill_pessimistic_both_hit", False)),
        runner_mode=bool(getattr(cfg, "runner_mode", False)),
        runner_activation_r=float(getattr(cfg, "runner_activation_r", 1.0) or 1.0),
        runner_trail_r=float(getattr(cfg, "runner_trail_r", 0.5) or 0.5),
    )
'''

runner_new = '''def _paper_broker(starting_balance: float, cfg: Optional[SystemConfig]) -> PaperBroker:
    """PaperBroker wired with the configured fill-realism settings."""
    return PaperBroker(
        starting_balance=starting_balance,
        slippage_ticks=float(getattr(cfg, "fill_slippage_ticks", 0.0) or 0.0),
        pessimistic_both_hit=bool(getattr(cfg, "fill_pessimistic_both_hit", False)),
        breakeven_at_1r=bool(getattr(cfg, "breakeven_at_1r", False)),
        runner_mode=bool(getattr(cfg, "runner_mode", False)),
        runner_activation_r=float(getattr(cfg, "runner_activation_r", 1.0) or 1.0),
        runner_trail_r=float(getattr(cfg, "runner_trail_r", 0.5) or 0.5),
        entry_fill_model=str(getattr(cfg, "entry_fill_model", "market") or "market"),
        entry_tolerance_ticks_by_root=dict(
            getattr(cfg, "entry_tolerance_ticks_by_root", {}) or {}
        ),
    )
'''

replace_once("webhook/runner.py", runner_old, runner_new)

marker = '''\n\ndef test_stop_multiplier_per_instrument_field_present():\n'''
insert = '''\n\ndef test_fill_model_fields_flow_to_normal_paper_broker(monkeypatch):
    monkeypatch.setenv("BREAKEVEN_AT_1R", "true")
    monkeypatch.setenv("ENTRY_FILL_MODEL", "ioc_limit")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "7")
    c = load_config()
    b = _paper_broker(1500.0, c)

    assert b._breakeven_at_1r is True
    assert b._entry_fill_model == "ioc_limit"
    assert b._entry_tol_by_root.get("MNQ") == 7.0


def test_stop_multiplier_per_instrument_field_present():
'''
replace_once("tests/test_runner_wiring.py", marker, insert)

# Remove branch-only write scaffolding before committing the actual fix.
for raw in (
    "docs/.write-probe",
    "scripts/_chatgpt_apply_futures_parity.py",
    ".github/workflows/chatgpt-apply-futures-parity.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
