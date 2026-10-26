from __future__ import annotations

# Offline audit: queries are encoded only from an existing embedding cache.
# Evaluation service IDs are used solely for diagnostic upper bounds.

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict, deque
from functools import lru_cache
from pathlib import Path

from civic_data import connect, read_jsonl, write_json, write_jsonl
from evaluate import evaluate
import graph_retrieval as gr
from graph_construct import load_graph
from retrieval import BM25, Dense, load_chunks, rrf

METHODS = ['bm25', 'dense', 'hybrid'] + [f'graph_h{h}' for h in range(4)]
STATUS = {'success': 2, 'partial': 1, 'failure': 0, 'not_scored_unanswerable': -1}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def js(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def indexed(path):
    return {r['query_id']: r for r in read_jsonl(path)}


class OfflineEmbeddings:
    def __init__(self, settings, cache):
        self.model = settings['embedding_model']
        self.fingerprint = settings['encoder_fingerprint']
        self.document_prefix = settings['document_prefix']
        self.query_prefix = settings['query_prefix']
        self.vectors = {}
        for row in read_jsonl(cache):
            assert row['encoder_fingerprint'] == self.fingerprint
            self.vectors[row['query_text_sha256']] = row['vector']

    def encode(self, texts, kind='query'):
        assert kind == 'query'
        return [self.vectors[hashlib.sha256(t.encode()).hexdigest()] for t in texts]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--old', default='20261026_232637')
    p.add_argument('--new', default='20261027_015454')
    p.add_argument('--baseline-ref', default='135de8b')
    a = p.parse_args()
    root = a.root.resolve()
    dirs = {label: root/'results/week4_run'/name for label, name in [('old', a.old), ('new', a.new)]}
    dest = dirs['new']/'analysis'
    questions = read_jsonl(root/'data/questions.jsonl')
    qmap = {q['question_id']: q for q in questions}
    manifests = {label: js(d/'run_manifest.json') for label, d in dirs.items()}
    assert all(m['status'] == 'completed' for m in manifests.values())
    for key, file in [('queries', 'queries.jsonl'), ('questions', 'questions.jsonl')]:
        assert manifests['old']['source_sha256'][key] == manifests['new']['source_sha256'][key] == sha(root/'data'/file)
    db = root/'data/processed/civic.sqlite'
    assert manifests['old']['source_sha256']['database'] == manifests['new']['source_sha256']['database'] == sha(db)
    for key, file in [('graph', 'graph.json'), ('graph_evidence', 'graph.evidence.json')]:
        assert manifests['new']['source_sha256'][key] == sha(root/'data/processed'/file)
    with connect(db, True) as c:
        docs = load_chunks(c)
    chunks = {d['chunk_id']: d for d in docs}
    predictions = {label: {} for label in dirs}
    cases = {label: {} for label in dirs}
    metrics = {label: {} for label in dirs}
    for label, directory in dirs.items():
        for method in METHODS:
            path = directory/f'{method}.predictions.jsonl'
            assert sha(path) == manifests[label]['methods'][method]['predictions_sha256']
            rows = read_jsonl(path)
            assert len(rows) == len(qmap) == 204
            assert {r['query_id'] for r in rows} == set(qmap)
            report, evaluated = evaluate(questions, rows, set(chunks))
            stored = js(directory/f'{method}.metrics.json')
            assert report['retrieval'] == stored['retrieval']
            stored_cases = indexed(directory/f'{method}.cases.jsonl')
            for row in evaluated:
                assert row['metrics'] == stored_cases[row['query_id']]['metrics'] if 'metrics' in row else True
                assert row['retrieval_status_at_10'] == stored_cases[row['query_id']]['retrieval_status_at_10']
            predictions[label][method] = {r['query_id']: r for r in rows}
            cases[label][method] = {r['query_id']: r for r in evaluated}
            metrics[label][method] = report
    print('Validated hashes and recalculated metrics for all 14 method runs.', flush=True)
    gr.token_set = lru_cache(maxsize=16000)(gr.token_set)
    graph, evidence = load_graph(root/'data/processed/graph.json', db)
    retriever = gr.GraphRetriever(graph, db, evidence)
    print('Graph lexical index ready.', flush=True)
    bm = BM25(docs)
    settings = js(dirs['new']/'dense.run.json')
    cache = root/'data/processed'/f"query_embeddings.{settings['encoder_fingerprint'][:12]}.jsonl"
    offline = OfflineEmbeddings(settings, cache)
    with connect(db, True) as c:
        dense = Dense(c, docs, offline)
    owners = defaultdict(list)
    for kind in ['node', 'edge']:
        for owner, eids in evidence[kind+'_bindings'].items():
            for eid in eids:
                for cid in evidence['records'][eid]['chunk_ids']:
                    owners[cid].append((kind, owner))

    @lru_cache(maxsize=None)
    def oracle_candidates(services, hops):
        result = set()
        def collect(kind, owner, source_doc_ids=None):
            for eid in evidence[kind+'_bindings'].get(owner, []):
                ev = evidence['records'][eid]
                if source_doc_ids is not None and ev['source_document_id'] not in source_doc_ids:
                    continue
                result.update(ev['chunk_ids'])
        for sid in services:
            if sid not in graph:
                continue
            collect('node', sid)
            queue = deque([(sid, 0)])
            seen = {sid}
            while queue:
                u, depth = queue.popleft()
                if depth >= hops:
                    continue
                for v, key, _ in retriever.outgoing[u]:
                    collect('edge', key)
                    if v not in seen:
                        seen.add(v)
                        source_docs = None
                        if graph.nodes[v]['type'] in ('Document', 'Agency'):
                            source_docs = {evidence['records'][eid]['source_document_id'] for eid in evidence['edge_bindings'][key]}
                        collect('node', v, source_docs)
                        queue.append((v, depth+1))
        return frozenset(result)

    def named_path(hit):
        nodes = hit['path_nodes']
        result = graph.nodes[nodes[0]]['label']
        for edge in hit['path_edges']:
            node = graph.nodes[edge['target']]
            name = node['label']
            if node['type'] == 'Article':
                name = chunks[next(iter(evidence['records'][eid]['chunk_ids']))]['title'] if (eid := evidence['node_bindings'][edge['target']][0]) else name
            result += ' → '+edge['relation']+' → '+name
        return result

    traces = []
    for number, q in enumerate(questions, 1):
        qid = q['question_id']
        groups = [set(e['acceptable_chunk_ids']) for e in q['evidence_requirements']]
        gold_cids = set().union(*groups) if groups else set()
        bm_ranked = bm.search(q['question'], len(docs))
        de_ranked = dense.search(q['question'], len(docs))
        hybrid_ranked = rrf([bm_ranked[:100], de_ranked[:100]], 200)
        ranks = {m: {cid: i for i, (cid, _) in enumerate(ranked, 1)} for m, ranked in [('bm25', bm_ranked), ('dense', de_ranked), ('hybrid', hybrid_ranked)]}
        replay_matches = {}
        for method, ranked in [('bm25', bm_ranked), ('dense', de_ranked), ('hybrid', hybrid_ranked)]:
            actual = predictions['new'][method][qid]['hits']
            assert [cid for cid, _ in ranked[:10]] == [h['chunk_id'] for h in actual], (qid, method)
            replay_matches[method] = True
        trace = dict(query_id=qid, question=q['question'], canonical_question=q.get('canonical_question'),
                     question_type=q['question_type'], expected_action=q['expected_action'],
                     service_ids=q.get('service_ids', []), intent_family_id=q.get('intent_family_id'),
                     gold_answer=q['gold_answer'], evidence_groups=[], methods={})
        for i, e in enumerate(q['evidence_requirements'], 1):
            trace['evidence_groups'].append(dict(group=i, source_alias=e['source_alias'], locator=e['locator'],
                quote=e['quote'], chunk_ids=e['acceptable_chunk_ids'], globally_bound=bool(set(e['acceptable_chunk_ids'])&owners.keys())))
        full_graph = {}
        for hop in range(4):
            method = f'graph_h{hop}'
            stored = predictions['new'][method][qid]
            full = retriever.search(q['question'], qid, top_k=len(chunks), max_hops=hop)
            assert full['candidate_chunk_ids'] == stored['candidate_chunk_ids'], (qid, method, 'candidates')
            assert [h['chunk_id'] for h in full['hits'][:10]] == [h['chunk_id'] for h in stored['hits']], (qid, method, 'top10')
            for actual, replay in zip(stored['hits'], full['hits']):
                assert actual['path_nodes'] == replay['path_nodes'] and actual['path_edges'] == replay['path_edges']
                assert math.isclose(actual['score'], replay['score'], rel_tol=1e-10)
            full_graph[method] = full
            ranks[method] = {h['chunk_id']: h['rank'] for h in full['hits']}
        for method in METHODS:
            old = cases['old'][method][qid]
            new = cases['new'][method][qid]
            pred = predictions['new'][method][qid]
            method_trace = dict(old_status=old['retrieval_status_at_10'], status=new['retrieval_status_at_10'],
                old_metrics=old.get('metrics', {}), metrics=new.get('metrics', {}), groups=[],
                top3=[dict(chunk_id=h['chunk_id'], rank=h['rank'], title=h['title']) for h in pred['hits'][:3]])
            if method.startswith('graph_'):
                hop = int(method[-1])
                method_trace.update(seeds=pred['seeds'], old_seeds=predictions['old'][method][qid]['seeds'],
                    candidates=pred['candidate_chunk_count'], visited=pred['visited_node_count'], truncated=pred['traversal_truncated'])
            for i, group in enumerate(groups, 1):
                found = sorted((ranks[method][c], c) for c in group if c in ranks[method])
                rank, cid = found[0] if found else (None, None)
                old_hits = [h for h in predictions['old'][method][qid]['hits'] if h['chunk_id'] in group]
                info = dict(group=i, old_rank=old_hits[0]['rank'] if old_hits else None, rank=rank, best_chunk=cid,
                            best_title=chunks[cid]['title'] if cid else None)
                if rank is not None and rank <= 10:
                    info['stage'] = 'retrieved'
                elif method.startswith('graph_'):
                    if rank is not None:stage = 'ranking'
                    elif not pred['seeds']:stage = 'no_seed'
                    elif not group&owners.keys():stage = 'unbound'
                    elif group&oracle_candidates(tuple(q.get('service_ids', [])), hop):stage = 'seed_selection'
                    else:stage = 'reachability'
                    info['stage'] = stage
                    info['gold_service_candidate'] = bool(group&oracle_candidates(tuple(q.get('service_ids', [])), hop))
                elif method == 'hybrid':
                    info['stage'] = 'rrf_ranking' if rank is not None else 'outside_top100_union'
                else:
                    info['stage'] = 'ranking' if rank is not None else 'no_lexical_match'
                if method.startswith('graph_') and cid:
                    hit = full_graph[method]['hits'][rank-1]
                    terms = gr.token_set(q['question'])&retriever.idf.keys()
                    weight = sum(retriever.idf[t] for t in terms)
                    relevance = sum(retriever.idf[t] for t in terms&retriever.chunk_terms[cid])/max(1., weight)
                    info.update(path=named_path(hit), hops=hit['graph_hops'], score=hit['score'],
                        relevance=relevance, evidence_rule=evidence['records'][hit['evidence_binding']['evidence_id']]['extraction_rule'],
                        evidence_binding=hit['evidence_binding'], evidence_quote=hit['evidence_quote'])
                if method == 'hybrid' and cid:
                    rb, rd = ranks['bm25'].get(cid), ranks['dense'].get(cid)
                    info['component_ranks'] = dict(bm25=rb, dense=rd)
                    info['rrf_contributions'] = dict(bm25=1/(60+rb) if rb and rb<=100 else 0,
                                                    dense=1/(60+rd) if rd and rd<=100 else 0)
                method_trace['groups'].append(info)
            if method.startswith('graph_') and pred['hits']:
                method_trace['top_path'] = named_path(pred['hits'][0])
            trace['methods'][method] = method_trace
        traces.append(trace)
        if number % 25 == 0:
            print(f'Replayed and checked {number}/204 questions without network.', flush=True)
    summary = {'validation': {'questions':204, 'scored':177, 'metrics_runs_recomputed':14,
        'current_top10_replayed':204*7, 'network_calls':0, 'dense_predictions_identical':
        manifests['old']['methods']['dense']['predictions_sha256']==manifests['new']['methods']['dense']['predictions_sha256']},
        'metrics':metrics, 'transitions':{}, 'graph_group_stages':{}, 'hop_transitions':[], 'method_overlap':{}}
    for method in METHODS:
        summary['transitions'][method] = dict(Counter(t['methods'][method]['old_status']+' → '+t['methods'][method]['status'] for t in traces))
    for hop in range(4):
        method = f'graph_h{hop}'
        summary['graph_group_stages'][method] = dict(Counter(g['stage'] for t in traces if t['expected_action']=='answer' for g in t['methods'][method]['groups']))
    for hop in range(1,4):
        previous, current = f'graph_h{hop-1}', f'graph_h{hop}'
        value = Counter()
        changed = []
        for qid,q in qmap.items():
            before, after = predictions['new'][previous][qid], predictions['new'][current][qid]
            a_ids, b_ids = [h['chunk_id'] for h in before['hits']], [h['chunk_id'] for h in after['hits']]
            added = set(after['candidate_chunk_ids'])-set(before['candidate_chunk_ids'])
            value['candidate_additions']+=len(added)
            value['questions_more_candidates']+=bool(added)
            value['questions_top10_order_changed']+=a_ids!=b_ids
            value['questions_top10_set_changed']+=set(a_ids)!=set(b_ids)
            comparable = lambda hits: [(h['chunk_id'], h['score'], h['path_nodes'], h['path_edges'], h['evidence_binding']) for h in hits]
            value['questions_paths_or_scores_changed']+=comparable(before['hits'])!=comparable(after['hits'])
            value['questions_top1_changed']+=a_ids[:1]!=b_ids[:1]
            value['new_candidates_in_top10']+=len(added&set(b_ids))
            if q['expected_action']=='answer':
                groups = [set(e['acceptable_chunk_ids']) for e in q['evidence_requirements']]
                old_found = [bool(g&set(a_ids)) for g in groups]
                new_found = [bool(g&set(b_ids)) for g in groups]
                value['newly_reachable_gold_groups']+=sum(not g&set(before['candidate_chunk_ids']) and bool(g&set(after['candidate_chunk_ids'])) for g in groups)
                value['newly_retrieved_gold_groups']+=sum(not x and y for x,y in zip(old_found,new_found))
                value['lost_gold_groups']+=sum(x and not y for x,y in zip(old_found,new_found))
                if old_found!=new_found:changed.append(dict(query_id=qid,before=old_found,after=new_found))
        summary['hop_transitions'].append(dict(hop=hop,counts=dict(value),changed_gold_questions=changed))
    scored = [t for t in traces if t['expected_action']=='answer']
    for hop in range(4):
        method=f'graph_h{hop}'
        ids=[t['query_id'] for t in scored if t['methods']['hybrid']['status']!='success' and t['methods'][method]['status']=='success']
        union=[]
        for t in scored:
            h=predictions['new']['hybrid'][t['query_id']]['hits']
            g=predictions['new'][method][t['query_id']]['hits']
            cids={x['chunk_id'] for x in h+g}
            if all(set(e['chunk_ids'])&cids for e in t['evidence_groups']):union.append(t['query_id'])
        summary['method_overlap'][method]=dict(graph_only_success_ids=ids,oracle_union_up_to20_success=len(union))
    summary['all_methods_incomplete_ids']=[t['query_id'] for t in scored if all(m['status']!='success' for m in t['methods'].values())]
    summary['source_hashes']={label:m['source_sha256'] for label,m in manifests.items()}
    summary['analysis_code_sha256']=sha(__file__)
    summary['replay_source_hashes']={f:sha(root/'scripts'/f) for f in ['tokenization.py','graph_retrieval.py','graph_construct.py','retrieval.py','evaluate.py']}
    try:
        old_graph = json.loads(subprocess.check_output(['git','show',a.baseline_ref+':data/processed/graph.json'],cwd=root))
        old_ev = json.loads(subprocess.check_output(['git','show',a.baseline_ref+':data/processed/graph.evidence.json'],cwd=root))
        old_edges={x['key'] for x in old_graph['edges']}
        new_edges={k for u,v,k in graph.edges(keys=True)}
        summary['graph_build_diff']=dict(old_nodes=len(old_graph['nodes']),new_nodes=len(graph),
            old_edges=len(old_edges),new_edges=len(new_edges),added_edges=len(new_edges-old_edges),removed_edges=len(old_edges-new_edges),
            old_rules=dict(Counter(e['extraction_rule'] for e in old_ev['records'].values())),new_rules=dict(Counter(e['extraction_rule'] for e in evidence['records'].values())))
        for key, filename in [('graph', 'graph.json'), ('graph_evidence', 'graph.evidence.json')]:
            raw = subprocess.check_output(['git', 'show', a.baseline_ref+':data/processed/'+filename], cwd=root)
            lf = raw.replace(b'\r\n', b'\n')
            variants = [raw, lf, lf.replace(b'\n', b'\r\n')]
            summary['graph_build_diff']['old_'+key+'_matches_baseline'] = any(
                hashlib.sha256(v).hexdigest() == manifests['old']['source_sha256'][key] for v in variants)
    except (subprocess.CalledProcessError, KeyError):
        summary['graph_build_diff']={'available':False}
    write_json(dest/'analysis_summary.json', summary)
    write_jsonl(dest/'question_traces.jsonl', traces)
    print(json.dumps({'output':str(dest),'validated':summary['validation'],'graph_stages':summary['graph_group_stages'],
        'hop_transitions':summary['hop_transitions'],'overlap':summary['method_overlap'],'all_incomplete':summary['all_methods_incomplete_ids']},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
