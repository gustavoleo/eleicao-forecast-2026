import argparse
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import json
import logging
import os
import threading
import time
import pandas as pd
from .config import Settings, UFS, build_url, discover_election_meta, discover_municipalities
from .http_client import CachedClient, BackoffError, atomic_json, utcnow
from .parse import parse_result
from .storage import Storage
from .project import project, certainty_gate
from .history import load_history, load_municipal_history
from .geography import municipal_sweep_interval, refine_regions
from .anofox import AnofoxForecaster, historical_priors, adjust_for_reporting_geography

def fresh_national(stats, checked_at, now, has_error=False):
    checked_age = (now - datetime.fromisoformat(checked_at)).total_seconds()
    generated_age = (now - datetime.fromisoformat(stats['generated_at'])).total_seconds()
    return (-60 <= checked_age < 120 and generated_age >= -60
            and (generated_age < 300 or stats['final']) and not has_error)

def synchronized_regions(results, checked, errors, now, interval=60):
    """Only combine recently checked UF files compatible with the BR generation."""
    national_time = datetime.fromisoformat(results['br'][1]['generated_at'])
    accepted, excluded = {}, {}
    for uf, data in results.items():
        if uf == 'br':
            continue
        stats = data[1]
        source_time = datetime.fromisoformat(stats['generated_at'])
        check_time = datetime.fromisoformat(checked[uf]) if uf in checked else None
        reason = None
        if uf in errors or check_time is None or not -60 <= (now-check_time).total_seconds() < max(120, 2*interval):
            reason = 'consulta ausente, antiga ou com erro'
        elif (source_time-now).total_seconds() > 60:
            reason = 'geração no futuro'
        elif not stats.get('final') and abs((source_time-national_time).total_seconds()) > 300:
            reason = 'geração difere do Brasil por mais de cinco minutos'
        if reason:
            excluded[uf] = reason
        else:
            accepted[uf] = data
    return accepted, excluded

class Ingestor:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.client = CachedClient(self.settings)
        self.storage = Storage(self.settings.data_dir / 'eleicoes.duckdb')
        self.results, self.checked, self.errors = {}, {}, {}
        self.municipal_results, self.directory = {}, {}
        self.first_results, self.first_meta = {}, None
        self.first_directory={}
        self.municipal_interval = self.settings.municipal_interval
        self.municipal_history_status = 'loading' if self.settings.municipal_enabled else 'disabled'
        self.history = pd.DataFrame()
        self.history_status = 'loading'
        self.meta = None
        self.last_projection = None
        self.last_project_time = 0
        self.timeline = []
        self.stop_event = threading.Event()
        self.history_thread = None
        self.primary_engine = os.getenv('PRIMARY_FORECAST_ENGINE','anofox_forecast')
        if self.primary_engine not in ('anofox_forecast','geographic_dirichlet'):
            raise ValueError('Unsupported PRIMARY_FORECAST_ENGINE')
        self.forecaster = AnofoxForecaster()

    def bootstrap(self):
        self.meta = discover_election_meta(self.client, self.settings)
        if self.meta['round']==2:
            self.first_meta=discover_election_meta(self.client,replace(self.settings,round='1'))
        if self.storage.exists('votes_projected'):
            old = self.storage.query('SELECT ts, name, projected_share FROM votes_projected WHERE election = ? ORDER BY ts', [self.meta['codigo_eleicao']])
            self.timeline = old.to_dict('records')
        def history_job():
            try:
                self.history = load_history(self.settings, self.storage)
                self.history_status = f'loaded:{len(self.history)}'
                if self.settings.municipal_enabled:
                    try:
                        municipal = load_municipal_history(self.settings,self.storage)
                        self.history = pd.concat([self.history,municipal],ignore_index=True)
                        self.municipal_history_status = f'loaded:{len(municipal)}'
                    except Exception as exc:
                        self.municipal_history_status = 'unavailable:' + type(exc).__name__
                        logging.warning('HISTORY municipal fallback %s',type(exc).__name__)
            except Exception as exc:
                self.history_status = 'unavailable:' + type(exc).__name__
                logging.warning('HISTORY fallback %s', type(exc).__name__)
        self.history_thread = threading.Thread(target=history_job, name='history-download', daemon=True)
        self.history_thread.start()
        self.restore_cached_units()

    def restore_cached_units(self):
        if not self.storage.exists('raw_turbo'):
            return
        codes=[self.meta['codigo_eleicao']]
        if self.first_meta: codes.append(self.first_meta['codigo_eleicao'])
        rows=self.storage.query("SELECT * FROM raw_turbo WHERE contains(uf, ':') AND election IN ("+','.join('?' for _ in codes)+") QUALIFY row_number() OVER (PARTITION BY uf,election ORDER BY ts DESC)=1",codes)
        for row in rows.itertuples():
            try:
                is_first=row.uf.startswith('r1:'); unit=row.uf[3:] if is_first else row.uf
                parent,municipality=unit.split(':',1) if ':' in unit else (unit,None)
                meta=self.first_meta if is_first else self.meta
                payload=json.loads(row.payload)
                f,s=parse_result(payload,meta['cargo'],municipality or unit,meta['codigo_eleicao'])
                if not s['official']: continue
                if municipality:
                    s.update(uf=unit,parent_uf=parent,municipality=municipality); f['uf']=unit
                target=self.first_results if is_first else self.municipal_results
                target[unit]=(f,s)
                index=self.client.cache/(hashlib.sha256(row.source_url.encode()).hexdigest()+'.index.json')
                cached=json.loads(index.read_text()) if index.exists() else {}
                self.checked[row.uf]=cached['checked_at'] if cached.get('hash')==row.payload_hash else row.ts
            except (ValueError,KeyError,TypeError):
                logging.warning('CACHE restore skipped invalid unit=%s',row.uf)
        logging.info('CACHE restored municipalities=%d first_round_units=%d',len(self.municipal_results),len(self.first_results))

    def restored_due(self,key,interval):
        if key not in self.checked: return 0.
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(self.checked[key])).total_seconds()
        return time.monotonic()+max(0,interval-age) if age>=0 else 0.

    def poll(self, uf):
        is_first=uf.startswith('r1:')
        unit=uf[3:] if is_first else uf
        meta=self.first_meta if is_first else self.meta
        parent, municipality = unit.split(':',1) if ':' in unit else (unit,None)
        url = build_url(parent, meta['cargo'], meta['codigo_eleicao'], self.settings, municipality=municipality)
        response = self.client.fetch(url)
        frame, stats = parse_result(response.payload, meta['cargo'], municipality or unit, meta['codigo_eleicao'])
        if municipality:
            if response.payload.get('tpabr') != 'mu':
                raise ValueError('Expected municipal result')
            stats.update(uf=unit,parent_uf=parent,municipality=municipality)
            frame['uf'] = unit
        results = self.first_results if is_first else (self.municipal_results if municipality else self.results)
        if not stats['official']:
            raise ValueError('Simulation/test data rejected on live deployment')
        if unit in results and stats['generated_at'] < results[unit][1]['generated_at']:
            raise ValueError('Older upstream generation rejected')
        previous = results.get(unit)
        results[unit] = (frame, stats)
        self.checked[uf] = response.checked_at
        self.errors.pop(uf, None)
        changed = previous is None or stats['hash'] != previous[1]['hash']
        if changed:
            present = self.storage.exists('raw_turbo') and not self.storage.query(
                'SELECT payload_hash FROM raw_turbo WHERE payload_hash = ? AND uf = ? LIMIT 1', [response.payload_hash, uf]).empty
            if not present:
                raw = pd.DataFrame([dict(ts=response.checked_at, uf=uf, election=stats['election'], payload_hash=response.payload_hash,
                                         payload=response.body.decode('utf-8'), source_url=url)])
                self.storage.ingest(raw, 'raw_turbo')
                frame = frame.copy()
                frame['ts'], frame['payload_hash'] = response.checked_at, response.payload_hash
                self.storage.ingest(frame, 'votes_parcial')
        logging.info('HEARTBEAT ts=%s uf=%s apurado_pct=%.9f bytes=%d hash=%s req/s=%d changed=%s',
                     response.checked_at, uf, stats['apurado_pct'], response.bytes_received,
                     response.payload_hash, self.client.rps, changed)

    def publish(self, force_project=False):
        now = datetime.now(timezone.utc)
        state = dict(published_at=now.isoformat(), meta=self.meta, backend=self.storage.backend,
                     source='Fonte: TSE — Dados Abertos (resultados.tse.jus.br)', disclaimer='Projeção estatística, não é resultado oficial.',
                     history_status=self.history_status, errors=self.errors.copy(), checked_at={k:v for k,v in self.checked.items() if ':' not in k},
                     municipalities=dict(enabled=self.settings.municipal_enabled, discovered=sum(map(len,self.directory.values())),
                         collected=len(self.municipal_results), planned_sweep_seconds=self.municipal_interval,
                         historical_status=self.municipal_history_status),
                     first_round=dict(meta=self.first_meta,regions=[dict(**s,candidates=f.to_dict('records')) for unit,(f,s) in self.first_results.items() if ':' not in unit]),
                     requests=dict(total=self.client.requests_total, current_rps=self.client.rps,
                                   mean_rps=self.client.requests_total / max(1, time.monotonic()-self.client.start),
                                   not_modified=self.client.not_modified, same_hash=self.client.same_hash,
                                   configured_max_rps=self.settings.rps),
                     regions=[dict(**s, checked_at=self.checked.get(uf),candidates=f.to_dict('records')) for uf, (f, s) in self.results.items() if uf != 'br'])
        if 'br' in self.results:
            br, stats = self.results['br']
            generated_age = (now - datetime.fromisoformat(stats['generated_at'])).total_seconds()
            fresh = fresh_national(stats, self.checked['br'], now, 'br' in self.errors)
            state.update(stats=stats, official_candidates=br.to_dict('records'), source_age_seconds=generated_age,
                         stale=not fresh, gate=certainty_gate(br, stats, fresh=fresh))
            regions, excluded = synchronized_regions(self.results, self.checked, self.errors, now, self.settings.uf_interval)
            state['excluded_regions'] = excluded
            region_ready = set(regions) == set(UFS)
            if force_project or time.monotonic() - self.last_project_time >= self.settings.projection_interval:
                if stats['valid_votes'] > 0:
                    model_regions, refined, rejected = refine_regions(regions,self.municipal_results,self.directory,self.checked,self.errors,now,self.municipal_interval)
                    hashes = {'br':stats['hash'], **{uf:s['hash'] for uf,(_,s) in model_regions.items()}}
                    seed = int(hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:8], 16)
                    try:
                        draws=int(os.getenv('SIMULATION_DRAWS','5000'))
                        model_history=self.history
                        first_frames=[]
                        for unit,(f,fs) in self.first_results.items():
                            if unit!='br' and fs['final']:
                                old=f[['candidate_id','full_name','votes']].copy()
                                old['uf'],old['round']=unit,stats['round']
                                first_frames.append(old)
                        if first_frames:
                            first_history=pd.concat(first_frames,ignore_index=True)
                            older=model_history[~model_history.uf.isin(first_history.uf.unique())] if not model_history.empty else model_history
                            model_history=pd.concat([older,first_history],ignore_index=True)
                        primary=None
                        if self.primary_engine=='anofox_forecast':
                            series=self.storage.query("SELECT candidate_id,votes,generated_at,payload_hash,apurado_pct FROM votes_parcial WHERE uf='br' AND election=? AND cargo='1' ORDER BY generated_at,payload_hash,candidate_id",[self.meta['codigo_eleicao']]) if self.storage.exists('votes_parcial') else pd.DataFrame()
                            ids=sorted(br.loc[br.destination.str.casefold().isin(['válido','valido','']),'candidate_id'].tolist())
                            anchor=None; geography=dict(adjusted_snapshots=0)
                            if not series.empty and series.payload_hash.nunique()>=12:
                                prior=historical_priors(model_history,ids,br.set_index('candidate_id').full_name.to_dict(),stats['round'],json.loads(os.getenv('HISTORICAL_CANDIDATE_MAP','{}')))
                                regional_series=self.storage.query("SELECT uf,candidate_id,votes,generated_at,payload_hash FROM votes_parcial WHERE uf<>'br' AND election=? AND cargo='1'",[self.meta['codigo_eleicao']])
                                series,anchor,geography=adjust_for_reporting_geography(series,regional_series,prior,{k:v for k,v in self.results.items() if k!='br'},self.directory,ids)
                            primary=dict(self.forecaster.forecast(series,ids,draws,anchor=anchor))
                            primary['geographic_adjustment']=geography
                        projection = project(br, stats, model_regions, model_history, draws, seed,primary=primary)
                    except (ValueError, KeyError) as exc:
                        # An inconsistent model input must not kill the official
                        # collector or prevent publication of its independent gate.
                        self.errors['projection'] = type(exc).__name__ + ': ' + str(exc)
                        state['errors'] = self.errors.copy()
                        state.update(projection=self.last_projection, timeline=self.timeline)
                        self.last_project_time = time.monotonic()
                        atomic_json(self.settings.data_dir / 'public.json', state)
                        logging.warning('PROJECTION unavailable %s', type(exc).__name__)
                        return state
                    self.errors.pop('projection', None)
                    state['errors'] = self.errors.copy()
                    self.last_projection = projection
                    self.last_projection['ts'] = now.isoformat()
                    self.last_projection['geographic_complete'] = region_ready
                    self.last_projection['excluded_regions'] = excluded
                    self.last_projection.update(uf_coverage=len(regions),municipal_units=sum(refined.values()),
                                                refined_ufs=refined,municipal_fallbacks=rejected)
                    self.last_projection['primary_forecast']={k:v for k,v in primary.items() if not k.startswith('_')} if primary else dict(engine='geographic_dirichlet',status='unvalidated')
                    self.last_projection['headline_ready']=fresh and primary is not None and primary.get('status')=='ready'
                    self.last_projection['first_round_prior_units']=len(first_frames)
                    rows = pd.DataFrame(self.last_projection['candidates'])
                    rows['ts'], rows['election'] = now.isoformat(), self.meta['codigo_eleicao']
                    self.storage.ingest(rows, 'votes_projected')
                    self.storage.ingest(pd.DataFrame([dict(ts=now.isoformat(), election=self.meta['codigo_eleicao'],
                        model_version=self.last_projection['model_version'], seed=seed,
                        metadata=json.dumps(self.last_projection, ensure_ascii=False),
                        input_hashes=json.dumps(hashes, sort_keys=True))]), 'simulations_meta')
                    self.timeline.extend(rows[['ts', 'name', 'projected_share']].to_dict('records'))
                self.last_project_time = time.monotonic()
            state.update(projection=self.last_projection, timeline=self.timeline)
            if state['gate']['goal_satisfied']:
                # Capture every confirming/revising source; do not silently freeze a call.
                atomic_json(self.settings.data_dir / 'completion.json', dict(observed_at=now.isoformat(), gate=state['gate'], stats=stats))
        else:
            state.update(stale=True, gate=dict(status='awaiting_data', goal_satisfied=False), projection=None, timeline=self.timeline)
        atomic_json(self.settings.data_dir / 'public.json', state)
        return state

    def run(self, duration=None):
        started = time.monotonic()
        while not self.meta and not self.stop_event.is_set():
            try:
                self.bootstrap()
            except Exception as exc:
                self.errors['discovery'] = type(exc).__name__
                self.publish()
                logging.warning('DISCOVERY unavailable %s', type(exc).__name__)
                if duration and time.monotonic() - started >= duration: return
                self.stop_event.wait(30)
        due = {uf: 0. for uf in ['br'] + UFS}
        if self.first_meta:
            due.update({f'r1:{uf}':self.restored_due(f'r1:{uf}',86400) if uf in self.first_results and self.first_results[uf][1]['final'] else 0. for uf in ['br']+UFS})
        last_publish = 0.
        directory_due = time.monotonic()
        try:
            while not self.stop_event.is_set() and (duration is None or time.monotonic()-started < duration):
                now = time.monotonic()
                ready_primary = [uf for uf in ['br']+UFS if due[uf] <= now]
                ready_first=[uf for uf,when in due.items() if uf.startswith('r1:') and uf.count(':')==1 and when<=now]
                ready_municipal = [uf for uf, when in due.items() if ':' in uf and uf not in ready_first and when <= now]
                uf = 'br' if 'br' in ready_primary else (min(ready_primary,key=lambda x:due[x]) if ready_primary else
                    (min(ready_first,key=lambda x:due[x]) if ready_first else
                    (min(ready_municipal,key=lambda x:due[x]) if ready_municipal else None)))
                # Discover only after the primary initial sweep; discovery failure
                # cannot stop BR/UF collection. Retry with the same origin backoff.
                if not ready_primary and self.settings.municipal_enabled and not self.directory and now >= directory_due:
                    try:
                        self.directory = discover_municipalities(self.client,self.meta,self.settings)
                        if self.first_meta:
                            try:
                                self.first_directory=discover_municipalities(self.client,self.first_meta,self.settings)
                            except Exception as exc:
                                self.errors['first_municipal_discovery']=type(exc).__name__
                        count = sum(map(len,self.directory.values()))
                        self.municipal_interval = municipal_sweep_interval(self.settings,count*2 if self.first_meta else count)
                        for parent in sorted(self.directory,key=lambda u:(len(self.directory[u]),u)):
                            for code in self.directory[parent]:
                                due[f'{parent}:{code}']=self.restored_due(f'{parent}:{code}',self.municipal_interval)
                                if code in self.first_directory.get(parent,[]):
                                    key=f'r1:{parent}:{code}'
                                    due[key]=self.restored_due(key,86400) if f'{parent}:{code}' in self.first_results and self.first_results[f'{parent}:{code}'][1]['final'] else 0.
                        self.errors.pop('municipal_discovery',None)
                        logging.info('MUNICIPAL discovery count=%d planned_sweep=%.1fs',count,self.municipal_interval)
                    except Exception as exc:
                        directory_due = max(now+300,getattr(exc,'retry_at',0))
                        self.errors['municipal_discovery'] = type(exc).__name__
                        logging.warning('MUNICIPAL discovery unavailable %s',type(exc).__name__)
                if uf:
                    try:
                        self.poll(uf)
                    except BackoffError as exc:
                        due[uf] = exc.retry_at
                        self.errors[uf] = f'HTTP {exc.status or "backoff"}; aguardando nova tentativa'
                    except Exception as exc:
                        due[uf] = now + 300
                        self.errors[uf] = type(exc).__name__
                        logging.exception('POLL failed uf=%s', uf)
                    else:
                        interval=(86400 if self.first_results[uf[3:]][1]['final'] else 3600) if uf.startswith('r1:') else (self.municipal_interval if ':' in uf else (self.settings.br_interval if uf == 'br' else self.settings.uf_interval))
                        due[uf] = time.monotonic() + interval
                if time.monotonic() - last_publish >= 5:
                    self.publish()
                    last_publish = time.monotonic()
                if not uf:
                    self.stop_event.wait(.25)
            self.publish(force_project=True)
        finally:
            if self.history_thread:
                self.history_thread.join(timeout=5)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Ingestor().run(args.duration)

if __name__ == '__main__': main()
