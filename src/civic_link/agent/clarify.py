"""Clarification gate: decide before answering whether the user must supply a missing fact.

One extra model call lists the facts that would flip the core conclusion (``blocks_core_answer``);
the decision to ask follows those per-fact flags, not the model's own summary field.
"""
from __future__ import annotations

import json

from .client import ModelError


GATE_PROMPT = (
    '당신은 민원 안내 답변 전에 사용자에게 더 물어야 할 정보가 있는지 점검하는 단계입니다. '
    '질문과 제공된 근거 본문을 읽고 다음 순서로 판단하세요. '
    '(1) 사용자가 묻는 핵심 결론(가능 여부, 필요 서류명, 적용 기준, 처리 방식)을 한 문장으로 정하세요. '
    '(2) 근거 본문에서 그 핵심 결론이 달라지는 조건을 모두 찾으세요. 서류 종류, 신청 채널·사용 중인 화면(본문이 채널별로 결론을 달리 설명하는 경우), '
    '접수 상태, 학교급·졸업연도·소재지처럼 본문이 분기 기준으로 쓰는 사실입니다. 본문이 분기 기준으로 쓰지 않는 사실은 넣지 마세요. '
    '(3) 사용자가 질문에서 이미 말한 사실은 제외하세요. 말하지 않았거나 "이 화면", "지금"처럼 가리키기만 한 사실만 남기세요. '
    '(4) 남은 사실마다 blocks_core_answer를 정하세요. 그 사실 없이는 (1)의 핵심 결론이 예/아니오나 서류명이 뒤집히거나 정해지지 않으면 true, '
    '결론은 정해지고 설명의 세부만 달라지면 false입니다. 사용자가 두 규정·서류의 차이를 구별해 달라고 했고 본문이 각각의 규칙을 직접 설명하면 false입니다. '
    '(5) 사용자가 "안 나온다·안 된다"처럼 실패를 말하면서 이유를 말하지 않았다면 화면에 뜨는 오류·안내 문구를 true인 사실로 포함하세요. '
    '사용자가 "처음인지 고치는 건지 모르겠다"처럼 자기 상태를 모른다고 하면 상태를 판단할 확인 단서(조회 결과, 화면 안내 문구)를 true인 사실로 넣으세요. '
    '(6) blocks_core_answer가 true인 사실이 하나도 없으면 can_answer_now는 true이고 question은 빈 문자열입니다. '
    '하나라도 있으면 can_answer_now는 false이고, question에는 true인 사실을 모두 포함한 질문 하나를 쓰세요(일반 안내는 쓰지 마세요). '
    '다른 설명 없이 {"core_conclusion":"핵심 결론","missing_facts":[{"fact":"필요한 사실","blocks_core_answer":true 또는 false,'
    '"changes_answer":"어떻게 결론이 달라지는지"}],"can_answer_now":true 또는 false,"question":"사용자에게 할 질문"} 형식의 JSON만 반환하세요.'
)
RESUME_RULE = (
    '사용자가 방금 답한 사실을 그대로 적용해 원래 질문에 답하세요. 사실 주장마다 이번 입력의 supplied_evidence_ids에 있는 '
    '[근거 ID]를 문장 옆에 표시하고 arguments.evidence_ids에도 넣으세요. 근거 본문이 뒷받침하지 않는 채널별 규칙·서류·기간은 쓰지 말고, '
    '확인되지 않은 부분은 확인되지 않았다고 밝히세요. 다시 되묻지 마세요.'
)

RESUME_RULE = (
    '사용자가 방금 답한 사실을 그대로 적용해 원래 질문에 답하세요. 사실 주장마다 이번 입력의 supplied_evidence_ids에 있는 '
    '[근거 ID]를 문장 옆에 표시하고 arguments.evidence_ids에도 넣으세요. 근거 본문이 뒷받침하지 않는 채널별 규칙·서류·기간은 쓰지 말고, '
    '확인되지 않은 부분은 확인되지 않았다고 밝히세요. 다시 되묻지 마세요.'
)


def parse_gate(raw):
    """Return the validated gate decision, or None when the output is unusable."""
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not (isinstance(value, dict) and isinstance(value.get('missing_facts'), list)
            and isinstance(value.get('question'), str)
            and all(isinstance(f, dict) and isinstance(f.get('blocks_core_answer'), bool)
                    for f in value['missing_facts'])):
        return None
    blocking = any(f['blocks_core_answer'] for f in value['missing_facts'])
    if blocking and not value['question'].strip():
        return None
    value['can_answer_now'] = not blocking
    return value


def check_gate(client, question, evidence):
    """One model call. Returns (decision or None, raw output or error text)."""
    request = [{'role': 'system', 'content': GATE_PROMPT},
               {'role': 'user', 'content': json.dumps({'question': question, 'evidence': evidence},
                                                      ensure_ascii=False)}]
    try:
        raw = client.complete(request)
    except (ModelError, OSError) as exc:
        return None, f'model_error: {exc}'
    return parse_gate(raw), raw
