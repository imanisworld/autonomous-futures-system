import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from execution import evidence_identity as identity
from execution import forward_evidence_campaign as campaign
from ops.forward_campaign_report import build_report


def record(instrument='MNQ', epoch=None, **kwargs):
    return campaign.candidate_record(
        strategy='vwap_rejection', variant='observer', direction='LONG',
        signal_timestamp=kwargs.pop('signal_timestamp', '2026-08-13T14:30:00+00:00'),
        source_timeframe='15m', session='new_york', regime='TREND', market_condition='TRENDING',
        original_entry=100, original_stop=99, original_target=102,
        entry_policy='canonical_resting_entry', exit_policy='fixed_bracket',
        instrument=instrument, evidence_epoch=epoch, **kwargs,
    )


@pytest.fixture
def configured(tmp_path, monkeypatch):
    config = json.loads(identity.CONFIG_PATH.read_text())
    for instrument, epoch in [('MES', 'mes-a'), ('MES', 'mes-b'), ('MNQ', 'mnq-b')]:
        config['populations'].append(dict(strategy='vwap_rejection', variant='observer',
                                          instrument=instrument, evidence_epoch=epoch))
    path = tmp_path / 'test-only-config.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(identity, 'CONFIG_PATH', path)
    monkeypatch.setenv(campaign.ENV_NAME, campaign.CAMPAIGN_ID)
    return identity.configured_populations()


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def outcome(row):
    return campaign.outcome_record(
        {'campaign_record': {**row, 'hypothetical_fill_price': 100}},
        {'result': 'WIN', 'exit_price': 102, 'exit_ts': '2026-08-13T15:00:00+00:00'},
    )


def test_identity_scopes_even_reused_caller_event_ids():
    rows = [record(event_id='same'), record('MES', 'mes-a', event_id='same'),
            record('MES', 'mes-b', event_id='same'), record('MNQ', 'mnq-b', event_id='same')]
    assert len({r['candidate_id'] for r in rows}) == 4
    assert len({identity.record_key(r) for r in rows}) == 4


def test_no_new_population_is_configured_or_writable(tmp_path, monkeypatch):
    populations = identity.configured_populations()
    assert len(populations) == 5
    assert {key[1] for key in populations} == {'MNQ'}
    assert {key[3] for key in populations} == {identity.LEGACY_EPOCH}
    monkeypatch.setenv(campaign.ENV_NAME, campaign.CAMPAIGN_ID)
    row = record('MES', 'mes-a')
    for writer in (campaign.append_record, campaign.open_campaign_position):
        with pytest.raises(campaign.EvidenceValidationError, match='unconfigured'):
            writer(tmp_path, row)
    assert not (tmp_path / campaign.STATE_FILENAME).exists()
    assert not (tmp_path / campaign.EVIDENCE_FILENAME).exists()


def test_independent_positions_epochs_and_resolution(tmp_path, configured):
    rows = [record(), record('MES', 'mes-a', commission_dollars=1.48, slippage_ticks=1),
            record('MES', 'mes-b', commission_dollars=1.48, slippage_ticks=1)]
    for row in rows:
        assert campaign.open_campaign_position(tmp_path, row)
        assert not campaign.open_campaign_position(tmp_path, row)
    state = json.loads((tmp_path / campaign.STATE_FILENAME).read_text())
    assert len(state['positions']) == 3
    assert rows[0]['candidate_id'] in state['positions']  # unchanged legacy storage key
    bars = [{'ts': '2026-08-13T14:35:00+00:00', 'high': 103, 'low': 98}]
    resolved = campaign.resolve_canonical_positions(tmp_path, instrument='MES', evidence_epoch='mes-a',
                                                   bars=bars, current_bar_ts=bars[0]['ts'])
    assert len(resolved) == 1
    assert resolved[0]['instrument'] == 'MES'
    assert resolved[0]['evidence_epoch'] == 'mes-a'
    assert resolved[0]['terminal_state'] == 'LOSS'
    assert resolved[0]['gross_pnl_dollars'] == -5
    assert resolved[0]['net_pnl_dollars'] == -7.73
    remaining = json.loads((tmp_path / campaign.STATE_FILENAME).read_text())['positions']
    assert {identity.population_key(p['campaign_record'])[1:] for p in remaining.values()} == {
        ('MNQ', 'observer', identity.LEGACY_EPOCH), ('MES', 'observer', 'mes-b')}
    before = (tmp_path / campaign.STATE_FILENAME).read_bytes()
    with pytest.raises(campaign.EvidenceValidationError, match='bar population mismatch'):
        campaign.resolve_canonical_positions(tmp_path, instrument='MNQ',
            bars=[{**bars[0], 'instrument': 'MES'}], current_bar_ts=bars[0]['ts'])
    assert (tmp_path / campaign.STATE_FILENAME).read_bytes() == before


def test_same_population_blocks_overlap_but_another_epoch_does_not(tmp_path, configured):
    assert campaign.open_campaign_position(tmp_path, record('MES', 'mes-a'))
    assert not campaign.open_campaign_position(tmp_path, record('MES', 'mes-a', signal_timestamp='2026-08-13T14:45:00+00:00'))
    assert campaign.open_campaign_position(tmp_path, record('MES', 'mes-b'))


def test_legacy_state_and_journal_read_without_rewriting(tmp_path, configured):
    row = record()
    row.pop('instrument')  # only this exact frozen campaign/schema proves MNQ
    position = {'campaign_record': row, 'direction': 'LONG', 'entry': 100, 'stop': 99,
                'target': 102, 'signal_ts': row['signal_timestamp'], 'entry_ts': None}
    state_path = tmp_path / campaign.STATE_FILENAME
    state_path.write_text(json.dumps({'positions': {row['candidate_id']: position},
                                     'seen_candidate_ids': [row['candidate_id']]}))
    path = tmp_path / campaign.EVIDENCE_FILENAME
    write_rows(path, [row])
    old_bytes = path.read_bytes()
    report = build_report(path)
    population = next(p for p in report['populations'] if p['strategy'] == 'vwap_rejection' and p['instrument'] == 'MNQ' and p['evidence_epoch'] == identity.LEGACY_EPOCH)
    assert population['candidates'] == 1
    assert path.read_bytes() == old_bytes
    bars = [{'ts': '2026-08-13T14:35:00+00:00', 'high': 103, 'low': 98}]
    assert campaign.resolve_canonical_positions(tmp_path, instrument='MNQ', bars=bars, current_bar_ts=bars[0]['ts'])[0]['terminal_state'] == 'LOSS'
    assert path.read_bytes().startswith(old_bytes)
    assert not campaign.open_campaign_position(tmp_path, record())


@pytest.mark.parametrize('change', [
    {'instrument': 'MES'}, {'instrument': None}, {'campaign_id': 'arbitrary'},
    {'evidence_epoch': None}, {'instrument': 'MK2', 'evidence_epoch': 'x'},
])
def test_legacy_identity_is_not_guessed(change):
    row = record()
    row.update(change)
    with pytest.raises(ValueError):
        identity.population_key(row)


def test_cost_proof_cannot_be_inherited_from_mnq():
    with pytest.raises(campaign.EvidenceValidationError, match='commission and slippage'):
        outcome(record('MES', 'mes-a'))


def test_report_partitions_even_colliding_candidate_and_event_ids(tmp_path, configured):
    mnq = record(event_id='same')
    mes = record('MES', 'mes-a', event_id='same', commission_dollars=1.48, slippage_ticks=1)
    mes['candidate_id'] = mnq['candidate_id']  # imported evidence must still be isolated
    path = tmp_path / 'report.jsonl'
    write_rows(path, [mnq, mes, outcome(mes)])
    result = build_report(path)
    selected = {(p['instrument'], p['evidence_epoch']): p for p in result['populations'] if p['strategy'] == 'vwap_rejection'}
    assert selected['MNQ', identity.LEGACY_EPOCH]['resolved'] == 0
    assert selected['MES', 'mes-a']['resolved'] == 1
    assert selected['MES', 'mes-a']['gross_pnl_dollars'] == 10
    assert selected['MES', 'mes-a']['cost_sensitivity']['1_rt_tick']['net_pnl_dollars'] == 7.27
    assert selected['MES', 'mes-b']['candidates'] == 0
    assert selected['MES', 'mes-b']['newest_candidate_timestamp'] is None
    assert result['evidence_integrity']['ok']


def test_sample_gates_are_independent_by_instrument_and_epoch(tmp_path, configured):
    rows = []
    for instrument, epoch, count in [('MNQ', None, 30), ('MES', 'mes-a', 15), ('MES', 'mes-b', 15)]:
        for i in range(count):
            ts = (datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(days=i % 20)).isoformat()
            r = record(instrument, epoch, event_id=str(i), signal_timestamp=ts,
                       commission_dollars=1.48, slippage_ticks=1)
            rows.extend([r, outcome(r)])
    path = tmp_path / 'samples.jsonl'
    write_rows(path, rows)
    result = build_report(path)
    selected = {(p['instrument'], p['evidence_epoch']): p for p in result['populations'] if p['strategy'] == 'vwap_rejection'}
    assert selected['MNQ', identity.LEGACY_EPOCH]['review_eligible']
    for epoch in ('mes-a', 'mes-b'):
        assert selected['MES', epoch]['resolved_filled_outcomes'] == 15
        assert selected['MES', epoch]['trading_days'] == 15
        assert not selected['MES', epoch]['review_eligible']


def test_corrupt_state_fails_without_reset(tmp_path, configured):
    path = tmp_path / campaign.STATE_FILENAME
    path.write_text('{bad json')
    with pytest.raises(ValueError):
        campaign.open_campaign_position(tmp_path, record())
    assert path.read_text() == '{bad json'


def test_concurrent_population_writes_do_not_overwrite(tmp_path, configured):
    from concurrent.futures import ThreadPoolExecutor
    rows = [record(), record('MES', 'mes-a'), record('MES', 'mes-b'), record('MNQ', 'mnq-b')]
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(executor.map(lambda row: campaign.open_campaign_position(tmp_path, row), rows))
    state = json.loads((tmp_path / campaign.STATE_FILENAME).read_text())
    assert len(state['positions']) == 4
    assert len(state['seen_candidate_ids']) == 4
    assert len((tmp_path / campaign.EVIDENCE_FILENAME).read_text().splitlines()) == 4


def test_matched_pairs_never_cross_instrument_or_epoch(tmp_path):
    control = record(event_id='shared')
    control.update(strategy='vwap_hold', variant='control')
    modified = record('MES', 'mes-a', event_id='shared')
    modified.update(strategy='vwap_hold', variant='modified')
    other_epoch = record('MNQ', 'mnq-b', event_id='shared')
    other_epoch.update(strategy='vwap_hold', variant='modified')
    path = tmp_path / 'pairs.jsonl'
    write_rows(path, [control, modified, other_epoch])
    pairs = build_report(path)['matched_pairs']
    assert len(pairs) == 3
    assert all(p['pair_complete_candidates'] == 0 for p in pairs)
    assert sum(p['control_only_events'] for p in pairs) == 1
    assert sum(p['modified_only_events'] for p in pairs) == 2


def test_zero_population_visibility_and_timestamps(tmp_path, configured):
    report = build_report(tmp_path / 'absent.jsonl')
    assert len(report['populations']) == len(configured)
    for p in report['populations']:
        assert p['candidates'] == p['fills'] == p['resolved'] == 0
        assert p['newest_data_timestamp'] is None
        assert p['newest_candidate_timestamp'] is None
        assert p['newest_outcome_timestamp'] is None
        assert not p['review_eligible']


def test_outcome_with_wrong_epoch_cannot_resolve_or_credit_candidate(tmp_path, configured):
    candidate = record('MES', 'mes-a', commission_dollars=1.48, slippage_ticks=1)
    mismatched = outcome(candidate)
    mismatched['evidence_epoch'] = 'mes-b'
    path = tmp_path / 'wrong-epoch.jsonl'
    write_rows(path, [candidate, mismatched])
    report = build_report(path)
    assert all(p['resolved_filled_outcomes'] == 0 for p in report['populations'])
    mes_a = next(p for p in report['populations'] if p['evidence_epoch'] == 'mes-a')
    assert mes_a['open'] == 1


def test_five_legacy_mnq_populations_are_byte_compatible(monkeypatch):
    import hashlib
    monkeypatch.setenv('AFS_RELEASE_SHA', 'compatibility-proof')
    config = json.loads(identity.CONFIG_PATH.read_text())
    rows = []
    for p in config['populations']:
        r = campaign.candidate_record(
            strategy=p['strategy'], variant=p['variant'], direction='LONG',
            signal_timestamp='2026-08-13T14:30:00+00:00', source_timeframe=p['source_timeframe'],
            session='new_york', regime='TREND', market_condition='TRENDING',
            original_entry=100, original_stop=99, original_target=102,
            entry_policy=p['entry_policy'], exit_policy=p['exit_policy'], hypothetical_fill_price=100,
        )
        rows.extend([r, campaign.outcome_record({'campaign_record': r},
            dict(result='WIN', exit_price=102, exit_ts='2026-08-13T15:00:00+00:00'))])
    # Generated from the audited 3da81b7 implementation, before the repair.
    assert hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() == (
        '417ab2d7b526f8063cf190ca554055faf0bd10417b5f8737adef3c872a089727')


def test_missing_cost_proof_cannot_satisfy_sample_gate(tmp_path, configured):
    rows = []
    for i in range(30):
        ts = (datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(days=i % 20)).isoformat()
        r = record('MES', 'mes-a', event_id=str(i), signal_timestamp=ts)
        rows.extend([r, {**r, 'record_type': 'OUTCOME', 'terminal_state': 'WIN',
                         'fillable_state': 'FILLED', 'gross_pnl_dollars': 10,
                         'net_pnl_dollars': 8}])
    path = tmp_path / 'unproven-costs.jsonl'
    write_rows(path, rows)
    mes = next(p for p in build_report(path)['populations'] if p['evidence_epoch'] == 'mes-a')
    assert mes['resolved_filled_outcomes'] == 0
    assert not mes['review_eligible']
    assert mes['cost_sensitivity']['1_rt_tick']['available'] is False
