"""Compare clarification behavior with fixed final inputs from the Week 8 sample."""

import argparse
import json
import os
from datetime import datetime

from _bootstrap import ROOT
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.experiment import read_json, result_path
from civic_link.agent.prompts import GUIDELINES
from civic_link.agent.protocol import InvalidDecision, parse_decision


CASES = {
    'SJ-X-050': '서류 종류와 신청 채널을 확인한다. 위임 여부는 필요할 때 후속으로 묻는다.',
    'SJ-A-086': '학교급·졸업연도, 이용 화면·발급 경로, 오류 문구 중 원인을 좁힐 정보를 요청한다.',
    'SJ-A-087': '기존 대장이 조회되는지, 대장이 없거나 변경이 필요하다는 안내가 나오는지 묻는다.',
    'W7-BH021': '민원명과 함께 화면 상태, 표시된 시각이 저장 시각인지 접수 시각인지 확인한다.',
    'W7V2-BH002': '지금 보고 있는 화면이 인터넷 신청인지 무인발급기인지 묻는다.',
    'W7V2-BH005': '대상 필지의 관할 시·군·구 또는 관할을 확인할 소재지 정보를 묻는다.',
    'W7-MS-033': '대조군. 증명서 3시간과 급여 결정 통지의 원칙 30일·예외 60일을 구분한다.',
    'W7V2-MS042': '대조군. 국세증명 입력값과 등기증명 조회 제한(3개월 이내 5회)을 구분한다.',
}
TRIAL_RULE = (
    '사용자가 요청한 적용 결과를 정하려면 서류 종류, 사용 중인 화면, 접수 상태처럼 '
    '사용자에게 확인해야 하는 사실이 필요한지 먼저 판단하세요. 빠진 사실이 결과를 바꾸면 '
    'respond에서 그 사실을 알려달라는 확인 질문을 먼저 쓰세요. 사용자가 이미 모른다고 한 '
    '분류는 화면 표시처럼 확인할 수 있는 단서로 바꿔 물으세요. 조건별 일반 안내는 확인 질문 '
    '뒤에 짧게 덧붙일 수 있습니다. 필요한 사실이 주어졌으면 다시 묻지 말고 답하세요.'
)
ANALYSIS_DIR = ROOT / 'outputs/week8-608-analysis'


def load_cases(run_dir):
    for qid, review in CASES.items():
        row = read_json(result_path(run_dir, 'iterative_search', qid))
        inputs = [event['messages'] for event in row['result']['events'] if event['stage'] == 'qwen_input']
        if row['query_id'] != qid or row['result']['status'] != 'responded' or not inputs:
            raise ValueError(f'{qid}: expected a saved B response with model inputs')
        payloads = [json.loads(messages[1]['content']) for messages in inputs]
        final = payloads[-1]
        final['실행 규칙'].update(allowed_actions=['respond'], remaining_model_calls=1, remaining_tool_calls=0)
        system = json.loads(inputs[-1][0]['content'])
        output = system['출력 양식']
        output['fields']['action']['allowed_values'] = ['respond']
        output['fields']['arguments'] = {'respond': output['fields']['arguments']['respond']}
        output['example'] = {
            'checks': [], 'action': 'respond',
            'arguments': {'text': '확인된 내용 또는 필요한 확인 질문', 'evidence_ids': []},
            'reason': '제공된 근거로 응답',
        }
        known_ids = {
            item['id'] for payload in payloads for item in payload['현재 실행 상태']['evidence']
        }
        supplied_ids = set(final['현재 실행 상태']['context']['supplied_evidence_ids'])
        yield qid, row['question'], review, system, final, known_ids, supplied_ids


def compare(client, cases, report, model):
    invalid = 0
    with report.open('x', encoding='utf-8') as stream:
        stream.write(
            f'# 확인 질문 지침 비교\n\n모델: `{model}` · 기존 B 조건의 최종 입력 재사용\n\n'
            '두 조건에서 새 도구 호출을 막고, 현재 지침에 아래 규칙을 추가한 효과만 비교했다. '
            '검수 기준은 보고서에만 기록했다.\n\n'
            f'> {TRIAL_RULE}\n\n'
            '확인 질문형은 필요한 사실을 실제로 요청하고 미확인 적용 결과를 단정하지 않았는지 확인한다. '
            '대조군은 불필요하게 되묻지 않고 필수 답변이 유지됐는지 확인한다.\n'
        )
        for qid, question, review, system, final, known_ids, supplied_ids in cases:
            print(f'Comparing {qid}...', flush=True)
            stream.write(f'\n## {qid}\n\n{question}\n\n**검수 기준** · {review}\n')
            stream.flush()
            for label, guidelines in [('현재 지침', GUIDELINES), ('확인 질문 지침 추가', [*GUIDELINES, TRIAL_RULE])]:
                system['판단 지침'] = guidelines
                messages = [
                    {'role': 'system', 'content': json.dumps(system, ensure_ascii=False, indent=2)},
                    {'role': 'user', 'content': json.dumps(final, ensure_ascii=False, indent=2)},
                ]
                try:
                    raw = client.complete(messages)
                except ModelError as exc:
                    stream.write(f'\n### {label}\n\n호출 실패 · {exc}\n')
                    raise
                try:
                    decision = parse_decision(raw, known_ids)
                    if decision['action'] != 'respond':
                        raise InvalidDecision('only respond is allowed')
                    if not set(decision['arguments']['evidence_ids']) <= supplied_ids:
                        raise InvalidDecision('respond evidence_ids must be supplied in the current input')
                    body = decision['arguments']['text']
                except InvalidDecision as exc:
                    body = f'출력 형식 오류 · {exc}\n\n```text\n{raw}\n```'
                    invalid += 1
                stream.write(f'\n### {label}\n\n{body}\n')
                stream.flush()
        stream.write(f'\n출력 형식 오류: {invalid}/{len(cases) * 2}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='validate the eight saved cases without an API call')
    args = parser.parse_args()
    run_dir = ANALYSIS_DIR / 'content-sample60-run'
    settings = read_json(run_dir / 'config.json')['settings']
    cases = list(load_cases(run_dir))
    if args.dry_run:
        print(f'Loaded {len(cases)} cases for {settings["model"]}:')
        print(', '.join(case[0] for case in cases))
        return

    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the PowerShell window that can reach the generation server')
    client = QwenClient(
        endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'],
    )
    report = ANALYSIS_DIR / f'clarification-prompt-{datetime.now():%Y%m%d-%H%M%S-%f}.md'
    print(f'Report: {report}', flush=True)
    try:
        compare(client, cases, report, settings['model'])
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}') from exc
    print(f'Finished {len(cases)} comparisons.', flush=True)


if __name__ == '__main__':
    main()
