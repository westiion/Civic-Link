"""Offline graph extension of frozen Hybrid rankings; never calls an AI API."""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import math
from pathlib import Path

from civic_data import ROOT, digest, read_jsonl, write_json, write_jsonl
from graph_construct import compact, load_graph
from graph_retrieval import GraphRetriever, path_text
from tokenization import TOKENIZER_NAME, token_set

LEGAL_RELATIONS = ('GROUNDED_BY', 'REFERENCES')
ALL_RELATIONS = LEGAL_RELATIONS + ('REQUIRES', 'HANDLED_BY', 'PART_OF')

SCORE_VERSION = 'kiwi-idf-extension-v1'
RERANK_WEIGHTS = dict(rrf=.5, chunk_coverage=.3, quote_coverage=.2)



@dataclass(frozen=True)
class Config:
    seed_mode: str = 'hybrid'
    seed_weight: str = 'native'
    rerank: str = 'none'
    hybrid_k: int = 100
    entry_k: int = 10
    seed_k: int = 5
    seeds_per_chunk: int = 2
    max_hops: int = 2
    max_nodes: int = 1000
    max_edges: int = 5000
    graph_k: int = 100
    top_k: int = 10
    rrf_constant: int = 60
    relations: tuple = ALL_RELATIONS

    def validate(self):
        if self.seed_mode not in ('hybrid', 'lexical') or self.seed_weight not in ('native', 'uniform'):
            raise ValueError('invalid seed policy')
        if self.rerank not in ('none', 'feature'):
            raise ValueError('invalid rerank policy')
        for name, value in asdict(self).items():
            if name in ('relations', 'seed_mode', 'seed_weight', 'rerank'):
                continue
            minimum = 0 if name in ('max_hops', 'rrf_constant') else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f'invalid {name}')
        if not self.top_k <= self.hybrid_k or self.entry_k > self.hybrid_k:
            raise ValueError('top_k and entry_k must not exceed hybrid_k')
        if not self.relations or not set(self.relations) <= {'GROUNDED_BY', 'REFERENCES', 'PART_OF', 'REQUIRES', 'HANDLED_BY'}:
            raise ValueError('unsupported relations')


class HybridGraph:
    def __init__(self, retriever, config=Config()):
        config.validate()
        self.r = retriever
        self.cfg = config
        self.entries = defaultdict(set)
        self.quote_terms = {}
        g, ev = retriever.graph, retriever.evidence
        for node, ids in ev['node_bindings'].items():
            if g.nodes[node]['type'] not in ('Service', 'Article'):
                continue
            for eid in ids:
                record = ev['records'][eid]
                if record['role'] not in ('service_context', 'article_context', 'service_mention'):
                    continue
                for cid in record['chunk_ids']:
                    self.entries[cid].add((node, 'node', node, eid))
        for u, v, key, edge in g.edges(keys=True, data=True):
            if edge['relation'] not in config.relations or g.nodes[u]['type'] not in ('Service', 'Article'):
                continue
            for eid in ev['edge_bindings'].get(key, []):
                for cid in ev['records'][eid]['chunk_ids']:
                    self.entries[cid].add((u, 'edge', key, eid))
        self.labels = {}
        if config.seed_mode == 'lexical':
            for node in sorted({entry[0] for entries in self.entries.values() for entry in entries}):
                data = g.nodes[node]
                self.labels[node] = [(compact(label), token_set(label))
                                     for label in (data.get('aliases') or [data['label']])]

    def search(self, question, row):
        cfg, r = self.cfg, self.r
        if not isinstance(question, str) or not question.strip():
            raise ValueError('empty question')
        if row.get('retriever') != 'hybrid':
            raise ValueError('only Hybrid predictions accepted')
        qid = row['query_id']
        if not isinstance(qid, str) or not qid:
            raise ValueError('invalid query ID')
        qterms = token_set(question)
        known_terms = qterms & r.idf.keys()
        query_weight = max(1., sum(r.idf[t] for t in sorted(known_terms)))

        def coverage(terms):
            return sum(r.idf[t] for t in sorted(known_terms & terms)) / query_weight

        seen = set()
        for rank, hit in enumerate(row['hits'], 1):
            cid = hit['chunk_id']
            if cid not in r.chunks or cid in seen or hit['rank'] != rank:
                raise ValueError('unknown/duplicate chunk or noncontiguous rank')
            if hit.get('query_id') != qid or hit.get('retriever') != 'hybrid':
                raise ValueError('hit identity mismatch')
            if hit['document_id'] != r.chunks[cid]['document_id'] or not math.isfinite(hit['score']):
                raise ValueError('invalid hit document or score')
            seen.add(cid)
        hybrid = row['hits'][:cfg.hybrid_k]
        seeds, decisions, chosen = [], [], set()
        for hit in (hybrid[:cfg.entry_k] if cfg.seed_mode == 'hybrid' else []):
            grouped = defaultdict(list)
            for node, kind, owner, eid in sorted(self.entries[hit['chunk_id']]):
                grouped[node].append(dict(kind=kind, owner_id=owner, evidence_id=eid))
            accepted = 0
            for node, bindings in sorted(grouped.items()):
                reason = ('duplicate_node' if node in chosen else
                          'seed_limit' if len(seeds) >= cfg.seed_k else
                          'per_chunk_limit' if accepted >= cfg.seeds_per_chunk else 'accepted')
                decisions.append(dict(chunk_id=hit['chunk_id'], node_id=node, reason=reason, bindings=bindings))
                if reason == 'accepted':
                    chosen.add(node)
                    accepted += 1
                    seeds.append(dict(node_id=node, entry_chunk_id=hit['chunk_id'],
                                      hybrid_rank=hit['rank'], score=1 / hit['rank'], bindings=bindings))
            if not grouped:
                decisions.append(dict(chunk_id=hit['chunk_id'], reason='no_eligible_binding'))
        if cfg.seed_mode == 'lexical':
            query_text = compact(question)
            scored = []
            for node, labels in self.labels.items():
                value = max((.7 * len(qterms & terms) / max(1, len(terms)) +
                             (.3 if label and label in query_text else 0.)) for label, terms in labels)
                scored.append((node, value))
            for node, value in sorted(scored, key=lambda x: (-x[1], x[0])):
                reason = 'below_threshold' if value < .18 else 'seed_limit' if len(seeds) >= cfg.seed_k else 'accepted'
                decisions.append(dict(node_id=node, reason=reason, selection_score=value))
                if reason == 'accepted':
                    seeds.append(dict(node_id=node, entry_chunk_id=None, hybrid_rank=None, score=value, bindings=[]))
        for seed in seeds:
            seed['native_score'] = seed['score']
            seed['selection_method'] = cfg.seed_mode
            if cfg.seed_weight == 'uniform':
                seed['score'] = 1.
        paths = defaultdict(list)

        def collect(kind, owner, nodes, edges, seed):
            for eid in r.evidence[kind + '_bindings'].get(owner, []):
                ev = r.evidence['records'][eid]
                if kind == 'node' and edges and r.graph.nodes[owner]['type'] in ('Document', 'Agency'):
                    sources = {r.evidence['records'][x]['source_document_id']
                               for x in r.evidence['edge_bindings'][edges[-1]['key']]}
                    if ev['source_document_id'] not in sources:
                        continue
                for cid in ev['chunk_ids']:
                    relevance = coverage(r.chunk_terms[cid])
                    factor = .95 if ev['role'] == 'condition' else 1.
                    score = seed['score'] * (.45 + relevance) / (1 + .12 * len(edges)) * factor
                    paths[cid].append(dict(score=score, seed_id=seed['node_id'],
                        entry_chunk_id=seed['entry_chunk_id'], entry_hybrid_rank=seed['hybrid_rank'],
                        path=path_text(r.graph, nodes, edges), path_nodes=nodes, path_edges=edges,
                        graph_hops=len(edges), evidence_binding=dict(kind=kind, owner_id=owner, evidence_id=eid),
                        evidence_quote=ev['quote'], evidence_span=[ev['start_char'], ev['end_char']],
                        evidence_source_document_id=ev['source_document_id'],
                        score_components=dict(seed_score=seed['score'], relevance=relevance, condition_factor=factor)))

        node_visits = edge_visits = 0
        truncated = []
        for seed in seeds:
            queue = deque([(seed['node_id'], [seed['node_id']], [])])
            expanded = set()
            while queue:
                u, nodes, edges = queue.popleft()
                if node_visits >= cfg.max_nodes:
                    truncated.append('max_nodes')
                    break
                node_visits += 1
                collect('node', u, nodes, edges, seed)
                if u in expanded or len(edges) >= cfg.max_hops:
                    continue
                expanded.add(u)
                for v, key, edge in r.outgoing[u]:
                    if edge['relation'] not in cfg.relations:
                        continue
                    if edge_visits >= cfg.max_edges:
                        truncated.append('max_edges')
                        break
                    edge_visits += 1
                    next_edges = edges + [dict(source=u, target=v, key=key, relation=edge['relation'])]
                    collect('edge', key, nodes + [v], next_edges, seed)
                    if v not in nodes:
                        queue.append((v, nodes + [v], next_edges))
                if truncated:
                    break
            if truncated:
                break
        for candidates in paths.values():
            candidates.sort(key=lambda p: (-p['score'], p['seed_id'], p['path_nodes'],
                                            [e['key'] for e in p['path_edges']], p['evidence_binding']['evidence_id']))
        graph_order = sorted(paths, key=lambda cid: (-paths[cid][0]['score'], cid))
        graph_ranks = {cid: i for i, cid in enumerate(graph_order, 1)}
        kept_graph = set(graph_order[:cfg.graph_k])
        hybrid_ranks = {h['chunk_id']: h['rank'] for h in hybrid}
        scores = {cid: (1 / (cfg.rrf_constant + hybrid_ranks[cid]) if cid in hybrid_ranks else 0) +
                       (1 / (cfg.rrf_constant + graph_ranks[cid]) if cid in kept_graph else 0)
                  for cid in set(hybrid_ranks) | kept_graph}
        rrf_order = sorted(scores, key=lambda cid: (-scores[cid], cid))
        rrf_ranks = {cid: i for i, cid in enumerate(rrf_order, 1)}
        features = {}
        if cfg.rerank == 'feature':
            for cid in scores:
                quote_score = 0.
                if cid in kept_graph:
                    for path in paths[cid]:
                        quote = path['evidence_quote']
                        if quote not in self.quote_terms:
                            self.quote_terms[quote] = token_set(quote)
                        quote_score = max(quote_score, coverage(self.quote_terms[quote]))
                features[cid] = dict(rrf=scores[cid] / (2 / (cfg.rrf_constant + 1)),
                                     chunk_coverage=coverage(r.chunk_terms[cid]), quote_coverage=quote_score)
            scores = {cid: sum(RERANK_WEIGHTS[k] * v for k, v in feature.items()) for cid, feature in features.items()}
        order = sorted(scores, key=lambda cid: (-scores[cid], cid))
        ranks = {cid: i for i, cid in enumerate(order, 1)}
        hits = []
        for cid in order[:cfg.top_k]:
            ch = r.chunks[cid]
            hits.append(dict(query_id=qid, retriever='hybrid_graph', rank=ranks[cid], chunk_id=cid,
                document_id=ch['document_id'], title=ch['title'], snippet=ch['text'][:300],
                source_url=r.docs[ch['document_id']]['source_url'], score=scores[cid],
                origins=(['hybrid'] if cid in hybrid_ranks else []) + (['graph'] if cid in kept_graph else []),
                hybrid_rank=hybrid_ranks.get(cid), graph_rank=graph_ranks.get(cid),
                graph_path=paths[cid][0] if cid in kept_graph else None,
                rrf_rank=rrf_ranks[cid], rerank=cfg.rerank,
                evidence_status='retrieved_not_entailment_verified'))
        diagnostics = [dict(chunk_id=cid, hybrid_rank=hybrid_ranks.get(cid), graph_rank=graph_ranks.get(cid),
            fusion_rank=ranks.get(cid), fusion_score=scores.get(cid), rrf_rank=rrf_ranks.get(cid),
            rerank_features=features.get(cid), paths=paths.get(cid, []),
            graph_excluded_by_cap=cid in paths and cid not in kept_graph,
            selected=cid in ranks and ranks[cid] <= cfg.top_k)
            for cid in sorted(set(paths) | set(hybrid_ranks))]
        return dict(query_id=qid, retriever='hybrid_graph', hits=hits, seeds=seeds,
            baseline_hits=hybrid[:cfg.top_k], rrf_top_chunk_ids=rrf_order[:cfg.top_k],
            seed_decisions=decisions, candidates=diagnostics,
            diagnostics=dict(hybrid_count=len(hybrid), input_shortfall=len(hybrid) < cfg.hybrid_k,
                graph_candidates=len(paths), graph_kept=len(kept_graph),
                graph_only_candidates=len(kept_graph - set(hybrid_ranks)),
                visited_nodes=node_visits, visited_edges=edge_visits, truncation_reasons=sorted(set(truncated))))


def source_hashes():
    directory = Path(__file__).resolve().parent
    return {name: digest((directory / name).read_bytes()) for name in
            ('hybrid_graph.py', 'graph_retrieval.py', 'tokenization.py', 'graph_construct.py', 'civic_data.py')}


def load_inputs(queries_path, results_path, run_path, graph, config):
    provenance = json.loads(Path(run_path).read_text(encoding='utf-8'))
    if provenance.get('method') != 'hybrid':
        raise ValueError('Hybrid run metadata required')
    for name, path in [('queries_sha256', queries_path), ('predictions_sha256', results_path)]:
        if provenance.get(name) != digest(Path(path).read_bytes()):
            raise ValueError(f'input fingerprint mismatch: {name}')
    if provenance.get('corpus_hash') != graph.graph.get('corpus_hash'):
        raise ValueError('Hybrid and Graph corpus mismatch')
    if provenance.get('bm25', {}).get('tokenizer') != TOKENIZER_NAME:
        raise ValueError('Hybrid input must use the current Kiwi tokenizer; regenerate Hybrid candidates')
    if provenance.get('top_k', 0) < config.hybrid_k:
        raise ValueError('Hybrid input depth is insufficient; export Hybrid candidates first')
    queries, rows = read_jsonl(queries_path), read_jsonl(results_path)
    if any(set(q) != {'query_id', 'question'} or not isinstance(q['question'], str) or not q['question'].strip() for q in queries):
        raise ValueError('queries must contain only query_id and nonempty question; no gold')
    ids = [q['query_id'] for q in queries]
    row_ids = [r['query_id'] for r in rows]
    if (not ids or any(not isinstance(qid, str) or not qid for qid in ids) or
            len(set(ids)) != len(ids) or len(set(row_ids)) != len(row_ids) or set(ids) != set(row_ids)):
        raise ValueError('query IDs must be unique and match Hybrid results exactly')
    return queries, {r['query_id']: r for r in rows}, provenance


def run_frozen(retriever, queries, lookup, provenance, config, output, graph_path):
    config.validate()
    output = Path(output)
    if output.exists() or output.with_suffix('.run.json').exists():
        raise ValueError('output already exists; choose a new path')
    engine = HybridGraph(retriever, config)
    predictions, timings = [], []
    for q in queries:
        started = time.perf_counter()
        predictions.append(engine.search(q['question'], lookup[q['query_id']]))
        timings.append((time.perf_counter() - started) * 1000)
    write_jsonl(output, predictions)
    graph_path = Path(graph_path)
    from importlib.metadata import version
    settings = dict(method='hybrid_graph', config=asdict(config), query_count=len(predictions),
        hybrid_run=provenance, tokenizer=TOKENIZER_NAME, tokenizer_package_version=version('kiwipiepy'),
        score_version=SCORE_VERSION, rerank_weights=RERANK_WEIGHTS, source_sha256=source_hashes(),
        graph_sha256=digest(graph_path.read_bytes()),
        evidence_sha256=digest(graph_path.with_suffix('.evidence.json').read_bytes()),
        predictions_sha256=digest(output.read_bytes()), query_latency_ms=timings, generation=None, api_calls=0)
    write_json(output.with_suffix('.run.json'), settings)
    return predictions, settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data/processed/civic.sqlite')
    parser.add_argument('--graph', type=Path, default=ROOT / 'data/processed/graph.json')
    parser.add_argument('--queries', type=Path, required=True)
    parser.add_argument('--hybrid-results', type=Path, required=True)
    parser.add_argument('--hybrid-run', type=Path, required=True, help='retrieval.py .run.json provenance')
    parser.add_argument('--output', type=Path, required=True)
    for name, default in asdict(Config()).items():
        if name in ('seed_mode', 'seed_weight', 'rerank'):
            choices = {'seed_mode': ('hybrid', 'lexical'), 'seed_weight': ('native', 'uniform'), 'rerank': ('none', 'feature')}
            parser.add_argument('--' + name.replace('_', '-'), choices=choices[name], default=default)
        elif name != 'relations':
            parser.add_argument('--' + name.replace('_', '-'), type=type(default), default=default)
    parser.add_argument('--relations', nargs='+', default=list(Config().relations))
    args = parser.parse_args()
    config = Config(**{name: (tuple(args.relations) if name == 'relations' else getattr(args, name))
                       for name in asdict(Config())})
    config.validate()
    graph, evidence = load_graph(args.graph, args.db)
    queries, lookup, provenance = load_inputs(args.queries, args.hybrid_results, args.hybrid_run, graph, config)
    output, _ = run_frozen(GraphRetriever(graph, args.db, evidence), queries, lookup, provenance, config, args.output, args.graph)
    print(f'{len(output)} results written to {args.output}')


if __name__ == '__main__':
    main()
