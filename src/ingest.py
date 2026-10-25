import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import threading
import time
import pandas as pd
from .config import Settings, UFS, build_url, discover_election_meta
from .http_client import CachedClient, BackoffError, atomic_json, utcnow
from .parse import parse_result
from .storage import Storage
from .project import project, certainty_gate
from .history import load_history

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
        self.history = pd.DataFrame()
        self.history_status = 'loading'
        self.meta = None
        self.last_projection = None
        self.last_project_time = 0
        self.timeline = []
        self.stop_event = threading.Event()
        self.history_thread = None

    def bootstrap(self):
        self.meta = discover_election_meta(self.client, self.settings)
        if self.storage.exists('votes_projected'):
            old = self.storage.query('SELECT ts, name, projected_share FROM votes_projected WHERE election = ? ORDER BY ts', [self.meta['codigo_eleicao']])
            self.timeline = old.to_dict('records')
        def history_job():
            try:
                self.history = load_history(self.settings, self.storage)
                self.history_status = f'loaded:{len(self.history)}'
            except Exception as exc:
                self.history_status = 'unavailable:' + type(exc).__name__
                logging.warning('HISTORY fallback %s', type(exc).__name__)
        self.history_thread = threading.Thread(target=history_job, name='history-download', daemon=True)
        self.history_thread.start()

    def poll(self, uf):
        url = build_url(uf, self.meta['cargo'], self.meta['codigo_eleicao'], self.settings)
        response = self.client.fetch(url)
        frame, stats = parse_result(response.payload, self.meta['cargo'], uf, self.meta['codigo_eleicao'])
        if not stats['official']:
            raise ValueError('Simulation/test data rejected on live deployment')
        if uf in self.results and stats['generated_at'] < self.results[uf][1]['generated_at']:
            raise ValueError('Older upstream generation rejected')
        previous = self.results.get(uf)
        self.results[uf] = (frame, stats)
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
                     history_status=self.history_status, errors=self.errors.copy(), checked_at=self.checked.copy(),
                     requests=dict(total=self.client.requests_total, current_rps=self.client.rps,
                                   mean_rps=self.client.requests_total / max(1, time.monotonic()-self.client.start),
                                   not_modified=self.client.not_modified, same_hash=self.client.same_hash,
                                   configured_max_rps=self.settings.rps),
                     regions=[dict(**s, checked_at=self.checked.get(uf)) for uf, (_, s) in self.results.items() if uf != 'br'])
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
                    seed = int(hashlib.sha256(json.dumps({uf:s['hash'] for uf, (_,s) in self.results.items()}, sort_keys=True).encode()).hexdigest()[:8], 16)
                    try:
                        projection = project(br, stats, regions, self.history, int(os.getenv('SIMULATION_DRAWS', '5000')), seed)
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
                    rows = pd.DataFrame(self.last_projection['candidates'])
                    rows['ts'], rows['election'] = now.isoformat(), self.meta['codigo_eleicao']
                    self.storage.ingest(rows, 'votes_projected')
                    self.storage.ingest(pd.DataFrame([dict(ts=now.isoformat(), election=self.meta['codigo_eleicao'],
                        model_version=self.last_projection['model_version'], seed=seed,
                        metadata=json.dumps(self.last_projection, ensure_ascii=False),
                        input_hashes=json.dumps({uf:s['hash'] for uf, (_,s) in self.results.items()}, sort_keys=True))]), 'simulations_meta')
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
        last_publish = 0.
        try:
            while not self.stop_event.is_set() and (duration is None or time.monotonic()-started < duration):
                now = time.monotonic()
                ready = [uf for uf, when in due.items() if when <= now]
                uf = 'br' if 'br' in ready else (min(ready, key=lambda x: due[x]) if ready else None)
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
                        due[uf] = time.monotonic() + (self.settings.br_interval if uf == 'br' else self.settings.uf_interval)
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
