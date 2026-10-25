import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from .schemas import UnifiedResult, Candidate

FIELD_MAP = {'candidate_id': ('n', 'numero', 'id'), 'name': ('nmu', 'nm', 'nome'),
             'votes': ('vap', 'v', 'votos')}

def number(value):
    if value is None or str(value).strip() == '':
        raise ValueError('Required numeric field is empty')
    text = str(value).strip()
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    return float(text)

def integer(value):
    n = number(value)
    if n < 0 or not n.is_integer():
        raise ValueError(f'Expected nonnegative integer, received {value!r}')
    return int(n)

def pick(d, keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    raise ValueError(f'Missing required candidate field; expected one of {keys}')

def parse_result(payload, cargo='1', expected_uf=None, expected_election=None):
    obj = UnifiedResult.model_validate(payload)
    if expected_uf and obj.cdabr.lower() != expected_uf.lower():
        raise ValueError('TSE response territory does not match requested territory')
    if expected_election and obj.ele != str(expected_election):
        raise ValueError('TSE response election does not match requested election')
    cargo_obj = next((x for x in obj.carg if str(x.get('cd')) == str(cargo)), None)
    if cargo_obj is None:
        raise ValueError(f'Cargo {cargo} missing in TSE result')
    candidates = []
    for group in cargo_obj.get('agr', []):
        for party in group.get('par', []):
            for c in party.get('cand', []):
                values = {k: pick(c, aliases) for k, aliases in FIELD_MAP.items()}
                values['votes'] = integer(values['votes'])
                values.update(elected=c.get('e') == 's', destination=c.get('dvt', ''))
                row = Candidate.model_validate(values).model_dump()
                row.update(party=party.get('sg', ''), official_status=c.get('st', ''), full_name=c.get('nm', row['name']))
                candidates.append(row)
    if not candidates:
        raise ValueError('EA20 carg.agr.par.cand has no candidates; refusing to invent zero-vote result')
    if len({c['candidate_id'] for c in candidates}) != len(candidates):
        raise ValueError('Duplicate candidate identifiers')
    sections_total, sections_counted = integer(obj.s['ts']), integer(obj.s['st'])
    if not 0 <= sections_counted <= sections_total:
        raise ValueError('Invalid section totals')
    generated = datetime.strptime(f'{obj.dg} {obj.hg}', '%d/%m/%Y %H:%M:%S').replace(tzinfo=ZoneInfo('America/Sao_Paulo'))
    valid = integer(obj.v['vv'])
    eligible = [c for c in candidates if c['destination'].casefold() in ('válido', 'valido', '')]
    eligible_sum = sum(c['votes'] for c in eligible)
    if eligible_sum != valid:
        raise ValueError(f'Candidate vote sum {eligible_sum} != official valid votes {valid}; unsupported destination/schema')
    total_electors, counted_electors = integer(obj.e['te']), integer(obj.e['est'])
    if counted_electors > total_electors:
        raise ValueError('Counted electorate exceeds total electorate')
    remaining = integer(obj.e['esnt']) if 'esnt' in obj.e else None
    if remaining is not None and remaining + counted_electors != total_electors:
        raise ValueError('Electorate remaining-bound reconciliation failed')
    bound_complete = all(k in obj.e for k in ('esnt', 'esna', 'esni')) and all(k in obj.v for k in ('vansj', 'vsan', 'vscv'))
    if integer(obj.v['tv']) > counted_electors or valid > integer(obj.v['tv']):
        raise ValueError('Vote totals exceed the reported counted electorate or total votes')
    stats = dict(election=obj.ele, uf=obj.cdabr.lower(), cargo=str(cargo), round=int(obj.t),
                 generated_at=generated.astimezone(timezone.utc).isoformat(),
                 sections_total=sections_total, sections_counted=sections_counted,
                 apurado_pct=(100 * sections_counted / sections_total if sections_total else 0),
                 electorate=total_electors, counted_electorate=counted_electors,
                 remaining_electors=remaining, valid_votes=valid,
                 bound_complete=bound_complete,
                 uncounted_electors=integer(obj.e.get('esna', 0)),
                 uninstalled_electors=integer(obj.e.get('esni', 0)),
                 unresolved_votes=sum(integer(obj.v.get(k, 0)) for k in ('vansj', 'vsan', 'vscv')),
                 final=payload.get('tf') == 's', official=payload.get('f') == 'o',
                 mathematical_flag=payload.get('md', 'n'), no_winner=payload.get('esae') == 's',
                 hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
    frame = pd.DataFrame(candidates)
    for key in ('election', 'uf', 'cargo', 'round', 'generated_at', 'apurado_pct'):
        frame[key] = stats[key]
    frame['share'] = frame.votes / valid if valid else 0.
    return frame, stats
