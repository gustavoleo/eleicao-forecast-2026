import os
import numpy as np
import pandas as pd
import pytest
from src.anofox import AnofoxForecaster,prepare_series,softmax,adjust_for_reporting_geography,historical_priors
from src.project import project


def series(progress=range(10,91),share=.6):
    rows=[]
    for i,p in enumerate(progress):
        total=int(p*10000)
        votes=int(total*share)
        for candidate,v in [('a',votes),('b',total-votes)]:
            rows.append(dict(candidate_id=candidate,votes=v,generated_at=f'2026-10-25T20:{i//60:02d}:{i%60:02d}+00:00',
                             payload_hash=f'hash-{i}',apurado_pct=float(p)))
    return pd.DataFrame(rows)


def test_repeated_source_counts_are_not_training_observations():
    rows=series(progress=[1]*100)
    prepared,info=prepare_series(rows,['a','b'])
    assert prepared is None and info['observations']==1


def test_count_regression_resets_training_segment():
    rows=series(progress=list(range(10,40))+[20,21,22])
    prepared,info=prepare_series(rows,['a','b'])
    assert prepared is None and info['observations']==3


def test_sparse_observations_cannot_manufacture_training_by_interpolation():
    prepared,info=prepare_series(series(progress=[10,90]),['a','b'])
    assert prepared is None and info['observations']==2


def test_log_ratios_and_softmax_preserve_valid_share_simplex():
    (grid,y),info=prepare_series(series(),['a','b'])
    predicted=softmax(np.column_stack([y,np.zeros(len(y))]))
    assert np.allclose(predicted.sum(axis=1),1)
    assert np.max(np.abs(predicted[:,0]-.6))<1e-4
    assert info['observations']==81


def test_resampling_cannot_leak_later_counts_into_training_grid():
    rows=series(progress=range(10,91,2))
    stamp=rows.loc[rows.apurado_pct==50,'generated_at'].iloc[0]
    rows.loc[(rows.generated_at==stamp)&(rows.candidate_id=='a'),'votes']=450000
    rows.loc[(rows.generated_at==stamp)&(rows.candidate_id=='b'),'votes']=50000
    (grid,y),_=prepare_series(rows,['a','b'])
    assert y[np.where(grid==49)[0][0],0] < 1
    assert y[np.where(grid==50)[0][0],0] > 2


def test_same_area_baselines_use_only_asof_reconciled_partials():
    national=series(progress=[10],share=.8)
    early=national.generated_at.iloc[0]
    regional=pd.DataFrame([dict(uf=uf,candidate_id=c,votes=v,generated_at=stamp,payload_hash=uf+stamp)
        for uf,vs,stamp in [('ac',[80000,20000],early),('sp',[10000,90000],'2026-10-25T21:00:00+00:00')]
        for c,v in zip(['a','b'],vs)])
    priors={'ac':np.array([.8,.2]),'sp':np.array([.2,.8])}
    current={k:(None,{'electorate':1000000}) for k in priors}
    adjusted,anchor,info=adjust_for_reporting_geography(national,regional,priors,current,{},['a','b'])
    assert np.allclose(anchor,[.5,.5])
    assert info['adjusted_snapshots']==1
    assert adjusted.set_index('candidate_id').loc['a','baseline_share']==pytest.approx(.8)
    regional.loc[regional.uf=='ac','votes']+=1
    _,_,info=adjust_for_reporting_geography(national,regional,priors,current,{},['a','b'])
    assert info['adjusted_snapshots']==0


def test_long_extrapolation_fails_validation_gate_without_calling_models(monkeypatch):
    engine=AnofoxForecaster()
    monkeypatch.setattr(engine,'load',lambda:None)
    result=engine.forecast(series(progress=range(1,21)),['a','b'])
    assert result['status']=='horizon_unvalidated'


def test_historical_identity_duplicates_and_missing_candidates_remain_excluded():
    rows=pd.DataFrame([
        dict(uf=u,candidate_id=c,full_name=n,votes=v,round=2)
        for u,c,n,v in [('ac','13','Person A',80),('ac','22','Person B',20),
                        ('sp','13','Person A',50),('sp','13','Person A',50),('sp','22','Person B',50),
                        ('rr','13','Person A',20),('ro','13','Another person',80),('ro','22','Person B',20)]])
    priors=historical_priors(rows,['a','b'],{'a':'PERSON A','b':'person b'},2,{})
    assert set(priors)=={'ac'}
    assert np.allclose(priors['ac'],[.8,.2])
    proxy=historical_priors(rows,['a','b'],{},2,{'a':'13','b':'22'})
    assert set(proxy)=={'ac','ro'}


def test_geographic_assembly_requires_coherent_payloads_and_complete_municipal_sets():
    national=series(progress=[10],share=.8)
    stamp=national.generated_at.iloc[0]
    regional=pd.DataFrame([dict(uf=uf,candidate_id=c,votes=v,generated_at=stamp,payload_hash=uf)
        for uf,vs in [('ac',[80000,20000]),('ac:1',[80000,10000]),('ac:2',[0,10000])]
        for c,v in zip(['a','b'],vs)])
    priors={'ac':np.array([.5,.5]),'ac:1':np.array([.9,.1]),'ac:2':np.array([.1,.9])}
    current={'ac':(None,{'electorate':1000000})}
    adjusted,_,info=adjust_for_reporting_geography(national,regional,priors,current,{'ac':['1','2']},['a','b'])
    assert info['municipalities_used']==2
    assert adjusted.iloc[0].baseline_share==pytest.approx(.82)
    # Mixed revisions cannot be combined into one synthetic candidate tally.
    regional.loc[(regional.uf=='ac:1')&(regional.candidate_id=='a'),'payload_hash']='different-revision'
    adjusted,_,info=adjust_for_reporting_geography(national,regional,priors,current,{'ac':['1','2']},['a','b'])
    assert info['municipalities_used']==0
    assert adjusted.iloc[0].baseline_share==pytest.approx(.5)


def test_anofox_scenarios_cannot_reallocate_counted_votes():
    f=pd.DataFrame([dict(candidate_id=k,name=k,full_name=k,party=k,destination='Válido',votes=v) for k,v in [('a',90),('b',10)]])
    stats=dict(round=2,electorate=120,counted_electorate=100,apurado_pct=90,sections_counted=9,sections_total=10)
    primary=dict(status='ready',target_shares={'a':.01,'b':.99},_share_draws=np.tile([.01,.99],(5000,1)),uncertainty='test scenario')
    result=project(f,stats,{},primary=primary)
    a=next(x for x in result['candidates'] if x['candidate_id']=='a')
    assert result['engine']=='anofox_forecast'
    assert a['ci_low']>=90/120 and a['projected_share']>=90/120


@pytest.mark.skipif(os.getenv('ANOFOX_INTEGRATION')!='1',reason='Real signed extension integration runs on Linux container')
def test_real_extension_rolling_validation_and_forecast():
    engine=AnofoxForecaster()
    try:
        result=engine.forecast(series(),['a','b'])
        assert engine.extension_version
        assert result['status']=='ready',result
        assert set(result['selection_mae_log_ratio'])=={'Naive','AutoETS','AutoARIMA','Theta'}
        assert result['holdout_coverage']>=.8
        assert result['_share_draws'].shape==(5000,2)
        assert sum(result['target_shares'].values())==pytest.approx(1)
        assert result['target_shares']['a']==pytest.approx(.6,abs=.01)
        assert np.isfinite(result['_share_draws']).all()
    finally:
        if engine.conn: engine.conn.close()
