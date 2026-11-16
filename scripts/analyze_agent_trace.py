"""Export per-call evidence diagnostics and paired content changes from saved runs."""
import _bootstrap
import argparse
import csv
import json
from pathlib import Path

from civic_link.agent.evaluation import covers, evaluate_row
from civic_link.agent.experiment import cached_question, read_json, result_path
from evaluate_agent_comparison import load_evaluation_inputs


def missing_evidence(rows, requirements):
    return [req['evidence_id'] for req in requirements if not covers(rows, req)]


def first_search_fields(first, requirements):
    if first is None:
        return {'first_search_group': 'missing_run'}
    result = first['result']
    returned = result['evidence']
    candidates = result.get('metadata', {}).get('candidate_ids')
    ranks = {eid: rank for rank, eid in enumerate(candidates or [], 1)}
    missing = missing_evidence(returned, requirements)
    matched_ranks = {}
    for req in requirements:
        accepted_ranks = [ranks[eid] for eid in req.get('acceptable_chunk_ids', []) if eid in ranks]
        if accepted_ranks:
            matched_ranks[req['evidence_id']] = min(accepted_ranks)
    if not requirements:
        group = 'no_evidence_requirements'
    elif not missing:
        group = 'returned_complete'
    elif result['status'] == 'error':
        group = 'search_error'
    elif candidates is None:
        group = 'candidates_unavailable'
    elif all(eid in matched_ranks for eid in missing):
        group = 'candidate_only_complete'
    elif any(not req.get('acceptable_chunk_ids') and req['evidence_id'] in missing
             for req in requirements):
        group = 'candidate_match_unknown'
    else:
        group = 'candidate_ids_incomplete'
    return dict(first_search_group=group, first_search_status=result['status'],
                first_search_seconds=first['seconds'],
                first_returned_complete=not missing if requirements else None,
                first_returned_missing_ids=missing, candidate_accepted_chunk_ranks=matched_ranks,
                candidate_count=len(candidates) if candidates is not None else None,
                first_returned_count=len(returned))


def trace_turns(row, requirements):
    turns = []
    stored = {}
    current = None
    for event in row['result']['events']:
        stage = event['stage']
        if stage == 'qwen_input':
            payload = json.loads(event['messages'][1]['content'])
            state = payload['현재 실행 상태']
            rules = payload['실행 규칙']
            context = state['context']
            missing = missing_evidence(state['evidence'], requirements)
            stored_missing = missing_evidence(stored.values(), requirements)
            current = dict(query_id=row['query_id'], condition=row['condition'], turn=len(turns) + 1,
                           allowed_actions=rules['allowed_actions'],
                           remaining_model_calls=rules['remaining_model_calls'],
                           remaining_tool_calls=rules['remaining_tool_calls'],
                           supplied_ids=context['supplied_evidence_ids'],
                           omitted_ids=context['omitted_evidence_ids'],
                           input_missing_requirement_ids=missing,
                           stored_but_not_covered_ids=[eid for eid in missing if eid not in stored_missing],
                           input_checks=state['checks'],
                           graph_candidate_ids=[candidate['id'] for candidate in state['graph_candidates']],
                           input_chars=event['input_chars'])
            turns.append(current)
        elif stage == 'tool_result':
            result = event['result']
            before = missing_evidence(stored.values(), requirements)
            evidence = result['evidence']
            new_ids = [item['id'] for item in evidence if item['id'] not in stored]
            for item in evidence:
                stored.setdefault(item['id'], item)
            after = missing_evidence(stored.values(), requirements)
            if current is not None:
                current.update(tool_status=result['status'], tool_cached=event['cached'],
                               tool_returned_ids=[item['id'] for item in evidence],
                               new_evidence_ids=new_ids, newly_covered_requirement_ids=[
                                   eid for eid in before if eid not in after],
                               tool_candidate_ids=result.get('metadata', {}).get('candidate_ids'),
                               tool_target=result.get('target'), tool_relations=result['relations'],
                               tool_next_cursor=result.get('next_cursor'), tool_error=result.get('error'))
        elif stage == 'decision':
            action = event['action']
            current.update(action=action, arguments=event['arguments'], decision_checks=event['checks'],
                           decision_reason=event['reason'])
            if requirements:
                missing = current['input_missing_requirement_ids']
                if action == 'respond' and missing:
                    current['review_flag'] = (
                        'respond_with_missing_coverage' if set(current['allowed_actions']) - {'respond'}
                        else 'respond_only_with_missing_coverage'
                    )
                elif action != 'respond' and not missing:
                    current['review_flag'] = 'tool_call_with_complete_coverage'
        elif stage == 'qwen_output':
            current.update(model_seconds=event.get('seconds'), usage=event.get('usage'))
        elif stage in ('request_rejected', 'model_error'):
            current.update(action=stage, execution_error=event.get('message', event.get('error')))
    return turns


def question_fields(row, question, judgment, first_fields):
    scores = evaluate_row(row, question, judgment)
    coverage = scores.pop('evidence_coverage')
    tasks = scores.pop('subtasks')
    requirements = question['evidence_requirements'] if scores['evidence_required'] else []
    for stage, flags in coverage.items():
        scores[f'{stage}_complete'] = all(flags) if flags else None
        scores[f'{stage}_missing_ids'] = [req['evidence_id'] for req, covered in zip(requirements, flags)
                                         if not covered]
    reviews = [task['judgment'] for task in tasks if 'judgment' in task]
    scores['judge_failures'] = {
        verdict['subtask_id']: [key for key in ('required_handling_ok', 'factual_correct',
                                              'grounded_in_provided_passages', 'citations_supported')
                                if verdict[key] is False]
        + (['unsupported_claim'] if verdict['unsupported_claim'] else [])
        for verdict in reviews
    }
    scores['judge_reasons'] = {verdict['subtask_id']: verdict['reason'] for verdict in reviews}
    result = row['result']
    return dict(**scores, **first_fields, question=question['question'],
                answer=result['text'], cited_ids=[source['id'] for source in result['sources']])


def analyze(run_dir, questions, judgments):
    config = read_json(run_dir / 'config.json')
    question_rows, turns, changes = [], [], []
    for query in config['queries']:
        qid = query['query_id']
        question = questions.get(qid)
        if question is None or question['question'] != query['question']:
            raise ValueError(f'{qid}: evaluation question does not match the frozen query')
        first, completed = cached_question(run_dir, query, config['conditions'])
        requirements = question.get('evidence_requirements', [])
        first_fields = first_search_fields(first, requirements)
        by_condition = {}
        for condition in config['conditions']:
            if condition not in completed:
                question_rows.append(dict(query_id=qid, condition=condition, status='missing_run',
                                          question=question['question'], **first_fields))
                continue
            row = read_json(result_path(run_dir, condition, qid))
            summary = question_fields(row, question, judgments.get((condition, qid)), first_fields)
            by_condition[condition] = summary
            question_rows.append(summary)
            turns.extend(trace_turns(row, requirements))
        for baseline, variant in (('single_search', 'iterative_search'),
                                  ('iterative_search', 'iterative_graph'),
                                  ('single_search', 'iterative_graph')):
            before, after = by_condition.get(baseline), by_condition.get(variant)
            if before is None or after is None:
                continue
            a, b = before['content_pass'], after['content_pass']
            if a is None or b is None or a == b:
                continue
            fields = ('status', 'content_pass', 'final_missing_ids', 'answer', 'cited_ids',
                      'judge_failures', 'judge_reasons')
            changes.append(dict(query_id=qid, baseline=baseline, variant=variant,
                                change='improved' if b else 'regressed', question=question['question'],
                                first_search_group=first_fields['first_search_group'],
                                **{f'before_{key}': before[key] for key in fields},
                                **{f'after_{key}': after[key] for key in fields}))
    return question_rows, turns, changes


def write_csv(path, rows, key_fields):
    columns = list(dict.fromkeys([*key_fields, *(key for row in rows for key in row)]))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                             else value for key, value in row.items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--questions', type=Path, required=True)
    parser.add_argument('--judgments', type=Path, help='directory produced by judge_agent_comparison.py')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error('use a new analysis output directory')
    try:
        questions, judgments, _ = load_evaluation_inputs(args.run_dir, args.questions, args.judgments)
        question_rows, turns, changes = analyze(args.run_dir, questions, judgments)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output_dir.mkdir(parents=True)
    write_csv(args.output_dir / 'questions.csv', question_rows, ('query_id', 'condition', 'status'))
    write_csv(args.output_dir / 'turns.csv', turns, ('query_id', 'condition', 'turn'))
    write_csv(args.output_dir / 'content_changes.csv', changes, ('query_id', 'baseline', 'variant', 'change'))
    print(f'Wrote {len(question_rows)} question rows, {len(turns)} turns, and {len(changes)} content changes.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
