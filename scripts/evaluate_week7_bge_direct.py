"""Evaluate the frozen Experiment 2 union using (-raw BGE score, chunk_id).

No retrieval, model loading, or generation runs. Existing caches are read only.
The exported CSV can be evaluated independently with --rankings.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path

import _bootstrap  # noqa: F401
from civic_link.common import ROOT, connect, digest, input_bytes, read_jsonl, write_json, write_jsonl, input_exists, input_path, read_json
from civic_link.evaluation.runner import retrieval_metrics
from civic_link.graph.build import corpus_identity
from civic_link.reranking.policies import rank_metrics, stored_order

METHOD = 'hybrid_graph_bge_direct'
LABEL = 'Hybrid+Graph → BGE 직접 정렬'
SOURCE = ROOT / 'week7/results/experiment2_20261031'
QUESTIONS = ROOT / 'data/benchmark/questions.jsonl'
QUERIES = ROOT / 'data/benchmark/queries.jsonl'
HYBRID = ROOT / 'data/retrieval/hybrid_candidates.jsonl.gz'
DB = ROOT / 'data/corpus/civic.sqlite'
FIELDS = ('query_id', 'chunk_id', 'bge_raw_score', 'final_rank')


def unique_rows(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate ' + key)
    return result


def direct_order(scores):
    if not scores or any(not isinstance(cid, str) or not cid for cid in scores):
        raise ValueError('empty pool or invalid chunk_id')
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in scores.values()):
        raise ValueError('nonfinite or nonnumeric raw BGE score')
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


def validate_pool(old, revised, neural, hybrid_row):
    candidates = unique_rows(old['candidates'], 'chunk_id')
    unique_rows(revised['candidates'], 'chunk_id')
    hybrid = {cid: c['hybrid_rank'] for cid, c in candidates.items()
              if c['hybrid_rank'] is not None}
    if sorted(hybrid.values()) != list(range(1, 101)):
        raise ValueError('Hybrid pool must contain the frozen top 100')
    frozen = [h['chunk_id'] for h in hybrid_row['hits']]
    if len(frozen) != 100 or frozen != sorted(hybrid, key=hybrid.get):
        raise ValueError('Hybrid candidates differ from frozen retrieval')
    graph = {cid: c['graph_rank'] for cid, c in candidates.items()
             if c['graph_rank'] is not None and not c['graph_excluded_by_cap']}
    if len(graph) > 100 or sorted(graph.values()) != list(range(1, len(graph) + 1)):
        raise ValueError('Graph pool must contain the admitted top <=100')
    union = set(hybrid) | set(graph)
    old_order, revised_order = stored_order(old), stored_order(revised)
    score_rows = unique_rows(neural['bge_scores'], 'chunk_id')
    scores = {cid: r['score'] for cid, r in score_rows.items()}
    if not (union == set(old_order) == set(revised_order) == set(scores)):
        raise ValueError('candidate union / H3 / coverage / BGE cache mismatch')
    if len(neural['ranked_chunk_ids']) != len(union) or set(neural['ranked_chunk_ids']) != union:
        raise ValueError('incomplete previous BGE ranking')
    order = direct_order(scores)
    # Check independence from both input orders, including exact ties.
    for prior in (old_order, revised_order, list(reversed(order))):
        if direct_order({cid: scores[cid] for cid in prior}) != order:
            raise ValueError('input order affected direct ranking')
    previous = sorted(revised_order, key=lambda cid: -scores[cid])
    if previous != neural['ranked_chunk_ids']:
        raise ValueError('raw cache does not reproduce original BGE ranking')
    return scores


def source_scores(source):
    protocol = read_json((source / 'protocol.json'))
    bge_meta = read_json((source / 'bge.run.json'))
    h3_meta = read_json((source / 'h3.run.json'))
    coverage_meta = read_json((source / 'coverage.run.json'))
    identity = bge_meta['identity']
    expected = dict(model='BAAI/bge-reranker-v2-m3',
        revision='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e', device='mps',
        dtype='float16', batch_size=32, max_length=512, truncation='only_second',
        attention_implementation='sdpa', passage='title + newline + text', limit=None)
    if any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('BGE model revision or inference settings differ')
    if bge_meta['status'] != 'completed' or coverage_meta['status'] != 'completed':
        raise ValueError('incomplete source run')
    for name, meta in (('h3', h3_meta), ('coverage', coverage_meta), ('bge', bge_meta)):
        if meta['query_count'] != 608 or digest(input_bytes((source / (name + '.jsonl')))) != meta['predictions_sha256']:
            raise ValueError('source cache checksum or count mismatch: ' + name)
    for name, path in (('questions', QUESTIONS), ('queries', QUERIES)):
        if digest(input_bytes(path)) != protocol[name + '_sha256']:
            raise ValueError('benchmark differs from Experiment 2: ' + name)
    if not (identity['queries_sha256'] == bge_meta['queries_sha256'] ==
            coverage_meta['queries_sha256'] == h3_meta['hybrid_run']['queries_sha256'] == protocol['queries_sha256']):
        raise ValueError('inference query fingerprint mismatch')
    if identity['coverage_sha256'] != coverage_meta['predictions_sha256']:
        raise ValueError('BGE input coverage fingerprint mismatch')
    if digest(input_bytes(HYBRID)) != h3_meta['hybrid_run']['predictions_sha256']:
        raise ValueError('frozen Hybrid cache fingerprint mismatch')
    if digest(input_bytes((source / 'executed_runner.py'))) != identity['source_sha256']:
        raise ValueError('executed inference source fingerprint mismatch')
    rows = {name: unique_rows(read_jsonl(source / (name + '.jsonl')), 'query_id')
            for name in ('h3', 'coverage', 'bge')}
    frozen = unique_rows(read_jsonl(HYBRID), 'query_id')
    queries = unique_rows(read_jsonl(QUERIES), 'query_id')
    if len(queries) != 608 or any(set(r) != set(queries) for r in (*rows.values(), frozen)):
        raise ValueError('source query coverage mismatch')
    scores = {qid: validate_pool(rows['h3'][qid], rows['coverage'][qid], rows['bge'][qid], frozen[qid])
              for qid in sorted(queries)}
    sizes = list(map(len, scores.values()))
    if (sum(sizes), min(sizes), max(sizes)) != (68430, 100, 184):
        raise ValueError('unexpected Experiment 2 candidate counts')
    if protocol['candidate_pairs'] != sum(sizes) or bge_meta['pairs'] != sum(sizes):
        raise ValueError('source pair count mismatch')
    ties = {qid: [n for n in Counter(s.values()).values() if n > 1] for qid, s in scores.items()}
    changes, metric_changes = {}, {}
    answer_questions = [q for q in read_jsonl(QUESTIONS) if q['expected_action'] == 'answer']
    for name, prior in (('h3_bge', {q: sorted(stored_order(r), key=lambda c: -scores[q][c])
                                 for q, r in rows['h3'].items()}),
                        ('coverage_bge', {q: r['ranked_chunk_ids'] for q, r in rows['bge'].items()})):
        changes[name] = {str(k): [q for q in scores if direct_order(scores[q])[:k] != prior[q][:k]]
                         for k in (10, 100)}
        changes[name]['full'] = [q for q in scores if direct_order(scores[q]) != prior[q]]
        metric_changes[name] = []
        for question in answer_questions:
            qid = question['question_id']
            groups = [e['acceptable_chunk_ids'] for e in question['evidence_requirements']]
            previous = rank_metrics(groups, prior[qid])
            direct = rank_metrics(groups, direct_order(scores[qid]))
            for k in ('10', '100'):
                for metric in ('all_evidence', 'recall', 'mrr'):
                    if previous[k][metric] != direct[k][metric]:
                        metric_changes[name].append(dict(query_id=qid, cutoff=int(k), metric=metric,
                            previous=previous[k][metric], direct=direct[k][metric]))
    return scores, dict(cache_reused_pairs=sum(sizes), additional_model_calls=0,
        query_count=608, candidate_min=min(sizes), candidate_max=max(sizes),
        candidate_mean=sum(sizes)/len(sizes), model=identity, original_packages=bge_meta['packages'],
        corpus_hash=protocol['corpus_hash'], exact_tie_queries=sum(bool(v) for v in ties.values()),
        exact_tie_groups=sum(map(len, ties.values())), tied_candidates=sum(map(sum, ties.values())),
        changed_order_query_ids=changes, metric_changes=metric_changes,
        input_order_independence_verified=True)


def export_rankings(path, scores):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        for qid in sorted(scores):
            for rank, cid in enumerate(direct_order(scores[qid]), 1):
                # repr(float) round-trips the cache value; never format or round it.
                writer.writerow(dict(query_id=qid, chunk_id=cid,
                    bge_raw_score=repr(scores[qid][cid]), final_rank=rank))


def read_rankings(path):
    scores, ranks = defaultdict(dict), defaultdict(dict)
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError('unexpected ranking CSV fields')
        for row in reader:
            qid, cid = row['query_id'], row['chunk_id']
            if not qid or cid in scores[qid]:
                raise ValueError('empty query_id or duplicate query/chunk pair')
            scores[qid][cid] = float(row['bge_raw_score'])
            ranks[qid][cid] = int(row['final_rank'])
    if not scores:
        raise ValueError('empty ranking CSV')
    for qid, values in scores.items():
        if any(ranks[qid][cid] != rank for rank, cid in enumerate(direct_order(values), 1)):
            raise ValueError('final ranks differ from (-bge_raw_score, chunk_id)')
    return dict(scores)


def evaluate_scores(questions, scores, chunks):
    query_map = unique_rows(questions, 'question_id')
    if set(query_map) != set(scores):
        raise ValueError('ranking CSV must cover all and only benchmark questions')
    predictions = [dict(query_id=qid, retriever=METHOD, hits=[dict(query_id=qid,
        retriever=METHOD, chunk_id=cid, document_id=chunks[cid]['document_id'],
        rank=rank, score=values[cid]) for rank, cid in enumerate(direct_order(values), 1)])
        for qid, values in sorted(scores.items())]
    result, per_question, _ = retrieval_metrics(questions, predictions, set(chunks))
    # Independent calculation via evidence-group minimum ranks, not top-k set scans.
    for row in per_question:
        qid = row['query_id']
        alternate = rank_metrics([e['acceptable_chunk_ids'] for e in query_map[qid]['evidence_requirements']],
                                 direct_order(scores[qid]))
        for k in ('10', '100'):
            for key in ('recall', 'all_evidence', 'mrr'):
                if alternate[k][key] != row['metrics'][k][key]:
                    raise ValueError('independent metric cross-check failed')
    overall = result['retrieval']['overall']
    cutoffs = {k: dict(complete_count=sum(r['metrics'][k]['all_evidence'] for r in per_question),
        denominator=len(per_question), complete_rate=overall['metrics'][k]['all_evidence'],
        recall=overall['metrics'][k]['recall'], mrr=overall['metrics'][k]['mrr']) for k in ('10', '100')}
    return dict(condition=LABEL, method=METHOD, query_count=len(questions),
        answerable_count=len(per_question), excluded_actions=dict(Counter(
            q['expected_action'] for q in questions if q['expected_action'] != 'answer')),
        sort_key='(-bge_raw_score, chunk_id)', score_rounding=False,
        evaluation_scope='Sort every candidate first, then evaluate top k; answer rows only; macro mean across questions.',
        cutoffs=cutoffs, retrieval=result['retrieval'], independent_metric_checks=len(per_question)*6), per_question


def run(source, destination, rankings=None):
    if destination.exists():
        raise ValueError('choose a new output directory to preserve existing results')
    questions = read_jsonl(QUESTIONS)
    if len(questions) != 608 or Counter(q['expected_action'] for q in questions) != dict(answer=488, clarify=60, abstain=60):
        raise ValueError('benchmark must contain 608 questions / 488 answer rows')
    queries = unique_rows(read_jsonl(QUERIES), 'query_id')
    if any(queries[q['question_id']]['question'] != q['question'] for q in questions):
        raise ValueError('question text differs from inference query')
    if rankings is None:
        scores, provenance = source_scores(source)
    else:
        scores = read_rankings(rankings)
        provenance = dict(reaggregated_from_csv=True, additional_model_calls=0)
    with connect(DB, True) as db:
        chunks = {r['chunk_id']: dict(r) for r in db.execute('select * from chunks')}
        corpus_hash = json.loads(db.execute("select value from metadata where key='corpus_hash'").fetchone()[0])
        if provenance.get('corpus_hash', corpus_hash) != corpus_hash:
            raise ValueError('corpus differs from Experiment 2')
        provenance['corpus_fingerprint'] = corpus_identity(db)
    if any(not set(s) <= set(chunks) for s in scores.values()):
        raise ValueError('unknown cached chunk')
    summary, per_question = evaluate_scores(questions, scores, chunks)
    summary['provenance'] = provenance
    destination.mkdir(parents=True)
    csv_path = destination / 'rankings.csv'
    export_rankings(csv_path, scores)
    if read_rankings(csv_path) != scores:
        raise ValueError('CSV raw scores did not round-trip exactly')
    write_json(destination / 'metrics.json', summary)
    write_jsonl(destination / 'per_question.jsonl', per_question)
    inputs = [QUESTIONS, QUERIES, DB, Path(__file__), ROOT/'src/civic_link/evaluation/runner.py',
              ROOT/'src/civic_link/evaluation/metrics.py', ROOT/'src/civic_link/reranking/policies.py']
    inputs += [rankings] if rankings else [HYBRID] + [source/name for name in
        ('protocol.json', 'h3.jsonl', 'h3.run.json', 'coverage.jsonl', 'coverage.run.json',
         'bge.jsonl', 'bge.run.json', 'executed_runner.py')]
    def entry(path):
        compressed = path.with_name(path.name + '.gz')
        if compressed.is_file():
            path = compressed
        return dict(path=path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name,
                    bytes=path.stat().st_size, sha256=digest(path.read_bytes()))
    write_json(destination / 'manifest.json', dict(condition=LABEL, inputs=[entry(p) for p in inputs],
        outputs=[entry(destination/name) for name in ('rankings.csv', 'metrics.json', 'per_question.jsonl')],
        network_calls=0, model_loads=0, raw_score_csv_roundtrip_verified=True))
    print(json.dumps(dict(output=str(destination), cutoffs=summary['cutoffs'],
        candidate_pairs=sum(map(len, scores.values())), additional_model_calls=0),
        ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=SOURCE)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--rankings', type=Path, help='Reaggregate the exported raw-score CSV without original caches')
    args = parser.parse_args()
    run(args.source_dir.resolve(), args.output_dir.resolve(), args.rankings.resolve() if args.rankings else None)
