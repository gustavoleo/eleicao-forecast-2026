from datetime import datetime, timedelta, timezone
import pandas as pd
from src.ingest import fresh_national, synchronized_regions

NOW = datetime(2026,10,25,21,0,tzinfo=timezone.utc)

def unit(seconds=0, final=False):
    return pd.DataFrame(), {'generated_at':(NOW+timedelta(seconds=seconds)).isoformat(),'final':final}

def test_future_generation_cannot_confirm_a_winner():
    assert not fresh_national(unit(120,True)[1],NOW.isoformat(),NOW)
    assert not fresh_national(unit()[1],(NOW+timedelta(seconds=120)).isoformat(),NOW)
    assert fresh_national(unit()[1],NOW.isoformat(),NOW)
    assert not fresh_national(unit(-600)[1],NOW.isoformat(),NOW)
    assert fresh_national(unit(-600,True)[1],NOW.isoformat(),NOW)

def test_region_age_generation_skew_and_failure_exclusions():
    results={'br':unit(),'ac':unit(),'sp':unit(-301),'rj':unit(),'ba':unit(),'zz':unit(-3600,True)}
    checked={u:NOW.isoformat() for u in results}
    checked['rj']=(NOW-timedelta(seconds=121)).isoformat()
    good,bad=synchronized_regions(results,checked,{'ba':'HTTP 500'},NOW)
    assert set(good)=={'ac','zz'}
    assert set(bad)=={'sp','rj','ba'}

def test_future_region_not_used_even_when_marked_final():
    good,bad=synchronized_regions({'br':unit(),'ac':unit(120,True)}, {'ac':NOW.isoformat()}, {},NOW)
    assert not good and 'ac' in bad

def test_model_failure_does_not_stop_official_publication(tmp_path,monkeypatch):
    import json
    from pathlib import Path
    from src.config import Settings
    from src.ingest import Ingestor
    from src.parse import parse_result
    monkeypatch.delenv('HARDCHARGER_URI',raising=False)
    worker=Ingestor(Settings(data_dir=tmp_path,contact='test@example.com'))
    worker.meta={'codigo_eleicao':'6258'}
    worker.results['br']=parse_result(json.loads((Path(__file__).parent/'fixtures/br-live.json').read_text(encoding='utf-8')))
    worker.checked['br']=datetime.now(timezone.utc).isoformat()
    def incompatible(*args,**kwargs):
        raise ValueError('Regional electorate exceeds national total')
    monkeypatch.setattr('src.ingest.project',incompatible)
    try:
        state=worker.publish(force_project=True)
        assert 'projection' in state['errors']
        assert state['stats']['valid_votes']==335743
        assert not state['gate']['goal_satisfied']
        assert json.loads((tmp_path/'public.json').read_text(encoding='utf-8'))['stats']==state['stats']
    finally:
        worker.storage.close()

def test_restart_restores_municipal_payload_without_claiming_new_fetch(tmp_path,monkeypatch):
    import json,hashlib
    from pathlib import Path
    from src.config import Settings,build_url
    from src.ingest import Ingestor
    monkeypatch.delenv('HARDCHARGER_URI',raising=False)
    worker=Ingestor(Settings(data_dir=tmp_path,contact='test@example.com'))
    worker.meta={'codigo_eleicao':'6258','cargo':'1'}
    body=(Path(__file__).parent/'fixtures/municipal-live.json').read_text(encoding='utf-8')
    checked=(NOW-timedelta(seconds=10)).isoformat()
    worker.storage.ingest(pd.DataFrame([dict(ts=checked,uf='ac:01120',election='6258',payload_hash=hashlib.sha256(body.encode()).hexdigest(),payload=body,source_url=build_url('ac',1,6258,municipality='01120'))]),'raw_turbo')
    try:
        worker.restore_cached_units()
        assert 'ac:01120' in worker.municipal_results
        assert worker.checked['ac:01120']==checked
        assert worker.client.requests_total==0
    finally: worker.storage.close()
