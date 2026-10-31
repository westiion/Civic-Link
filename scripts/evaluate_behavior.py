"""Evaluate explicit answer/clarify/abstain labels separately from retrieval.

Prediction JSONL rows must contain ``query_id`` and ``predicted_action``. The
labels must come from a system decision or a separate review of its response,
never gold labels or retrieval scores. Omitting --predictions creates an honest
coverage audit with null quality metrics. No model or network calls are made.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from civic_data import ROOT, digest, now, write_json, write_jsonl


ACTIONS = ('answer', 'clarify', 'abstain')
SCHEMA_VERSION = 'civic-link-behavior-evaluation-accepted-actions'


def _index(rows, key, kind):
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f'{kind} must be a JSON object')
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f'{kind} requires a nonempty {key}')
        if identifier in result:
            raise ValueError(f'duplicate {kind} ID: {identifier}')
        result[identifier] = row
    return result


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate_behavior(questions, predictions=()):
    """Return coverage and observed label metrics; missing is never abstention.

    Accuracy, recall, F1 and error rates are conditional on measured questions.
    Missing predictions have a separate confusion-matrix column and per-class
    coverage; they are neither fabricated failures nor fabricated abstentions.
    This evaluates declared actions, not generated text or factual correctness.
    """
    gold = _index(questions, 'question_id', 'question')
    predicted = _index(predictions, 'query_id', 'prediction')
    if not gold:
        raise ValueError('questions must not be empty')
    unknown = set(predicted) - set(gold)
    if unknown:
        raise ValueError('unknown prediction query ID: ' + ', '.join(sorted(unknown)))
    for qid, question in gold.items():
        action = question.get('expected_action')
        if action not in ACTIONS:
            raise ValueError(f'invalid or missing expected_action: {qid}')
        allowed = question.get('acceptable_actions', [action])
        if (not isinstance(allowed, list) or not allowed or action not in allowed
                or any(a not in ACTIONS for a in allowed) or len(set(allowed)) != len(allowed)):
            raise ValueError(f'invalid acceptable_actions: {qid}')
        answerability = question.get('expected_answerability')
        if answerability is not None and answerability != (
                'answerable' if action == 'answer' else 'unanswerable'):
            raise ValueError(f'expected_action/expected_answerability mismatch: {qid}')
    for qid, prediction in predicted.items():
        if prediction.get('predicted_action') not in ACTIONS:
            raise ValueError(f'explicit predicted_action required (answer/clarify/abstain): {qid}')

    matrix = {action: {label: 0 for label in (*ACTIONS, 'missing_prediction')}
              for action in ACTIONS}
    cases = []
    for qid, question in gold.items():
        expected = question['expected_action']
        actual = predicted[qid]['predicted_action'] if qid in predicted else None
        allowed = question.get('acceptable_actions', [expected])
        matrix[expected][actual or 'missing_prediction'] += 1
        cases.append(dict(
            query_id=qid, question=question.get('question'), expected_action=expected,
            predicted_action=actual, acceptable_actions=allowed,
            correct=actual in allowed if actual is not None else None,
            strict_correct=actual == expected if actual is not None else None,
            status='measured' if actual is not None else 'unmeasured_missing_prediction'))

    by_action = {}
    for action in ACTIONS:
        support = sum(matrix[action].values())
        measured = support - matrix[action]['missing_prediction']
        predicted_count = sum(matrix[expected][action] for expected in ACTIONS)
        true_positive = matrix[action][action]
        false_positive = predicted_count - true_positive
        false_negative = measured - true_positive
        by_action[action] = dict(
            question_count=support, measured_count=measured,
            missing_count=support - measured, coverage=_ratio(measured, support),
            predicted_count=predicted_count, correct_count=true_positive,
            precision_on_measured=_ratio(true_positive, predicted_count),
            recall_on_measured=_ratio(true_positive, measured),
            f1_on_measured=_ratio(2 * true_positive,
                                  2 * true_positive + false_positive + false_negative))

    measured_count = len(predicted)
    strict_correct_count = sum(matrix[action][action] for action in ACTIONS)
    correct_count = sum(c['correct'] is True for c in cases)
    nonanswer_measured = sum(by_action[action]['measured_count'] for action in ('clarify', 'abstain'))
    answer_on_nonanswer = sum(c['expected_action'] != 'answer' and c['predicted_action'] == 'answer'
                             and not c['correct'] for c in cases)
    unnecessary_nonanswer = sum(c['expected_action'] == 'answer' and c['predicted_action'] in ('clarify','abstain')
                               and not c['correct'] for c in cases)
    # Do not present a three-class macro average when a class has no measured
    # gold examples (including a wholly unmeasured class in a partial run).
    macro_f1 = (sum(by_action[a]['f1_on_measured'] for a in ACTIONS) / len(ACTIONS)
                if all(by_action[a]['measured_count'] for a in ACTIONS) else None)
    status = ('unmeasured' if not measured_count else
              'complete' if measured_count == len(gold) else 'partial')
    summary = dict(
        schema_version=SCHEMA_VERSION, status=status,
        scope='explicit_action_labels_only_not_generated_response_quality',
        question_count=len(gold), prediction_count=measured_count,
        measured_count=measured_count, missing_count=len(gold) - measured_count,
        missing_predictions=sorted(set(gold) - set(predicted)),
        coverage=_ratio(measured_count, len(gold)), correct_count=correct_count,
        accuracy_on_measured=_ratio(correct_count, measured_count),
        strict_correct_count=strict_correct_count,
        strict_accuracy_on_measured=_ratio(strict_correct_count, measured_count),
        acceptable_alternative_count=correct_count-strict_correct_count,
        macro_f1_on_measured=macro_f1, by_expected_action=by_action,
        confusion_matrix=matrix,
        error_rates_on_measured=dict(
            answer_on_nonanswer=dict(count=answer_on_nonanswer, denominator=nonanswer_measured,
                                     rate=_ratio(answer_on_nonanswer, nonanswer_measured)),
            unnecessary_nonanswer=dict(count=unnecessary_nonanswer,
                                       denominator=by_action['answer']['measured_count'],
                                       rate=_ratio(unnecessary_nonanswer, by_action['answer']['measured_count']))),
        generation_quality=dict(status='unmeasured', accuracy=None),
        review_status_counts=dict(sorted(Counter(q.get('review_status', 'unspecified')
                                                 for q in gold.values()).items())),
        explicitly_ineligible_question_count=sum(
            q.get('eligible_for_official_metrics') is False for q in gold.values()),
        definitions=dict(
            measured='A valid explicit predicted_action was supplied for the question.',
            metrics='Accuracy/error rates accept declared alternative actions. Confusion matrix, per-class metrics and macro F1 retain the canonical expected_action; inspect strict_accuracy and coverage.',
            acceptable_actions='Accepted action does not certify the response content; required and must_not rubrics still need response review.',
            missing='Missing predictions are unmeasured, not abstain; full-benchmark accuracy is not claimed.',
            macro_f1='Arithmetic mean of three class F1 values; null unless all three classes have measured gold examples.',
            zero_denominator='Undefined rates are null, never zero.',
            official_status='Evaluating a file does not promote pending-review questions or make them an independent holdout.',
            response_quality='Action labels do not establish response correctness, citation quality or appropriate clarification content.'))
    return summary, cases


def _report(summary, benchmark_version):
    def percent(value):
        return 'unmeasured / undefined' if value is None else f'{100 * value:.2f}%'

    rows = [
        '# Explicit action evaluation', '',
        f'- Benchmark version: `{benchmark_version or "unspecified; identified by question-file SHA-256"}`',
        f'- Status: **{summary["status"]}**',
        f'- Measured: {summary["measured_count"]}/{summary["question_count"]} '
        f'({percent(summary["coverage"])})',
        f'- Action accuracy on measured questions: {percent(summary["accuracy_on_measured"])}', '',
        '| Canonical expected action | Questions | Measured | Coverage | Strict precision | Strict recall | Strict F1 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for action, metric in summary['by_expected_action'].items():
        rows.append(f'| {action} | {metric["question_count"]} | {metric["measured_count"]} | '
                    + ' | '.join(percent(metric[key]) for key in
                                 ('coverage', 'precision_on_measured', 'recall_on_measured', 'f1_on_measured')) + ' |')
    rows += ['', 'Missing predictions are recorded separately and never treated as abstentions. '
             'Accuracy accepts declared alternative actions; the confusion matrix and class F1 retain canonical labels. '
             'All classification metrics are conditional on measured questions; partial coverage does not establish full-benchmark performance.', '',
             'Only explicit action labels are evaluated. Retrieval scores do not determine actions. '
             'Generated response correctness, citation quality and the content of clarification questions remain unmeasured. '
             'Pending-review data remain pending; this command does not promote a benchmark.']
    return '\n'.join(rows) + '\n'


def run(questions_path, output_dir, *, predictions_path=None, benchmark_version=None,
        prediction_source=None, require_complete=False):
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError('output exists; choose a new directory')
    questions_path = Path(questions_path).resolve()
    predictions_path = Path(predictions_path).resolve() if predictions_path is not None else None
    # Hash exactly the bytes parsed, so the manifest cannot fingerprint a newer
    # file than the one actually evaluated.
    question_bytes = questions_path.read_bytes()
    prediction_bytes = predictions_path.read_bytes() if predictions_path is not None else None
    questions = [json.loads(line) for line in question_bytes.decode('utf-8').splitlines() if line.strip()]
    predictions = ([json.loads(line) for line in prediction_bytes.decode('utf-8').splitlines() if line.strip()]
                   if prediction_bytes is not None else [])
    summary, cases = evaluate_behavior(questions, predictions)
    if require_complete and summary['status'] != 'complete':
        raise ValueError('complete explicit action predictions required; '
                         f'{summary["missing_count"]} missing')
    summary['benchmark_version'] = benchmark_version
    summary['questions_sha256'] = digest(question_bytes)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output/'metrics.json', summary)
    write_jsonl(output/'cases.jsonl', cases)
    (output/'REPORT.md').write_text(_report(summary, benchmark_version), encoding='utf-8')
    manifest = dict(
        schema_version=SCHEMA_VERSION, status='completed', created_at=now(), api_calls=0,
        benchmark_version=benchmark_version, prediction_source=prediction_source,
        inputs=dict(questions=str(questions_path),
                    predictions=str(predictions_path) if predictions_path is not None else None),
        input_sha256=dict(questions=digest(question_bytes),
                          predictions=digest(prediction_bytes) if prediction_bytes is not None else None),
        source_sha256={name: digest((ROOT/'scripts'/name).read_bytes())
                       for name in ('evaluate_behavior.py', 'civic_data.py')},
        output_sha256={name: digest((output/name).read_bytes())
                       for name in ('metrics.json', 'cases.jsonl', 'REPORT.md')},
        evaluation_status=summary['status'], require_complete=require_complete)
    write_json(output/'manifest.json', manifest)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions', type=Path, default=ROOT/'data/questions.jsonl')
    parser.add_argument('--predictions', type=Path,
                        help='JSONL containing explicit query_id and predicted_action; omit to audit missing coverage')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--benchmark-version')
    parser.add_argument('--prediction-source', help='System/run or response-review provenance for the supplied labels')
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    summary = run(args.questions, args.output_dir, predictions_path=args.predictions,
                  benchmark_version=args.benchmark_version, prediction_source=args.prediction_source,
                  require_complete=args.require_complete)
    print(json.dumps({key: summary[key] for key in
                      ('status', 'question_count', 'measured_count', 'coverage', 'accuracy_on_measured')}, indent=2))


if __name__ == '__main__':
    main()
