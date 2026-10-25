import json
from pathlib import Path
from datetime import datetime,timezone
import pandas as pd
from src.project import local_plurality_gate
from src.presentation import brazil_map,state_calls,compare_rounds,scorecard
from src.parse import parse_result

def region(votes=(90,10),remaining=10):
    f=pd.DataFrame([dict(candidate_id=k,name=k,full_name=k,party=k,votes=v,share=v/sum(votes),elected=False,destination='Válido') for k,v in zip(['13','22'],votes)])
    s=dict(uf='ac',cargo='1',official=True,bound_complete=True,remaining_electors=remaining,
        uncounted_electors=0,uninstalled_electors=0,unresolved_votes=0,valid_votes=sum(votes),
        final=False,generated_at=datetime.now(timezone.utc).isoformat(),checked_at=datetime.now(timezone.utc).isoformat())
    return f,s

def test_map_uses_arithmetic_not_current_leader_or_simulation_probability():
    f,s=region()
    assert local_plurality_gate(f,s)['candidate_id']=='13'
    s['remaining_electors']=80
    assert local_plurality_gate(f,s)['candidate_id'] is None
    s['remaining_electors']=10
    assert local_plurality_gate(f,s,fresh=False)['candidate_id'] is None
    s['unresolved_votes']=1
    assert local_plurality_gate(f,s)['candidate_id'] is None

def test_map_requires_recent_checks_and_supports_27_states():
    f,s=region()
    state=dict(published_at=s['checked_at'],regions=[dict(s,candidates=f.to_dict('records'))],errors={})
    assert state_calls(state)['ac']['candidate_id']=='13'
    svg=brazil_map(state)
    assert svg.count('aria-label="')==28
    assert 'fill="#f32d42"' in svg and 'fill="#30342d"' in svg
    state['errors']['ac']='HTTP 500'
    assert 'fill="#f32d42"' not in brazil_map(state)

def test_first_round_comparison_is_exact_and_person_matched():
    f,s=region()
    current=dict(s,candidates=f.to_dict('records'))
    first=dict(current,final=True)
    rows,leader=compare_rounds(current,first)
    assert leader=='13' and all(r['Votos iguais'] for r in rows)
    first['candidates']=[dict(c) for c in first['candidates']]
    first['candidates'][0]['full_name']='Different person'
    rows,leader=compare_rounds(current,first)
    assert len(rows)==1 and leader is None
    first['final']=False
    assert compare_rounds(current,first)==([],None)

def test_scorecard_escapes_source_names():
    f,s=region()
    f.loc[0,'name']='<script>alert(1)</script>'
    markup=scorecard(f.to_dict('records'),s)
    assert '<script>' not in markup and '&lt;script&gt;' in markup

def test_real_first_round_final_is_not_a_second_round_prediction():
    payload=json.loads((Path(__file__).parent/'fixtures/first-round-br-live.json').read_text(encoding='utf-8'))
    f,s=parse_result(payload,expected_election='6257')
    assert s['round']==1 and s['final'] and s['apurado_pct']==100
    assert local_plurality_gate(f,s)['candidate_id'] is None
