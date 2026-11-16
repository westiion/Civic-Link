"""Scale the clarification gate to the benchmark's gold clarification labels.

gate    60 clarify questions (gold: required slots, rubric) + 60 answer questions as false-positive controls.
        The gate is the final (v3) prompt from run_week9_clarify_gate.py. Six clarify questions were used while
        tuning that prompt; they are reported separately from the 54 held-out questions.
resume  The 5 clarify questions that carry a gold resolved question and answer. A Qwen "user" answers the gate's
        question from the hidden resolved situation, then Qwen answers from the saved first-search evidence only.
Judge calls see only the gold rubric and the text under test, never the variant that produced it.
"""

import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from _bootstrap import ROOT  # noqa: F401
from civic_link.agent.client import ModelError, QwenClient
from civic_link.agent.evaluation import covers
from civic_link.agent.experiment import atomic_json, identity, read_json, result_path
from civic_link.agent.prompts import GUIDELINES, corpus_scope, messages
from civic_link.agent.protocol import InvalidDecision, parse_decision
from run_week9_clarify_gate import GATE_PROMPT, RESUME_RULE, run_gate
from run_week9_probes import ARCHIVE, BENCHMARK, DB, WORK, initial_evidence, load_questions

TUNED = {'SJ-X-050', 'SJ-A-086', 'SJ-A-087', 'W7-BH021', 'W7V2-BH002', 'W7V2-BH005'}
OUT = WORK / 'feedback' / 'gate-scale'

GATE_JUDGE = (
    '당신은 확인 질문의 채점자입니다. 질문, 기대하는 확인 정보(required_slots·required)와 금지 사항(must_not)이 주어집니다. '
    '"asked_required"는 제시된 확인 질문이 required가 요구하는 정보를 실질적으로 묻는지입니다(표현 일치나 모든 슬롯의 동시 제시는 요구하지 않고, '
    '그저 "정보를 알려달라"는 질문은 아닙니다). "must_not_hit"는 확인 질문이 금지 사항을 어기는지입니다. '
    '{"asked_required":true,"must_not_hit":false,"note":"짧은 사유"} 형식의 JSON만 반환하세요.'
)
USER_SIM = (
    '당신은 아래 상황의 민원인입니다. 원래 질문을 했고, 상담 시스템이 확인 질문을 했습니다. 숨은 실제 상황에 비추어 확인 질문에 구어체로 짧게 답하세요. '
    '실제 상황에 없는 정보는 지어내지 말고 모른다고 하세요. 확인 질문이 묻지 않은 정보는 말하지 마세요. {"reply":"민원인의 답"} 형식의 JSON만 반환하세요.'
)
RESUME_JUDGE = (
    '당신은 민원 안내 답변의 채점자입니다. 질문 상황, 정답 요지, 답변이 주어집니다. "matches_gold"는 답변이 정답 요지를 담는지, '
    '"unsupported_claim"은 정답 요지와 무관하거나 근거 없이 단정한 주장이 핵심 결론을 해치는지입니다. '
    '{"matches_gold":true,"unsupported_claim":false,"note":"짧은 사유"} 형식의 JSON만 반환하세요.'
)


def llm_json(client, directory, label, qid, system, payload, check):
    path = result_path(directory, label, qid)
    if path.is_file():
        return read_json(path)
    print(qid, label, flush=True)
    started = perf_counter()
    raw = client.complete([{'role': 'system', 'content': system},
                           {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}])
    try:
        value = json.loads(raw)
        ok = check(value)
    except ValueError:
        value, ok = None, False
    row = dict(query_id=qid, status='ok' if ok else 'invalid_output', value=value if ok else None, raw=raw,
               seconds=perf_counter() - started, usage=client.last_usage)
    atomic_json(path, row)
    return row


def run_gate_part(client, directory):
    questions = load_questions(BENCHMARK / 'questions.jsonl')
    clarify = [q for q in questions if q['expected_action'] == 'clarify']
    answers = sorted((q for q in questions if q['expected_action'] == 'answer'),
                     key=lambda q: identity(['week9-gate-control-v1', q['question_id']]))[:60]
    rows = []
    for group, items in (('clarify', clarify), ('answer_control', answers)):
        for q in items:
            qid = q['question_id']
            gate = run_gate(client, directory, qid, q['question'], initial_evidence(qid))
            decision = gate['gate'] or {}
            asked = decision.get('can_answer_now') is False
            row = dict(query_id=qid, group=group, tuned=qid in TUNED, gate_status=gate['status'], asked=asked,
                       question=decision.get('question', ''))
            if group == 'clarify' and asked:
                rubric = q['action_rubric']
                judged = llm_json(client, directory, 'judge', qid, GATE_JUDGE, dict(
                    original_question=q['question'], clarification_question=row['question'],
                    required_slots=q['clarification']['required_slots'], required=rubric.get('required'),
                    must_not=rubric.get('must_not'), minimal_clarification_policy=rubric.get('minimal_clarification_policy')),
                    lambda v: isinstance(v, dict) and isinstance(v.get('asked_required'), bool)
                    and isinstance(v.get('must_not_hit'), bool))
                row['judge_status'] = judged['status']
                row['pass'] = bool(judged['value'] and judged['value']['asked_required']
                                   and not judged['value']['must_not_hit'])
            elif group == 'clarify':
                row['pass'] = False
            rows.append(row)
    atomic_json(directory / 'gate-summary.json', rows)
    clar = [r for r in rows if r['group'] == 'clarify']
    for name, sel in (('all clarify', clar), ('held-out clarify', [r for r in clar if not r['tuned']])):
        print(f"{name} n={len(sel)} asked={sum(r['asked'] for r in sel)} pass={sum(r['pass'] for r in sel)}"
              f" gate_invalid={sum(r['gate_status'] != 'ok' for r in sel)}")
    ctrl = [r for r in rows if r['group'] == 'answer_control']
    print(f"answer controls n={len(ctrl)} unnecessary_questions={sum(r['asked'] for r in ctrl)}"
          f" gate_invalid={sum(r['gate_status'] != 'ok' for r in ctrl)}")


def run_resume_part(client, directory):
    scope = corpus_scope(DB)
    rows = []
    for q in load_questions(BENCHMARK / 'questions.jsonl'):
        cl = q.get('clarification') or {}
        if q['expected_action'] != 'clarify' or 'simulated_user_response' not in cl:
            continue
        qid, evidence = q['question_id'], initial_evidence(q['question_id'])
        saved_covers = [covers(evidence, r) for r in cl['resolved_evidence_requirements']]
        gate = run_gate(client, directory, qid, q['question'], evidence)
        decision = gate['gate'] or {}
        row = dict(query_id=qid, saved_evidence_covers_resolved=saved_covers, asked=decision.get('can_answer_now') is False)
        if row['asked']:
            clarification = decision['question']
            sim = llm_json(client, directory, 'user', qid, USER_SIM,
                           dict(original_question=q['question'], hidden_situation=cl['resolved_question'],
                                clarification_question=clarification),
                           lambda v: isinstance(v, dict) and isinstance(v.get('reply'), str) and v['reply'].strip())
            reply = (sim['value'] or {}).get('reply')
            row.update(clarification=clarification, simulated_reply=reply)
            if reply:
                ids = [e['id'] for e in evidence]
                payload = {'원래 질문': q['question'],
                           '현재 실행 상태': dict(phase='followup', evidence=evidence, checks=[], history=[],
                                             graph_candidates=[], document_candidates=[], graph_pages=[],
                                             context=dict(supplied_evidence_ids=ids, omitted_evidence_ids=[],
                                                          stored_evidence_count=len(ids))),
                           '실행 규칙': dict(allowed_actions=['respond'], remaining_model_calls=1, remaining_tool_calls=0),
                           '대화 이력': [dict(assistant_clarification=clarification, user_reply=reply)]}
                request = messages(payload, scope, {})
                system = json.loads(request[0]['content'])
                system['판단 지침'] = [*GUIDELINES, RESUME_RULE]
                request[0]['content'] = json.dumps(system, ensure_ascii=False, indent=2)
                path = result_path(directory, 'resume', qid)
                if path.is_file():
                    resumed = read_json(path)
                else:
                    print(qid, 'resume', flush=True)
                    raw = client.complete(request)
                    try:
                        decision2 = parse_decision(raw, set(ids))
                        if decision2['action'] != 'respond':
                            raise InvalidDecision('only respond is allowed')
                        resumed = dict(status='responded', answer=decision2['arguments']['text'], raw=raw)
                    except InvalidDecision as exc:
                        resumed = dict(status='invalid_output', answer=None, raw=raw, error=str(exc))
                    atomic_json(path, resumed)
                row['resume_status'] = resumed['status']
                if resumed['answer']:
                    judged = llm_json(client, directory, 'resume-judge', qid, RESUME_JUDGE,
                                      dict(situation=cl['resolved_question'], gold=cl['resolved_gold_answer'],
                                           answer=resumed['answer']),
                                      lambda v: isinstance(v, dict) and isinstance(v.get('matches_gold'), bool)
                                      and isinstance(v.get('unsupported_claim'), bool))
                    row['matches_gold'] = (judged['value'] or {}).get('matches_gold')
                    row['unsupported_claim'] = (judged['value'] or {}).get('unsupported_claim')
        rows.append(row)
    atomic_json(directory / 'resume-summary.json', rows)
    print(f"resume cases n={len(rows)} asked={sum(r['asked'] for r in rows)}"
          f" saved_evidence_covers_resolved={sum(all(r['saved_evidence_covers_resolved']) for r in rows)}"
          f" matches_gold={sum(bool(r.get('matches_gold')) for r in rows)}"
          f" unsupported_claim={sum(bool(r.get('unsupported_claim')) for r in rows)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('part', choices=('gate', 'resume'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    if args.dry_run:
        q = load_questions(BENCHMARK / 'questions.jsonl')
        print(sum(x['expected_action'] == 'clarify' for x in q), 'clarify;',
              sum('simulated_user_response' in (x.get('clarification') or {}) for x in q), 'with gold resolved;',
              len(GATE_PROMPT), 'gate prompt chars')
        return
    endpoint = os.environ.get('QWEN_ENDPOINT')
    if not endpoint:
        parser.error('set QWEN_ENDPOINT in the connected PowerShell window')
    settings = read_json(ARCHIVE / 'config.json')['settings']
    client = QwenClient(endpoint, settings['model'], api_key=os.environ.get('QWEN_API_KEY'),
                        timeout=settings['timeout'], max_tokens=settings['max_output_tokens'],
                        json_mode=settings['json_mode'], enable_thinking=settings['enable_thinking'])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        (run_gate_part if args.part == 'gate' else run_resume_part)(client, args.output_dir)
    except ModelError as exc:
        raise SystemExit(f'Qwen request failed: {exc}. Rerun the same command to continue.') from exc
    print(f'Results: {args.output_dir}')


if __name__ == '__main__':
    main()
