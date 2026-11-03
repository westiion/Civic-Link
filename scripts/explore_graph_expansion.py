"""Add graph-navigation candidates without dropping the frozen original pool.

Prepare and evaluate need no model. Infer uses the existing pinned local BGE
runner at the original 512-token/MPS/float16 settings, with no downloads or APIs.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401
import argparse
import gzip
import json
from pathlib import Path
import statistics

from civic_link.common import ROOT, digest, input_bytes, read_json, read_jsonl, write_json, write_jsonl
from civic_link.reranking.policies import rank_metrics
from explore_graph_weights import json_lines, paired, aggregate
from week7_hypothesis_support import BASELINE, SOURCE, load_frozen

REVISION = '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'


def prepare(out, traversal):
    if out.exists():
        raise ValueError('choose a new output directory')
    queries, pools, protocol = load_frozen()
    manifest = read_json(traversal/'manifest.json')
    for item in manifest['outputs']:
        if digest((traversal/item['path']).read_bytes()) != item['sha256']:
            raise ValueError('traversal output checksum mismatch')
    records = list(json_lines(traversal/'per_question.jsonl.gz'))
    if {r['query_id'] for r in records} != set(queries):
        raise ValueError('incomplete traversal outputs')
    plan, rows = [], []
    for r in records:
        qid = r['query_id']
        old = sorted(pools[qid], key=lambda c: (-pools[qid][c], c))
        additions = {v: sorted(set(r['variants'][v]['new_chunks'])-set(old))
                     for v in ('more_seeds', 'reverse5')}
        new = set().union(*map(set, additions.values()))
        anchors = old[9:11]  # Original ranks 10/11, chosen without gold.
        ids = sorted(new | set(anchors))
        rows.append(dict(query_id=qid, candidates=[dict(chunk_id=c, fusion_rank=i)
                                                   for i, c in enumerate(ids, 1)]))
        plan.append(dict(query_id=qid, additions=additions, anchors=anchors))
    out.mkdir(parents=True)
    write_jsonl(out/'plan.jsonl', plan)
    with gzip.open(out/'coverage.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(',', ':'))+'\n')
    write_json(out/'protocol.json', dict(
        queries_sha256=protocol['queries_sha256'], questions_sha256=protocol['questions_sha256'],
        corpus_hash=protocol['corpus_hash'], original_pairs=sum(map(len, pools.values())),
        new_pairs=sum(len(set().union(*map(set, r['additions'].values()))) for r in plan),
        anchors=len(plan)*2, scoring_pairs=sum(len(r['candidates']) for r in rows),
        baseline_sha256=digest(input_bytes(BASELINE)),
        traversal_manifest_sha256=digest((traversal/'manifest.json').read_bytes()),
        traversal_records_sha256=digest(input_bytes(traversal/'per_question.jsonl.gz')),
        plan_sha256=digest(input_bytes(out/'plan.jsonl')),
        coverage_sha256=digest(input_bytes(out/'coverage.jsonl.gz')),
        revision=REVISION, max_length=512,
        policy='Original candidate pool always preserved. Union of additional more_seeds and reverse5 graph candidates.',
        ranking_uses_gold=False, independent_holdout=False))
    print(json.dumps(read_json(out/'protocol.json'), indent=2))


def check(out):
    protocol = read_json(out/'protocol.json')
    queries, pools, frozen = load_frozen()
    for field in ('queries_sha256', 'questions_sha256'):
        if protocol[field] != frozen[field]:
            raise ValueError('frozen input changed')
    for p, field in ((out/'plan.jsonl', 'plan_sha256'), (out/'coverage.jsonl.gz', 'coverage_sha256'),
                     (BASELINE, 'baseline_sha256')):
        if digest(input_bytes(p)) != protocol[field]:
            raise ValueError('input checksum differs')
    return queries, pools, protocol


def evaluate(out):
    if (out/'summary.json').exists():
        raise ValueError('evaluation already exists')
    queries, pools, protocol = check(out)
    run = read_json(out/'bge.run.json')
    if run['status'] != 'completed' or digest(input_bytes(out/'bge.jsonl')) != run['predictions_sha256']:
        raise ValueError('inference incomplete or modified')
    expected = read_json(SOURCE/'bge.run.json')
    for key in ('model', 'revision', 'device', 'dtype', 'batch_size', 'max_length',
                'truncation', 'attention_implementation', 'passage'):
        if run['identity'][key] != expected['identity'][key]:
            raise ValueError('model settings differ: '+key)
    if run['packages'] != expected['packages']:
        raise ValueError('inference packages differ from the frozen baseline')
    results = {r['query_id']: r for r in read_jsonl(out/'bge.jsonl')}
    plan = read_jsonl(out/'plan.jsonl')
    gold_path = ROOT/'data/benchmark/questions.jsonl'
    if digest(input_bytes(gold_path)) != protocol['questions_sha256']:
        raise ValueError('gold changed')
    gold = {q['question_id']: q for q in read_jsonl(gold_path)}
    scores = {q: {s['chunk_id']: s['score'] for s in r['bge_scores']} for q, r in results.items()}
    assert set(scores) == set(queries)
    records, controls, budgets, rankings = [], [], [], []
    for p in plan:
        qid = p['query_id']
        # Non-answer questions may still carry diagnostic evidence. Eligibility
        # must follow expected_action, not the presence of evidence records.
        eligible = gold[qid]['expected_action'] == 'answer'
        groups = [e['acceptable_chunk_ids'] for e in gold[qid]['evidence_requirements']] if eligible else []
        if eligible and not groups:
            raise ValueError('answer question without evidence groups')
        new = set().union(*map(set, p['additions'].values()))
        assert set(scores[qid]) == new | set(p['anchors'])
        controls.extend(dict(query_id=qid, chunk_id=c, old=pools[qid][c], new=scores[qid][c],
                             delta=scores[qid][c]-pools[qid][c]) for c in p['anchors'])
        conditions = dict(bge=set(), more_seeds=set(p['additions']['more_seeds']),
                          reverse5=set(p['additions']['reverse5']), union=new)
        metrics, saved = {}, {}
        for name, ids in conditions.items():
            values = dict(pools[qid])
            values.update({c: scores[qid][c] for c in ids})
            order = sorted(values, key=lambda c: (-values[c], c))
            saved[name] = order[:10]
            if groups:
                metrics[name] = rank_metrics(groups, order)
            if name == 'union':
                budgets.append(len(order))
        # Deterministic score-offset stress tests, not confidence intervals.
        if groups:
            for delta in (-.1, -.05, .05, .1):
                values = dict(pools[qid])
                values.update({c: scores[qid][c]+delta for c in new})
                order = sorted(values, key=lambda c: (-values[c], c))
                metrics[f'union_offset_{delta:g}'] = rank_metrics(groups, order)
            records.append(dict(query_id=qid, question_type=gold[qid]['question_type'], metrics=metrics))
        rankings.append(dict(query_id=qid, top10=saved))
    names = tuple(records[0]['metrics'])
    if len(records) != 488:
        raise ValueError('answer-only evaluation must contain 488 questions')
    original = {r['query_id']: r['metrics'] for r in read_jsonl(BASELINE.parent/'per_question.jsonl')}
    for record in records:
        for k in ('10', '100'):
            for field in ('all_evidence', 'recall', 'mrr'):
                if record['metrics']['bge'][k][field] != original[record['query_id']][k][field]:
                    raise ValueError('frozen answer baseline not reproduced')
    drifts = sorted(abs(c['delta']) for c in controls)
    summary = dict(overall={n: aggregate(records, n) for n in names},
                   comparisons={n: paired(records, n) for n in ('more_seeds', 'reverse5', 'union')},
                   by_type={t: {n: aggregate([r for r in records if r['question_type'] == t], n)
                                for n in ('bge', 'more_seeds', 'reverse5', 'union')}
                            for t in sorted({r['question_type'] for r in records})},
                   controls=dict(n=len(controls), exact=sum(d == 0 for d in drifts),
                                 mean_abs=statistics.mean(drifts), median_abs=statistics.median(drifts),
                                 p95_abs=drifts[int(.95*(len(drifts)-1))], max_abs=max(drifts)),
                   budget=dict(new_pairs=protocol['new_pairs'], scoring_pairs=run['pairs'],
                               original_pairs=protocol['original_pairs'], mean_pool=statistics.mean(budgets),
                               min_pool=min(budgets), max_pool=max(budgets),
                               inference_seconds=run['inference_query_seconds']),
                   api_calls=0, new_model_inferences=run['pairs'],
                   limitations=['Synthetic, repeatedly inspected development benchmark; no independent holdout.',
                                'New pairs scored at512; all original scores kept exactly frozen.',
                                'Anchor score drift diagnoses batch-sensitive MPS float16 arithmetic.',
                                'Score-offset stress tests are diagnostics, not guaranteed error bounds.',
                                'Evidence retrieval, no generated-answer evaluation.'])
    write_json(out/'summary.json', summary)
    write_jsonl(out/'controls.jsonl', controls)
    write_jsonl(out/'top10.jsonl', rankings)
    with gzip.open(out/'per_question.jsonl.gz', 'wt', encoding='utf-8') as stream:
        for r in records:
            stream.write(json.dumps(r, separators=(',', ':'))+'\n')
    inputs = [BASELINE, gold_path, ROOT/'data/benchmark/queries.jsonl', Path(__file__),
              ROOT/'scripts/run_week7_experiment2.py']
    write_json(out/'manifest.json', dict(
        inputs=[dict(path=str(p.relative_to(ROOT)), logical_sha256=digest(input_bytes(p))) for p in inputs],
        corpus_hash=protocol['corpus_hash'],
        outputs=[dict(path=p.name, sha256=digest(p.read_bytes())) for p in sorted(out.iterdir())],
        ranking_uses_gold=False, independent_holdout=False, api_calls=0))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--traversal', type=Path, required=True)
    for cmd in ('infer', 'evaluate'):
        p = sub.add_parser(cmd)
        if cmd == 'infer':
            p.add_argument('--model-dir', type=Path, required=True)
    for p in sub.choices.values():
        p.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.out, args.traversal)
    elif args.command == 'infer':
        check(args.out)
        from run_week7_experiment2 import infer
        infer(args.out, args.model_dir, REVISION)
    else:
        evaluate(args.out)


if __name__ == '__main__':
    main()
