import itertools
import numpy as np
import pandas as pd
import pytest
from src.project import project, certainty_gate, concentration

def case(votes=(60,40), remaining=30, round=2):
    frame = pd.DataFrame([dict(candidate_id=str(i), name=f'Candidate {i}', full_name=f'Candidate {i}',
        votes=v, elected=False, destination='Válido', party=f'P{i}') for i,v in enumerate(votes)])
    stats = dict(round=round, uf='br', cargo='1', official=True, final=False, no_winner=False,
        mathematical_flag='n', remaining_electors=remaining, valid_votes=sum(votes), bound_complete=True,
        uncounted_electors=0, uninstalled_electors=0, unresolved_votes=0,
        electorate=sum(votes)+remaining, counted_electorate=sum(votes), apurado_pct=50,
        sections_counted=5, sections_total=10)
    return frame, stats

def test_no_monte_carlo_certainty():
    f,s=case((1000000,1), 1)
    out=project(f,s,{},seed=1)
    assert all(0 < c['p_win'] < 1 for c in out['candidates'])

@pytest.mark.parametrize('round', [1,2])
def test_certainty_bound_exhaustive_small_races(round):
    for a,b,r in itertools.product(range(1,9),range(1,9),range(5)):
        f,s=case((a,b),r,round)
        gate=certainty_gate(f,s)
        if gate['goal_satisfied']:
            winner=int(gate['candidate_id'])
            for x in range(r+1):
                final=[a+x,b+r-x]
                assert final[winner] > final[1-winner]

def test_tie_cannot_secure():
    f,s=case((60,40),20)
    assert not certainty_gate(f,s)['goal_satisfied']
    s['remaining_electors']=19
    assert certainty_gate(f,s)['goal_satisfied']

def test_first_round_needs_absolute_majority():
    f,s=case((45,30,25),0,1)
    assert not certainty_gate(f,s)['goal_satisfied']

def test_stale_simulated_non_national_and_pending_are_not_calls():
    f,s=case((90,10),1)
    assert not certainty_gate(f,s,fresh=False)['goal_satisfied']
    for field,value in [('official',False),('uf','sp'),('no_winner',True),('unresolved_votes',5)]:
        assert not certainty_gate(f,{**s,field:value})['goal_satisfied']

def test_md_s_means_runoff_not_elected():
    f,s=case((45,30,25),100,1)
    f.loc[0,'elected']=True
    s['mathematical_flag']='s'
    assert not certainty_gate(f,s)['goal_satisfied']
    s['mathematical_flag']='e'
    assert certainty_gate(f,s)['status']=='official_elected'

def test_uninstalled_and_uncounted_electorate_in_bound():
    f,s=case((60,40),10)
    s.update(uncounted_electors=5,uninstalled_electors=6)
    assert not certainty_gate(f,s)['goal_satisfied']

def test_missing_bound_categories_fail_closed():
    f,s=case((90,10),1)
    del s['bound_complete']
    assert not certainty_gate(f,s)['goal_satisfied']

def test_projection_has_ranked_runoff_scenarios():
    f,s=case((35,34,31),200,1)
    out=project(f,s,{},seed=7)
    assert len(out['pairings'])==3
    probabilities=[p['probability'] for p in out['pairings']]
    assert probabilities==sorted(probabilities, reverse=True)
    assert sum(probabilities)==pytest.approx(out['p_any_runoff'])
    assert sum(c['projected_share'] for c in out['candidates'])==pytest.approx(1)
    assert all(0 <= c['ci_low'] <= c['ci_high'] <= 1 for c in out['candidates'])

def test_second_round_omits_first_round_and_pairings():
    f,s=case()
    out=project(f,s,{})
    assert out['pairings']==[] and out['p_any_runoff'] is None
    assert all(c['p_first_round'] is None for c in out['candidates'])

def test_completed_count_does_not_resimulate_counted_votes():
    f,s=case((60,40),0)
    s.update(sections_counted=10,apurado_pct=100)
    out=project(f,s,{})
    for c in out['candidates']:
        assert c['ci_low']==pytest.approx(c['votes']/100)
        assert c['ci_high']==pytest.approx(c['votes']/100)

def test_geography_uses_large_uncounted_region_history(monkeypatch):
    monkeypatch.setenv('HISTORICAL_CANDIDATE_MAP','{"0":"0","1":"1"}')
    f,s=case((60,40),900)
    small,ss=case((60,40),0)
    ss.update(apurado_pct=100)
    large,ls=case((0,0),900)
    ls.update(apurado_pct=0)
    old=pd.DataFrame([dict(uf=u,round=2,candidate_id=str(i),full_name=f'Candidate {i}',votes=v)
        for u,vs in [('ac',[60,40]),('sp',[10,90])] for i,v in enumerate(vs)])
    out=project(f,s,{'ac':(small,ss),'sp':(large,ls)},old)
    assert out['candidates'][0]['candidate_id']=='1'
    assert out['candidates'][0]['naive_share']==.4

def test_alpha_schedule():
    assert [concentration(p) for p in [0,29,30,80,81,100]]==[20,20,100,100,500,500]

def test_missing_region_does_not_discard_its_remaining_electorate():
    f,s=case((60,40),900)
    small,ss=case((60,40),0)
    ss.update(apurado_pct=100)
    out=project(f,s,{'ac':(small,ss)})
    assert out['uncovered_pending_electorate']==900
    assert out['remaining_valid_estimate']==pytest.approx(675)
    assert out['candidates'][0]['ci_high']-out['candidates'][0]['ci_low']>.05

def test_lagging_regions_cannot_resimulate_nationally_counted_votes():
    f,s=case((600,400),100)
    old,os=case((60,40),1000)
    out=project(f,s,{'sp':(old,os)})
    assert out['regional_reconciliation_factor']==pytest.approx(.1)
    assert out['remaining_valid_estimate'] <= 100
    assert out['uncovered_pending_electorate']==0
    # Even assigning every uncounted vote against the leader cannot erase these votes.
    assert out['candidates'][0]['ci_low'] >= 600/1100

def test_overlapping_regions_rejected():
    f,s=case((60,40),100)
    with pytest.raises(ValueError,match='Regional electorate'):
        project(f,s,{'ac':(f,s),'sp':(f,s)})
