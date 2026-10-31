"""Recorded Week 7 adjudication rules and frozen-evidence validation."""
import copy
import json
from collections import Counter
from pathlib import Path

from civic_data import connect


def revise(rows):
    result=copy.deepcopy(rows); changes=[]
    for q in result:
        qid=q['question_id']; before=copy.deepcopy(q); notes=[]
        if qid=='SJ-N-032':
            q['canonical_question']=q['canonical_question'].replace('온라인 ','')
            q['gold_rubric']['must']=[s.replace('온라인 ','') for s in q['gold_rubric']['must']]
            notes.append('근거 없는 온라인 한정을 보조 주석에서 제거')
        if q['expected_action']=='clarify':
            slots=q['action_rubric']['missing_slots']
            if not q.get('clarification'):
                q['clarification']=dict(required_slots=list(slots),first_turn_retrieval_scored=False,
                    gold_followup_question=q['gold_answer'],resolved_question_status='not_fabricated_or_scored')
                notes.append('누락된 추가 확인 구조를 기존 행동 정책에서 복원')
            elif q['clarification']['required_slots']!=slots:
                q['clarification']['required_slots']=list(slots)
                notes.append('확인 슬롯을 검토된 최소 질문 정책과 통일')
        if qid=='SJ-A-082':
            q['acceptable_actions']=['clarify','answer']
            q['action_rubric']['acceptable_actions']=['clarify','answer']
            q['action_rubric']['alternative_answer_boundary']='귀속연도·소득 종류·조회 범위 점검목록을 안내해도 허용한다. 개인의 누락 원인·미신고·오류를 확정하거나 실제 조회한 것처럼 답하지 않는다.'
            notes.append('기본 clarify 유지; 정확한 점검목록 answer도 허용, 응답 내용 검수는 별도')
        if qid in ('W7V2-BH020','W7-MS-016'):
            other='W7-MS-016' if qid=='W7V2-BH020' else 'W7V2-BH020'
            q['related_question_ids']=sorted(set(q.get('related_question_ids',[])+[other]))
            q['rule_family_id']='land_use_restriction_optional_omission'
            notes.append('동일 규칙의 답변/추가 확인 대조 연결; 작성 부모를 추정하지 않음')
        if qid=='W7V2-BH016':
            answer='고졸검정고시 합격연도를 먼저 확인해 주세요. 2015년도 이전 합격이면 영문 제17호서식 대상이며 선택Ⅱ 반영 여부를 다시 물을 필요가 없습니다. 그 이후 합격이라면 2015년도 이전 선택Ⅱ 성적을 합산 반영했는지 확인해 제17호 적용 여부를 구분합니다. 해당 조건이 아니면 영문 제13호서식이며 합격증명서와 구별합니다.'
            q['gold_answer']=answer;q['action_rubric']['expected_response']=answer;q['clarification']['gold_followup_question']=answer
            q['gold_rubric']['must'][0]='합격연도를 먼저 확인하고, 2015년도 이전 합격 조건만으로 확정되지 않을 때 과거 선택Ⅱ 성적 합산 여부를 확인한다.'
            q['action_rubric']['required']=list(q['gold_rubric']['must'])
            q['clarification']['slot_policy']='conditional; exam_pass_year first; carryover only when pre-2015 pass is not established'
            q['action_rubric']['slot_policy']=q['clarification']['slot_policy']
            notes.append('조건부 OR 확인 정책: 합격연도만으로 충분하면 추가 슬롯을 요구하지 않음')
        if qid=='W7V2-BH012':
            answer=q['gold_answer']+' 생계를 같이했다는 판단을 동거에만 한정하지 말고 정기적으로 수급자에게 생활비를 지급했는지도 확인합니다.'
            q['gold_answer']=answer;q['action_rubric']['expected_response']=answer;q['clarification']['gold_followup_question']=answer
            q['gold_rubric']['must_not'].append('생계 공동을 동거만으로 제한하지 않는다.')
            q['action_rubric']['must_not']=list(q['gold_rubric']['must_not'])
            q['review_support']=[dict(chunk_id='DOC-f8f8c1de46fbe5059f00-C0023',locator='기초연금법 시행령 제17조제4항',quote='2. 정기적으로 기초연금 수급자에게 생활비를 지급한 부양의무자')]
            notes.append('생계 공동 확인에 정기 생활비 지급을 포함; 추가 검토 근거 명시')
        if notes:
            fields=[k for k in q if q[k]!=before.get(k)]
            changes.append(dict(question_id=qid,reasons=notes,before={k:before.get(k) for k in fields},after={k:q[k] for k in fields}))
        # Keep stable IDs and provenance, but remove authoring-wave distinctions
        # from the current schema and every public CSV/Markdown view.
        q.pop('generation_stage',None)
        q['dataset_id']='week7-master608'
        q['agent_review_status']=('reviewed_frozen_evidence' if q['row_origin']=='new_synthetic'
                                  else 'targeted_review' if notes or q['expected_action']!='answer'
                                  else 'structural_validation_only')
        q['human_review_status']='user_reported_review; item_level_local_decisions_unrecorded'
        # Do not mark the agent's work as human verification.
    return result,changes


def validate(rows,original,db_path):
    assert len(rows)==len(original)==608
    assert len({q['question_id'] for q in rows})==608
    old={q['question_id']:q for q in original}
    assert all(q['question']==old[q['question_id']]['question'] for q in rows)
    assert all(q['expected_action']==old[q['question_id']]['expected_action'] for q in rows)
    assert all(q['evidence_requirements']==old[q['question_id']]['evidence_requirements'] for q in rows)
    assert all('generation_stage' not in q for q in rows)
    with connect(db_path,True) as db:
        chunks={r['chunk_id']:dict(r) for r in db.execute('SELECT * FROM chunks')}
        docs={r['document_id']:dict(r) for r in db.execute('SELECT * FROM documents')}
    evidence_count=0;new_evidence_count=0;wide=[]
    for q in rows:
        if q['expected_action']=='clarify':
            assert q['clarification']['required_slots']==q['action_rubric']['missing_slots']
        for e in q['evidence_requirements']:
            evidence_count+=1
            doc=docs[e['document_id']]
            body=doc['body']
            assert body[e['start_char']:e['end_char']]==e['quote'],q['question_id']
            ids=e['acceptable_chunk_ids'];assert ids and all(cid in chunks and chunks[cid]['document_id']==e['document_id'] for cid in ids)
            contained=any(chunks[cid]['start_char']<=e['start_char'] and chunks[cid]['end_char']>=e['end_char'] for cid in ids)
            if q['row_origin']=='new_synthetic':
                new_evidence_count+=1;assert contained,q['question_id']
            elif not contained:wide.append(dict(question_id=q['question_id'],evidence_id=e['evidence_id']))
        for support in q.get('review_support',[]):assert support['quote'] in chunks[support['chunk_id']]['text']
    return dict(question_count=len(rows),actions=dict(Counter(q['expected_action'] for q in rows)),
        evidence_count=evidence_count,new_evidence_count=new_evidence_count,
        unchanged_query_text=True,unchanged_retrieval_labels=True,
        historical_wide_evidence=wide,historical_wide_question_count=len({x['question_id'] for x in wide}),
        agent_review_counts=dict(Counter(q['agent_review_status'] for q in rows)),
        human_verified_promoted=False)

