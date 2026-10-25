"""Replace complete, reconciled UFs with municipalities; never add both levels."""
from datetime import datetime


def municipal_sweep_interval(settings, count):
    # Leave 20% headroom after BR/UF polling. Slow endpoints can extend a sweep;
    # actual checked_at ages, not the planned interval, decide model eligibility.
    primary_rate = 1/settings.br_interval + 28/settings.uf_interval
    spare = max(.05, settings.rps-primary_rate)
    return max(settings.municipal_interval, count/spare*1.25)


def refine_regions(regions, municipalities, directory, checked, errors, now, interval):
    selected, refined, rejected = dict(regions), {}, {}
    for uf, (parent_frame, parent) in regions.items():
        codes = directory.get(uf, [])
        if not codes:
            continue
        keys = [f'{uf}:{code}' for code in codes]
        if any(k not in municipalities for k in keys):
            rejected[uf] = 'coleta municipal incompleta'
            continue
        reason = None
        for key in keys:
            stats = municipalities[key][1]
            age = (now-datetime.fromisoformat(checked[key])).total_seconds() if key in checked else float('inf')
            generated = datetime.fromisoformat(stats['generated_at'])
            parent_time = datetime.fromisoformat(parent['generated_at'])
            if key in errors or not -60 <= age <= interval*1.5:
                reason = 'consulta municipal antiga ou com erro'
                break
            if (generated-now).total_seconds() > 60 or (not stats['final'] and abs((generated-parent_time).total_seconds()) > 300):
                reason = 'gerações municipal e UF incompatíveis'
                break
        if reason:
            rejected[uf] = reason
            continue
        fields = ('electorate','counted_electorate','valid_votes','sections_total','sections_counted',
                  'uncounted_electors','uninstalled_electors','unresolved_votes')
        if any(sum(municipalities[k][1][f] for k in keys) != parent[f] for f in fields):
            rejected[uf] = 'totais municipais ainda não reconciliados com a UF'
            continue
        # Total valid counts alone can conceal opposing candidate revisions.
        totals = {}
        for key in keys:
            for row in municipalities[key][0].itertuples():
                totals[row.candidate_id] = totals.get(row.candidate_id,0) + int(row.votes)
        if totals != {r.candidate_id:int(r.votes) for r in parent_frame.itertuples()}:
            rejected[uf] = 'votos por candidato não reconciliados com a UF'
            continue
        del selected[uf]
        selected.update({k:municipalities[k] for k in keys})
        refined[uf] = len(keys)
    return selected, refined, rejected
