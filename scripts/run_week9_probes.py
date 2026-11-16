"""Replay fixed evidence for the Week 9 planning and reranking answer experiments."""

import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from _bootstrap import ROOT
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.evaluation import subtasks
from civic_link.agent.experiment import atomic_json, identity, read_json, result_path
from civic_link.agent.prompts import GUIDELINES, corpus_scope, messages
from civic_link.agent.protocol import InvalidDecision, parse_decision


WORK = ROOT / 'outputs/week8-608-analysis'
ARCHIVE = WORK / 'week8-gpu-608-w16'
BENCHMARK = ROOT / 'data/benchmark'
DB = ROOT / 'data/corpus/civic.sqlite'
ORIGINALS = ('SJ-N-027', 'SJ-A-028', 'W7-MS-033', 'W7V2-MS042', 'W7V2-BH002', 'W7-BH029')
PLAN = {
    'without_explicit_plan': '질문에 대해 바로 응답을 작성하고 checks에는 빈 목록을 반환하세요.',
    'with_explicit_plan': (
        '답변을 작성하기 전에 요청별로 확인할 항목과 필요한 조건을 나누고 checks에 기록하세요. '
        '복합 문의는 A/B의 상태, 조건, 근거를 각각 연결하고, 이어서 각 문의에 응답하세요.'
    ),
}
PLANNING_CALL = (
    '사용자 질문과 근거를 읽고 답변 전에 처리할 항목을 정하세요. 복합 문의는 A/B별로 필요한 '
    '결론, 사용자가 알려줘야 하는 정보, 근거로 확인된 조건을 구분하세요. 서로의 조건을 섞지 마세요. '
    '답변 대신 {"checks":["짧은 확인 항목", "..."]} 형식의 JSON만 반환하세요.'
)
ANSWER_CALL = (
    '제공된 본문에 근거하여 사용자의 질문에 직접 답하세요. 복합 문의는 요청된 A/B별로 '
    '답변하고, 사용자 정보가 부족한 부분은 필요한 정보를 질문하며, 시스템이 실행할 수 없는 '
    '작업은 한계를 밝히세요. 각 사람의 조건을 유지하고 근거가 없는 주장을 추가하지 마세요. '
    '사실 주장에는 제공된 실제 근거 ID를 함께 표시하세요. '
    '계획 항목이 제공되면 참고하여 답변을 쓰세요. {"answer":"사용자에게 전달할 답변"} 형식의 JSON만 반환하세요.'
)


def load_questions(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def initial_evidence(qid):
    row = read_json(result_path(ARCHIVE, 'initial', qid))
    if row['query_id'] != qid or row['result']['status'] != 'ok':
        raise ValueError(f'{qid}: archived first search did not complete successfully')
    return [{key: item[key] for key in ('id', 'document_id', 'title', 'text', 'source')}
            for item in row['result']['evidence']]


def planning_cases(per_action=2, extra_originals=0):
    """Defaults reproduce the first 12 cases; larger values append new, hash-ordered cases."""
    original = {row['question_id']: row for row in load_questions(BENCHMARK / 'questions.jsonl')}
    composite = load_questions(BENCHMARK / 'week8/new_questions.jsonl')
    selected = []
    for action in ('answer', 'clarify', 'abstain'):
        available = sorted((q for q in composite if q['expected_action'] == action),
                           key=lambda q: identity(['week9-planning-v1', q['question_id']]))
        chosen = [available[0]]
        for candidate in available[1:]:
            if len(chosen) >= per_action:
                break
            if all(candidate['composition_service_id'] != c['composition_service_id'] for c in chosen):
                chosen.append(candidate)
        selected.extend(chosen)
    extra = sorted((q for q in original.values() if q['expected_action'] == 'answer'
                    and q['question_id'] not in ORIGINALS),
                   key=lambda q: identity(['week9-planning-originals-v1', q['question_id']]))[:extra_originals]
    cases = []
    for question in [*(original[qid] for qid in ORIGINALS), *extra, *selected]:
        qid = question['question_id']
        parents = question.get('parent_question_ids', [qid])
        evidence = {item['id']: item for parent in parents for item in initial_evidence(parent)}
        cases.append(dict(query_id=qid, question=question['question'], evidence=list(evidence.values()),
                          group='composite' if len(parents) > 1 else 'original',
                          expected=subtasks(question), parents=parents))
    return cases


def request_messages(question, evidence, scope, planning_rule):
    ids = [row['id'] for row in evidence]
    payload = {
        '원래 질문': question,
        '현재 실행 상태': {
            'phase': 'followup', 'evidence': evidence, 'checks': [], 'history': [],
            'graph_candidates': [], 'document_candidates': [], 'graph_pages': [],
            'context': {'supplied_evidence_ids': ids, 'omitted_evidence_ids': [],
                        'stored_evidence_count': len(ids)},
        },
        '실행 규칙': {'allowed_actions': ['respond'], 'remaining_model_calls': 1, 'remaining_tool_calls': 0},
    }
    request = messages(payload, scope, {})
    if planning_rule:
        system = json.loads(request[0]['content'])
        system['판단 지침'] = [planning_rule, *GUIDELINES[1:]]
        request[0]['content'] = json.dumps(system, ensure_ascii=False, indent=2)
    return request


def run(cases, suite, client, directory, scope, protocol):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'manifest.json'
    if manifest.exists():
        if read_json(manifest) != protocol:
            raise ValueError(f'use a new output folder: configuration changed in {directory}')
    else:
        atomic_json(manifest, protocol)
    for index, case in enumerate(cases):
        if suite == 'planning-staged':
            staged_case(client, directory, case, plan_first=bool(index % 2))
            continue
        if suite == 'planning':
            variants = [(label, case['evidence'], rule) for label, rule in PLAN.items()]
        else:
            variants = [(label, evidence, None) for label, evidence in case['variants'].items()]
        if index % 2:
            variants.reverse()
        for label, evidence, rule in variants:
            path = result_path(directory, label, case['query_id'])
            if path.is_file():
                continue
            print(case['query_id'], label, flush=True)
            request = request_messages(case['question'], evidence, scope, rule)
            started = perf_counter()
            raw = client.complete(request)
            elapsed = perf_counter() - started
            try:
                response = parse_decision(raw, {item['id'] for item in evidence})
                if response['action'] != 'respond':
                    raise InvalidDecision('only respond is allowed')
                status, error = 'responded', None
            except InvalidDecision as exc:
                response, status, error = None, 'invalid_output', str(exc)
            atomic_json(path, dict(query_id=case['query_id'], variant=label, group=case['group'],
                                   response=response, raw=raw, status=status, error=error,
                                   seconds=elapsed, usage=client.last_usage, request=request))


def staged_request(client, case, directory, label, instructions, field, plan=None):
    path = result_path(directory, label, case['query_id'])
    if path.is_file():
        return read_json(path)
    payload = {key: case[key] for key in ('question', 'evidence')}
    if plan is not None:
        payload['plan'] = plan
    request = [{'role': 'system', 'content': instructions},
               {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    print(case['query_id'], label, flush=True)
    started = perf_counter()
    raw = client.complete(request)
    seconds = perf_counter() - started
    try:
        value = json.loads(raw)
        content = value[field] if isinstance(value, dict) and set(value) == {field} else None
    except ValueError:
        content = None
    if field == 'checks':
        valid = (isinstance(content, list) and bool(content)
                 and all(isinstance(item, str) and item.strip() for item in content))
    else:
        valid = isinstance(content, str) and bool(content.strip())
    result = dict(query_id=case['query_id'], variant=label, group=case['group'],
                  status='responded' if valid else 'invalid_output', content=content,
                  raw=raw, seconds=seconds, usage=client.last_usage, request=request)
    atomic_json(path, result)
    return result


def staged_case(client, directory, case, *, plan_first):
    labels = ('with_plan', 'without_plan') if plan_first else ('without_plan', 'with_plan')
    for label in labels:
        if result_path(directory, label, case['query_id']).is_file():
            continue
        plan = None
        if label == 'with_plan':
            stage = staged_request(client, case, directory, 'plans', PLANNING_CALL, 'checks')
            if stage['status'] != 'responded':
                atomic_json(result_path(directory, label, case['query_id']),
                            dict(query_id=case['query_id'], variant=label, group=case['group'],
                                 status='planning_failed', content=None))
                continue
            plan = stage['content']
        staged_request(client, case, directory, label, ANSWER_CALL, 'answer', plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', choices=('planning', 'planning-staged', 'reranking'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--per-action', type=int, default=2, help='composite questions per expected action')
    parser.add_argument('--extra-originals', type=int, default=0, help='additional original answer questions')
    args = parser.parse_args()
    cases = (read_json(WORK / 'feedback' / 'answer-cases.json') if args.suite == 'reranking'
             else planning_cases(args.per_action, args.extra_originals))
    settings = read_json(ARCHIVE / 'config.json')['settings']
    scope = corpus_scope(DB)
    protocol = dict(suite=args.suite, question_ids=[case['query_id'] for case in cases],
                    cases=identity(cases), scope=identity(scope), guidelines=identity(GUIDELINES),
                    plan=PLAN if args.suite == 'planning' else {},
                    generation={key: settings[key] for key in ('model', 'max_output_tokens', 'json_mode',
                                                               'enable_thinking', 'temperature', 'timeout')})
    if args.suite == 'planning-staged':
        protocol['instructions'] = {'plan': PLANNING_CALL, 'answer': ANSWER_CALL}
    if args.dry_run:
        print(json.dumps(protocol, ensure_ascii=False, indent=2))
        return
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    directory = args.output_dir or WORK / 'feedback' / args.suite
    client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    try:
        run(cases, args.suite, client, directory, scope, protocol)
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    print(f'Results: {directory}', flush=True)


if __name__ == '__main__':
    main()
