"""Read-only audit of the 44 BGE-direct failures; no model/API or gold changes.

The case notes are analyst hypotheses, not model-internal causal explanations.
Recomputes ranks from the exported CSV and preserves the supporting source text.
"""
from pathlib import Path
import csv
import gzip
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parents[1]
# A single primary observation per case, not mutually exclusive true causes.
NOTES = {
 'SJ-A-058': ('비교 한쪽 편중', '농지 관련 절차가 상위를 차지한다. 토지대장 기본정보는 6위지만 누구나 신청 가능이라는 개요는 12위여서 비교의 반대쪽 자격 조건이 빠진다.'),
 'SJ-B-012': ('질문 의도·절차 혼동', '기간연장 절차 대신 과세표준증명 일반 안내 5개와 다른 세법의 보완·과세 관련 조항이 앞선다. 국세청 규정 제20조의 기간연장 문장은 뒤쪽 제4항에 있어 입력 잘림도 점검해야 한다.'),
 'SJ-B-014': ('질문 의도·절차 혼동', '거부 이유 통지를 묻지만 휴업·폐업 발급 안내가 다수 앞선다. 다만 상위의 일반 민원법 불복 규정이 일부 요구를 대신 뒷받침하는지도 검수해야 한다.'),
 'SJ-B-019': ('보완 의무·정의 누락', '급여 변경의 직권·신청 규정은 1위다. 질문에 암묵적으로 포함된 본인의 신고 의무는 35위다. 직접 표현된 변경·통지와 추가 행동 의무를 함께 선택하지 못한다.'),
 'SJ-N-020': ('비교 한쪽 편중', '세목별 과세증명과 지방세 조항이 상위를 차지하고 체납 없음의 납세증명 기본정보는 24위다. 두 서류의 증명 범위를 함께 확보하지 못한다.'),
 'SJ-N-022': ('서비스·근거구간 불일치', '사업자등록증명 제출서류 2위·개요 6위지만 목적을 설명하는 기본정보는 11위다. 타 서비스 등록·증명 규정도 앞선다. 서비스 미발견과 필수 설명 누락을 구분해야 한다.'),
 'SJ-N-028': ('서비스·근거구간 불일치', '상위 1~3위가 모두 해당 서비스다. 2위 개요 본문에도 발급서류명이 명시돼 있다. 서류명을 묻는 질문에 기본정보만 허용한 평가 기준이 과도한지 검수 우선.'),
 'W7-PR-012': ('서비스·근거구간 불일치', '민원 목적은 1위에서 찾지만 발급서류명이 명시된 서비스 개요는 21위다. 목적 설명과 문서 이름이 서로 다른 청크에 나뉜다.'),
 'W7-PR-013': ('서비스·근거구간 불일치', '정확한 민원 기본정보가 1위이나 신청서 이름이 있는 개요는 13위다. 민원명 질문에 신청서 구간까지 필수로 둔 이유를 검수해야 한다.'),
 'W7-PR-020': ('서비스·근거구간 불일치', '출결 기재 규정 1위·생활기록부 서비스 개요 4위를 찾았다. 증명 민원 설명인 기본정보 18위만 허용한 그룹은 개요가 대체 가능한지 검수 대상이다.'),
 'W7-PR-021': ('서비스·근거구간 불일치', '급여 신청·수급자 관리 규정이 앞서고 증명서 발급 기본정보는 13위다. 발급 개요 7위는 있으므로 발급과 신규 급여 신청 구분이 실제로 가능한지도 확인해야 한다.'),
 'W7-PR-022': ('서비스·근거구간 불일치', '보호대상자 증명 목적은 1위에서 찾았으나 발급서류명 개요는 32위다. 제목만으로 이름을 추정하는 것과 본문 근거를 확보하는 것을 구분해 검수한다.'),
 'W7-PR-024': ('보완 의무·정의 누락', '임차료 지급 규정은 1위이나 주거급여 정의는 11위다. 민원명 질문에서 정의까지 반드시 별도 회수해야 하는지 점검한다.'),
 'W7V2-PR054': ('보완 의무·정의 누락', '수급 기준은 2위이나 생활안정 목적 조항은 20위다. 구체적 자격 관련 조항이 목적 조항보다 앞선다. 민원 이름 판단의 필수 근거 범위를 검수한다.'),
 'SJ-B-038': ('서비스·근거구간 불일치', '국문 구비서류 없음은 4위에 있다. 이미 등록된 장애인의 증명 목적 기본정보가 12위여서 실패다. 등록 진단과 증명 발급을 구별하는 보충 근거 필요성을 검수한다.'),
 'W7V2-PR017': ('서비스·근거구간 불일치', '동의 정보 종류를 묻는데 1위 시행령 제36조가 금융·신용·보험 범위를 직접 열거한다. 제출 동의서를 명시한 시행규칙만 허용한 정답 그룹과 질문 의도의 정합성을 검수할 필요가 크다.'),
 'W7-LA-047': ('서비스·근거구간 불일치', '발급기관을 직접 정한 제9조는 1위다. 등록 장애인 증명 목적 기본정보 24위 때문에 실패했다. 이미 찾은 조항만으로 핵심 답이 가능한지 검수한다.'),
 'W7V2-LA046': ('질문 의도·절차 혼동', '지적공부 원본 보존과 중앙 복제 관리 주체 대신 다른 장부의 보존·전산 규정이 앞선다. 임야대장과 지적공부의 용어 연결, 동일 업무처럼 보이는 타 분야 규정의 구별이 약한 패턴이다.'),
 'SJ-N-050': ('참조 연결 근거 누락', '국세청 규정 제41조는 6위지만 명시적으로 인용한 민원법 제28조·시행령 제32조는 74·58위다. 개별 질문-청크 점수가 참조 근거의 묶음 유지까지 보장하지 않는다.'),
 'SJ-N-051': ('비교 한쪽 편중', '근로소득 정의 1위와 여러 소득 조항이 앞서고 증명 서비스 기본정보는 12위다. 법적 정의와 서비스 설명을 함께 선택하지 못한다.'),
 'W7-LA-024': ('질문 의도·절차 혼동', '신청 철회 유도 금지 대신 민법의 철회 조항과 급여 지급 조항이 앞선다. 정답의 금지 문장은 긴 제21조 후반 제5항이므로 입력 잘림 여부를 우선 점검한다.'),
 'W7V2-LA010': ('동일 조항 분할구간 누락', '같은 수수료규칙 제6조의 뒷 청크가 4위이나 정답의 환급·서명 문장이 있는 앞 청크는 11위다. 실제 뒷 청크는 다른 환급 준용·반환 제한 내용을 담아 완전한 대체 근거로 단정할 수 없다. 앞 청크 제4항의 입력 잘림도 점검.'),
 'W7V2-LA014': ('질문 의도·절차 혼동', '미수령 종결·전자문서 예외 대신 폐업 발급 안내와 전자정부법 일반 규정이 앞선다. 같은 국세청 규정에서도 신청 방법 제15조가 10위, 종결 조건 제23조가 11위다.'),
 'W7V2-LA016': ('동일 조항 분할구간 누락', '제11조의 다른 청크는 2·5위지만 소유자 표시 제한이 있는 청크는 18위다. 같은 조항 제목만으로 필요한 항을 확보할 수 없으며 긴 앞부분의 서식 목록과 잘림도 점검한다.'),
 'SJ-B-034': ('참조 연결 근거 누락', '거주지 변경 업무처리 제33조는 4위지만 주거급여에 해당 규정을 준용시키는 연결 제2조는 35위다. 적용 근거를 보존하지 못한 패턴이다.'),
 'SJ-N-044': ('참조 연결 근거 누락', '재열람 시간 제10조와 별도 건수로 보지 않는 제11조는 1·2위다. 상위 위임 규정 제28조는 29위다. 직접 답변 근거와 법적 연결 완전성의 평가 목적을 구분해야 한다.'),
 'SJ-N-060': ('참조 연결 근거 누락', '서비스 종료 후 재열람 예외를 직접 설명한 제10조가 1위다. 인터넷 업무의 일반 위임 규정 제28조 20위가 추가 필수인지 검수한다.'),
 'W7V2-MS007': ('참조 연결 근거 누락', '평면도 제한을 다른 규칙 제11조제3항에 따르도록 하는 조항은 1위이나 실제 제한은 21위다. 같은 규칙의 다른 제11조 청크는 9위라서 참조와 항별 분할 문제가 겹친다.'),
 'W7V2-MS014': ('참조 연결 근거 누락', '주민등록 규칙의 참조 문장은 1위, 가족관계법의 실제 성년 조건은 15·16위다. 같은 분야의 주변 조항이 인용 대상보다 앞선다.'),
 'SJ-A-003': ('비교 한쪽 편중', '상위 10개 전부 성남시 무인발급 표다. 정부24 개요 63위·창구 예외 표 행 24위가 빠진다. 지역·발급방식별 비교보다 유사한 표 행이 상위를 점유한다.'),
 'SJ-N-057': ('비교 한쪽 편중', '지방세 발급 조항은 6위, 국세 발급 조항은 22위다. 관련 시행령·시행규칙과 납부내역증명 안내가 앞서 두 세목의 직접 근거가 함께 남지 않는다.'),
 'SJ-N-062': ('비교 한쪽 편중', '농지대장 기본정보는 7위지만 토지대장 기본정보는 24위다. 농지 관련 조항과 머리말이 앞서 비교 대상의 목적 설명이 빠진다.'),
 'W7-MS-024': ('비교 한쪽 편중', '성적증명 졸업연도 기준은 1위, 생활기록부 기준은 23위다. 경기도·성적 관련 설명이 앞서 두 서비스의 연도 조건을 함께 확보하지 못한다.'),
 'W7-MS-026': ('비교 한쪽 편중', '주민등록의 본인·세대원 범위는 4위이나 가족관계 무인발급의 본인 제한은 19위다. 같은 세대라는 조건을 다른 증명서에 적용할 수 없는 근거가 빠진다.'),
 'W7-MS-033': ('비교 한쪽 편중', '증명서 즉시 처리 개요는 1위지만 급여 심사 통지 규정은 36위, 증명 목적은 42위다. 여러 서비스의 즉시 처리 안내가 앞서 신청과 증명 발급의 서로 다른 절차가 구별되지 않는다.'),
 'W7-MS-039': ('비교 한쪽 편중', '병적 위임 예외는 3위지만 졸업증명 위임 요건은 30위다. 상위가 병적 규정에 편중돼 비교 대상의 일반 요건이 빠진다.'),
 'W7-MS-045': ('비교 한쪽 편중', '소득금액증명 범위는 1위, 부가세증명 기본정보는 13위다. 부가세 서비스 개요가 10위여도 증명 범위를 설명하는 구간은 다르다.'),
 'W7V2-MS033': ('보완 의무·정의 누락', '농지대장 기재 목적은 2위지만 등기의 물권변동 효력 제186조는 25위다. 매매 계약 제563조가 1위인 점에서 계약 성립과 물권변동 요건을 구별하는 근거 선택이 부족한 패턴이다.'),
 'W7V2-MS036': ('보완 의무·정의 누락', '퇴직소득 조항과 증명 서비스는 찾았지만 종합소득에서 퇴직소득을 제외하는 분류 제4조는 13위다. 특정 소득 설명과 상위 분류 규정의 보완 관계가 반영되지 않는다.'),
 'W7V2-MS037': ('비교 한쪽 편중', '생활기록부 정정 관련 조항이 상위를 차지해 5그룹 중 4개는 회수한다. 증빙으로 제시할 기본증명서 성별 기재사항은 23위다. 보조 서류의 내용을 확인하는 하위 요구가 빠진다.'),
 'W7V2-MS041': ('참조 연결 근거 누락', '신청 대리와 대리수령 규칙은 2·1위다. 신청 대리 규칙이 인용한 민법 제777조의 친족 범위는 11위다. 명시적 인용 대상이 경계 밖으로 밀린 사례다.'),
 'W7V2-MS042': ('비교 한쪽 편중', '상위 10개 전부 등기 관련 문서이며 국세 진위확인의 입력값 규정은 55위다. 두 서비스의 확인 절차 중 하나가 완전히 밀린다.'),
 'W7V2-MS043': ('비교 한쪽 편중', '기초연금 부부 감액은 4위, 주거급여 가구 단위 규정은 11위다. 부부 연금 관련 조항에 비해 다른 급여의 지급 단위 근거가 밀린다.'),
 'W7V2-MS044': ('비교 한쪽 편중', '등기 부속서류 제한은 7위이나 평면도 일반 제한·임차인 예외는 65·12위다. 비교 한쪽과 같은 조항의 앞뒤 구간을 동시에 놓친다. 일반 제한은 긴 청크 후반에 있어 잘림도 점검한다.'),
}


def load_jsonl(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s]


def main():
    folder = ROOT/'week7/failure_analysis'
    case_text = (folder/'ranking-cases.md').read_text(encoding='utf-8')
    ids = re.findall(r'^## ([\w-]+)$', case_text, re.M)
    assert len(ids) == len(set(ids)) == 44 and set(ids) == set(NOTES)
    qs = {q['question_id']: q for q in load_jsonl(ROOT/'data/benchmark/questions.jsonl')}
    ranked = defaultdict(list)
    csvpath = ROOT/'week7/results/experiment2_bge_direct_20261101/rankings.csv'
    with csvpath.open(encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if r['query_id'] in NOTES:
                ranked[r['query_id']].append(dict(chunk_id=r['chunk_id'], rank=int(r['final_rank']), score=float(r['bge_raw_score'])))
    db = sqlite3.connect(':memory:')
    db.deserialize(gzip.decompress((ROOT/'data/corpus/corpus.sqlite.gz').read_bytes()))
    db.row_factory = sqlite3.Row
    chunks = {r['chunk_id']: dict(r) for r in db.execute('select * from chunks')}
    db.close()
    input_stats = {}
    with gzip.open(ROOT/'week7/results/experiment2_20261031/bge.jsonl.gz','rt',encoding='utf-8') as f:
        for l in f:
            r = json.loads(l)
            if r['query_id'] in NOTES:
                input_stats[r['query_id']] = r['input_stats']
    details = []
    for qid in ids:
        q=qs[qid]; rows=sorted(ranked[qid],key=lambda r:r['rank']); positions={r['chunk_id']:r for r in rows}
        assert [r['rank'] for r in rows] == list(range(1,len(rows)+1))
        assert rows == sorted(rows,key=lambda r:(-r['score'],r['chunk_id']))
        top=rows[:10];topdocs={chunks[r['chunk_id']]['document_id'] for r in top}
        groups=[]
        for i,e in enumerate(q['evidence_requirements'],1):
            choices=[positions[c] for c in e['acceptable_chunk_ids'] if c in positions]
            assert choices
            best=min(choices,key=lambda r:r['rank']); ch=chunks[best['chunk_id']]
            # Character offsets are diagnostic only; never equate them to model token positions.
            quote=e['quote'];offset=ch['text'].find(quote)
            groups.append(dict(group=i,**best, quote=quote,locator=e['locator'],source_url=e.get('source_url'),
                title=ch['title'],text=ch['text'],acceptable_chunk_ids=e['acceptable_chunk_ids'],
                quote_character_offset=offset if offset>=0 else None,chunk_characters=len(ch['text']),
                document_in_top10=ch['document_id'] in topdocs,
                gap_from_rank10=top[-1]['score']-best['score']))
        assert 10 < max(g['rank'] for g in groups) <= 100
        missing=[g for g in groups if g['rank']>10]
        top_details=[dict(**r,title=chunks[r['chunk_id']]['title'],text=chunks[r['chunk_id']]['text'],document_id=chunks[r['chunk_id']]['document_id']) for r in top]
        details.append(dict(question_id=qid,question=q['question'],question_type=q['question_type'],
            primary_observation=NOTES[qid][0],cause_hypothesis=NOTES[qid][1],
            evidence_status='rank_and_text_verified; model_cause_not_proven; gold_not_readjudicated',
            group_ranks=[g['rank'] for g in groups],missing_count=len(missing),
            max_document_occupancy=max(Counter(r['document_id'] for r in top_details).values()),
            missed_documents=('all_present' if all(g['document_in_top10'] for g in missing) else
                              'all_absent' if not any(g['document_in_top10'] for g in missing) else 'mixed'),
            input_stats=input_stats[qid],groups=groups,top10=top_details))
    summary=dict(cases=len(details),missing_groups=sum(d['missing_count'] for d in details),
        partial=sum(any(r<=10 for r in d['group_ranks']) for d in details),
        multi_group=sum(len(d['groups'])>1 for d in details),
        complete_by20=sum(max(d['group_ranks'])<=20 for d in details),
        missed_document_status=dict(Counter(d['missed_documents'] for d in details)),
        primary_observations=dict(Counter(d['primary_observation'] for d in details)),
        case_queries_with_any_truncation=sum(d['input_stats']['truncated_pairs']>0 for d in details),
        note='Truncation counts are query-level; no claim that a particular gold sentence was truncated.',
        sources_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [csvpath,folder/'ranking-cases.md',ROOT/'data/benchmark/questions.jsonl']})
    assert (summary['partial'],summary['multi_group'],summary['complete_by20'],summary['missing_groups'])==(33,35,24,51)
    for name,obj in [('ranking-cause-summary.json',summary)]:
        (folder/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (folder/'ranking-cause-details.jsonl').write_text(''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in details),encoding='utf-8')
    lines=['# 순위 탈락 44문항: 문항별 원인 가설 및 원문 대조','',
        'BGE 직접 정렬의 저장 점수·순위를 재검산했다. 아래 원인 설명은 관찰에 근거한 가설이며 모델 내부 원인 확정이나 사람의 정답 검수 완료를 뜻하지 않는다.', '',
        '| 질문 ID | 필수 근거 순위 | 주된 관찰 | 원인 가설·추가 검수 |','|---|---|---|---|']
    for d in details:
        qid=d['question_id'];lines.append(f"| [{qid}](ranking-cases.md#{qid.lower()}) | {d['group_ranks']} | {d['primary_observation']} | {d['cause_hypothesis']} |")
    lines += ['', '## 실제 점수와 필수 문장', '', '점수는 BGE 원점수(logit)이며 정답 확률이 아니다. 10위와의 차이는 해당 질문 내부에서만 비교한다. 원문 인용은 저장된 정답표이며 최신 법령 검증을 수행한 것은 아니다.', '']
    for d in details:
        lines += [f"### {d['question_id']}", '', d['question'], '', d['cause_hypothesis'], '',
            f"입력 기록: 후보 {d['input_stats']['pairs']}쌍 중 {d['input_stats']['truncated_pairs']}쌍 잘림. 어느 청크의 어느 문장이 잘렸는지는 이 기록만으로 확인할 수 없다.", '']
        for g in d['groups']:
            if g['rank']<=10:continue
            lines += [f"- 누락 근거 {g['group']}: **{g['rank']}위**, `{g['chunk_id']}` / {g['title']}",
                f"- BGE 점수 {g['score']:.6f}, 10위와의 차이 {g['gap_from_rank10']:.6f}; 같은 문서 top10 존재: {g['document_in_top10']}",
                f"- 원문 위치: {g['locator']} / [출처]({g['source_url']})", '', '> '+g['quote'].replace('\n','\n> '), '']
        lines += ['| 실제 상위 3개 | 청크 | BGE 점수 |','|---|---|---|']
        for r in d['top10'][:3]:lines.append(f"| {r['rank']}. {r['title']} | {r['chunk_id']} | {r['score']:.6f} |")
        lines+=['']
    (folder/'ranking-cause-cases.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
