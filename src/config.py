from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import logging
import os
from dotenv import load_dotenv

load_dotenv()
UFS = 'ac al ap am ba ce df es go ma mt ms mg pa pb pr pe pi rj rn rs ro rr sc sp se to zz'.split()

@dataclass
class Settings:
    base_url: str = field(default_factory=lambda: os.getenv('TSE_BASE_URL', 'https://resultados.tse.jus.br/oficial').rstrip('/'))
    cycle: str = field(default_factory=lambda: os.getenv('ELECTION_CYCLE', 'ele2026'))
    round: str = field(default_factory=lambda: os.getenv('ELECTION_ROUND', 'auto'))
    cargo: str = field(default_factory=lambda: os.getenv('CARGO', '1'))
    contact: str = field(default_factory=lambda: os.getenv('TSE_CONTACT_EMAIL', ''))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', 'data')))
    br_interval: float = field(default_factory=lambda: max(15., float(os.getenv('BR_INTERVAL', '15'))))
    uf_interval: float = field(default_factory=lambda: max(60., float(os.getenv('UF_INTERVAL', '60'))))
    projection_interval: float = field(default_factory=lambda: max(60., float(os.getenv('PROJECTION_INTERVAL', '60'))))
    rps: float = field(default_factory=lambda: min(10., max(.1, float(os.getenv('MAX_REQUESTS_PER_SECOND', '2')))))

    @property
    def user_agent(self):
        if '@' not in self.contact or '\n' in self.contact or '\r' in self.contact:
            raise ValueError('Set TSE_CONTACT_EMAIL to a real contact email before live ingestion')
        return f'EleicaoForecast/1.0 (+{self.contact})'

def discover_election_meta(client=None, settings=None, payload=None, today=None):
    s = settings or Settings()
    if s.cargo != '1':
        raise ValueError('This national model supports Presidente (CARGO=1) only; other contests require separate territorial/seat rules')
    if payload is None:
        if client is None:
            from .http_client import CachedClient
            client = CachedClient(s)
        payload = client.fetch(s.base_url + '/comum/config/ele-c.json').payload
    today = today or datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    matches = []
    for pleito in payload.get('pl', []):
        if pleito.get('c') != s.cycle:
            continue
        date = datetime.strptime(pleito['dt'], '%d/%m/%Y').date()
        for e in pleito.get('e', []):
            cargos = [c for a in e.get('abr', []) for c in a.get('cp', [])]
            if not any(str(c.get('cd')) == s.cargo for c in cargos):
                continue
            if s.round != 'auto' and str(e['t']) != s.round:
                continue
            matches.append((date, int(e['t']), e))
    if not matches:
        raise ValueError(f'EA11 contains no matching election for {s.cycle}, cargo {s.cargo}, round {s.round}')
    past = [m for m in matches if m[0] <= today]
    date, turno, e = max(past or matches, key=lambda x: (x[0], x[1])) if past else min(matches, key=lambda x: x[0])
    meta = dict(cargo=s.cargo, codigo_eleicao=str(e['cd']), ufs=UFS, round=turno,
                name=e['nm'], cycle=s.cycle, election_date=date.isoformat())
    logging.info('DISCOVERY %s', meta)
    return meta

def build_url(uf, cargo, eleicao, settings=None, cycle=None):
    s = settings or Settings()
    uf = uf.lower()
    if uf not in UFS + ['br']:
        raise ValueError(f'Unknown UF: {uf}')
    return f'{s.base_url}/{cycle or s.cycle}/{int(eleicao)}/dados/{uf}/{uf}-c{int(cargo):04d}-e{int(eleicao):06d}-u.json'
