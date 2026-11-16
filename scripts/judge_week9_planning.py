"""Blind Qwen rubric judge for the planning-staged answers (variant label is not shown to the judge).

Each answer is judged per expected subtask: right action (answer/clarify/abstain), required
points met, must-not claims avoided. A case passes only if every subtask passes.
Judge output is a draft for review, not ground truth.
"""

import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from _bootstrap import ROOT  # noqa: F401
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.experiment import atomic_json, read_json, result_path
from run_week9_probes import ARCHIVE, WORK, planning_cases

JUDGE = (
    '당신은 민원 안내 답변의 채점자입니다. 질문, 기대 하위과제 목록, 사용자에게 전달된 답변이 주어집니다. '
    '각 하위과제(subtask_id)마다 판정하세요. action_ok: 답변이 그 하위과제에서 기대 행동(expected_action 또는 acceptable_actions: '
    'answer=답변, clarify=필요한 정보를 되묻기, abstain=보류·한계 고지)을 했는가. '
    'required_met: required 항목을 모두 답변이 포함하는가. must_not_hit: must_not에 해당하는 주장이 있는가(있으면 true). '
    '요청받지 않은 부가 설명은 must_not에 해당하지 않는 한 감점하지 마세요. 복합 질문의 A와 B는 서로 다른 하위과제입니다. '
    '{"subtasks":[{"subtask_id":"A","action_ok":true,"required_met":true,"must_not_hit":false,"note":"짧은 사유"}]} '
    '형식의 JSON만 반환하세요.'
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=WORK / 'feedback' / 'planning-staged-38')
    parser.add_argument('--output-dir', type=Path, default=WORK / 'feedback' / 'planning-judge-38')
    parser.add_argument('--per-action', type=int, default=6)
    parser.add_argument('--extra-originals', type=int, default=14)
    args = parser.parse_args()
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    settings = read_json(ARCHIVE / 'config.json')['settings']
    client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = planning_cases(args.per_action, args.extra_originals)
    summary = []
    try:
        for case in cases:
            row = dict(query_id=case['query_id'], group=case['group'])
            for variant in ('without_plan', 'with_plan'):
                answer = read_json(result_path(args.input_dir, variant, case['query_id'])).get('content')
                path = result_path(args.output_dir, variant, case['query_id'])
                if path.is_file():
                    judged = read_json(path)
                else:
                    if not answer:
                        judged = dict(status='no_answer', passed=False)
                    else:
                        wanted = [{k: s.get(k) for k in ('subtask_id', 'expected_action', 'acceptable_actions',
                                                          'required', 'must_not')} for s in case['expected']]
                        request = [{'role': 'system', 'content': JUDGE},
                                   {'role': 'user', 'content': json.dumps(
                                       {'question': case['question'], 'subtasks': wanted, 'answer': answer},
                                       ensure_ascii=False)}]
                        print(case['query_id'], variant, flush=True)
                        started = perf_counter()
                        raw = client.complete(request)
                        try:
                            parts = json.loads(raw)['subtasks']
                            ids = {p['subtask_id'] for p in parts}
                            ok = ids == {s['subtask_id'] for s in case['expected']}
                            passed = ok and all(p['action_ok'] and p['required_met'] and not p['must_not_hit']
                                                for p in parts)
                            status = 'ok' if ok else 'invalid_output'
                        except (ValueError, KeyError, TypeError):
                            parts, passed, status = None, False, 'invalid_output'
                        judged = dict(status=status, passed=passed, subtasks=parts, raw=raw,
                                      seconds=perf_counter() - started)
                    atomic_json(path, judged)
                row[variant] = judged['passed'] if judged['status'] == 'ok' else None
            summary.append(row)
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    atomic_json(args.output_dir / 'summary.json', summary)
    for group in ('original', 'composite'):
        rows = [r for r in summary if r['group'] == group]
        print(group, len(rows), 'without_plan', sum(bool(r['without_plan']) for r in rows),
              'with_plan', sum(bool(r['with_plan']) for r in rows),
              'invalid', sum(r['without_plan'] is None or r['with_plan'] is None for r in rows))
    print(f'Results: {args.output_dir}')


if __name__ == '__main__':
    main()
