"""Reproducible CPU-only score ablation; freeze selection before validation."""
from __future__ import annotations

import _bootstrap  # Make the local src package available to script entry points.

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time

from civic_link.common import ROOT, connect, digest, read_jsonl, write_json, write_jsonl
from civic_link.evaluation.runner import verify_prediction_queries
from civic_link.reranking.policies import rank_metrics, rerank, stored_order
from civic_link.reranking.coverage import rank
from civic_link.reranking.overlap import rerank_overlap


POLICIES = {
    'h3_feature': {},
    'rrf_only': {},
    'uniform_graph_rrf': {},
    'h3_overlap': {},
    'score_balanced': {'weights': (.4, .4, .2), 'penalty': 0.},
    'score_balanced_overlap': {'weights': (.4, .4, .2), 'penalty': .15},
    'score_text_priority_overlap': {'weights': (.3, .5, .2), 'penalty': .15},
    'score_hybrid_priority_overlap': {'weights': (.5, .3, .2), 'penalty': .15},
    'no_graph_control': {'weights': (.4, .4, .2), 'penalty': .15, 'use_graph': False},
}
SELECTABLE = ('h3_feature', 'h3_overlap', 'score_balanced', 'score_balanced_overlap',
              'score_text_priority_overlap', 'score_hybrid_priority_overlap')


def split_questions(questions):
    """Keep declared intent and overlapping-evidence components together."""
    parent = {q['question_id']: q['question_id'] for q in questions}
    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    seen = {}
    for q in questions:
        for field in ('intent_component_id', 'evidence_component_id'):
            key = (field, q[field])
            if key in seen:
                parent[root(q['question_id'])] = root(seen[key])
            seen[key] = q['question_id']
    groups = defaultdict(list)
    for qid in parent:
        groups[root(qid)].append(qid)
    result = {}
    for group in groups.values():
        signature = hashlib.sha256('|'.join(sorted(group)).encode()).hexdigest()
        split = 'validation' if int(signature[:8], 16) % 10 < 3 else 'development'
        result.update({qid: dict(split=split, group=signature[:16]) for qid in group})
    return result


def metrics(questions, orders):
    rows = []
    for q in questions:
        if q['expected_action'] != 'answer':
            continue
        values = rank_metrics([e['acceptable_chunk_ids'] for e in q['evidence_requirements']],
                              orders[q['question_id']])
        rows.append(dict(question_id=q['question_id'], question_type=q['question_type'], **values))
    n = len(rows)
    return dict(n=n, complete=sum(r['10']['all_evidence'] for r in rows),
                complete_at_10=sum(r['10']['all_evidence'] for r in rows)/n,
                recall_at_10=sum(r['10']['recall'] for r in rows)/n,
                mrr_at_100=sum(r['100']['mrr'] for r in rows)/n,
                pool_complete=sum(r['pool_recall'] == 1 for r in rows)), rows


def run(args):
    out = args.output_dir
    if out.exists():
        raise ValueError('output already exists')
    questions = read_jsonl(args.questions)
    queries = read_jsonl(args.queries)
    cached = read_jsonl(args.h3)
    metadata = json.loads(args.h3.with_suffix('.run.json').read_text(encoding='utf-8'))
    verify_prediction_queries(questions, cached, queries, metadata, args.h3.read_bytes(), args.queries.read_bytes())
    if metadata['config']['rerank'] != 'feature':
        raise ValueError('cached feature H3 required')
    with connect(ROOT/'data/corpus/civic.sqlite', True) as db:
        chunks = {r['chunk_id']: dict(r) for r in db.execute('SELECT * FROM chunks')}
    splits = split_questions(questions)
    out.mkdir(parents=True)
    write_json(out/'protocol.json', dict(policies=POLICIES, selectable=SELECTABLE, split=splits,
        selection='development Complete@10, then Recall@10, then MRR@100; deterministic policy order tie-break',
        candidate_pool='same previously admitted H3 union for every rescoring',
        no_neural=True, model_loads=0, api_calls=0,
        source_shared=True, independent_test=False,
        inputs={str(p): digest(p.read_bytes()) for p in (args.questions,args.queries,args.h3)},
        code={p: digest((ROOT/p).read_bytes()) for p in ('src/civic_link/reranking/coverage.py','scripts/run_score_comparison.py','src/civic_link/reranking/overlap.py')}))
    all_orders = {name: {} for name in POLICIES}
    times = defaultdict(list)
    for row in cached:
        expected = set(stored_order(row))
        for name, cfg in POLICIES.items():
            start = time.perf_counter()
            if name == 'h3_feature':
                order = stored_order(row)
            elif name == 'rrf_only':
                order = rerank(row)
                stored_rrf = sorted((c for c in row['candidates'] if c['fusion_rank'] is not None), key=lambda c:c['rrf_rank'])
                if order != [c['chunk_id'] for c in stored_rrf]:
                    raise ValueError('baseline RRF reconstruction mismatch')
            elif name == 'uniform_graph_rrf':
                order = rerank(row, policy='uniform')
            elif name == 'h3_overlap':
                order, _ = rerank_overlap(row['candidates'], chunks)
            else:
                order, _ = rank(row['candidates'], chunks, **cfg)
            times[name].append((time.perf_counter()-start)*1000)
            if len(order) != len(expected) or set(order) != expected:
                raise ValueError('candidate pool changed')
            all_orders[name][row['query_id']] = order
    write_jsonl(out/'rankings.jsonl', [dict(query_id=qid, orders={name: orders[qid] for name, orders in all_orders.items()}) for qid in sorted(splits)])
    dev = [q for q in questions if splits[q['question_id']]['split'] == 'development']
    dev_metrics = {name: metrics(dev, orders)[0] for name,orders in all_orders.items()}
    selected = max(SELECTABLE, key=lambda name: tuple(dev_metrics[name][k] for k in ('complete_at_10','recall_at_10','mrr_at_100')))
    # Persist decision before any validation metric is computed or exposed.
    write_json(out/'selection.json', dict(selected=selected, config=POLICIES[selected], development=dev_metrics))
    summary = dict(selected=selected, timing_ms={name:dict(mean=sum(v)/len(v), maximum=max(v)) for name,v in times.items()}, subsets={})
    per_question = []
    for subset in ('development','validation','all'):
        qs = questions if subset=='all' else [q for q in questions if splits[q['question_id']]['split']==subset]
        summary['subsets'][subset] = {}
        for name, orders in all_orders.items():
            aggregate, rows = metrics(qs, orders)
            summary['subsets'][subset][name] = aggregate
            if subset=='all':
                per_question.extend(dict(policy=name, split=splits[r['question_id']]['split'], **r) for r in rows)
    base = {r['question_id']:r for r in per_question if r['policy']=='h3_feature'}
    changes = [dict(**r, previous_complete=base[r['question_id']]['10']['all_evidence'],
                    previous_group_ranks=base[r['question_id']]['group_ranks'])
               for r in per_question if r['policy']==selected and r['10']['all_evidence']!=base[r['question_id']]['10']['all_evidence']]
    write_json(out/'summary.json',summary)
    write_jsonl(out/'per_question.jsonl',per_question)
    write_jsonl(out/'changes.jsonl',changes)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions',type=Path,default=ROOT/'data/benchmark/questions.jsonl')
    parser.add_argument('--queries',type=Path,default=ROOT/'data/benchmark/queries.jsonl')
    parser.add_argument('--h3',type=Path,default=ROOT/'outputs/baseline/h3.jsonl')
    parser.add_argument('--output-dir',type=Path,required=True)
    run(parser.parse_args())
