"""Gold-free graph score fusion on an unchanged Hybrid/Graph candidate pool.

No embedding or neural model is imported. Query IDs, types and gold labels never
enter this module. Paths establish support, not proof of answer correctness.
"""
from __future__ import annotations

import math
from overlap_rerank import rerank_overlap


DEFAULT_WEIGHTS = (.3, .5, .2)


def score_candidates(candidates, *, weights=DEFAULT_WEIGHTS, use_graph=True):
    """Use cached query coverage and observed paths, without seed-rank reuse.

    H = 61/(60+hybrid_rank), C = IDF query coverage of title+chunk.
    G = max_path(C/(1+.12*hop) * .25**PART_OF_count).
    F = wH*H + wC*C + wG*G. Missing branches contribute zero.
    The graph support magnitude survives fusion; no rank of G is substituted.
    """
    if (len(weights) != 3 or any(not math.isfinite(w) or w < 0 for w in weights)
            or not math.isclose(sum(weights), 1.)):
        raise ValueError('weights must be finite, nonnegative and sum to one')
    rows = [c for c in candidates if c['fusion_rank'] is not None]
    if len({c['chunk_id'] for c in rows}) != len(rows):
        raise ValueError('duplicate candidate')
    if sorted(c['fusion_rank'] for c in rows) != list(range(1, len(rows)+1)):
        raise ValueError('noncontiguous candidate ranks')
    scored = []
    for c in rows:
        rank = c['hybrid_rank']
        if rank is not None and (type(rank) is not int or rank < 1):
            raise ValueError('invalid Hybrid rank')
        coverage = c['rerank_features']['chunk_coverage']
        if not math.isfinite(coverage) or not 0 <= coverage <= 1 + 1e-12:
            raise ValueError('invalid query coverage')
        hybrid = 61/(60+rank) if rank is not None else 0.
        graph = 0.
        if use_graph and c['graph_rank'] is not None and not c['graph_excluded_by_cap']:
            for path in c['paths']:
                hops = path['graph_hops']
                if type(hops) is not int or hops < 0 or hops != len(path['path_edges']):
                    raise ValueError('invalid graph hops')
                broad_links = sum(e['relation'] == 'PART_OF' for e in path['path_edges'])
                graph = max(graph, coverage / (1 + .12*hops) * .25**broad_links)
        score = weights[0]*hybrid + weights[1]*coverage + weights[2]*graph
        scored.append(dict(chunk_id=c['chunk_id'], score=score,
                           hybrid_support=hybrid, chunk_coverage=coverage, graph_support=graph))
    return sorted(scored, key=lambda c: (-c['score'], c['chunk_id']))


def rank(candidates, chunks, *, weights=DEFAULT_WEIGHTS, use_graph=True, penalty=.15):
    scored = score_candidates(candidates, weights=weights, use_graph=use_graph)
    rows = [dict(chunk_id=c['chunk_id'], fusion_rank=i, fusion_score=c['score'])
            for i, c in enumerate(scored, 1)]
    order, trace = rerank_overlap(rows, chunks, penalty=penalty)
    return order, dict(features=scored, selections=trace)


def apply(row, chunks, *, top_k=10):
    """Apply the development-selected policy to a freshly built or cached H3 row."""
    order, trace = rank(row['candidates'], chunks)
    # Full paths remain in the immutable H3 input; do not duplicate 100 MB of
    # traversal data for every scoring experiment.
    fields = ('chunk_id','hybrid_rank','graph_rank','fusion_rank','fusion_score',
              'rrf_rank','graph_excluded_by_cap')
    result = dict(query_id=row['query_id'], retriever='hybrid_graph',
                  candidates=[{k:c[k] for k in fields} for c in row['candidates']])
    ranks = {cid: i for i, cid in enumerate(order, 1)}
    features = {c['chunk_id']: c for c in trace['features']}
    for candidate in result['candidates']:
        cid = candidate['chunk_id']
        candidate['previous_fusion_rank'] = candidate['fusion_rank']
        candidate['previous_fusion_score'] = candidate['fusion_score']
        candidate['fusion_rank'] = ranks.get(cid)
        candidate['fusion_score'] = 1/ranks[cid] if cid in ranks else None
        candidate['score_revision_features'] = features.get(cid)
        candidate['selected'] = cid in ranks and ranks[cid] <= top_k
    result['hits'] = [dict(query_id=row['query_id'], retriever='hybrid_graph',
        chunk_id=cid, document_id=chunks[cid]['document_id'], rank=i, score=1/i,
        title=chunks[cid]['title'], snippet=chunks[cid]['text'][:300],
        rerank='coverage', evidence_status='retrieved_not_entailment_verified')
        for i,cid in enumerate(order[:top_k],1)]
    result['score_interpretation'] = 'reciprocal ordinal; base and greedy scores in score_revision_trace'
    result['score_revision_trace'] = dict(selections=trace['selections'])
    result['scoring_policy'] = 'coverage-path-overlap'
    return result
