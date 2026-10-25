"""Heuristic geographic model. Its Monte Carlo probabilities are not a certainty proof."""
import hashlib
import itertools
import json
import os
import numpy as np
import pandas as pd

MODEL_VERSION = 'geo-dirichlet-remaining-v1'

def certainty_gate(frame, stats, fresh=True):
    result = dict(status='undecided', candidate_id=None, goal_satisfied=False,
                  reason='Os votos ainda podem alterar o resultado.', remaining_bound=None)
    if not fresh or not stats.get('official') or stats.get('uf') != 'br' or stats.get('cargo') != '1':
        result['reason'] = 'Exige arquivo oficial nacional válido e consulta recente.'
        return result
    if stats.get('no_winner'):
        result['reason'] = 'TSE informa eleição sem atribuição de eleito.'
        return result
    elected = frame[frame.elected]
    if len(elected) == 1 and (stats.get('final') or stats.get('mathematical_flag') == 'e'):
        result.update(status='official_elected', candidate_id=str(elected.iloc[0].candidate_id),
                      goal_satisfied=True, reason='TSE indica candidato eleito no arquivo nacional EA20.')
        return result
    # Do not convert md=s (runoff defined) into an elected candidate.
    if not stats.get('bound_complete', False) or stats.get('remaining_electors') is None or stats.get('unresolved_votes', 0):
        result['reason'] = 'Limite conservador indisponível ou votos pendentes de decisão.'
        return result
    eligible = frame[frame.destination.str.casefold().isin(['válido', 'valido', ''])].sort_values('votes', ascending=False)
    if len(eligible) < 2 or not stats.get('valid_votes'):
        return result
    leader, runner = eligible.iloc[0], eligible.iloc[1]
    # Each uncounted/uninstalled elector can cast at most one presidential vote.
    remaining = stats['remaining_electors'] + stats.get('uncounted_electors', 0) + stats.get('uninstalled_electors', 0)
    result['remaining_bound'] = int(remaining)
    if stats['round'] == 1:
        secure = 2 * int(leader.votes) > int(stats['valid_votes']) + remaining
        expression = f'2 × {int(leader.votes):,} > {stats["valid_votes"]:,} + {remaining:,}'
    else:
        secure = int(leader.votes) > int(runner.votes) + remaining
        expression = f'{int(leader.votes):,} > {int(runner.votes):,} + {remaining:,}'
    result['inequality'] = expression
    if secure:
        result.update(status='mathematically_secured', candidate_id=str(leader.candidate_id), goal_satisfied=True,
                      reason='Vantagem supera o limite máximo dos votos restantes; condicionado aos dados publicados, sem revisões futuras.')
    return result

def concentration(apurado_pct):
    key, default = ('ALPHA_LOW', 20) if apurado_pct < 30 else (('ALPHA_HIGH', 500) if apurado_pct > 80 else ('ALPHA_MEDIUM', 100))
    value = float(os.getenv(key, default))
    if not np.isfinite(value) or value <= 0:
        raise ValueError('Dirichlet concentration must be finite and positive')
    return value

def normalize(values):
    values = np.maximum(np.asarray(values, dtype=float), 1e-8)
    return values / values.sum()

def project(national, stats, regions, historical=None, draws=5000, seed=42):
    if draws < 5000:
        raise ValueError('At least 5000 simulations are required')
    national = national[national.destination.str.casefold().isin(['válido', 'valido', ''])].sort_values('candidate_id').copy()
    ids = list(national.candidate_id)
    if len(ids) < 2:
        raise ValueError('At least two eligible candidates required')
    observed = national.votes.to_numpy(dtype=float)
    baseline = observed / observed.sum() if observed.sum() else np.ones(len(ids)) / len(ids)
    historical = historical if historical is not None else pd.DataFrame()
    mapping = json.loads(os.getenv('HISTORICAL_CANDIDATE_MAP', '{}'))
    priors, flags, confident = {}, [], []
    for uf, (votes, s) in regions.items():
        partial = votes.set_index('candidate_id').votes.reindex(ids, fill_value=0).to_numpy(dtype=float)
        old = historical[(historical.uf == uf) & (historical['round'] == stats['round'])] if not historical.empty else historical
        prior = baseline.copy()
        if not old.empty:
            old_ids = old.set_index('candidate_id')
            mapped = []
            for row in national.itertuples():
                # Exact same-person match unless operator explicitly supplies a proxy mapping.
                exact = old[old.full_name.str.casefold() == row.full_name.casefold()]
                historical_id = mapping.get(row.candidate_id)
                mapped.append(float(old_ids.loc[historical_id, 'votes']) if historical_id in old_ids.index else
                              (float(exact.iloc[0].votes) if len(exact) == 1 else np.nan))
            if all(np.isfinite(mapped)):
                prior = normalize(mapped)
            else:
                flags.append(f'{uf.upper()}: sem correspondência histórica completa; prior nacional')
        else:
            flags.append(f'{uf.upper()}: histórico indisponível; prior nacional')
        priors[uf] = prior
        if s['apurado_pct'] > 5 and partial.sum() > 0:
            confident.append((partial / partial.sum() - prior, partial.sum()))
    swing = np.average([x[0] for x in confident], axis=0, weights=[x[1] for x in confident]) if confident else np.zeros(len(ids))
    remaining_by_candidate = np.zeros(len(ids))
    for uf, (votes, s) in regions.items():
        partial = votes.set_index('candidate_id').votes.reindex(ids, fill_value=0).to_numpy(dtype=float)
        share = partial / partial.sum() if s['apurado_pct'] > 5 and partial.sum() else normalize(priors[uf] + swing)
        # Electorate coverage, not section percentage, estimates remaining vote volume.
        rate = partial.sum() / s['counted_electorate'] if s['counted_electorate'] > 0 and s['apurado_pct'] > 5 else .75
        pending = max(0, s['electorate'] - s['counted_electorate']) + s.get('uncounted_electors', 0)
        remaining_by_candidate += share * pending * min(1., max(0., rate))
    if not regions:
        remaining_by_candidate = baseline * max(0, stats['electorate'] - stats['counted_electorate']) * .75
        flags.append('Sem UFs: modelo nacional provisório; não é extrapolação geográfica completa')
    if stats['sections_counted'] == stats['sections_total'] and not stats.get('uncounted_electors', 0):
        remaining_by_candidate[:] = 0
    remaining_total = remaining_by_candidate.sum()
    scale = concentration(stats['apurado_pct'])
    rng = np.random.default_rng(seed)
    if remaining_total > 0:
        simulated_remaining = rng.dirichlet(normalize(remaining_by_candidate) * scale, size=draws) * remaining_total
        counts = observed + simulated_remaining
    else:
        counts = np.tile(observed, (draws, 1))
    denominators = counts.sum(axis=1)
    if not np.all(denominators > 0):
        raise ValueError('No votes or electorate available for a projection')
    simulated = counts / denominators[:, None]
    expected = normalize(observed + remaining_by_candidate)
    order = np.argsort(-simulated, axis=1)
    maxima = simulated.max(axis=1)
    unique = (simulated == maxima[:, None]).sum(axis=1) == 1
    majority = maxima > .5
    runoff = ~majority if stats['round'] == 1 else np.zeros(draws, dtype=bool)
    output = []
    for i, row in enumerate(national.itertuples()):
        first_wins = int(((simulated[:, i] > .5) & unique).sum())
        wins = int(((order[:, 0] == i) & unique).sum())
        qualifies = int((np.any(order[:, :2] == i, axis=1) & runoff).sum())
        output.append(dict(candidate_id=row.candidate_id, name=row.name, party=row.party, votes=int(row.votes),
                           observed_share=float(baseline[i]), naive_share=float(baseline[i]), projected_share=float(expected[i]),
                           ci_low=float(np.quantile(simulated[:, i], .05)), ci_high=float(np.quantile(simulated[:, i], .95)),
                           p_win=(wins + .5) / (draws + 1),
                           p_first_round=(first_wins + .5) / (draws + 1) if stats['round'] == 1 else None,
                           p_runoff=qualifies / draws if stats['round'] == 1 else None))
    pairs = []
    if stats['round'] == 1:
        for a, b in itertools.combinations(range(len(ids)), 2):
            n = int((runoff & np.isin(order[:, 0], [a, b]) & np.isin(order[:, 1], [a, b])).sum())
            pairs.append(dict(pair=[ids[a], ids[b]], names=[output[a]['name'], output[b]['name']],
                              probability=n / draws, conditional_probability=n / int(runoff.sum()) if runoff.sum() else None))
        pairs.sort(key=lambda x: -x['probability'])
    return dict(candidates=sorted(output, key=lambda x: -x['projected_share']), pairings=pairs,
                p_any_runoff=float(runoff.mean()) if stats['round'] == 1 else None,
                draws=draws, seed=seed, alpha_scale=scale, model_version=MODEL_VERSION,
                historical_mapping=mapping, assumptions=sorted(set(flags)),
                remaining_valid_estimate=float(remaining_total), geographic_units=len(regions),
                calibration='Heurística não calibrada empiricamente. IC de 90% condicionado ao modelo; não inclui todos os erros sistemáticos.')
