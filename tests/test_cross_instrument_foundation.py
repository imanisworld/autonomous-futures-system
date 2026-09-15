import pytest

from config.futures_contracts import contract_economics
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from webhook.state_builder import futures_root, normalize_instrument


@pytest.mark.parametrize('root', ['MNQ', 'MES', 'MGC', 'MCL', 'M2K', 'MBT'])
@pytest.mark.parametrize('suffix', ['', '1!', 'Z6', 'H2027', 'M26'])
def test_exact_contract_normalization(root, suffix):
    symbol = f'CME:{root}{suffix}'
    assert futures_root(symbol) == root
    assert normalize_instrument(symbol) == root


@pytest.mark.parametrize('symbol', ['MK2', 'M2KXX', 'MBTX', 'MBT2026', 'M2KZ2026extra', 'UNKNOWN'])
def test_unrecognized_symbols_do_not_gain_a_root(symbol):
    assert futures_root(symbol) is None


def order(root, tick=0.25):
    return BracketOrder(instrument=root, direction='LONG', entry=1000.0,
                        stop=1000 - 10 * tick, target=1000 + 10 * tick, contracts=1, rr_ratio=1.0, strategy="test")


@pytest.mark.parametrize('root,tick,value', [
    ('MNQ', .25, .5), ('MES', .25, 1.25), ('MGC', .1, 1),
    ('MCL', .01, 1), ('M2K', .1, .5), ('MBT', 5, .5),
])
@pytest.mark.parametrize('exit_kind', ['target', 'stop', 'force'])
def test_outright_contract_economics(root, tick, value, exit_kind):
    assert contract_economics(root) == (tick, value)
    broker = PaperBroker(slippage_ticks=1, pessimistic_both_hit=True)
    fill = broker.execute_bracket(order(root, tick))
    assert fill.entry_price == pytest.approx(1000 + tick)
    if exit_kind == 'force':
        result = broker.force_resolve('WIN', 1000 + 10 * tick)
        expected = 9 * value
    elif exit_kind == 'target':
        result = broker.resolve_position(NextBarOHLC(high=1000 + 11 * tick, low=1000))
        expected = 9 * value
    else:
        # Both touched: pessimistic stop, with an adverse tick on each market leg.
        result = broker.resolve_position(NextBarOHLC(high=1000 + 11 * tick, low=1000 - 11 * tick))
        expected = -12 * value
    assert result.pnl_dollars == pytest.approx(expected)


@pytest.mark.parametrize('root', ['UNKNOWN', 'MK2', 'M2KZ2026', '', 'MNQXXX'])
@pytest.mark.parametrize('model', ['market', 'ioc_limit', 'stop_market'])
def test_unknown_economics_rejected_before_mutation(root, model):
    broker = PaperBroker(entry_fill_model=model)
    with pytest.raises(ValueError, match='Unsupported paper instrument'):
        broker.execute_bracket(order(root), market_price=1000)
    with pytest.raises(ValueError, match='Unsupported paper instrument'):
        broker.restore_position(root, 'LONG', 1000, 990, 1010)
    with pytest.raises(ValueError, match='Unsupported paper instrument'):
        broker.restore_pending_stop_entry(order(root))
    assert broker.get_position() is None
    assert not broker.has_pending_entry()
    assert broker.get_account_balance() == 1500
    assert broker.resolve_position(NextBarOHLC(high=1010, low=990)) is None


def test_m2k_ioc_tolerance_retains_digit():
    broker = PaperBroker(entry_fill_model='ioc_limit', entry_tolerance_ticks_by_root={'M2K': 2})
    assert broker.execute_bracket(order('M2K', .1), market_price=1000.1).result == 'OPEN'


def test_missing_economic_metadata_rejected(monkeypatch):
    import config.futures_contracts as contracts
    monkeypatch.setattr(contracts, 'TICK_VALUE', {})
    with pytest.raises(ValueError, match='Unsupported paper instrument'):
        PaperBroker().execute_bracket(order('MNQ'))


@pytest.mark.parametrize('root,tick,value', [('M2K', .1, .5), ('MBT', 5, .5), ('MGC', .1, 1), ('MCL', .01, 1)])
def test_post_fill_risk_uses_same_proven_economics(root, tick, value):
    from dataclasses import replace
    from execution.post_fill_validation import validate_post_fill
    bracket = replace(order(root, tick), post_fill_validation_required=True,
                      min_rr_ratio=0, max_dollar_risk=10 * value)
    validation = validate_post_fill(bracket, bracket.entry)
    assert validation.accepted
    assert validation.actual_dollar_risk == pytest.approx(10 * value)
    assert validation.tick_size == tick
    assert validation.tick_value == value
    assert PaperBroker().execute_bracket(bracket).result == 'OPEN'
    rejected = replace(bracket, max_dollar_risk=9 * value)
    assert PaperBroker().execute_bracket(rejected).result == 'CANCELLED'


def test_real_book_and_external_ingest_allowlists_unchanged():
    from config.settings import load_config
    from webhook.app import _INGEST_FUTURES_ROOTS
    from context.wide_stop_ledger_paper import ledger_for
    config = load_config('risk_rules.yaml')
    assert config.allowed_instruments == ['MNQ']
    assert config.enabled_concepts == ['orb_breakout']
    assert not config.live_trading_enabled
    assert _INGEST_FUTURES_ROOTS == ('MNQ', 'MES', 'ES', 'NQ', 'MGC', 'MCL')
    for root in ('M2K', 'MGC', 'MCL', 'MBT'):
        assert root not in config.allowed_instruments
        assert ledger_for(root, 'strat_4hr_retrigger') is None
        assert ledger_for(root, 'strat_322_first_live') is None
