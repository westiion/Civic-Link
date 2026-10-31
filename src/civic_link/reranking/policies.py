"""Experimental rescoring of observed graph paths, with no access to evaluation gold.

The candidate pool stays fixed for score ablations. Pool-size experiments must
explicitly opt out. These policies are hypotheses, not tuned production defaults.
"""
from __future__ import annotations

import math

RELATION_WEIGHTS = {
    'GROUNDED_BY': 1.0, 'REFERENCES': 1.0, 'REQUIRES': 0.8,
    'HANDLED_BY': 0.5, 'PART_OF': 0.25,
}
POLICIES = ('native', 'uniform', 'structure', 'relations')


def path_score(path, policy='native'):
    if policy not in POLICIES:
        raise ValueError('unknown graph policy')
    c = path['score_components']
    if policy == 'native':
        score = path['score']
    else:
        score = 1 / (1 + .12 * path['graph_hops'])
        if policy != 'structure':
            score *= (.45 + c['relevance']) * c['condition_factor']
        if policy == 'relations':
            score *= c['seed_score']
            score *= math.prod(RELATION_WEIGHTS[e['relation']] for e in path['path_edges'])
    if not math.isfinite(score) or score < 0:
        raise ValueError('non-finite or negative graph score')
    return score


def rerank(row, *, policy='native', fusion='rrf', graph_k=100,
           fixed_pool=True, rrf_constant=60, graph_weight=.5):
    """Return all ranked candidates. No question labels or gold are accepted."""
    if policy not in POLICIES or fusion not in ('rrf', 'linear'):
        raise ValueError('unknown scoring policy')
    if type(graph_k) is not int or graph_k < 1 or rrf_constant < 0:
        raise ValueError('invalid candidate cap or RRF constant')
    if not 0 <= graph_weight <= 1:
        raise ValueError('graph weight must be within [0, 1]')
    candidates = row['candidates']
    ids = [c['chunk_id'] for c in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate candidate')
    hybrid = {c['chunk_id']: c['hybrid_rank'] for c in candidates if c['hybrid_rank'] is not None}
    scores = {c['chunk_id']: max(path_score(p, policy) for p in c['paths'])
              for c in candidates if c['paths']}
    graph_order = sorted(scores, key=lambda cid: (-scores[cid], cid))
    if fixed_pool:
        # Reorder exactly the previously admitted graph candidates; do not let
        # changed graph scores covertly introduce new candidates.
        kept = {c['chunk_id'] for c in candidates
                if c['graph_rank'] is not None and not c['graph_excluded_by_cap']}
    else:
        kept = set(graph_order[:graph_k])
    graph = {cid: i for i, cid in enumerate((c for c in graph_order if c in kept), 1)}
    pool = set(hybrid) | kept
    if fusion == 'rrf':
        final = {cid: (1 / (rrf_constant + hybrid[cid]) if cid in hybrid else 0.) +
                     (1 / (rrf_constant + graph[cid]) if cid in graph else 0.) for cid in pool}
    else:
        max_graph = max((scores[cid] for cid in kept), default=1.) or 1.
        # Hybrid rank is used once; the uniform policy removes it from graph scores.
        final = {cid: (1 - graph_weight) * ((rrf_constant + 1) / (rrf_constant + hybrid[cid])
                                         if cid in hybrid else 0.) +
                     graph_weight * (scores[cid] / max_graph if cid in kept else 0.) for cid in pool}
    return sorted(pool, key=lambda cid: (-final[cid], cid))


def stored_order(row):
    included = [c for c in row['candidates'] if c['fusion_rank'] is not None]
    included.sort(key=lambda c: c['fusion_rank'])
    if [c['fusion_rank'] for c in included] != list(range(1, len(included) + 1)):
        raise ValueError('noncontiguous stored fusion ranks')
    return [c['chunk_id'] for c in included]


def rank_metrics(requirements, order):
    if not requirements or any(not g for g in requirements):
        raise ValueError('answerable questions require nonempty evidence groups')
    if len(order) != len(set(order)):
        raise ValueError('duplicate ranked chunk')
    ranks = {cid: i for i, cid in enumerate(order, 1)}
    group_ranks = [min((ranks[c] for c in group if c in ranks), default=None) for group in requirements]
    first = min((r for r in group_ranks if r is not None), default=None)
    result = dict(group_ranks=group_ranks, first_rank=first, pool_size=len(order),
                  pool_recall=sum(r is not None for r in group_ranks) / len(group_ranks))
    for k in (10, 100):
        found = [r is not None and r <= k for r in group_ranks]
        result[str(k)] = dict(recall=sum(found) / len(found), all_evidence=int(all(found)),
                             mrr=1 / first if first is not None and first <= k else 0.,
                             completion_rr=1 / max(group_ranks) if all(found) else 0.)
    missed = [r for r in group_ranks if r is None or r > 10]
    result['failure_stage'] = ('complete' if not missed else 'candidate_missing' if all(r is None for r in missed)
                               else 'ranking_only' if all(r is not None for r in missed) else 'mixed')
    return result
