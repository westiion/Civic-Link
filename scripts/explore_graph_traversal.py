"""Offline seed/traversal ablations; preserve the graph and frozen Hybrid input.

Reverse PART_OF is a query-time navigation step, not a new factual relation.
No BGE score is invented for newly retrieved question/chunk pairs.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
from collections import defaultdict, deque
import gzip
import json
import math
from pathlib import Path
import time

from civic_link.common import ROOT, digest, input_bytes, read_json, read_jsonl, write_json
from civic_link.graph.build import compact, load_graph
from civic_link.graph.retriever import GraphRetriever
from civic_link.retrieval.hybrid_graph import HybridGraph
from civic_link.reranking.policies import rank_metrics, stored_order
from civic_link.tokenization import token_set
from explore_graph_weights import json_lines
from week7_hypothesis_support import BASELINE, SOURCE, load_frozen


VARIANTS = dict(native={}, uniform=dict(uniform=True),
                more_seeds=dict(seed_policy='more'), ranked_seeds=dict(seed_policy='ranked'),
                reverse5=dict(reverse=5), reverse20=dict(reverse=20),
                ranked_reverse5=dict(seed_policy='ranked', reverse=5))


class Probe:
    def __init__(self, r):
        self.r = r
        self.entries = HybridGraph(r).entries
        self.children = defaultdict(list)
        for u, v, _, edge in r.graph.edges(keys=True, data=True):
            if edge['relation'] == 'PART_OF':
                self.children[v].append(u)
        self.article_chunks = {}
        for node in r.graph:
            self.article_chunks[node] = sorted({cid for eid in r.evidence['node_bindings'].get(node, [])
                if r.evidence['records'][eid]['role'] == 'article_context'
                for cid in r.evidence['records'][eid]['chunk_ids']})
        self.label_terms = {n: [token_set(a) for a in (d.get('aliases') or [d['label']])]
                            for n, d in r.graph.nodes(data=True)}

    def select_seeds(self, hybrid, qterms, policy):
        seeds, chosen = [], set()
        if policy == 'ranked':
            candidates = {}
            for hit in hybrid[:20]:
                for node, _, _, _ in self.entries[hit['chunk_id']]:
                    match = max((len(qterms & terms)/max(1, len(terms))
                                 for terms in self.label_terms[node]), default=0.)
                    value = .7/math.sqrt(hit['rank']) + .3*match
                    row = dict(node_id=node, entry_chunk_id=hit['chunk_id'],
                               hybrid_rank=hit['rank'], score=1/hit['rank'])
                    if node not in candidates or value > candidates[node][0]:
                        candidates[node] = (value, row)
            entry_counts = defaultdict(int)
            for _, row in sorted(candidates.values(), key=lambda x: (-x[0], x[1]['node_id'])):
                if entry_counts[row['entry_chunk_id']] >= 2:
                    continue
                seeds.append(row)
                entry_counts[row['entry_chunk_id']] += 1
                if len(seeds) == 5:
                    break
            return seeds
        seed_limit, entry_limit = (10, 20) if policy == 'more' else (5, 10)
        for hit in hybrid[:entry_limit]:
            accepted = 0
            for node in sorted({e[0] for e in self.entries[hit['chunk_id']]}):
                if node in chosen or accepted >= 2 or len(seeds) >= seed_limit:
                    continue
                seeds.append(dict(node_id=node, entry_chunk_id=hit['chunk_id'],
                                  hybrid_rank=hit['rank'], score=1/hit['rank']))
                chosen.add(node)
                accepted += 1
        return seeds

    def search(self, hybrid, qterms, chunk_coverage, *, seed_policy='native', uniform=False, reverse=0):
        r = self.r
        seeds = self.select_seeds(hybrid, qterms, seed_policy)
        scores, quotes = {}, defaultdict(set)
        visits, edge_visits, capped = 0, 0, False
        reverse_selections = {}
        if reverse:
            for law, children in self.children.items():
                ranked = sorted(children, key=lambda n: (
                    -max((chunk_coverage[c] for c in self.article_chunks[n]), default=0.), n))
                reverse_selections[law] = [n for n in ranked[:reverse]
                    if any(chunk_coverage[c] > 0 for c in self.article_chunks[n])]

        def collect(kind, owner, edges, seed):
            for eid in r.evidence[kind+'_bindings'].get(owner, []):
                ev = r.evidence['records'][eid]
                if kind == 'node' and edges and r.graph.nodes[owner]['type'] in ('Document', 'Agency'):
                    last = edges[-1]
                    sources = {r.evidence['records'][x]['source_document_id']
                               for x in r.evidence['edge_bindings'].get(last['key'], [])}
                    if ev['source_document_id'] not in sources:
                        continue
                for cid in ev['chunk_ids']:
                    score = (1. if uniform else seed['score']) * (.45+chunk_coverage[cid]) / (1+.12*len(edges))
                    score *= .95 if ev['role'] == 'condition' else 1.
                    scores[cid] = max(scores.get(cid, 0.), score)
                    quotes[cid].add(eid)

        for seed in seeds:
            queue = deque([(seed['node_id'], [seed['node_id']], [])])
            expanded = set()
            while queue:
                node, nodes, edges = queue.popleft()
                if visits >= 1000:
                    capped = True
                    break
                visits += 1
                collect('node', node, edges, seed)
                if node in expanded or len(edges) >= 2:
                    continue
                expanded.add(node)
                outgoing = [(v, k, e['relation']) for v, k, e in r.outgoing[node]]
                outgoing += [(v, '', 'PART_OF_REVERSE') for v in reverse_selections.get(node, [])]
                for target, key, relation in outgoing:
                    if edge_visits >= 5000:
                        capped = True
                        break
                    edge_visits += 1
                    next_edges = edges + [dict(source=node, target=target, key=key, relation=relation)]
                    if key:
                        collect('edge', key, next_edges, seed)
                    if target not in nodes:
                        queue.append((target, nodes+[target], next_edges))
                if capped:
                    break
            if capped:
                break
        order = sorted(scores, key=lambda c: (-scores[c], c))
        return dict(order=order, scores=scores, quote_ids=quotes, seeds=seeds,
                    visited_nodes=visits, visited_edges=edge_visits, truncated=capped)


def fusion(hybrid, result, cover, quote_cover):
    hr = {h['chunk_id']: h['rank'] for h in hybrid}
    gr = {c: i for i, c in enumerate(result['order'][:100], 1)}
    pool = set(hr) | set(gr)
    values = {}
    for cid in pool:
        rrf = (1/(60+hr[cid]) if cid in hr else 0.) + (1/(60+gr[cid]) if cid in gr else 0.)
        quote = max((quote_cover[eid] for eid in result['quote_ids'][cid]), default=0.) if cid in gr else 0.
        values[cid] = .5*(rrf/(2/61)) + .3*cover[cid] + .2*quote
    return sorted(pool, key=lambda c: (-values[c], c))


def summarize(records, variant, ranking):
    vals = [r['variants'][variant][ranking] for r in records if r['eligible']]
    return dict(n=len(vals), complete10=sum(v['10']['all_evidence'] for v in vals),
                recall10=sum(v['10']['recall'] for v in vals)/len(vals),
                mrr10=sum(v['10']['mrr'] for v in vals)/len(vals),
                complete100=sum(v['100']['all_evidence'] for v in vals),
                pool_complete=sum(v['pool_recall'] == 1 for v in vals))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('choose a new output directory')
    start = time.perf_counter()
    queries, pools, protocol = load_frozen()
    questions_path = ROOT/'data/benchmark/questions.jsonl'
    if digest(input_bytes(questions_path)) != protocol['questions_sha256']:
        raise ValueError('gold changed')
    questions = {q['question_id']: q for q in read_jsonl(questions_path)}
    graph_path, db_path = ROOT/'data/graph/graph.json', ROOT/'data/corpus/civic.sqlite'
    source = read_json(SOURCE/'h3.run.json')
    for p, field in ((graph_path, 'graph_sha256'), (graph_path.with_suffix('.evidence.json'), 'evidence_sha256')):
        if digest(input_bytes(p)) != source[field]:
            raise ValueError('graph changed')
    graph, evidence = load_graph(graph_path, db_path)
    r = GraphRetriever(graph, db_path, evidence)
    probe = Probe(r)
    quote_terms = {eid: token_set(ev['quote']) for eid, ev in evidence['records'].items()}
    setup = time.perf_counter()-start
    records = []
    seen = set()
    for trace in json_lines(SOURCE/'h3.jsonl'):
        qid = trace['query_id']
        if qid in seen:
            raise ValueError('duplicate query')
        seen.add(qid)
        question = queries[qid]['question']
        q = questions[qid]
        assert q['question'] == question
        terms = token_set(question) & r.idf.keys()
        total = max(1., sum(r.idf[t] for t in sorted(terms)))
        def cov(ts):
            return sum(r.idf[t] for t in sorted(terms & ts))/total
        cover = {cid: cov(ts) for cid, ts in r.chunk_terms.items()}
        quote_cover = {eid: cov(ts) for eid, ts in quote_terms.items()}
        hybrid = sorted((dict(chunk_id=c['chunk_id'], rank=c['hybrid_rank']) for c in trace['candidates']
                         if c['hybrid_rank'] is not None), key=lambda h: h['rank'])
        groups = [g['acceptable_chunk_ids'] for g in q['evidence_requirements']]
        result = dict(query_id=qid, eligible=q['expected_action'] == 'answer', question_type=q['question_type'], variants={})
        for variant, config in VARIANTS.items():
            prediction = probe.search(hybrid, terms, cover, **config)
            order = fusion(hybrid, prediction, cover, quote_cover)
            if variant == 'native':
                old_graph = sorted((c for c in trace['candidates'] if c['graph_rank'] is not None),
                                   key=lambda c: c['graph_rank'])
                assert prediction['order'] == [c['chunk_id'] for c in old_graph], qid
                assert order == stored_order(trace), qid
                assert set(order) == set(pools[qid]), qid
                assert [(s['node_id'], s['score']) for s in prediction['seeds']] == [
                    (s['node_id'], s['score']) for s in trace['seeds']], qid
                for c in old_graph:
                    assert prediction['scores'][c['chunk_id']] == c['paths'][0]['score'], qid
            new = set(order)-set(pools[qid])
            lost = set(pools[qid])-set(order)
            v = dict(pool_size=len(order), graph_discovered=len(prediction['order']),
                     new_chunks=sorted(new), lost_chunks=sorted(lost),
                     seed_ids=[s['node_id'] for s in prediction['seeds']],
                     visited_nodes=prediction['visited_nodes'], visited_edges=prediction['visited_edges'],
                     truncated=prediction['truncated'])
            if result['eligible']:
                v['graph'] = rank_metrics(groups, prediction['order'][:100])
                v['fusion'] = rank_metrics(groups, order)
            result['variants'][variant] = v
        records.append(result)
        if len(records) % 100 == 0:
            print(f'{len(records)}/608 queries complete', flush=True)
    assert seen == set(queries) and sum(r['eligible'] for r in records) == 488
    summary = dict(overall={v: {ranking: summarize(records, v, ranking) for ranking in ('graph', 'fusion')}
                            for v in VARIANTS},
                   budget={v: dict(mean_pool=sum(r['variants'][v]['pool_size'] for r in records)/len(records),
                                   new_pairs=sum(len(r['variants'][v]['new_chunks']) for r in records),
                                   lost_pairs=sum(len(r['variants'][v]['lost_chunks']) for r in records),
                                   capped_queries=sum(r['variants'][v]['truncated'] for r in records)) for v in VARIANTS},
                   candidate_changes={v: dict(
                       recovered=[r['query_id'] for r in records if r['eligible'] and
                                  r['variants'][v]['fusion']['pool_recall'] == 1 and
                                  r['variants']['native']['fusion']['pool_recall'] != 1],
                       lost=[r['query_id'] for r in records if r['eligible'] and
                             r['variants'][v]['fusion']['pool_recall'] != 1 and
                             r['variants']['native']['fusion']['pool_recall'] == 1]) for v in VARIANTS},
                   variants=VARIANTS, setup_seconds=setup, total_seconds=time.perf_counter()-start,
                   baseline_exactly_reproduced_queries=len(records), api_calls=0, new_model_inferences=0,
                   limitations=['Exploratory synthetic development data.',
                                'New candidate pairs have no BGE scores; no end-to-end BGE gain claimed.',
                                'Reverse steps are bounded navigation over existing membership edges.',
                                'Same graph cap100, Hybrid100, hops2, max_nodes1000, max_edges5000.'])
    args.out.mkdir(parents=True)
    write_json(args.out/'summary.json', summary)
    with gzip.open(args.out/'per_question.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(',', ':'))+'\n')
    inputs = [questions_path, ROOT/'data/benchmark/queries.jsonl', graph_path,
              graph_path.with_suffix('.evidence.json'), SOURCE/'h3.jsonl', BASELINE, Path(__file__),
              ROOT/'scripts/explore_graph_weights.py', ROOT/'src/civic_link/graph/retriever.py',
              ROOT/'src/civic_link/retrieval/hybrid_graph.py', ROOT/'src/civic_link/tokenization.py']
    write_json(args.out/'manifest.json', dict(
        inputs=[dict(path=str(p.relative_to(ROOT)), logical_sha256=digest(input_bytes(p))) for p in inputs],
        corpus_fingerprint=graph.graph['corpus_fingerprint'],
        outputs=[dict(path=p.name, sha256=digest(p.read_bytes())) for p in sorted(args.out.iterdir())],
        ranking_uses_gold=False, independent_holdout=False, api_calls=0))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
