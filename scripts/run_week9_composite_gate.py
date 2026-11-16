"""Clarification gate on the composite (A/B) questions.

40 composite questions have one inquiry that needs a clarifying question and one that can be answered.
The gate (src/civic_link/agent/clarify.py, the same prompt used by the re-questioning flow) sees the first-search
evidence of both parent questions and must ask about the inquiry that needs it. Controls: 40 composite questions
where the other inquiry must be held back (answer/abstain) and 40 where both can be answered
(hash-ordered sample); any question asked there is unnecessary.
Judge (Qwen, sees the gold rubric only): does the question ask for the required slots, avoid the forbidden
behaviour, aim at the right inquiry, and avoid asking for information the answerable inquiry does not need?
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from _bootstrap import ROOT  # noqa: F401
from civic_link.agent.clarify import GATE_PROMPTS, check_gate
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.evaluation import subtasks
from civic_link.agent.experiment import atomic_json, identity, read_json, result_path
from run_week9_probes import ARCHIVE, BENCHMARK, WORK, initial_evidence, load_questions

OUT = WORK / 'feedback' / 'composite-gate'
JUDGE = (
    '당신은 복합 민원 질문에서 시스템이 한 확인 질문의 채점자입니다. 두 사람의 문의 A, B 중 정보가 부족한 문의(clarify)와 '
    '그렇지 않은 문의가 있습니다. 확인 질문, 두 문의의 기대 처리(subtasks)가 주어집니다. '
    '"asked_required": 확인 질문이 clarify 문의의 required가 요구하는 정보를 실질적으로 묻는가(표현 일치·모든 슬롯의 동시 제시는 요구하지 않으며, '
    '그저 "정보를 알려달라"는 질문은 아님). "must_not_hit": 확인 질문이 그 문의의 must_not을 어기는가. '
    '"targets_right_inquiry": 질문이 정보가 부족한 문의에 관한 것인가. '
    '"asks_unneeded": 질문이 답변 가능한 문의에는 필요하지 않은 정보까지 묻는가. '
    '{"asked_required":true,"must_not_hit":false,"targets_right_inquiry":true,"asks_unneeded":false,"note":"짧은 사유"} 형식의 JSON만 반환하세요.'
)


def evidence_for(question):
    merged = {}
    for parent in question['parent_question_ids']:
        for row in initial_evidence(parent):
            merged.setdefault(row['id'], row)
    return list(merged.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--prompt', choices=sorted(GATE_PROMPTS), default='v3', help='gate prompt version')
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    composite = load_questions(BENCHMARK / 'week8/new_questions.jsonl')

    def pick(action, n=40):
        items = [q for q in composite if q['expected_action'] == action]
        return sorted(items, key=lambda q: identity(['week9-composite-gate-v1', q['question_id']]))[:n]
    groups = {'clarify': pick('clarify'), 'abstain_control': pick('abstain'), 'answer_control': pick('answer')}
    if args.dry_run:
        print({k: len(v) for k, v in groups.items()})
        return
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    settings = read_json(ARCHIVE / 'config.json')['settings']
    client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def run(item):
        group, q = item
        qid = q['question_id']
        path = result_path(args.output_dir, group, qid)
        if path.is_file():
            return read_json(path)
        print(qid, group, flush=True)
        started = perf_counter()
        decision, raw = check_gate(client, q['question'], evidence_for(q), GATE_PROMPTS[args.prompt])
        asked = decision is not None and not decision['can_answer_now']
        row = dict(query_id=qid, group=group, gate_valid=decision is not None, asked=asked,
                   question=decision['question'] if asked else '', raw=raw)
        if group == 'clarify' and asked:
            wanted = [{k: s.get(k) for k in ('subtask_id', 'expected_action', 'required', 'must_not')}
                      for s in subtasks(q)]
            request = [{'role': 'system', 'content': JUDGE},
                       {'role': 'user', 'content': json.dumps(
                           dict(original_question=q['question'], clarification_question=row['question'],
                                subtasks=wanted), ensure_ascii=False)}]
            judged_raw = client.complete(request)
            try:
                value = json.loads(judged_raw)
                ok = all(isinstance(value.get(k), bool) for k in
                         ('asked_required', 'must_not_hit', 'targets_right_inquiry', 'asks_unneeded'))
            except ValueError:
                value, ok = None, False
            row.update(judge=value if ok else None, judge_valid=ok,
                       passed=bool(ok and value['asked_required'] and not value['must_not_hit']
                                   and value['targets_right_inquiry']))
        elif group == 'clarify':
            row.update(passed=False)
        row['seconds'] = perf_counter() - started
        atomic_json(path, row)
        return row

    items = [(g, q) for g, qs in groups.items() for q in qs]
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            rows = list(pool.map(run, items))
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    atomic_json(args.output_dir / 'summary.json', rows)
    clarify = [r for r in rows if r['group'] == 'clarify']
    print(f"clarify n={len(clarify)} asked={sum(r['asked'] for r in clarify)} pass={sum(r['passed'] for r in clarify)}"
          f" asks_unneeded={sum(bool((r.get('judge') or {}).get('asks_unneeded')) for r in clarify)}"
          f" gate_invalid={sum(not r['gate_valid'] for r in clarify)}")
    for group in ('abstain_control', 'answer_control'):
        sel = [r for r in rows if r['group'] == group]
        print(f"{group} n={len(sel)} unnecessary_questions={sum(r['asked'] for r in sel)}"
              f" gate_invalid={sum(not r['gate_valid'] for r in sel)}")
    print(f'Results: {args.output_dir}')


if __name__ == '__main__':
    main()
