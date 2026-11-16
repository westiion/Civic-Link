"""Clarification gate + real Qwen resume on fixed Week 8 final inputs.

Stage 1 (gate): a separate Qwen call lists the user facts that change the answer and, if any are
missing, writes one clarification question. Stage 2 (resume): the saved evidence and the
simulated user reply go back to Qwen, which answers with no new tool call.
The simulated replies are written for this experiment, so they are labeled as such in the output.
"""

import argparse
import json
import os
import re
from pathlib import Path
from time import perf_counter

from _bootstrap import ROOT
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.experiment import atomic_json, read_json, result_path
from civic_link.agent.prompts import GUIDELINES
from civic_link.agent.protocol import InvalidDecision, parse_decision
from compare_clarification_prompt import ANALYSIS_DIR, load_cases


from civic_link.agent.clarify import GATE_PROMPT  # noqa: E402

RESUME_RULE = (
    '사용자가 방금 답한 사실을 그대로 적용해 원래 질문에 답하세요. 사실 주장마다 이번 입력의 supplied_evidence_ids에 있는 '
    '[근거 ID]를 문장 옆에 표시하고 arguments.evidence_ids에도 넣으세요. 근거 본문이 뒷받침하지 않는 채널별 규칙·서류·기간은 쓰지 말고, '
    '확인되지 않은 부분은 확인되지 않았다고 밝히세요. 다시 되묻지 마세요.'
)

# ask_terms: each group must be hit by the clarification question. reply: simulated user answer.
CASES = {
    'SJ-X-050': dict(ask=[r'서류|증명서|종류|무슨|어떤', r'인터넷|온라인|무인|방문|창구|채널|어디'],
                     reply='주민등록등본이고, 정부24 인터넷으로 신청하려고요. 부모님 대신 제가 신청하는 거예요.'),
    'SJ-A-086': dict(ask=[r'학교|졸업연도|졸업 연도|졸업한', r'오류|문구|메시지|화면|안내'],
                     reply='서울에 있는 고등학교를 2010년에 졸업했고, 정부24에서 "발급 대상이 아닙니다"라고 떠요.'),
    'SJ-A-087': dict(ask=[r'조회|등록|기존|있', r'안내|문구|화면|메시지|뜨'],
                     reply='농지대장을 조회하니 아직 등록된 대장이 없다고 나와요.'),
    'W7-BH021': dict(ask=[r'민원명|민원 이름|신청 이름|명칭', r'임시저장|접수 완료|접수완료|저장|접수'],
                     reply='민원명은 "가족관계증명서 발급"이고, 화면에는 "접수가 완료되었습니다"라고 떠요.'),
    'W7V2-BH002': dict(ask=[r'인터넷|온라인|사이트|정부24', r'무인|발급기|키오스크'],
                       reply='무인민원발급기 화면이에요.'),
    'W7V2-BH005': dict(ask=[r'성남|하남|지번|소재지|필지|토지|주소'],
                       reply='토지 주소는 성남시 수정구 ○○동 123번지예요.'),
    'W7-MS-033': dict(control=True),
    'W7V2-MS042': dict(control=True),
}


def run_gate(client, directory, qid, question, evidence):
    path = result_path(directory, 'gate', qid)
    if path.is_file():
        return read_json(path)
    request = [{'role': 'system', 'content': GATE_PROMPT},
               {'role': 'user', 'content': json.dumps({'question': question, 'evidence': evidence},
                                                      ensure_ascii=False)}]
    print(qid, 'gate', flush=True)
    started = perf_counter()
    raw = client.complete(request)
    seconds = perf_counter() - started
    try:
        value = json.loads(raw)
        ok = (isinstance(value, dict) and isinstance(value.get('can_answer_now'), bool)
              and isinstance(value.get('missing_facts'), list) and isinstance(value.get('question'), str)
              and all(isinstance(f, dict) and isinstance(f.get('blocks_core_answer'), bool)
                      for f in value['missing_facts']))
        if ok:  # the decision follows the per-fact flags, not the model's own summary field
            blocking = any(f['blocks_core_answer'] for f in value['missing_facts'])
            ok = not (blocking and not value['question'].strip())
            value['can_answer_now'] = not blocking
    except ValueError:
        value, ok = None, False
    row = dict(query_id=qid, status='ok' if ok else 'invalid_output', gate=value if ok else None,
               raw=raw, seconds=seconds, usage=client.last_usage)
    atomic_json(path, row)
    return row


def run_resume(client, directory, qid, system, final, known_ids, supplied_ids, clarification, reply):
    path = result_path(directory, 'resume', qid)
    if path.is_file():
        return read_json(path)
    system = dict(system, **{'판단 지침': GUIDELINES})
    state = dict(final)
    state['대화 이력'] = [{'assistant_clarification': clarification, 'user_reply': reply}]
    system['판단 지침'] = [*GUIDELINES, RESUME_RULE]
    request = [{'role': 'system', 'content': json.dumps(system, ensure_ascii=False, indent=2)},
               {'role': 'user', 'content': json.dumps(state, ensure_ascii=False, indent=2)}]
    print(qid, 'resume', flush=True)
    started = perf_counter()
    raw = client.complete(request)
    seconds = perf_counter() - started
    try:
        decision = parse_decision(raw, known_ids)
        if decision['action'] != 'respond':
            raise InvalidDecision('only respond is allowed')
        if not set(decision['arguments']['evidence_ids']) <= supplied_ids:
            raise InvalidDecision('respond evidence_ids must be supplied in the current input')
        status, error, text = 'responded', None, decision['arguments']['text']
    except InvalidDecision as exc:
        status, error, text = 'invalid_output', str(exc), None
    row = dict(query_id=qid, status=status, error=error, answer=text, reply=reply, raw=raw,
               new_tool_calls=0, saved_evidence_count=len(supplied_ids), seconds=seconds,
               usage=client.last_usage)
    atomic_json(path, row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=ANALYSIS_DIR / 'feedback' / 'clarify-gate-v3')
    args = parser.parse_args()
    run_dir = ANALYSIS_DIR / 'content-sample60-run'
    settings = read_json(run_dir / 'config.json')['settings']
    cases = [c for c in load_cases(run_dir)]
    if args.dry_run:
        print(', '.join(c[0] for c in cases), '| gate prompt chars:', len(GATE_PROMPT))
        return
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    try:
        for qid, question, _review, system, final, known_ids, supplied_ids in cases:
            spec = CASES[qid]
            evidence = final['현재 실행 상태']['evidence']
            gate = run_gate(client, args.output_dir, qid, question, evidence)
            item = dict(query_id=qid, control=bool(spec.get('control')), gate_status=gate['status'])
            decision = gate['gate'] or {}
            asked = decision.get('can_answer_now') is False
            item['asked'] = asked
            if spec.get('control'):
                item['pass'] = gate['status'] == 'ok' and not asked
            else:
                text = decision.get('question', '')
                hits = [bool(re.search(p, text)) for p in spec['ask']]
                item.update(question=text, ask_term_hits=hits, pass_auto=asked and all(hits))
                if asked:
                    resume = run_resume(client, args.output_dir, qid, system, final, known_ids,
                                        supplied_ids, text, spec['reply'])
                    item.update(resume_status=resume['status'], resume_seconds=resume['seconds'])
            summary.append(item)
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    atomic_json(args.output_dir / 'summary.json', summary)
    clar = [s for s in summary if not s['control']]
    ctrl = [s for s in summary if s['control']]
    print(f"clarification asked+terms hit: {sum(s['pass_auto'] for s in clar)}/{len(clar)}")
    print(f"controls not asked: {sum(s['pass'] for s in ctrl)}/{len(ctrl)}")
    print(f'Results: {args.output_dir}')


if __name__ == '__main__':
    main()
