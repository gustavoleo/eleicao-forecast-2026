"""SQL forecasts over counting progress, with chronological validation gates.

This is an experimental election application of general time-series models.
Neither extension intervals nor scenario frequencies certify an election winner.
"""
import hashlib
import json
import math
import os
import numpy as np
import pandas as pd
import duckdb

os.environ.setdefault('DATAZOO_DISABLE_TELEMETRY','1')


def softmax(values):
    values=np.asarray(values,dtype=float)
    values=values-values.max(axis=-1,keepdims=True)
    e=np.exp(values)
    return e/e.sum(axis=-1,keepdims=True)


def historical_priors(historical,ids,names,round_number,mapping):
    """Same-person history unless an explicit operator proxy was configured."""
    priors={}
    if historical.empty:
        return priors
    for unit,old in historical[historical['round']==round_number].groupby('uf'):
        matched=[]
        for candidate in ids:
            target=mapping.get(candidate)
            hit=old[old.candidate_id==target] if target is not None else old[old.full_name.str.casefold()==names[candidate].casefold()]
            matched.append(float(hit.iloc[0].votes) if len(hit)==1 else np.nan)
        if np.isfinite(matched).all() and sum(matched)>0:
            values=np.maximum(matched,.5)
            priors[unit]=values/values.sum()
    return priors


def adjust_for_reporting_geography(rows,regional,priors,current_regions,directory,ids):
    """As-of partials explain which areas have reported. No future UF rows leak.

    A complete municipal set replaces its parent only after candidate totals agree.
    Missing/inconsistent coverage keeps a constant national historical baseline;
    it cannot silently borrow newer municipal counts for an older training point.
    """
    anchor=np.zeros(len(ids)); weight=0
    for unit,(_,stats) in current_regions.items():
        if unit in priors:
            anchor+=priors[unit]*stats['electorate']; weight+=stats['electorate']
    if not weight or rows.empty:
        return rows,None,dict(adjusted_snapshots=0,reason='Histórico regional compatível indisponível.')
    anchor/=anchor.sum()
    output=rows.copy()
    output['baseline_share']=output.candidate_id.map(dict(zip(ids,anchor)))
    adjusted=0; municipal_used=set()
    if not regional.empty:
        regional=regional.copy()
        regional['generated_at']=pd.to_datetime(regional.generated_at,utc=True)
        for (_,digest),snap in rows.groupby(['generated_at','payload_hash']):
            stamp=pd.to_datetime(snap.generated_at.iloc[0],utc=True)
            past=regional[regional.generated_at<=stamp]
            latest=past.sort_values(['generated_at','payload_hash']).drop_duplicates(['uf','candidate_id'],keep='last')
            units={}
            for key,g in latest.groupby('uf'):
                if set(g.candidate_id)==set(ids) and g.payload_hash.nunique()==1:
                    units[key]=g.set_index('candidate_id').votes.reindex(ids).to_numpy(dtype=float)
            selected={k:v for k,v in units.items() if ':' not in k}
            used=[]
            for parent in list(selected):
                children=[f'{parent}:{code}' for code in directory.get(parent,[])]
                if children and all(k in units and k in priors for k in children) and np.array_equal(sum((units[k] for k in children),np.zeros(len(ids))),selected[parent]):
                    del selected[parent]
                    selected.update({k:units[k] for k in children}); used+=children
            # Exact reconciliation also checks candidate-specific revisions.
            national=snap.set_index('candidate_id').votes.reindex(ids).to_numpy(dtype=float)
            if not selected or not all(k in priors for k in selected) or not np.array_equal(sum(selected.values(),np.zeros(len(ids))),national):
                continue
            expected=sum((priors[k]*v.sum() for k,v in selected.items()),np.zeros(len(ids)))
            if expected.sum()<=0:
                continue
            expected/=expected.sum()
            mask=output.payload_hash==digest
            output.loc[mask,'baseline_share']=output.loc[mask,'candidate_id'].map(dict(zip(ids,expected)))
            adjusted+=1; municipal_used.update(used)
    return output,anchor,dict(adjusted_snapshots=adjusted,municipalities_used=len(municipal_used),
                            historical_units=len(priors),anchor_shares=dict(zip(ids,anchor.tolist())),
                            method='As-of, reconciled reporting mix; historical log-ratio residuals')


def prepare_series(rows,ids,min_observations=12):
    """Only official source snapshots, never generated projection/history rows."""
    if rows.empty:
        return None,dict(status='insufficient_history',reason='Nenhuma série oficial disponível.',observations=0)
    rows=rows[rows.candidate_id.isin(ids)].copy()
    rows['generated_at']=pd.to_datetime(rows.generated_at,utc=True)
    records=[]
    for (stamp,digest),g in rows.groupby(['generated_at','payload_hash'],sort=True):
        if set(g.candidate_id)!=set(ids) or g.candidate_id.duplicated().any():
            continue
        votes=g.set_index('candidate_id').votes.reindex(ids).to_numpy(dtype=float)
        progress=float(g.apurado_pct.iloc[0])
        if votes.sum()<=0 or not 0 < progress <= 100 or not np.isfinite(votes).all():
            continue
        if records and progress<records[-1][0]:
            records=[]  # Start a new monotonic segment after a count correction.
        if records and progress==records[-1][0]:
            records.pop()  # Revisions replace the same progress point, not new observations.
        baseline=g.set_index('candidate_id').baseline_share.reindex(ids).to_numpy(dtype=float) if 'baseline_share' in g else np.ones(len(ids))/len(ids)
        if not np.isfinite(baseline).all() or np.any(baseline<=0):
            continue
        records.append((progress,votes,digest,baseline))
    info=dict(status='insufficient_history',observations=len(records),minimum_observations=min_observations,
              reason='Aguardando apurações distintas suficientes para validação cronológica.')
    if len(records)<min_observations:
        return None,info
    x=np.array([r[0] for r in records])
    grid=np.arange(math.ceil(x[0]),math.floor(x[-1])+1,dtype=float)
    info.update(progress_start=float(x[0]),progress_end=float(x[-1]),grid_points=len(grid),
                source_hashes=[r[2] for r in records])
    if len(grid)<20 or np.diff(x).max()>10:
        info['reason']='Série curta ou lacunas superiores a dez pontos percentuais de apuração.'
        return None,info
    # Additive log ratios ensure a coherent nonnegative simplex after forecasting.
    votes=np.array([r[1] for r in records])+.5
    ratios=np.log(votes[:,:-1]/votes[:,-1,None])
    baselines=np.array([r[3] for r in records])
    ratios-=np.log(baselines[:,:-1]/baselines[:,-1,None])
    # As-of carry-forward only. Linear interpolation across a validation cutoff
    # would leak the subsequent official count into the training fold.
    y=ratios[np.searchsorted(x,grid,side='right')-1]
    info.update(grid_end=float(grid[-1]),reference_candidate=ids[-1],
                axis='percentage of counted sections; 1 grid step = 1 percentage point')
    return (grid,y),info


class AnofoxForecaster:
    def __init__(self):
        self.conn=None
        self.extension_version=None
        self.cache_key=None
        self.cached=None
        self.load_error=None

    def load(self):
        if self.conn is not None:
            return
        c=duckdb.connect()
        try:
            try: c.execute('LOAD anofox_forecast')
            except duckdb.Error:
                c.execute('INSTALL anofox_forecast FROM community')
                c.execute('LOAD anofox_forecast')
            c.execute('SET anofox_telemetry_enabled=false')
            self.extension_version=c.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name='anofox_forecast'").fetchall()[0][0]
            self.conn=c
        except Exception:
            c.close()
            raise

    def sql_forecast(self,y,horizon,model):
        inputs=pd.DataFrame({'ds':pd.date_range('2000-01-01',periods=len(y),freq='D'),'y':y})
        self.conn.register('_anofox_input',inputs)
        try:
            result=self.conn.execute("""SELECT anofox_fcst_ts_forecast_agg(
                CAST(ds AS TIMESTAMP),CAST(y AS DOUBLE),?,?,
                MAP{'seasonal_period':'1','confidence_level':'0.90'}) FROM _anofox_input""",
                [model,int(horizon)]).fetchall()[0][0]
        finally:
            self.conn.unregister('_anofox_input')
        point=np.array(result['point_forecast'],dtype=float)
        if len(point)!=horizon or not np.isfinite(point).all() or result.get('error_message'):
            raise ValueError('Anofox returned an invalid forecast')
        return point,result

    def forecast(self,rows,ids,draws=5000,anchor=None):
        key=hashlib.sha256(pd.util.hash_pandas_object(rows,index=True).values.tobytes()+json.dumps([ids,draws,None if anchor is None else list(anchor)]).encode()).hexdigest()
        if key==self.cache_key and self.cached is not None:
            return self.cached
        result=self._forecast(rows,ids,draws,anchor)
        # Extension installation failures retry on a later call; data insufficiency
        # is cached until actual official input changes, not the wall clock.
        if result['status']!='extension_unavailable':
            self.cache_key,self.cached=key,result
        return result

    def _forecast(self,rows,ids,draws,anchor=None):
        try:
            self.load()
        except Exception as exc:
            return dict(status='extension_unavailable',engine='anofox_forecast',reason=type(exc).__name__)
        prepared,info=prepare_series(rows,ids)
        info.update(engine='anofox_forecast',extension_version=self.extension_version,
                    validation='Chronological selection, separate calibration and final holdout; no election-wide calibration claim.')
        if prepared is None:
            return info
        grid,y=prepared
        horizon=int(100-grid[-1])
        if horizon<=0:
            info.update(status='count_complete',reason='Apuração completa: usar os votos oficiais, não extrapolação.')
            return info
        n=len(grid)
        h=min(10,(n-8)//3)
        if horizon>4*h:
            info.update(status='horizon_unvalidated',reason='Distância até 100% excede quatro vezes o horizonte validável.',
                        target_horizon=horizon,validation_horizon=h)
            return info
        models=['Naive','AutoETS','AutoARIMA','Theta']
        scores,forecasts,failures={}, {}, {}
        # Fold 1 selects the model; folds 2/3 cannot influence selection.
        for model in models:
            try:
                prediction=np.column_stack([self.sql_forecast(y[:n-3*h,i],h,model)[0] for i in range(y.shape[1])])
                scores[model]=float(np.mean(np.abs(y[n-3*h:n-2*h]-prediction)))
            except Exception as exc:
                failures[model]=type(exc).__name__
        info.update(selection_mae_log_ratio=scores,model_failures=failures)
        if 'Naive' not in scores or len(scores)<2:
            info.update(status='validation_failed',reason='Modelos insuficientes para comparação com baseline.')
            return info
        best=min(scores,key=scores.get)
        selected=[m for m in scores if scores[m]<=scores[best]*1.10+1e-10]
        weights=np.array([1/max(scores[m],1e-6) for m in selected]); weights/=weights.sum()
        def ensemble(data,length):
            pred=[]
            for model in selected:
                pred.append(np.column_stack([self.sql_forecast(data[:,i],length,model)[0] for i in range(y.shape[1])]))
            return np.average(pred,axis=0,weights=weights)
        try:
            calibration=y[n-2*h:n-h]-ensemble(y[:n-2*h],h)
            test_pred=ensemble(y[:n-h],h)
            test_error=y[n-h:]-test_pred
            naive_error=y[n-h:]-y[n-h-1]
            # Split-conformal absolute error radius; small samples remain visible.
            rank=min(h,math.ceil((h+1)*.90))-1
            radius=np.sort(np.abs(calibration),axis=0)[rank]
            coverage=float(np.mean(np.abs(test_error)<=radius+1e-12))
            mae=float(np.abs(test_error).mean()); naive_mae=float(np.abs(naive_error).mean())
            info.update(selected_models=selected,weights=weights.tolist(),calibration_observations=h,
                        holdout_observations=h,holdout_coverage=coverage,holdout_mae=mae,baseline_holdout_mae=naive_mae,
                        target_horizon=horizon,validation_horizon=h)
            if mae>naive_mae*1.10+1e-8 or coverage<.80:
                info.update(status='validation_failed',reason='Holdout não superou os critérios de erro/cobertura; previsão principal suspensa.')
                return info
            point=ensemble(y,horizon)[-1]
            if anchor is not None:
                point+=np.log(np.asarray(anchor)[:-1]/anchor[-1])
            # Empirical errors drive paired scenarios; sqrt scaling is a stated
            # horizon heuristic, not a guarantee of conformal coverage at 100%.
            residuals=np.vstack([calibration,test_error])
            scale=math.sqrt(max(1,horizon/h))
            rng=np.random.default_rng(int(hashlib.sha256(json.dumps(info['source_hashes']).encode()).hexdigest()[:8],16))
            samples=point+residuals[rng.integers(0,len(residuals),draws)]*scale
            # Finite backtest samples cannot demonstrate zero uncertainty.
            samples+=rng.normal(0,.02*scale,size=samples.shape)
            share_draws=softmax(np.column_stack([samples,np.zeros(draws)]))
            target=softmax(np.append(point,0.))
            info.update(status='ready',reason='Validação cronológica passou; previsão experimental por avanço da apuração.',
                        target_shares=dict(zip(ids,target.tolist())),_share_draws=share_draws,
                        uncertainty='Erros pareados de validação; escala sqrt(horizonte/h_validado) e piso log-ratio 0,02. Frequência de cenários não é probabilidade eleitoral calibrada.')
            return info
        except Exception as exc:
            info.update(status='model_error',reason=type(exc).__name__)
            return info
