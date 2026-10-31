"""Run versioned retrieval and explicit-action evaluation on a chosen benchmark.

Retrieval consumes question text only. --run-bm25 is a local integration check,
not a replacement for the adopted Hybrid/H3/BGE experiment. Missing action
outputs remain unmeasured. This command never promotes synthetic review data.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from civic_data import ROOT, connect, digest, now, read_jsonl, write_json, write_jsonl
from evaluate import evaluate
from evaluate_behavior import evaluate_behavior


def expand_rankings(predictions, chunks, prediction_format):
    """Use full stored rankings, never mistake the displayed top 10 for the pool."""
    if prediction_format == 'canonical':
        return predictions
    if prediction_format not in ('h3', 'neural'):
        raise ValueError('unknown retrieval prediction format')
    from feedback_rerank import stored_order
    result = []
    for row in predictions:
        order = stored_order(row) if prediction_format == 'h3' else row['ranked_chunk_ids']
        if (not isinstance(order, list) or len(order) != len(set(order))
                or not set(order) <= set(chunks)):
            raise ValueError('unknown or duplicate full ranked chunk')
        if [hit['chunk_id'] for hit in row['hits']] != order[:len(row['hits'])]:
            raise ValueError('displayed hits disagree with full ranking')
        method = row['retriever']
        scores = {c['chunk_id']: c['fusion_score'] for c in row['candidates']} if prediction_format == 'h3' else {}
        result.append(dict(query_id=row['query_id'], retriever=method,
            score_interpretation='stored fusion score' if prediction_format == 'h3' else
                'reciprocal ordinal placeholder for schema validation; not neural confidence',
            hits=[dict(query_id=row['query_id'], retriever=method, chunk_id=cid,
                document_id=chunks[cid]['document_id'], rank=rank,
                score=scores[cid] if prediction_format == 'h3' else 1/rank)
                for rank, cid in enumerate(order, 1)]))
    return result


def verify_prediction_queries(questions, predictions, queries, metadata, prediction_bytes, query_bytes):
    """Allow predeclared subsets while verifying exact inference question text."""
    query_map = {q['query_id']: q['question'] for q in queries}
    if len(query_map) != len(queries) or len({p['query_id'] for p in predictions}) != len(predictions):
        raise ValueError('duplicate inference query IDs')
    if set(query_map) != {p['query_id'] for p in predictions}:
        raise ValueError('inference query coverage differs')
    query_hash = metadata.get('queries_sha256') or metadata.get('hybrid_run', {}).get('queries_sha256')
    if query_hash != digest(query_bytes) or metadata.get('predictions_sha256') != digest(prediction_bytes):
        raise ValueError('prediction or query provenance checksum differs')
    if metadata.get('status', 'completed') != 'completed':
        raise ValueError('prediction run is incomplete')
    for question in questions:
        if query_map.get(question['question_id']) != question['question']:
            raise ValueError('evaluated question differs from inference text: ' + question['question_id'])
    selected = {q['question_id'] for q in questions}
    return [p for p in predictions if p['query_id'] in selected]


def retrieval_metrics(questions, predictions, known_ids):
    """Require all answerable predictions; nonanswer retrieval is not scored."""
    evaluate_behavior(questions)
    expected = {q['question_id'] for q in questions if q['expected_action'] == 'answer'}
    actual = {p['query_id'] for p in predictions}
    if expected - actual:
        raise ValueError('missing answerable retrieval predictions: ' + ', '.join(sorted(expected - actual)))
    # Reuse established identity, rank, score and evidence-reference validation.
    summary, cases = evaluate(questions, predictions, known_ids)
    if any(len(p['hits']) > 10 for p in predictions):
        # Legacy case explanations are specifically about the displayed top 10.
        # Validate the full order above, but do not use deeper documents to
        # diagnose a top-10 failure or label the full pool as `top_10`.
        _, cases = evaluate(questions, [{**p, 'hits': p['hits'][:10]} for p in predictions], known_ids)
    by_id = {p['query_id']: p for p in predictions}
    rows = []
    for q in questions:
        if q['expected_action'] != 'answer':
            continue
        ranked = [h['chunk_id'] for h in by_id[q['question_id']]['hits']]
        groups = [set(e['acceptable_chunk_ids']) for e in q['evidence_requirements']]
        if not groups or any(not group for group in groups):
            raise ValueError('answerable question requires nonempty evidence groups')
        values = {}
        for k in (1, 3, 5, 10, 100):
            found = [bool(group.intersection(ranked[:k])) for group in groups]
            values[str(k)] = dict(recall=sum(found)/len(found), hit=int(any(found)),
                all_evidence=int(all(found)),
                mrr=next((1/(i+1) for i, cid in enumerate(ranked[:k])
                          if any(cid in group for group in groups)), 0))
        rows.append(dict(query_id=q['question_id'], question_type=q['question_type'],
                         row_origin=q.get('row_origin', 'unspecified'), metrics=values))

    def aggregate(items):
        return dict(question_count=len(items), metrics={str(k): {
            key: sum(r['metrics'][str(k)][key] for r in items)/len(items) if items else None
            for key in ('recall', 'hit', 'all_evidence', 'mrr')}
            for k in (1, 3, 5, 10, 100)})

    grouped = defaultdict(list)
    for row in rows:
        grouped[row['question_type']].append(row)
    by_type = {key: aggregate(value) for key, value in sorted(grouped.items())}
    origins = defaultdict(list)
    for row in rows:
        origins[row['row_origin']].append(row)
    macro = {str(k): {metric: sum(group['metrics'][str(k)][metric]
                                for group in by_type.values())/len(by_type) if by_type else None
                       for metric in ('recall', 'hit', 'all_evidence', 'mrr')}
             for k in (1, 3, 5, 10, 100)}
    summary['retrieval'].update(overall=aggregate(rows), by_type=by_type,
        by_origin={key: aggregate(value) for key, value in sorted(origins.items())},
        category_macro=dict(category_count=len(by_type), metrics=macro))
    summary.update(status='complete_answerable_retrieval' if rows else 'not_applicable_no_answer_rows',
        behavior_rows_excluded_from_retrieval=len(questions)-len(rows),
        note='Metrics compare supplied evidence groups, not claim entailment or generated answers.')
    return summary, rows, cases


def run(questions_path, output_dir, *, benchmark_version, db=ROOT/'data/processed/civic.sqlite',
        retrieval_predictions=None, behavior_predictions=None,
        retrieval_format='canonical', prediction_queries=None):
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError('output exists; choose a new directory')
    if not benchmark_version.strip():
        raise ValueError('benchmark version is required')
    questions_path = Path(questions_path).resolve()
    question_bytes = questions_path.read_bytes()
    questions = [json.loads(line) for line in question_bytes.decode('utf-8').splitlines() if line.strip()]
    input_snapshots = {}
    def snapshot(path, name):
        value = Path(path).read_bytes()
        input_snapshots[name] = dict(path=str(Path(path).resolve()), sha256=digest(value),
            query_text_provenance='unverified_external_predictions; IDs_and_chunks_validated_only')
        return [json.loads(line) for line in value.decode('utf-8').splitlines() if line.strip()]
    # Behavior validation also validates question IDs and action/answerability.
    behavior_input = snapshot(behavior_predictions, 'behavior') if behavior_predictions else []
    behavior, behavior_cases = evaluate_behavior(questions, behavior_input)
    predictions = None
    if retrieval_predictions is not None:
        predictions = snapshot(retrieval_predictions, 'retrieval')
        if prediction_queries is not None:
            query_bytes = Path(prediction_queries).read_bytes()
            queries = [json.loads(line) for line in query_bytes.decode().splitlines() if line.strip()]
            metadata_path = Path(retrieval_predictions).with_suffix('.run.json')
            metadata_bytes = metadata_path.read_bytes()
            metadata = json.loads(metadata_bytes)
            full_count = len(predictions)
            predictions = verify_prediction_queries(questions, predictions, queries, metadata,
                Path(retrieval_predictions).read_bytes(), query_bytes)
            input_snapshots['retrieval'].update(query_text_provenance='verified_exact_text_and_prediction_checksum',
                inference_query_count=full_count, evaluated_subset_count=len(predictions),
                queries_sha256=digest(query_bytes), metadata_sha256=digest(metadata_bytes))
    if predictions is None:
        retrieval = dict(status='unmeasured', question_count=len(questions), metrics=None)
        retrieval_rows, retrieval_cases = [], []
    else:
        with connect(db, True) as connection:
            chunks = {r['chunk_id']: dict(r) for r in connection.execute('SELECT chunk_id, document_id FROM chunks')}
        predictions = expand_rankings(predictions, chunks, retrieval_format)
        retrieval, retrieval_rows, retrieval_cases = retrieval_metrics(questions, predictions, set(chunks))
    output.mkdir(parents=True)
    write_json(output/'retrieval.metrics.json', retrieval)
    write_jsonl(output/'retrieval.per_question.jsonl', retrieval_rows)
    write_jsonl(output/'retrieval.cases.jsonl', retrieval_cases)
    write_json(output/'behavior.metrics.json', behavior)
    write_jsonl(output/'behavior.cases.jsonl', behavior_cases)
    manifest = dict(benchmark_version=benchmark_version, created_at=now(), status='completed',
        questions=str(questions_path), questions_sha256=digest(question_bytes),
        question_count=len(questions), retrieval_status=retrieval['status'],
        behavior_status=behavior['status'],
        independent_holdout=False, human_review='item_level_verification_unrecorded', api_calls=0,
        prediction_inputs=input_snapshots,
        retrieval_prediction_format=retrieval_format,
        source_sha256={name: digest((ROOT/'scripts'/name).read_bytes())
            for name in ('evaluate_week7.py', 'evaluate.py', 'evaluate_behavior.py', 'tokenization.py')},
        limitation='New synthetic items and legacy repeated development data are diagnostic; no independent performance claim.')
    lines = ['# Week 7 diagnostic evaluation', '', f'Benchmark: {benchmark_version}', '',
        f'Questions: {len(questions)}', f'Retrieval: {retrieval["status"]}',
        f'Explicit behavior: {behavior["status"]} ({behavior["measured_count"]}/{len(questions)})', '',
        'This is a diagnostic development benchmark; agent review and user-reported review do not establish independent gold. '
        'The original 204 questions are included in the current 608-question benchmark.']
    if predictions is not None:
        overall = retrieval['retrieval']['overall']
        values = overall['metrics']
        def shown(value):
            return 'N/A' if value is None else f'{value:.4f}'
        lines += ['', '| Scope | Count | Recall@10 | Complete@10 | MRR@100 |',
                  '|---|---:|---:|---:|---:|',
                  f'| Answerable | {overall["question_count"]} | {shown(values["10"]["recall"])} | '
                  f'{shown(values["10"]["all_evidence"])} | {shown(values["100"]["mrr"])} |']
    lines += ['', 'Action metrics require explicit predicted_action outputs. Retrieval scores are never converted into actions. '
              'Response quality and clarification wording are not measured by action classification.']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    manifest['output_sha256'] = {p.name: digest(p.read_bytes()) for p in sorted(output.iterdir())
                                 if p.is_file() and p.name != 'manifest.json'}
    write_json(output/'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions', type=Path, required=True)
    parser.add_argument('--benchmark-version', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--db', type=Path, default=ROOT/'data/processed/civic.sqlite')
    parser.add_argument('--retrieval-predictions', type=Path)
    parser.add_argument('--retrieval-format', choices=('canonical', 'h3', 'neural'), default='canonical')
    parser.add_argument('--prediction-queries', type=Path,
                        help='Verify exact inference text and adjacent .run.json; supports a benchmark subset')
    parser.add_argument('--behavior-predictions', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.questions, args.output_dir, benchmark_version=args.benchmark_version,
        db=args.db, retrieval_predictions=args.retrieval_predictions,
        behavior_predictions=args.behavior_predictions,
        retrieval_format=args.retrieval_format, prediction_queries=args.prediction_queries), ensure_ascii=False, indent=2))
