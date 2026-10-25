import copy
import json
from pathlib import Path
import pytest
from src.parse import parse_result, number
from src.config import Settings, discover_election_meta, build_url
from datetime import date

FIXTURES = Path(__file__).parent / 'fixtures'

@pytest.fixture
def real_result():
    return json.loads((FIXTURES / 'br-live.json').read_text(encoding='utf-8'))

def test_real_live_fixture(real_result):
    frame, stats = parse_result(real_result)
    assert set(frame.name) == {'LULA', 'FLAVIO BOLSONARO'}
    assert frame.votes.sum() == 335743
    assert stats['sections_counted'] == 1225
    assert stats['remaining_electors'] + stats['counted_electorate'] == stats['electorate']
    assert 0 < stats['apurado_pct'] < 1

def test_real_zero_vote_uf():
    frame, stats = parse_result(json.loads((FIXTURES / 'sp-live.json').read_text(encoding='utf-8')))
    assert frame.votes.sum() == 0
    assert stats['apurado_pct'] == 0

def test_required_field_fails_clearly(real_result):
    del real_result['carg'][0]['agr'][0]['par'][0]['cand'][0]['vap']
    with pytest.raises(ValueError, match='Missing required candidate field'):
        parse_result(real_result)

def test_total_mismatch_is_rejected(real_result):
    real_result['v']['vv'] = '999'
    with pytest.raises(ValueError, match='vote sum'):
        parse_result(real_result)

def test_wrong_election_and_territory(real_result):
    with pytest.raises(ValueError, match='election'):
        parse_result(real_result, expected_election='123')
    with pytest.raises(ValueError, match='territory'):
        parse_result(real_result, expected_uf='sp')

def test_unknown_additional_fields_tolerated(real_result):
    real_result['future_field'] = {'value': 5}
    assert parse_result(real_result)[1]['valid_votes'] > 0

def test_locale_numeric():
    assert number('57,655409048') == 57.655409048
    assert number('1.234,56') == 1234.56

def test_missing_bound_fields_disable_certainty(real_result):
    del real_result['e']['esna']
    assert not parse_result(real_result)[1]['bound_complete']

def test_discovery_changes_round_by_date():
    config = json.loads((FIXTURES / 'ea11-live.json').read_text(encoding='utf-8'))
    first = discover_election_meta(None, Settings(), config, date(2026,10,4))
    second = discover_election_meta(None, Settings(), config, date(2026,10,25))
    assert first['round'] == 1 and second['round'] == 2
    assert first['codigo_eleicao'] != second['codigo_eleicao']
    assert 'zz' in second['ufs']
    assert build_url('br', 1, second['codigo_eleicao']).endswith(f'br-c0001-e{int(second["codigo_eleicao"]):06d}-u.json')

def test_new_election_code_discovered_without_constant():
    config = json.loads((FIXTURES / 'ea11-live.json').read_text(encoding='utf-8'))
    for p in config['pl']:
        for e in p['e']:
            if p['c'] == 'ele2026' and e['t'] == '2' and e['tp'] == '8': e['cd'] = '99999'
    assert discover_election_meta(None, Settings(), config, date(2026,10,25))['codigo_eleicao'] == '99999'
