"""Does Qwen pick a Graph target that reaches the evidence the first search missed?

Replays the first model input of the archived C runs for the 28 answer questions whose final
evidence lacked a required item, then scores each chosen Graph request on the stored Graph.
Conditions (same question, evidence and candidate list unless stated):
  natural     allowed actions as archived (search/graph/respond): how often Graph is chosen
  graph_only  only graph is allowed: can Qwen pick a useful target at all
  top8        graph_only with the candidate list cut to its first 8 entries
              (the archived order: question-named targets, then unresolved-item related, then the rest)
Scoring uses the benchmark's required-evidence ranges only after the choice; they are never shown.
The Graph tool is executed offline in the archived (outgoing) direction and, for the reverse-reference
plan, in both directions with the same Qwen-chosen arguments.
"""

import argparse
import csv
import json
import os
from pathlib import Path
from time import perf_counter

from _bootstrap import ROOT
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.evaluation import covers
from civic_link.agent.experiment import atomic_json, read_json, result_path
from civic_link.agent.protocol import InvalidDecision, parse_decision
from run_week9_probes import ARCHIVE, BENCHMARK, DB, WORK, load_questions

OUT = WORK / 'feedback' / 'graph-choice'
VARIANTS = ('natural', 'graph_only', 'top8')


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def load_cases():
    questions = {q['question_id']: q for q in
                 [*load_questions(BENCHMARK / 'questions.jsonl'), *load_questions(BENCHMARK / 'week8/new_questions.jsonl')]}
    rows = {r['query_id']: r for r in read_csv(WORK / 'trace-608/questions.csv')
            if r['condition'] == 'iterative_graph' and r['expected_action'] == 'answer'}
    cases = []
    for qid, row in sorted(rows.items()):
        if row['final_complete'] == 'True':
            continue
        saved = read_json(result_path(ARCHIVE, 'iterative_graph', qid))
        first = next(e for e in saved['result']['events'] if e['stage'] == 'qwen_input')['messages']
        missing = json.loads(row['first_returned_missing_ids'])
        requirements = [r for r in questions[qid]['evidence_requirements'] if r['evidence_id'] in missing]
        cases.append(dict(query_id=qid, system=json.loads(first[0]['content']), state=json.loads(first[1]['content']),
                          requirements=requirements, final_missing=json.loads(row['final_missing_ids'])))
    return cases


def build_request(case, variant):
    system, state = json.loads(json.dumps(case['system'])), json.loads(json.dumps(case['state']))
    if variant != 'natural':
        state['실행 규칙']['allowed_actions'] = ['graph']
        output = system['출력 양식']
        output['fields']['action']['allowed_values'] = ['graph']
        output['fields']['arguments'] = {'graph': output['fields']['arguments']['graph']}
        output['example'] = {'checks': [{'item': '확인할 정보', 'status': '미확인', 'evidence_ids': []}],
                             'action': 'graph', 'arguments': {'mode': 'direct', 'target_type': 'node',
                                                              'target_id': '실제 노드 ID'},
                             'reason': '선택 목적'}
    if variant == 'top8':
        state['현재 실행 상태']['graph_candidates'] = state['현재 실행 상태']['graph_candidates'][:8]
    return [{'role': 'system', 'content': json.dumps(system, ensure_ascii=False, indent=2)},
            {'role': 'user', 'content': json.dumps(state, ensure_ascii=False, indent=2)}]


def call(client, directory, case, variant):
    path = result_path(directory, variant, case['query_id'])
    if path.is_file():
        return read_json(path)
    request = build_request(case, variant)
    print(case['query_id'], variant, flush=True)
    started = perf_counter()
    raw = client.complete(request)
    seconds = perf_counter() - started
    known = {e['id'] for e in case['state']['현재 실행 상태']['evidence']}
    try:
        decision = parse_decision(raw, known)
        status, error = 'parsed', None
    except InvalidDecision as exc:
        decision, status, error = None, 'invalid_output', str(exc)
    row = dict(query_id=case['query_id'], variant=variant, status=status, error=error, decision=decision,
               raw=raw, seconds=seconds, usage=client.last_usage,
               candidates_shown=len(json.loads(request[1]['content'])['현재 실행 상태']['graph_candidates']))
    atomic_json(path, row)
    return row


def score(row, case, tools):
    action = (row['decision'] or {}).get('action')
    out = dict(query_id=row['query_id'], variant=row['variant'], status=row['status'], action=action,
               candidates_shown=row['candidates_shown'], seconds=row['seconds'])
    if action != 'graph':
        return out
    arguments = row['decision']['arguments']
    out['arguments'] = arguments
    candidate_ids = {c['id'] for c in case['state']['현재 실행 상태']['graph_candidates']}
    out['target_in_shown_candidates'] = arguments.get('target_id') in candidate_ids
    for name, tool in tools.items():
        result = tool.run(arguments)
        rows = result.get('evidence', [])
        out[f'tool_status_{name}'] = result.get('status')
        out[f'new_cover_{name}'] = [r['evidence_id'] for r in case['requirements'] if rows and covers(rows, r)]
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--evaluate-only', action='store_true')
    parser.add_argument('--variants', nargs='+', choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    cases = load_cases()
    if args.dry_run:
        for case in cases:
            print(case['query_id'], 'missing_at_turn1', [r['evidence_id'] for r in case['requirements']],
                  'candidates', len(case['state']['현재 실행 상태']['graph_candidates']),
                  'chars', len(build_request(case, 'natural')[1]['content']))
        print(len(cases), 'cases;', len(cases) * len(args.variants), 'calls')
        return
    settings = read_json(ARCHIVE / 'config.json')['settings']
    if not args.evaluate_only:
        endpoint = os.environ.get('QWEN_ENDPOINT')
        if not endpoint:
            parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
        client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                            timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                            json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
        try:
            for index, case in enumerate(cases):
                order = list(args.variants)
                if index % 2:
                    order.reverse()
                for variant in order:
                    call(client, args.output_dir, case, variant)
        except ModelError as exc:
            raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    from civic_link.agent.graph import GraphTool
    tools = {'outgoing': GraphTool.from_files(db=DB, direction='outgoing'),
             'both': GraphTool.from_files(db=DB, direction='both')}
    scored = []
    for case in cases:
        for variant in args.variants:
            path = result_path(args.output_dir, variant, case['query_id'])
            if path.is_file():
                scored.append(score(read_json(path), case, tools))
    atomic_json(args.output_dir / 'scored.json', scored)
    for variant in args.variants:
        rows = [r for r in scored if r['variant'] == variant]
        graph = [r for r in rows if r['action'] == 'graph']
        print(variant, f'n={len(rows)}', 'invalid', sum(r['status'] != 'parsed' for r in rows),
              'actions', {a: sum(r['action'] == a for r in rows) for a in ('search', 'graph', 'respond')},
              'graph_in_shown', sum(bool(r.get('target_in_shown_candidates')) for r in graph),
              'new_cover_outgoing', sum(bool(r.get('new_cover_outgoing')) for r in graph),
              'new_cover_both', sum(bool(r.get('new_cover_both')) for r in graph))
    print(f'Results: {args.output_dir}')


if __name__ == '__main__':
    main()
