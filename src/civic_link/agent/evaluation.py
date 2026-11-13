"""Separate evidence exposure, response execution, and reviewed answer quality."""
from __future__ import annotations
from collections import defaultdict
import json
from statistics import mean, median

from .experiment import identity, read_json, result_path


def covers(rows, requirement):
    """Credit a full accepted chunk or a completely exposed gold source range.

    Overlapping chunk IDs on a partial graph page are navigation metadata only.
    Multiple pages can jointly cover a required quote without any page doing so.
    """
    accepted = set(requirement.get('acceptable_chunk_ids', []))
    if any(row['id'] in accepted for row in rows):
        return True
    start, end = requirement.get('start_char'), requirement.get('end_char')
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    spans = sorted((r['start_char'], r['end_char']) for r in rows
                   if r.get('document_id') == requirement.get('document_id')
                   and isinstance(r.get('start_char'), int) and isinstance(r.get('end_char'), int))
    cursor = start
    for a,b in spans:
        if a > cursor:
            break
        if b > cursor:
            cursor = b
        if cursor >= end:
            return True
    return False


def exposures(result):
    inputs = [json.loads(e['messages'][1]['content'])['현재 실행 상태']['evidence']
              for e in result['events'] if e['stage'] == 'qwen_input']
    supplied = {row['id']: row for page in inputs for row in page}
    return list(supplied.values()), inputs[-1] if inputs else []


def subtasks(question):
    return question.get('agentic', {}).get('subtasks') or [dict(
        subtask_id='main', expected_action=question['expected_action'],
        expected_response=question.get('gold_answer'), required=question.get('gold_rubric', {}).get('must', []),
        must_not=question.get('gold_rubric', {}).get('must_not', []),
        action_rubric=question.get('action_rubric'), clarification=question.get('clarification'),
        evidence_ids=[r['evidence_id'] for r in question.get('evidence_requirements', [])])]


def validate_judgment(value, question):
    if not isinstance(value, dict) or set(value) != {'subtasks'} or not isinstance(value['subtasks'], list):
        raise ValueError('judge must return a subtasks list')
    expected = {s['subtask_id'] for s in subtasks(question)}
    seen = set()
    fields = {'subtask_id','observed_action','required_handling_ok','factual_correct',
              'grounded_in_provided_passages','citations_supported','unsupported_claim','reason'}
    for row in value['subtasks']:
        if not isinstance(row, dict) or set(row) != fields or row['subtask_id'] not in expected or row['subtask_id'] in seen:
            raise ValueError('invalid or duplicate judge subtask')
        seen.add(row['subtask_id'])
        if row['observed_action'] not in ('answer','clarify','abstain'):
            raise ValueError('invalid observed action')
        if any(type(row[k]) is not bool for k in ('required_handling_ok','unsupported_claim')):
            raise ValueError('judge boolean required')
        if any(row[k] is not None and type(row[k]) is not bool for k in ('factual_correct','grounded_in_provided_passages','citations_supported')):
            raise ValueError('judge nullable boolean required')
        if not isinstance(row['reason'],str) or not row['reason'].strip():
            raise ValueError('judge reason required')
        if row['observed_action'] == 'answer' and any(row[k] is None for k in ('factual_correct','grounded_in_provided_passages','citations_supported')):
            raise ValueError('an answer requires factual and grounding judgments')
    if seen != expected:
        raise ValueError('judge omitted a subtask')
    return value


def content_pass(judgment):
    return all(s['required_handling_ok'] and not s['unsupported_claim'] and
               s['factual_correct'] is not False and s['grounded_in_provided_passages'] is not False
               and s['citations_supported'] is not False
               for s in judgment['subtasks'])


def evaluate_row(row, question, judgment=None):
    result = row['result']
    if (row['question'] != question['question'] or row['result_sha256'] != identity(result)
            or row['response_sha256'] != identity(result['text'])):
        raise ValueError('question or result changed since generation')
    supplied, final = exposures(result)
    required = question.get('evidence_requirements', [])
    stages = {'retrieved': result['evidence'], 'supplied': supplied, 'final': final,
              'cited': [r for r in result['evidence'] if r['id'] in {s['id'] for s in result['sources']}]}
    coverage = {name:[covers(rows,r) for r in required] for name,rows in stages.items()}
    measures = dict(query_id=row['query_id'], condition=row['condition'], question_type=question.get('question_type'),
        expected_action=question['expected_action'], status=result['status'], elapsed_seconds=row['elapsed_seconds'],
        model_calls=result['model_calls'], tool_calls=result['tool_calls'],
        invalid_requests=sum(e['stage']=='request_rejected' for e in result['events']),
        tool_errors=sum(e['stage']=='tool_result' and e['result']['status']=='error' for e in result['events']),
        max_input_chars=max((e.get('input_chars',0) for e in result['events'] if e['stage']=='qwen_input'), default=0),
        evidence_required=len(required), evidence_coverage=coverage, content_pass=None, subtasks=[])
    outputs = [e for e in result['events'] if e['stage']=='qwen_output']
    usages = [e.get('usage') for e in outputs]
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        measures[key] = sum(u[key] for u in usages) if usages and all(isinstance(u,dict) and isinstance(u.get(key),int) for u in usages) else None
    for task in subtasks(question):
        indices = [i for i,r in enumerate(required) if r['evidence_id'] in task['evidence_ids']]
        measures['subtasks'].append(dict(subtask_id=task['subtask_id'], expected_action=task['expected_action'],
            evidence_required=len(indices), final_complete=all(coverage['final'][i] for i in indices) if indices else None))
    if judgment is not None:
        if judgment['response_sha256'] != row['response_sha256'] or judgment['result_sha256'] != row['result_sha256']:
            raise ValueError('judgment does not belong to this response and evidence trace')
        valid = validate_judgment(judgment['judgment'], question)
        measures['content_pass'] = content_pass(valid) if result['status']=='responded' else False
        for task, reviewed in zip(measures['subtasks'], sorted(valid['subtasks'],key=lambda x:[s['subtask_id'] for s in measures['subtasks']].index(x['subtask_id']))):
            task['judgment'] = reviewed
    elif result['status'] != 'responded':
        measures['content_pass'] = False
    return measures


def summarize(rows):
    if not rows:
        return {'questions':0}
    summary = dict(questions=len(rows), responded=sum(r['status']=='responded' for r in rows),
        execution_error_rate=mean(r['status']!='responded' for r in rows),
        mean_seconds=mean(r['elapsed_seconds'] for r in rows), median_seconds=median(r['elapsed_seconds'] for r in rows),
        mean_model_calls=mean(r['model_calls'] for r in rows), mean_tool_calls=mean(r['tool_calls'] for r in rows),
        invalid_requests=sum(r['invalid_requests'] for r in rows), tool_errors=sum(r['tool_errors'] for r in rows))
    eligible = [r for r in rows if r['evidence_required']]
    summary['evidence_questions'] = len(eligible)
    for stage in ('retrieved','supplied','final','cited'):
        summary[stage+'_complete_rate'] = mean(all(r['evidence_coverage'][stage]) for r in eligible) if eligible else None
        summary[stage+'_requirement_recall'] = (sum(sum(r['evidence_coverage'][stage]) for r in eligible) /
                                              sum(r['evidence_required'] for r in eligible)) if eligible else None
    reviewed = [r for r in rows if r['content_pass'] is not None]
    summary['content_scored_questions'] = len(reviewed)
    summary['content_pass_rate'] = mean(r['content_pass'] for r in rows) if len(reviewed)==len(rows) else None
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        summary['mean_'+key] = mean(r[key] for r in rows) if all(r[key] is not None for r in rows) else None
    tasks = [task for row in rows for task in row['subtasks']]
    summary['subtask_handling'] = {}
    for action in ('answer','clarify','abstain'):
        expected = [task for task in tasks if task['expected_action']==action]
        judged = [task['judgment'] for task in expected if 'judgment' in task]
        summary['subtask_handling'][action] = dict(planned=len(expected), judged=len(judged),
            required_handling_rate=mean(j['required_handling_ok'] for j in judged) if judged and len(judged)==len(expected) else None)
    summary['content_checks'] = {}
    for key in ('factual_correct','grounded_in_provided_passages','citations_supported','unsupported_claim'):
        values = [t['judgment'][key] for t in tasks if t.get('judgment',{}).get(key) is not None]
        summary['content_checks'][key] = dict(scored_subtasks=len(values), true_rate=mean(values) if values else None)
    return summary


def evaluate_comparison(directory, questions, judgments=None):
    config = read_json(directory/'config.json')
    judgments = judgments or {}
    conditions = config['conditions']
    paired, missing = [], []
    for query in config['queries']:
        qid = query['query_id']
        if qid not in questions or questions[qid]['question'] != query['question']:
            raise ValueError('evaluation questions do not match the frozen query set')
        absent = [c for c in conditions if not result_path(directory,c,qid).is_file()]
        if absent:
            missing.append(dict(query_id=qid, conditions=absent))
        else:
            paired.append(qid)
    rows = []
    for qid in paired:
        first = read_json(result_path(directory,'initial',qid))
        for condition in conditions:
            row = read_json(result_path(directory,condition,qid))
            if row['query_id'] != qid or row['condition'] != condition:
                raise ValueError('response is stored under the wrong condition or query')
            if row['initial_sha256'] != first['sha256'] or first['sha256'] != identity(first['result']):
                raise ValueError('paired conditions did not share the same first search')
            rows.append(evaluate_row(row,questions[qid],judgments.get((condition,qid))))
    summaries = {c:summarize([r for r in rows if r['condition']==c]) for c in conditions}
    by_type = {c:{kind:summarize([r for r in rows if r['condition']==c and r['question_type']==kind])
                 for kind in sorted({r['question_type'] for r in rows})} for c in conditions}
    contrasts = {}
    for a,b in [('single_search','iterative_search'),('iterative_search','iterative_graph'),('single_search','iterative_graph')]:
        if a not in conditions or b not in conditions:
            continue
        contrast = {}
        for metric in ('final_complete','content_pass'):
            pairs = []
            for qid in paired:
                x,y = [next(r for r in rows if r['condition']==c and r['query_id']==qid) for c in (a,b)]
                values = [r['content_pass'] if metric=='content_pass' else
                          all(r['evidence_coverage']['final']) if r['evidence_required'] else None for r in (x,y)]
                if None not in values:
                    pairs.append(values)
            contrast[metric] = dict(pairs=len(pairs), improved=sum(not x and y for x,y in pairs),
                                   regressed=sum(x and not y for x,y in pairs), unchanged=sum(x==y for x,y in pairs))
        contrasts[a+' -> '+b] = contrast
    return dict(scope='paired_development_comparison; evidence coverage is not answer correctness',
                planned_questions=len(config['queries']), paired_questions=len(paired), missing=missing,
                summaries=summaries, by_question_type=by_type, contrasts=contrasts, rows=rows)
