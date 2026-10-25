import copy
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
from src.config import Settings,build_url,discover_municipalities
from src.geography import refine_regions,municipal_sweep_interval
from src.parse import parse_result
from src.project import project
from src.history import aggregate_municipal_history

NOW=datetime.now(timezone.utc)
FIX=Path(__file__).parent/'fixtures'

def unit(key,votes,electorate,counted,sections,counted_sections):
    frame=pd.DataFrame([dict(candidate_id=str(i),name=f'C{i}',full_name=f'C{i}',party=f'P{i}',
        votes=v,elected=False,destination='Válido') for i,v in enumerate(votes)])
    stats=dict(uf=key,electorate=electorate,counted_electorate=counted,valid_votes=sum(votes),
        sections_total=sections,sections_counted=counted_sections,apurado_pct=100*counted_sections/sections,
        uncounted_electors=0,uninstalled_electors=0,unresolved_votes=0,round=2,
        generated_at=NOW.isoformat(),final=False)
    return frame,stats

def inputs():
    parent=unit('ac',[50,0],1000,100,10,1)
    towns={'ac:00001':unit('ac:00001',[50,0],100,100,1,1),
           'ac:00002':unit('ac:00002',[0,0],900,0,9,0)}
    directory={'ac':['00001','00002']}
    checked={k:NOW.isoformat() for k in towns}
    return {'ac':parent},towns,directory,checked

def test_complete_reconciled_municipalities_replace_parent_not_duplicate():
    regions,towns,directory,checked=inputs()
    selected,refined,rejected=refine_regions(regions,towns,directory,checked,{},NOW,900)
    assert set(selected)==set(towns) and 'ac' not in selected
    assert refined=={'ac':2} and not rejected
    assert sum(s['electorate'] for _,s in selected.values())==1000

@pytest.mark.parametrize('fault',['missing','total','candidate','old','future','error','skew'])
def test_incomplete_or_incompatible_municipalities_keep_entire_uf(fault):
    regions,towns,directory,checked=inputs()
    errors={}
    if fault=='missing': del towns['ac:00002']
    elif fault=='total': towns['ac:00002'][1]['electorate']+=1
    elif fault=='candidate': towns['ac:00001'][0].loc[:,'votes']=[0,50]
    elif fault=='old': checked['ac:00001']=(NOW-timedelta(seconds=1400)).isoformat()
    elif fault=='future': towns['ac:00001'][1]['generated_at']=(NOW+timedelta(seconds=120)).isoformat()
    elif fault=='skew': towns['ac:00001'][1]['generated_at']=(NOW-timedelta(seconds=301)).isoformat()
    else: errors['ac:00001']='HTTP 500'
    selected,refined,rejected=refine_regions(regions,towns,directory,checked,errors,NOW,900)
    assert set(selected)=={'ac'} and not refined and 'ac' in rejected

def test_municipal_history_changes_remaining_vote_geography(monkeypatch):
    monkeypatch.setenv('HISTORICAL_CANDIDATE_MAP','{}')
    regions,towns,directory,checked=inputs()
    selected,_,_=refine_regions(regions,towns,directory,checked,{},NOW,900)
    f,s=regions['ac']
    old=pd.DataFrame([dict(uf=k,round=2,candidate_id=str(i),full_name=f'C{i}',votes=v)
        for k,vs in [('ac:00001',[100,0]),('ac:00002',[10,90])] for i,v in enumerate(vs)])
    out=project(f,s,selected,old)
    assert out['candidates'][0]['candidate_id']=='1'
    assert out['remaining_valid_estimate']==pytest.approx(675)

def test_real_ea12_directory_and_leading_zero_url():
    payload=json.loads((FIX/'ea12-live.json').read_text(encoding='utf-8'))
    client=SimpleNamespace(fetch=lambda url:SimpleNamespace(payload=payload))
    directory=discover_municipalities(client,{'codigo_eleicao':'6258','cargo':'1'},Settings())
    assert len(directory)==28 and sum(map(len,directory.values()))==5757
    assert '01120' in directory['ac']
    assert build_url('ac',1,999,municipality='1120').endswith('/ac/ac01120-c0001-e000999-u.json')
    with pytest.raises(ValueError): build_url('br',1,999,municipality='01120')
    with pytest.raises(ValueError): build_url('ac',1,999,municipality='../other')

def test_real_municipal_ea20_fixture():
    payload=json.loads((FIX/'municipal-live.json').read_text(encoding='utf-8'))
    frame,stats=parse_result(payload,expected_uf='01120',expected_election='6258')
    assert len(frame)==2 and stats['uf']=='01120' and payload['tpabr']=='mu'
    assert stats['electorate'] > 0

def test_sweep_reserves_primary_polling_bandwidth():
    assert municipal_sweep_interval(Settings(rps=2),5757) > 3600
    assert municipal_sweep_interval(Settings(rps=10),5757) == 900

def test_municipal_history_sums_zones_with_valid_votes_only(tmp_path):
    csv=tmp_path/'votes.csv'
    csv.write_text('NR_TURNO;SG_UF;CD_MUNICIPIO;CD_CARGO;NR_CANDIDATO;NM_CANDIDATO;QT_VOTOS_NOMINAIS_VALIDOS\n2;AC;1120;1;13;Person;4\n2;AC;1120;1;13;Person;6\n2;AC;1120;3;13;Other;100\n',encoding='latin1')
    old=aggregate_municipal_history(csv)
    assert len(old)==1 and old.iloc[0].votes==10 and old.iloc[0].uf=='ac:01120'
