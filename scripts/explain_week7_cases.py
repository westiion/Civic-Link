"""All 608 cases, ordered by review priority; observed retrieval failures, no invented answers."""
from __future__ import annotations
import _bootstrap  # Make local src imports available.
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

from civic_link.common import connect, read_jsonl, write_json, input_bytes, input_exists, input_path, read_json
from run_week7_experiment2 import DB, QUESTIONS

LABELS={'h3':'① 기존','h3_bge':'② 기존→BGE','coverage':'③ 변경','bge':'④ 변경→BGE',
    'hybrid':'Hybrid','coverage_no_graph_signal':'G 제거','hybrid_bge':'Hybrid→BGE',
    'h3_top50_bge':'기존50→BGE','coverage_top50_bge':'변경50→BGE',
    'graph_hybrid_seed':'Hybrid 시작점 Graph','graph_lexical_h0':'독립 Graph0',
    'graph_lexical_h1':'독립 Graph1','graph_lexical_h2':'독립 Graph2','graph_lexical_h3':'독립 Graph3'}
CORE=('h3','h3_bge','coverage','bge')
PRIORITIES={1:'답변 보류 필요·행동 미측정',2:'추가 확인 필요·행동 미측정',
    3:'수식 또는 BGE 적용 후 악화',4:'핵심 조건에서 지속 실패',
    5:'일부 조건에서 검색 복구',6:'핵심 네 조건 모두 검색 성공'}


def group_ranks(question,order):
    position={c:i for i,c in enumerate(order,1)}
    return [min((position[c] for c in e['acceptable_chunk_ids'] if c in position),default=None)
            for e in question['evidence_requirements']]


def failure_stage(ranks):
    missing=[r for r in ranks if r is None or r>10]
    if not missing:return 'complete'
    if all(r is None for r in missing):return 'candidate_missing'
    if all(r is not None for r in missing):return 'ranking_only'
    return 'mixed'


def priority(question,ranks):
    if question['expected_action']=='abstain':return 1
    if question['expected_action']=='clarify':return 2
    complete={m:failure_stage(v)=='complete' for m,v in ranks.items()}
    if any(complete[a] and not complete[b] for a,b in [('h3','coverage'),('coverage','bge'),('h3','h3_bge')]):return 3
    if not any(complete[m] for m in CORE):return 4
    if not all(complete[m] for m in CORE):return 5
    return 6


def clean(value):return re.sub(r'\s+',' ',str(value)).strip().replace('|','／')


def build(out,destination,model_dir,behavior_path=None):
    from transformers import AutoTokenizer
    if destination.exists():raise ValueError('choose a new case directory')
    tokenizer=AutoTokenizer.from_pretrained(model_dir,local_files_only=True,trust_remote_code=False)
    questions=read_jsonl(QUESTIONS)
    behavior=read_json(behavior_path) if behavior_path else None
    if behavior is not None:
        assert set(behavior)=={q['question_id'] for q in questions}
        assert all(set(behavior[qid])==set(CORE) for qid in behavior)
    orders=read_json((out/'diagnostic_rankings.json'))
    bge={r['query_id']:r for r in read_jsonl(out/'bge.jsonl')}
    assert set(orders)==set(LABELS)
    assert len(questions)==608 and len({q['question_id'] for q in questions})==608
    assert all(set(v)==set(bge) for v in orders.values())
    with connect(DB,True) as db:chunks={r['chunk_id']:dict(r) for r in db.execute('select * from chunks')}
    stages={m:Counter() for m in CORE};details={};records={};groups=defaultdict(list);truncation=[]
    stage_text={'candidate_missing':'놓친 근거가 모두 후보 밖에 있어 순위 변경만으로 복구할 수 없음',
        'ranking_only':'후보에는 근거가 있지만 10위 밖으로 밀림',
        'mixed':'후보에 없는 근거와 후보 안에서 10위 밖으로 밀린 근거가 함께 있음',
        'complete':'필수 근거 묶음이 모두 10위 안에 있음'}
    for q in questions:
        qid=q['question_id'];answer=q['expected_action']=='answer'
        ranks={m:group_ranks(q,lookup[qid]) for m,lookup in orders.items()} if answer else {}
        pr=priority(q,ranks);highest=max(x['score'] for x in bge[qid]['bge_scores'])
        action_errors=sum(v.get('acceptable_action_match') is not True for v in behavior[qid].values()) if behavior else 0
        diagnostics=[v['rubric_diagnostic'] for v in behavior[qid].values() if v.get('rubric_diagnostic')] if behavior else []
        content_flags=sum(d.get('content_pass') is False for d in diagnostics)
        unsupported_flags=sum(d['judgment']['unsupported_claim'] for d in diagnostics)
        if answer and (action_errors or content_flags):pr=min(pr,3)
        missing=sum(x is None or x>10 for x in ranks.get('bge',[]))
        worst=max((x or 10000 for x in ranks.get('bge',[])),default=0)
        groups[pr].append(((-unsupported_flags,-content_flags,-action_errors,-highest if not answer else -missing,-worst,qid),q))
        records[qid]=dict(question=q['question'],expected_action=q['expected_action'],
            acceptable_actions=q.get('acceptable_actions',[q['expected_action']]),expected_response=q['gold_answer'],
            action_rubric=q.get('action_rubric'),priority=pr,
            review_priority_signals=dict(model_unsupported_claim_flags=unsupported_flags,model_content_fail_flags=content_flags,action_label_errors=action_errors),
            evidence=[dict(number=i+1,quote=e['quote']) for i,e in enumerate(q['evidence_requirements'])],
            retrieval={m:dict(evidence_ranks=v,stage=failure_stage(v),found_at_10=sum(r is not None and r<=10 for r in v),required=len(v)) for m,v in ranks.items()},
            bge_top_score=highest,qwen=behavior[qid] if behavior else None)
        lines=[f"## {qid} · {clean(q['question'])}",'',f"**기대 행동:** {q['expected_action']} · **검토 우선도:** {pr}",'',f"**기대 답변/처리 요지:** {clean(q['gold_answer'])}",'']
        if answer:
            counts={m:sum(x is not None and x<=10 for x in v) for m,v in ranks.items()};n=len(q['evidence_requirements'])
            lines += ['**조건별 검색 결과** — 성공은 모든 근거 회수, 부분은 일부 회수, 실패는 0개 회수. 생성 답변 판정이 아니다.','']
            for names in (CORE,tuple(m for m in LABELS if m not in CORE)):
                lines += [' · '.join(f"{LABELS[m]}: {'성공' if counts[m]==n else '부분' if counts[m] else '실패'} {counts[m]}/{n}" for m in names),'']
            lines += ['| 필수 근거 | ① 기존 | ② 기존→BGE | ③ 변경 | ④ 변경→BGE |','|---|---:|---:|---:|---:|']
            for i,e in enumerate(q['evidence_requirements']):
                lines.append('| '+str(i+1)+' | '+' | '.join(str(ranks[m][i]) if ranks[m][i] is not None else '후보 없음' for m in CORE)+' |')
            lines += ['', '**정답 근거** — 각 번호 안에서는 허용 청크 중 하나만 찾으면 된다. 번호끼리는 모두 필요하다.','']
            for i,e in enumerate(q['evidence_requirements'],1):
                lines += [f"{i}. {clean(e['quote'])}",'']
            lines += ['**관측된 이유와 다음 확인**','']
            for m in ('h3','coverage','bge'):
                stage=failure_stage(ranks[m]);stages[m][stage]+=1
                missed=[str(i+1)+('번 후보 없음' if r is None else f'번 {r}위') for i,r in enumerate(ranks[m]) if r is None or r>10]
                lines += [f"- {LABELS[m]}: {counts[m]}/{n}개 회수. {stage_text[stage]}"+(' — '+', '.join(missed) if missed else '')+'.']
            stages['h3_bge'][failure_stage(ranks['h3_bge'])]+=1
            if counts['bge']<n:
                lines += ['- 개선 방향: '+('후보 확보·시작점·근거 연결부터 점검한다.' if any(r is None for r in ranks['bge']) else '필수 근거가 밀린 순위와 경쟁 청크를 검토한다. 복수 근거를 묶어서 보존할 필요가 있는지 확인한다.')]
                accepted=set().union(*(set(e['acceptable_chunk_ids']) for e in q['evidence_requirements']))
                rival=next((c for c in orders['bge'][qid][:10] if c not in accepted),None)
                if rival:
                    ch=chunks[rival];pos=orders['bge'][qid].index(rival)+1
                    lines += ['',f"**상위 경쟁 청크:** {pos}위 · {clean(ch['title'])}",f"> {clean(ch['text'])[:260]}",'이는 제공된 정답 근거 묶음에 포함되지 않은 청크다. 내용 전체가 거짓이거나 무관하다는 판정은 아니다.']
                for i,e in enumerate(q['evidence_requirements']):
                    if ranks['bge'][i] is not None and ranks['bge'][i]>10:
                        cid=orders['bge'][qid][ranks['bge'][i]-1];ch=chunks[cid];passage=ch['title']+'\n'+ch['text']
                        encoded=tokenizer(q['question'],passage,max_length=512,truncation='only_second',return_offsets_mapping=True)
                        end=max((span[1] for span,seq in zip(encoded['offset_mapping'],encoded.sequence_ids()) if seq==1),default=0)
                        quote=e['quote'];start=passage.find(quote)
                        state='quote_not_wholly_in_chunk' if start<0 else 'quote_visible' if start+len(quote)<=end else 'quote_cut'
                        note={'quote_not_wholly_in_chunk':'허용 청크 자체에 인용 전체가 들어 있지 않아 절단 탓으로 단정할 수 없다.',
                            'quote_visible':'필수 인용문 전체가 모델 입력에 남아 있다. 인용문 절단으로 이 실패를 설명할 수 없다.',
                            'quote_cut':'필수 인용문 전체가 512토큰 입력 안에 남지 않았다. 절단 영향을 재검토할 대상이지만 인과가 입증된 것은 아니다.'}[state]
                        lines += ['',f"**절단 확인 · 근거 {i+1}:** 가장 높은 허용 청크({ranks['bge'][i]}위)에서 {note}"]
                        truncation.append(dict(question_id=qid,evidence_number=i+1,rank=ranks['bge'][i],state=state))
            else:
                lines += ['- 개선 방향: 현재 top-10 근거 회수는 충족했다. 실제 답변이 이 근거를 정확하게 사용했는지는 별도 생성 평가가 필요하다.']
        else:
            rubric=q.get('action_rubric') or {}
            if q.get('acceptable_actions'):lines += [f"**허용 행동 대안:** {', '.join(q['acceptable_actions'])}. 행동 라벨만 허용된다는 뜻이며 실제 응답 내용 검수는 별도다.",'']
            for label,key in [('필수 처리','required'),('하면 안 되는 것','must_not'),('능력·자료 한계','system_capability_basis')]:
                if rubric.get(key):lines += [f"**{label}:** "+' / '.join(map(clean,rubric[key])),'']
            if rubric.get('missing_slots'):lines += ['**확인할 정보:** '+', '.join(rubric['missing_slots']),'']
            if rubric.get('minimal_clarification_policy'):lines += [clean(rubric['minimal_clarification_policy']),'']
            lines += [('**실제 처리:** 14개 검색 조건과 핵심 네 조건의 Qwen 행동·응답을 구분해 평가했다. 아래 실제 응답을 확인한다. 검색된 자료만으로 개인 승인이나 지급을 확정할 수는 없다.' if behavior else '**실제 처리:** 14개 조건의 검색만 실행했다. 행동 선택·답변·추가 질문 출력은 없으므로 모든 조건의 행동 성공/실패는 미측정이다. 검색이 됐다는 사실은 답변 가능함이나 승인 확정을 뜻하지 않는다.'),'']
            lines += ['| 핵심 조건 | 1위 청크 제목 |','|---|---|']
            for m in CORE:lines.append(f"| {LABELS[m]} | {clean(chunks[orders[m][qid][0]]['title'])} |")
            cid=orders['bge'][qid][0];ch=chunks[cid]
            lines += ['',f"**BGE 1위 관련도 점수:** {highest:.4f} (정답 확률 아님)",f"> {clean(ch['text'])[:360]}",'']
            if not behavior:
                lines += ['**왜 맞음/틀림을 말할 수 없나:** 위 내용은 검색된 자료일 뿐 실제 응답이 아니다. 모델이 거절했는지, 필요한 질문을 했는지, 개인 승인이나 지급액을 잘못 확정했는지 관측되지 않았다.','',
                    '**다음 조치:** 판단 모델·실행 경로를 연결하고 action과 실제 응답을 저장한 뒤, 위 필수 처리·금지사항'+('·확인 정보' if q['expected_action']=='clarify' else '')+'를 검수한다.']
        if behavior:
            lines += ['', '**Qwen 행동·실제 응답** — 같은 모델·프롬프트로 검색 top-10 전체를 읽었다. 행동 일치는 답변 내용의 정확성 보증이 아니다.','',
                '| 조건 | 실제 행동 | 허용 행동과 비교 | 내용 기준 · 모델 판정 | 인용 번호 형식 |','|---|---|---|---|---|']
            for m in CORE:
                r=behavior[qid][m];match=r.get('acceptable_action_match')
                d=r.get('rubric_diagnostic');content=('충족' if d.get('content_pass') else '미충족') if d else '미측정'
                notes=r.get('content_review_annotations',[])
                review_warning=any(n['finding'] in ('judge_boolean_reason_contradiction','action_label_contaminates_content_judgment','accepted_alternative_and_conditional_language_need_review','judge_overstates_jurisdiction_claim') for n in notes)
                if d and (not d['excerpt_is_verbatim'] or d.get('field_warnings') or review_warning):content+=' · 검수 경고'
                elif d and notes:content+=' · 교차 확인'
                lines.append(f"| {LABELS[m]} | {r.get('predicted_action') or '출력 형식 오류'} | {'일치' if match is True else '불일치' if match is False else '미측정'} | {content} | {'유효' if r.get('citation_syntax_valid') else '오류/미측정'} |")
            bundles={}
            for m in CORE:
                r=behavior[qid][m];key=json.dumps(r,ensure_ascii=False,sort_keys=True)
                bundles.setdefault(key,[]).append(m)
            for encoded,methods in bundles.items():
                r=json.loads(encoded);lines += ['', '**'+ ' / '.join(LABELS[m] for m in methods)+' 실제 응답:**', '> '+clean(r.get('response') or '유효한 응답 형식을 얻지 못했다.')]
                if r.get('raw_response_on_parse_error'):
                    lines += ['**형식 오류가 있는 원래 출력:**', '> '+clean(r['raw_response_on_parse_error']), '출력 한도 등으로 JSON이 완성되지 않은 경우 내용을 임의 복구해 행동 성적에 넣지 않았다.']
                if r.get('clarification_questions'):lines += ['**실제로 한 확인 질문:** '+' / '.join(map(clean,r['clarification_questions']))]
                if r.get('cited_source_titles'):
                    lines += ['**모델이 표시한 인용:** '+' / '.join(f'{number}번 {clean(title)}' for number,title in r['cited_source_titles'].items())+' · 인용을 표시했다는 사실이며 문장과 근거의 일치까지 검증한 값은 아니다.']
                actual=r.get('predicted_action');expected=q['expected_action']
                if r.get('acceptable_action_match') is True:
                    lines += [f'**행동 판정 이유:** 선언한 {actual}가 검수 기준에서 허용된 행동이다. 응답의 필수 내용 충족은 별도로 확인해야 한다.']
                elif actual:
                    lines += [f'**행동 판정 이유:** 기대 행동은 {expected}인데 {actual}를 선택했다. '+('자료만으로 답할 수 있는 문항에서 추가 확인이나 보류를 선택했다.' if expected=='answer' else '위 기대 처리와 실제 응답의 결론·확인 질문을 대조해야 한다.')]
                if r.get('citation_syntax_valid') is False:lines += ['**인용 주의:** 제공된 1–10번 자료 범위를 벗어나거나 잘못된 형식의 인용이 있다. 행동 라벨 정확도와 별도 오류다.']
                diagnostic=r.get('rubric_diagnostic')
                if diagnostic:
                    j=diagnostic['judgment'];lines += ['**응답 내용 사후 진단(같은 Qwen 모델, 사람 확정 아님):** '+clean(j['reason'])]
                    if answer:
                        retrieval_complete=all(all(rank is not None and rank<=10 for rank in ranks[m]) for m in methods)
                        if retrieval_complete and not diagnostic.get('content_pass'):
                            lines += ['**어느 단계에서 문제가 표시됐나:** 필수 근거는 상위 10개에 모두 있었지만 응답 내용의 모델 진단은 미충족이다. 검색 누락만으로 설명할 수 없으며 실제 응답과 채점 기준·채점 오류 가능성을 함께 검토한다.']
                        elif not retrieval_complete and diagnostic.get('content_pass'):
                            lines += ['**검색과 응답 판정의 차이:** 필수 근거를 모두 회수하지는 못했지만 채점 모델은 내용 기준 충족으로 판정했다. 일부 자료만으로 답했는지, 검색 정답의 범위와 답변 기준이 다른지, 모델 채점이 잘못됐는지를 추가 확인한다.']
                        elif not retrieval_complete:
                            lines += ['**어느 단계에서 문제가 표시됐나:** 필수 근거 완전회수와 응답 내용 모델 진단이 함께 실패했다. 위의 실제 근거 순위·누락 단계부터 확인하되, 검색 실패가 내용 오류의 유일한 원인이라고 단정하지 않는다.']
                    if 'content_pass' in diagnostic:
                        lines += ['**모델 판정 요약:** 내용 기준 '+('충족' if diagnostic['content_pass'] else '미충족')+' · 미지원 단정 '+('표시됨' if j['unsupported_claim'] else '표시 없음')]
                        if j.get('failed_requirements'):lines += ['**모델이 지적한 누락·오류:** '+' / '.join(map(clean,j['failed_requirements']))]
                        if j.get('clarification_sufficient') is not None:lines += ['**최소 확인 정책:** '+('충족으로 판정' if j['clarification_sufficient'] else '미충족으로 판정')]
                        if j.get('grounded_in_provided_passages') is not None:lines += ['**당시 제공 자료의 뒷받침:** '+('있다고 판정' if j['grounded_in_provided_passages'] else '부족하다고 판정')]
                        lines += ['**채점 모델이 판단에 사용한 응답 발췌:** '+clean(j['response_excerpt'])]
                    if not diagnostic['excerpt_is_verbatim']:lines += ['채점 모델의 인용 발췌가 실제 응답과 정확히 일치하지 않아 이 진단은 추가 확인이 필요하다.']
                    if 'missing_clarification_verdict' in diagnostic.get('field_warnings',[]):lines += ['**확인 질문 판정 누락:** 채점 모델이 이 항목을 null로 출력했다. 다른 명시적 판정은 보존하고 확인 질문 충족 여부만 미측정으로 둔다.']
                    if 'irrelevant_clarification_verdict' in diagnostic.get('field_warnings',[]):lines += ['이 문항은 clarify 대상이 아니므로 채점 모델의 확인 질문 판정은 집계에서 제외한다.']
                elif r.get('content_judgment_error'):
                    lines += ['**응답 내용 사후 진단:** 채점 출력 형식 오류로 미측정. 원래 응답은 위에 보존했다.']
                for note in r.get('content_review_annotations',[]):
                    lines += ['**원문·채점 교차 확인(사람 확정 아님):** '+clean(note['note'])]
            lines += ['', '**다음 확인:** '+('위 기대 처리·금지사항과 실제 응답을 사람이 확인한다. 필요한 확인 질문을 구체적으로 했는지와 개인 결론을 미지원 단정했는지를 우선 본다.' if not answer else '행동이 일치해도 생성된 문장과 근거의 사실 일치 여부는 별도 검수가 필요하다.')]
        details[qid]='\n'.join(lines)+'\n'
    destination.mkdir(parents=True)
    index=[];all_ids=[];counts={}
    for pr,items in sorted(groups.items()):
        ordered=[q for _,q in sorted(items,key=lambda x:x[0])];counts[str(pr)]=len(ordered)
        for start in range(0,len(ordered),60):
            batch=ordered[start:start+60];part=start//60+1
            title=({1:'답변 보류 필요',2:'추가 확인 필요',3:'응답 또는 검색 우선 검토'}.get(pr,PRIORITIES[pr]) if behavior else PRIORITIES[pr])
            name=f'{title} - {part}편.md' if behavior else f'{pr:02d}-{part:02d} {title}.md'
            header=f'# {title} · {part}\n\n이 부록은 전체 608문항 중 이 그룹의 {len(batch)}문항이다. 검색 결과와 실제 답변 품질은 구분한다.\n\n'
            header+=('중요도: 보류·추가 확인 → 응답 또는 검색 우선 검토 → 지속 검색 실패 → 일부 복구 → 검색 성공. ' if behavior else '중요도: 행동 평가 공백(보류·확인) → 수식/BGE 악화 → 지속 실패 → 일부 복구 → 모두 성공. ')
            header+='같은 그룹 안에서는 누락 근거 수와 BGE 근거 순위, 비답변은 최고 관련도 점수 순으로 검토한다. 관련도 점수는 행동 실패 점수가 아니다.\n\n'
            header+='조건 번호는 ① 기존 수식, ② 기존 수식 뒤 전체 BGE, ③ 변경 수식, ④ 변경 수식 뒤 전체 BGE다. 순위는 각 근거를 인정하는 청크 중 가장 높은 순위이며, 10위 밖이면 완전회수에 기여하지 않는다.\n\n'
            if behavior:header+='행동 불일치나 사후 모델 채점의 내용 미충족이 있는 답변 문항도 우선도 3에 포함했다. 그룹 안에서는 모델의 미지원 단정 표시, 내용 미충족, 행동 불일치 순으로 먼저 본다. 모델 채점은 사람 확정이 아니며 원문 발췌 불일치도 별도 표시한다. 동일한 입력의 응답은 재사용했다.\n\n'
            sections=[]
            for q in batch:
                detail=details[q['question_id']]
                if behavior:
                    heading,body=detail.split('\n',1)
                    detail=heading+'\n\n<details>\n<summary>정답 근거 · 조건별 결과 · 실제 응답 보기</summary>\n'+body+'\n</details>\n'
                sections.append(detail)
            text=header+'\n---\n\n'.join(sections)
            (destination/name).write_text(text)
            ids=[q['question_id'] for q in batch];all_ids+=ids
            index.append(dict(file=name,priority=pr,count=len(ids),question_ids=ids))
    assert len(all_ids)==608 and len(set(all_ids))==608 and set(all_ids)=={q['question_id'] for q in questions}
    extracted=[]
    for item in index:
        extracted+=re.findall(r'^## (\S+) · ',(destination/item['file']).read_text(),flags=re.M)
    assert extracted==all_ids
    write_json(destination/'index.json',dict(question_count=608,unique_ids=608,priority_counts=counts,pages=index))
    write_json(destination/'case_details.json',dict(question_count=608,ordered_ids=all_ids,conditions=LABELS,cases=records))
    write_json(destination/'failure_analysis.json',dict(stages={m:dict(v) for m,v in stages.items()},
        truncation_observations=truncation,truncation_summary=dict(Counter(r['state'] for r in truncation)),
        scope='best ranked allowed chunk for each missed BGE evidence group; no causal attribution'))
    print(json.dumps(dict(counts=counts,pages=len(index),bytes=sum(p.stat().st_size for p in destination.iterdir()),failure_stages={m:dict(v) for m,v in stages.items()},truncation=Counter(r['state'] for r in truncation)),ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--case-dir',type=Path,required=True)
    p.add_argument('--model-dir',type=Path,required=True);p.add_argument('--behavior-cases',type=Path)
    a=p.parse_args();build(a.output_dir,a.case_dir,a.model_dir,a.behavior_cases)
