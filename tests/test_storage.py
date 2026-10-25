import pandas as pd
from src.storage import Storage

def test_fallback_storage_append_and_parameters(tmp_path,monkeypatch):
    monkeypatch.delenv('HARDCHARGER_URI',raising=False)
    s=Storage(tmp_path/'votes.duckdb')
    assert not s.exists('votes')
    s.ingest(pd.DataFrame([{'candidate':'A','votes':2}]),'votes')
    s.ingest(pd.DataFrame([{'candidate':'B','votes':3}]),'votes')
    assert s.exists('votes')
    assert s.query('SELECT sum(votes) AS n FROM votes').iloc[0]['n']==5
    assert s.query('SELECT * FROM votes WHERE candidate = ?', ['A']).iloc[0]['votes']==2
    s.close()
