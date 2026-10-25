"""Load only the presidential member of the official 2022 archive using HTTP Range."""
import io
import hashlib
import json
import logging
import time
import zipfile
import pandas as pd
import requests
from .http_client import atomic_json

class RangeFile(io.RawIOBase):
    def __init__(self, session, url, cache):
        self.session, self.url, self.cache, self.position = session, url, cache, 0
        r = session.head(url, timeout=30)
        r.raise_for_status()
        self.size = int(r.headers['Content-Length'])
        self.etag = r.headers.get('ETag', '')
        self.records = []

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.position
    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else ((self.position if whence == 1 else self.size) + offset)
        return self.position
    def read(self, n=-1):
        start = self.position
        end = self.size - 1 if n < 0 else min(self.size - 1, start + n - 1)
        if start > end:
            return b''
        time.sleep(.5)
        headers = {'Range': f'bytes={start}-{end}'}
        if self.etag: headers['If-Range'] = self.etag
        r = self.session.get(self.url, headers=headers, timeout=90)
        r.raise_for_status()
        if r.status_code != 206 or len(r.content) != end - start + 1:
            raise ValueError('Historical archive does not honor range; refusing full 600MB download')
        digest = hashlib.sha256(r.content).hexdigest()
        self.cache.mkdir(parents=True, exist_ok=True)
        (self.cache / f'{digest}.bin').write_bytes(r.content)
        self.records.append(dict(start=start, end=end, hash=digest, bytes=len(r.content)))
        self.position = end + 1
        return r.content

def load_history(settings, storage):
    if storage.exists('historical_2022'):
        return storage.query('SELECT * FROM historical_2022')
    cache = settings.data_dir / 'historical'
    cache.mkdir(parents=True, exist_ok=True)
    processed = cache / 'presidente-2022.parquet'
    if processed.exists():
        result = pd.read_parquet(processed)
        storage.ingest(result, 'historical_2022')
        return result
    session = requests.Session()
    session.headers['User-Agent'] = settings.user_agent
    api = 'https://dadosabertos.tse.jus.br/api/3/action/package_show?id=resultados-2022'
    r = session.get(api, timeout=30)
    r.raise_for_status()
    raw = r.content
    digest = hashlib.sha256(raw).hexdigest()
    (cache / f'{digest}.json').write_bytes(raw)
    catalog = r.json()['result']
    resource = next(x for x in catalog['resources'] if x['name'] == 'Votação nominal por município e zona')
    reader = RangeFile(session, resource['url'], cache)
    with zipfile.ZipFile(reader) as z:
        name = next(n for n in z.namelist() if n.lower().endswith('_br.csv'))
        # Reading a complete selected compressed member avoids thousands of tiny range requests.
        entry = z.getinfo(name)
        reader.seek(entry.header_offset)
        import struct, zlib
        local_header = reader.read(30)
        lengths = struct.unpack('<HH', local_header[26:30])
        reader.seek(entry.header_offset + 30 + sum(lengths))
        compressed = reader.read(entry.compress_size)
        body = zlib.decompress(compressed, -15) if entry.compress_type == zipfile.ZIP_DEFLATED else compressed
        if (zlib.crc32(body) & 0xffffffff) != entry.CRC:
            raise ValueError('Historical CSV CRC mismatch')
    (cache / f'{hashlib.sha256(body).hexdigest()}.csv').write_bytes(body)
    cols = ['NR_TURNO', 'SG_UF', 'CD_CARGO', 'NR_CANDIDATO', 'NM_CANDIDATO', 'QT_VOTOS_NOMINAIS']
    parts = []
    for chunk in pd.read_csv(io.BytesIO(body), encoding='latin1', sep=';', usecols=cols, chunksize=50000, dtype=str):
        chunk = chunk[chunk.CD_CARGO == '1'].copy()
        chunk['QT_VOTOS_NOMINAIS'] = pd.to_numeric(chunk.QT_VOTOS_NOMINAIS)
        parts.append(chunk.groupby(['NR_TURNO', 'SG_UF', 'NR_CANDIDATO', 'NM_CANDIDATO'], as_index=False).QT_VOTOS_NOMINAIS.sum())
    result = pd.concat(parts).groupby(['NR_TURNO', 'SG_UF', 'NR_CANDIDATO', 'NM_CANDIDATO'], as_index=False).QT_VOTOS_NOMINAIS.sum()
    result = result.rename(columns=dict(NR_TURNO='round', SG_UF='uf', NR_CANDIDATO='candidate_id', NM_CANDIDATO='full_name', QT_VOTOS_NOMINAIS='votes'))
    result['round'] = result['round'].astype(int)
    result['uf'] = result.uf.str.lower()
    result['share'] = result.votes / result.groupby(['round', 'uf']).votes.transform('sum')
    result.to_parquet(processed, index=False)
    atomic_json(cache / 'source.json', dict(catalog=api, url=resource['url'], member=name, etag=reader.etag, ranges=reader.records,
                                         csv_sha256=hashlib.sha256(body).hexdigest(), rows=len(result)))
    storage.ingest(result, 'historical_2022')
    logging.info('HISTORY official_2022 rows=%d territories=%d', len(result), result.uf.nunique())
    return result

def load_municipal_history(settings, storage):
    """Reuse the hash-verified presidential CSV already fetched for UF history."""
    table = 'historical_municipal_2022'
    if storage.exists(table):
        return storage.query(f'SELECT * FROM {table}')
    cache = settings.data_dir / 'historical'
    source = json.loads((cache / 'source.json').read_text(encoding='utf-8'))
    csv = cache / (source['csv_sha256'] + '.csv')
    if hashlib.sha256(csv.read_bytes()).hexdigest() != source['csv_sha256']:
        raise ValueError('Historical municipal CSV hash mismatch')
    result = aggregate_municipal_history(csv)
    result.to_parquet(cache / 'municipios-presidente-2022.parquet', index=False)
    storage.ingest(result, table)
    logging.info('HISTORY municipal_2022 rows=%d territories=%d',len(result),result.uf.nunique())
    return result

def aggregate_municipal_history(csv):
    columns = ['NR_TURNO','SG_UF','CD_MUNICIPIO','CD_CARGO','NR_CANDIDATO','NM_CANDIDATO','QT_VOTOS_NOMINAIS_VALIDOS']
    group = ['NR_TURNO','SG_UF','CD_MUNICIPIO','NR_CANDIDATO','NM_CANDIDATO']
    parts = []
    for chunk in pd.read_csv(csv,encoding='latin1',sep=';',usecols=columns,chunksize=50000,dtype=str):
        chunk = chunk[chunk.CD_CARGO=='1'].copy()
        chunk['QT_VOTOS_NOMINAIS_VALIDOS'] = pd.to_numeric(chunk.QT_VOTOS_NOMINAIS_VALIDOS,errors='raise')
        if (chunk.QT_VOTOS_NOMINAIS_VALIDOS < 0).any():
            raise ValueError('Negative historical votes')
        parts.append(chunk.groupby(group,as_index=False).QT_VOTOS_NOMINAIS_VALIDOS.sum())
    result = pd.concat(parts).groupby(group,as_index=False).QT_VOTOS_NOMINAIS_VALIDOS.sum()
    result = result.rename(columns={'NR_TURNO':'round','SG_UF':'parent_uf','CD_MUNICIPIO':'municipality',
        'NR_CANDIDATO':'candidate_id','NM_CANDIDATO':'full_name','QT_VOTOS_NOMINAIS_VALIDOS':'votes'})
    result['round'] = result['round'].astype(int)
    result['parent_uf'] = result.parent_uf.str.lower()
    result['municipality'] = result.municipality.str.zfill(5)
    result['uf'] = result.parent_uf + ':' + result.municipality
    result['share'] = result.votes / result.groupby(['round','uf']).votes.transform('sum')
    return result.fillna({'share':0.})
