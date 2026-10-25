import json
from pathlib import Path
from src.parse import parse_result
from src.project import project,certainty_gate
from src.storage import Storage
import pandas as pd

def test_real_fixture_to_persisted_projection(tmp_path):
    body=json.loads((Path(__file__).parent/'fixtures/br-live.json').read_text(encoding='utf-8'))
    f,s=parse_result(body)
    out=project(f,s,{})
    db=Storage(tmp_path/'dry.duckdb',use_hardcharger=False)
    db.ingest(f,'votes_parcial')
    db.ingest(pd.DataFrame(out['candidates']),'votes_projected')
    assert len(db.query('SELECT * FROM votes_projected'))==2
    assert out['candidates'][0]['name']==f.sort_values('votes').iloc[-1]['name']
    assert not certainty_gate(f,s)['goal_satisfied']
    db.close()
