import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
from civic_link.evaluation.behavior import evaluate_behavior, run


def questions(*actions):
    return [dict(question_id=f'q{i}', question=f'Question {i}', expected_action=action,
                 expected_answerability='answerable' if action == 'answer' else 'unanswerable')
            for i, action in enumerate(actions)]


def predictions(*actions):
    return [dict(query_id=f'q{i}', predicted_action=action) for i, action in enumerate(actions)]


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')


def test_accepted_alternative_does_not_corrupt_strict_confusion_or_invent_response_quality():
    gold=questions('clarify','abstain')
    gold[0]['acceptable_actions']=['clarify','answer']
    summary,cases=evaluate_behavior(gold,predictions('answer','abstain'))
    assert summary['accuracy_on_measured']==1
    assert summary['strict_accuracy_on_measured']==.5
    assert summary['acceptable_alternative_count']==1
    assert summary['confusion_matrix']['clarify']['answer']==1
    assert summary['error_rates_on_measured']['answer_on_nonanswer']['count']==0
    assert cases[0]['correct'] is True and cases[0]['strict_correct'] is False
    assert summary['generation_quality']['status']=='unmeasured'
    gold[0]['acceptable_actions']=['answer']
    with pytest.raises(ValueError,match='acceptable_actions'):
        evaluate_behavior(gold)


def test_three_class_confusion_and_error_rates_use_explicit_decisions():
    gold = questions('answer', 'answer', 'clarify', 'clarify', 'abstain', 'abstain')
    supplied = predictions('answer', 'clarify', 'answer', 'clarify', 'abstain', 'clarify')
    original = copy.deepcopy((gold, supplied))
    summary, cases = evaluate_behavior(gold, supplied)
    assert (gold, supplied) == original
    assert summary['status'] == 'complete'
    assert summary['accuracy_on_measured'] == .5
    assert summary['confusion_matrix'] == {
        'answer': dict(answer=1, clarify=1, abstain=0, missing_prediction=0),
        'clarify': dict(answer=1, clarify=1, abstain=0, missing_prediction=0),
        'abstain': dict(answer=0, clarify=1, abstain=1, missing_prediction=0)}
    per_class = summary['by_expected_action']
    assert per_class['answer']['precision_on_measured'] == .5
    assert per_class['clarify']['precision_on_measured'] == pytest.approx(1/3)
    assert per_class['clarify']['recall_on_measured'] == .5
    assert per_class['clarify']['f1_on_measured'] == pytest.approx(.4)
    assert per_class['abstain']['precision_on_measured'] == 1
    assert per_class['abstain']['f1_on_measured'] == pytest.approx(2/3)
    assert summary['macro_f1_on_measured'] == pytest.approx((.5 + .4 + 2/3) / 3)
    assert summary['error_rates_on_measured'] == {
        'answer_on_nonanswer': dict(count=1, denominator=4, rate=.25),
        'unnecessary_nonanswer': dict(count=1, denominator=2, rate=.5)}
    assert [row['correct'] for row in cases] == [True, False, False, True, True, False]
    assert summary['generation_quality'] == dict(status='unmeasured', accuracy=None)


def test_missing_predictions_remain_unmeasured_including_all_27_diagnostic_questions():
    gold = questions(*(['answer'] * 177 + ['clarify'] * 17 + ['abstain'] * 10))
    summary, cases = evaluate_behavior(gold)
    assert summary['question_count'] == 204
    assert summary['status'] == 'unmeasured'
    assert summary['coverage'] == 0
    assert summary['accuracy_on_measured'] is None
    assert summary['macro_f1_on_measured'] is None
    assert all(row['correct'] is None and row['predicted_action'] is None for row in cases)
    for action, count in (('answer', 177), ('clarify', 17), ('abstain', 10)):
        metric = summary['by_expected_action'][action]
        assert metric['question_count'] == metric['missing_count'] == count
        assert metric['recall_on_measured'] is metric['precision_on_measured'] is metric['f1_on_measured'] is None
        assert summary['confusion_matrix'][action]['missing_prediction'] == count
    assert summary['error_rates_on_measured']['answer_on_nonanswer']['rate'] is None


def test_partial_predictions_expose_missing_class_without_treating_it_as_abstain():
    summary, cases = evaluate_behavior(questions('answer', 'clarify', 'abstain'), predictions('answer'))
    assert summary['status'] == 'partial'
    assert summary['accuracy_on_measured'] == 1
    assert summary['coverage'] == pytest.approx(1/3)
    assert summary['missing_predictions'] == ['q1', 'q2']
    assert summary['macro_f1_on_measured'] is None
    assert summary['by_expected_action']['abstain']['predicted_count'] == 0
    assert summary['by_expected_action']['abstain']['coverage'] == 0
    assert cases[2]['status'] == 'unmeasured_missing_prediction'


def test_no_true_positives_are_zero_but_undefined_precision_is_null():
    summary, _ = evaluate_behavior(questions('answer', 'clarify'), predictions('clarify', 'abstain'))
    per_class = summary['by_expected_action']
    assert per_class['answer']['precision_on_measured'] is None
    assert per_class['answer']['recall_on_measured'] == 0
    assert per_class['answer']['f1_on_measured'] == 0
    assert per_class['abstain']['precision_on_measured'] == 0
    assert per_class['abstain']['recall_on_measured'] is None
    assert per_class['abstain']['f1_on_measured'] == 0


@pytest.mark.parametrize('row', [
    dict(query_id='q0', hits=[dict(score=1, rank=1)]),
    dict(query_id='q0', expected_action='answer'),
    dict(query_id='q0', action='answer'),
    dict(query_id='q0', predicted_action=None),
    dict(query_id='q0', predicted_action='unknown'),
])
def test_never_infers_behavior_from_hits_gold_or_ambiguous_field_names(row):
    with pytest.raises(ValueError, match='explicit predicted_action required'):
        evaluate_behavior(questions('answer'), [row])


def test_scores_do_not_affect_explicit_actions():
    supplied = [dict(query_id='q0', predicted_action='abstain', score=999,
                     hits=[dict(score=1e9)], expected_action='answer')]
    summary, _ = evaluate_behavior(questions('answer'), supplied)
    assert summary['accuracy_on_measured'] == 0
    assert summary['error_rates_on_measured']['unnecessary_nonanswer']['rate'] == 1


@pytest.mark.parametrize(('gold', 'supplied', 'error'), [
    ([], [], 'questions must not be empty'),
    (questions('answer') * 2, [], 'duplicate question ID'),
    (questions('answer'), predictions('answer') * 2, 'duplicate prediction ID'),
    (questions('answer'), [dict(query_id='other', predicted_action='answer')], 'unknown prediction query ID'),
    ([dict(question_id='q0', expected_action='refuse')], [], 'expected_action'),
    ([dict(question_id='q0', expected_answerability='answerable')], [], 'expected_action'),
    ([dict(question_id='q0', expected_action='clarify', expected_answerability='answerable')], [], 'mismatch'),
    ([dict(question_id='q0', expected_action='answer', expected_answerability='unanswerable')], [], 'mismatch'),
    ([dict(question_id='', expected_action='answer')], [], 'nonempty question_id'),
    (questions('answer'), [dict(query_id=None, predicted_action='answer')], 'nonempty query_id'),
    ([None], [], 'JSON object'),
])
def test_invalid_or_ambiguous_inputs_fail_closed(gold, supplied, error):
    with pytest.raises(ValueError, match=error):
        evaluate_behavior(gold, supplied)


def test_new_benchmark_paths_provenance_review_status_and_no_overwrite(tmp_path):
    path, pred = tmp_path/'expanded.jsonl', tmp_path/'decisions.jsonl'
    gold = questions('answer', 'clarify', 'abstain', 'clarify')
    gold[-1].update(review_status='pending_human_review', eligible_for_official_metrics=False)
    write_jsonl(path, gold)
    write_jsonl(pred, predictions('answer', 'clarify', 'abstain', 'clarify'))
    output = tmp_path/'behavior'
    summary = run(path, output, predictions_path=pred, benchmark_version='expanded-fixture-v2',
                  prediction_source='test-only-explicit-labels', require_complete=True)
    assert summary['question_count'] == 4
    assert summary['benchmark_version'] == 'expanded-fixture-v2'
    assert summary['explicitly_ineligible_question_count'] == 1
    assert summary['review_status_counts']['pending_human_review'] == 1
    manifest = json.loads((output/'manifest.json').read_text())
    assert manifest['evaluation_status'] == 'complete'
    assert manifest['api_calls'] == 0
    assert manifest['prediction_source'] == 'test-only-explicit-labels'
    assert manifest['input_sha256']['questions'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert manifest['input_sha256']['predictions'] == hashlib.sha256(pred.read_bytes()).hexdigest()
    for name, checksum in manifest['output_sha256'].items():
        assert hashlib.sha256((output/name).read_bytes()).hexdigest() == checksum
    with pytest.raises(ValueError, match='output exists'):
        run(path, output)


def test_require_complete_rejects_missing_before_writing_outputs(tmp_path):
    path, output = tmp_path/'questions.jsonl', tmp_path/'strict'
    write_jsonl(path, questions('answer', 'clarify'))
    with pytest.raises(ValueError, match='2 missing'):
        run(path, output, require_complete=True)
    assert not output.exists()


def test_cli_without_predictions_publishes_unmeasured_audit(tmp_path):
    path, output = tmp_path/'questions.jsonl', tmp_path/'audit'
    write_jsonl(path, questions('answer', 'clarify', 'abstain'))
    completed = subprocess.run(
        [sys.executable, str(ROOT/'scripts/evaluate_behavior.py'), '--questions', str(path),
         '--output-dir', str(output), '--benchmark-version', 'new-unscored-v1'],
        check=True, capture_output=True, text=True)
    printed = json.loads(completed.stdout)
    assert printed['status'] == 'unmeasured'
    assert printed['question_count'] == 3
    assert printed['accuracy_on_measured'] is None
    manifest = json.loads((output/'manifest.json').read_text())
    assert manifest['inputs']['predictions'] is None
    assert manifest['input_sha256']['predictions'] is None
    assert 'Missing predictions' in (output/'REPORT.md').read_text()
