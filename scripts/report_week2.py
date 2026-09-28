"""Build the upload bundle from frozen inputs and measured outputs only."""
from __future__ import annotations
import csv,hashlib,json,statistics,shutil,tempfile,zipfile
from collections import Counter
from pathlib import Path
from civic_data import ROOT,connect,read_jsonl,write_json,write_jsonl,digest,now
from generation import action_metrics
from civic_data import services as service_registry
ENTRY={int(s['service_id'][-3:]):s['entry_alias'] for s in service_registry()}

BENCH=ROOT/'benchmark'
RESULTS=BENCH/'results'
OUT=ROOT/'docs/reports/week2/submission'
DATA=OUT.parent/'data'
DB=ROOT/'data/processed/civic.sqlite'
METHODS=['bm25','dense','hybrid']
LABEL={'bm25':'BM25','dense':'Dense','hybrid':'Hybrid'}
STATUS={'success':'전체 위치 회수','partial':'일부 위치 회수','failure':'위치 미회수','not_scored_unanswerable':'검색 채점 제외'}

def table(headers,rows):
    def cell(x):return str(x if x is not None else '—').replace('|',' / ').replace('\n','<br>')
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(cell(x) for x in r)+' |' for r in rows])

def pct(x):return '—' if x is None else f'{x*100:.2f}%'
def js(p):return json.loads(p.read_text())
def emit(name,lines): (OUT/name).write_text('\n\n'.join(lines)+'\n')

def export_database():
    """Export a consistent SQLite snapshot, including committed WAL contents."""
    DATA.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=DATA,prefix='.civic-',suffix='.sqlite',delete=False) as f:
        temporary=Path(f.name)
    try:
        with connect(DB,True) as source,connect(temporary) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or target.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('database snapshot failed integrity checks')
        temporary.replace(DATA/'civic.sqlite')
    finally:
        temporary.unlink(missing_ok=True)

def covered_fraction(start,end,spans):
    """Union overlap, not the sum of possibly overlapping chunk lengths."""
    intervals=sorted((max(start,a),min(end,b)) for a,b in spans if a<end and b>start)
    covered=0;last=start
    for a,b in intervals:
        a=max(a,last)
        if b>a:covered+=b-a
        last=max(last,b)
    return covered/(end-start)

def analyze(qs,chunks,cases,canonical):
    rows=[]
    for q in qs:
        qid=q['question_id'];row={'query_id':qid,'expected_action':q['expected_action'],'methods':{}}
        for m in METHODS:
            case=cases[m][qid];hits=case['top_10'];es=q['evidence_requirements'];ids={h['chunk_id'] for h in hits}
            rank_info=[]
            for e in es:
                rank=next((h['rank'] for h in hits if h['chunk_id'] in e['acceptable_chunk_ids']),None)
                spans=[(chunks[cid]['start_char'],chunks[cid]['end_char']) for cid in ids if chunks[cid]['document_id']==e['document_id']]
                fraction=covered_fraction(e['start_char'],e['end_char'],spans)
                rank_info.append({'source_alias':e['source_alias'],'locator':e['locator'],'first_rank':rank,'gold_span_coverage':fraction,
                                  'source_present':True,'gold_chunk_count':len(e['acceptable_chunk_ids'])})
            missed=[e for e in rank_info if e['first_rank'] is None]
            hypotheses=[];observations=[]
            if es:
                observations.append(f"필수 위치 {len(es)}개 중 {len(es)-len(missed)}개 회수. 모든 지정 원문·인용 위치가 DB에 존재한다.")
                if missed:
                    observations.append('Top-10 밖: '+', '.join(e['source_alias']+' '+e['locator'] for e in missed))
                    c=canonical[m][qid];current=case['metrics']['10']['recall'];standard=c['metrics']['10']['recall']
                    observations.append(f'기준 문장 Recall {standard:.3f}, 대화형 질문 Recall {current:.3f}.')
                    if standard>current:hypotheses.append('VOCABULARY_MISMATCH')
                    if q['document_hops'] and q['document_hops']>=1:hypotheses.append('MULTI_HOP_REQUIRED')
                    if q['question_type'] in ('legal_grounding','cross_reference'):hypotheses.append('EXACT_TERM_MISS')
                    if any(t in q['challenge_tags'] for t in ['service_confusion','near_name_confusion','similar_service_contrast']):hypotheses.append('SEMANTIC_CONFUSION')
                    if any(e['document_id'] in {h['document_id'] for h in hits} for e in q['evidence_requirements'] if not set(e['acceptable_chunk_ids'])&ids):
                        observations.append('같은 문서의 다른 위치가 검색됐다. 문서 미수집과 구분되는 위치 순위 오류다.')
                    if not hypotheses:hypotheses.append('RANKING_MISS')
                else:observations.append('Top-10이 지정 근거 위치를 모두 포함한다. 답변의 사실적 정확성은 별도 평가한다.')
                if 'table_and_note' in q['challenge_tags']:
                    observations.append('표 행·주석 원문과 구조화 표가 보존돼 있다. 주석 누락만으로 TABLE_PARSING_ERROR를 확정하지 않는다.')
                if any(e['first_rank'] is not None and e['gold_span_coverage']<1-1e-12 for e in rank_info):
                    observations.append('회수한 위치 중 일부 인용 범위만 포함한 경우가 있다. 아래 원문 범위 지표로 분리한다.')
            else:
                if q['expected_action']=='clarify':observations.append('첫 질문의 의도가 미확정이다. 특정 정답 문서를 찾지 못했다고 검색 실패로 처리하지 않는다. 누락 정보: '+', '.join(q['clarification']['required_slots']))
                elif q['task_track']=='provided_evidence_only':observations.append('제공 근거 전용 시험이다. 검색 호출은 기록했지만 생성 입력에서 제외한다. 조건: '+q['evidence_condition'])
                else:observations.append('개인 조회·실제 처리·개별 결정·보장 등 확정할 수 없는 요구다. 생성 응답의 범위 준수 여부를 평가한다.')
                hypotheses=['UNANSWERABLE' if q['unanswerable_reason']!='OUT_OF_SCOPE' else 'OUT_OF_SCOPE']
            row['methods'][m]={'retrieval_status':case['retrieval_status_at_10'],'evidence':rank_info,'observations':observations,
                               'cause_hypotheses':hypotheses,'confirmed_failure':'TOP_K_LOCATOR_MISS' if missed else None,
                               'span_macro_coverage':sum(e['gold_span_coverage'] for e in rank_info)/len(rank_info) if rank_info else None,
                               'all_gold_spans_covered':all(e['gold_span_coverage']>=1-1e-12 for e in rank_info) if rank_info else None}
        rows.append(row)
    return rows

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    export_database()
    qs=read_jsonl(BENCH/'questions.jsonl');manifest=js(BENCH/'manifest.json')
    with connect(DATA/'civic.sqlite',True) as c:
        docs=[dict(r) for r in c.execute('SELECT * FROM documents ORDER BY alias')]
        services=[dict(r) for r in c.execute('SELECT * FROM services ORDER BY service_id')]
        chunks={r['chunk_id']:dict(r) for r in c.execute('SELECT * FROM chunks')}
        schema=[dict(r) for r in c.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
        counts={r['name']:c.execute('SELECT COUNT(*) FROM '+r['name']).fetchone()[0] for r in schema if r['type']=='table'}
        checks=[dict(r) for r in c.execute('SELECT * FROM source_checks ORDER BY check_id')]
        dimensions=[tuple(r) for r in c.execute('SELECT model,dimension,COUNT(*) FROM embeddings GROUP BY model,dimension')]
        integrity=c.execute('PRAGMA integrity_check').fetchone()[0];fk=list(c.execute('PRAGMA foreign_key_check'))
        columns={r['name']:[dict(x) for x in c.execute('PRAGMA table_info('+r['name']+')')] for r in schema if r['type']=='table'}
        metadata={r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM metadata ORDER BY key')}
    reports={};cases={};canonical={};generation={};gencases={};predictions={}
    for m in METHODS:
        reports[m]={t:js(RESULTS/f'{m}_{t}_metrics.json') for t in ['conversational','canonical','clarified'] if (RESULTS/f'{m}_{t}_metrics.json').exists()}
        cases[m]={r['query_id']:r for r in read_jsonl(RESULTS/f'{m}_conversational_cases.jsonl')}
        canonical[m]={r['query_id']:r for r in read_jsonl(RESULTS/f'{m}_canonical_cases.jsonl')} if (RESULTS/f'{m}_canonical_cases.jsonl').exists() else cases[m]
        gd=RESULTS/('generation_'+m)
        if (gd/'generation_metrics.json').exists():
            generation[m]=js(gd/'generation_metrics.json');gencases[m]={r['query_id']:r for r in read_jsonl(gd/'generation_cases.jsonl')}
            generation[m]['action_classification']=action_metrics(list(gencases[m].values()))
            write_json(gd/'generation_metrics.json',generation[m])
            predictions[m]={r['query_id']:r for r in read_jsonl(gd/'generation_predictions.jsonl')}
    analysis=analyze(qs,chunks,cases,canonical);byid={r['query_id']:r for r in analysis}
    write_jsonl(RESULTS/'failure_analysis.jsonl',analysis)
    retrieval_rows=[]
    for m in METHODS:
        for track,report in reports[m].items():
            for k,v in report['retrieval']['overall']['metrics'].items():retrieval_rows.append({'method':m,'track':track,'k':int(k),'n':report['retrieval']['overall']['question_count'],**v})
    with (RESULTS/'summary.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(retrieval_rows[0]));w.writeheader();w.writerows(retrieval_rows)
    checks_summary={'created_at':now(),'counts':counts,'sqlite_integrity':integrity,'foreign_key_errors':len(fk),
                    'source_types':dict(Counter(d['source_type'] for d in docs)),
                    'embedding_models':[{'model':x[0],'dimension':x[1],'vectors':x[2]} for x in dimensions],
                    'source_checks':dict(Counter(x['status'] for x in checks)),
                    'null_fields':{k:sum(d[k] is None for d in docs) for k in ['effective_date','law_name','article_number','parent_document_id']}}
    write_json(RESULTS/'data_audit.json',checks_summary)
    shutil.copyfile(BENCH/'questions.jsonl',OUT/'questions.jsonl');shutil.copyfile(BENCH/'queries.jsonl',OUT/'queries.jsonl')
    shutil.copyfile(BENCH/'decision_inputs.jsonl',OUT/'decision_inputs.jsonl')
    worksheet=RESULTS/'human_review.csv'
    if not worksheet.exists():
        with worksheet.open('w') as f:
            fields=['question_id','question','reviewer','reviewed_at','gold_sufficient','minimum_hops_verified','natural_wording','notes']
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for q in qs:w.writerow({'question_id':q['question_id'],'question':q['question']})
    shutil.copyfile(worksheet,OUT/'human_review.csv')
    source_rows=[[d['alias'],d['title'],d['source_type'],d['effective_date'],d['collected_at'],d['last_checked_at'],f"[원문]({d['source_url']})"] for d in docs]
    # Scope: retain the prior audit's scope while explicitly limiting its hop claim.
    scope=(ROOT/'docs/scope.md').read_text();audit=scope.split('## Graphability Audit',1)[1].split('### 주요 정정',1)[0]
    byalias={d['alias']:d for d in docs}
    emit('01_scope_audit.md',['# 서비스 범위와 Graphability Audit',
        '10개 카테고리의 40개 서비스를 다룬다. 증명 서비스 36개와 생계급여 신청·주거급여 신청·긴급복지 생계지원 요청·기초연금 신청 4개를 포함한다. 복지 분야는 증명 3개와 신청·지원 4개, 총 7개다.',
        '## 서비스 등록부',table(['ID','서비스','카테고리','진입 출처','관측 Max Hop','분류','공식 진입 원문'],[[s[k] for k in ['service_id','name','category','entry_source_id','max_document_hops','audit_classification']]+[f"[{ENTRY[int(s['service_id'][-3:])]}]({byalias[ENTRY[int(s['service_id'][-3:])]]['source_url']})"] for s in services]),
        '## 카테고리별 감사',table(['카테고리','서비스 수','single-hop','multi-hop','관측 최대'],[[cat,len(group),sum(s['audit_classification']=='single-hop' for s in group),sum(s['audit_classification']=='multi-hop' for s in group),max(s['max_document_hops'] for s in group)] for cat in sorted({s['category'] for s in services}) for group in [[s for s in services if s['category']==cat]]]),
        'single-hop 18개, multi-hop 22개다. direct-only와 unresolved 서비스는 현재 등록부에 없지만 개별 질문은 0-hop 또는 답변 불가일 수 있다.',
        '## 진입 문서와 참조 경로',audit.strip(),
        '## 범위 결정',
        '서비스 식별, 증명 내용, 공개 신청 조건·필요 서류, 전국 공통 법령, 성남시 발급 목록·본인확인 안내를 포함한다. 기기별 가동 상태·실시간 위치, 개인 기록 조회, 실제 발급, 개인별 급여 승인·금액 확정은 제외한다. 표의 서비스 지원 여부를 개별 기기의 정상 작동 보장으로 해석하지 않는다.',
        '## 다음 단계',
        'Week 3은 명시적 관계의 Graph DB, Week 4는 검색과 그래프 확장 결합이다. 이후 0/1/2/3-hop 제한을 같은 질문 묶음으로 비교한다. 현재 텍스트 검색 결과만으로 그래프의 개선 효과를 확정하지 않는다. Gold Evidence의 충분성과 최소 hop은 별도 검수가 필요하다.',
        '추가 원문 검증 대상은 개별 기기 운영, 특수 예외의 별표·서식, 개인별 급여액 산정에 필요한 연도별 사업안내다. 현재 정답 근거 64건에 지정된 원문은 모두 존재한다. 이 사실이 서비스 관련 자료의 전수 수집을 뜻하지는 않는다.'])
    # All implemented table columns, keys and SQL are exported from the live DB.
    data=['# 데이터베이스와 공통 데이터 명세',
          'SQLite 파일: `data/processed/civic.sqlite`. 원문 HTML과 정규화 본문을 함께 저장한다. 문서 ID는 원본 URL과 정규화 본문 해시로, 청크 ID는 문서 ID와 순번으로 결정한다. 서비스와 문서는 다대다 관계다.',
          '## 적재 현황',table(['테이블','행 수'],counts.items()),
          '무결성 검사: '+integrity+f'. 외래키 오류: {len(fk)}건. '+', '.join(f'{model} {dim:,}차원 벡터 {n:,}개' for model,dim,n in dimensions)+'.',
          '## 날짜와 자료 기준',
          '`effective_date`는 법령 시행일, `collected_at`은 원문 수집 시각, `last_checked_at`은 원격 응답을 마지막으로 확인한 시각이다. 법령은 2026-09-24 기준 시행본을 고정했다. 성남시 안내는 2026-09-26 수집본이며 과거 상태라고 소급하지 않는다. 미기재 시행일은 null이다. 수집 실패 시 기존 문서의 수집일을 갱신하지 않는다.',
          '법령·행정규칙 64건은 모두 시행일을 갖는다. 안내 27건은 적용할 법정 시행일이 없어 null이다. 원문 머리말과 날짜 필드의 대조 기록은 `benchmark/results/source_date_repairs.json`에 저장한다.',
          table(['필드','null 문서 수','해석'],[[k,v,{'effective_date':'일반 안내 또는 시행일 미기재. 수집일로 대체하지 않음','law_name':'비법령 문서','article_number':'문서 행은 전문 단위. 조문은 chunks.locator','parent_document_id':'현재 문서는 원문 스냅샷의 루트'}[k]] for k,v in checks_summary['null_fields'].items()]),
          '## 원문과 검색 단위',
          '`raw_sources.payload`에 HTML 전체 바이트를 보존한다. `documents.body`는 검색용 정규화 본문이다. 법령은 조문, 안내는 섹션, 표는 행과 주석으로 구분한다. 긴 단위는 최대 1,200자, 겹침 150자로 분할하며 가능하면 줄바꿈에서 자른다. offset은 정규화 본문의 Python 문자 인덱스로 `[start_char,end_char)`를 사용한다.',
          '법령의 첫 부칙 이후와 별표·서식 다운로드 영역은 raw에 보존하지만 본문 검색에는 포함하지 않는다. 양식 번호를 설명하는 조문을 찾는 것과 첨부 양식 자체를 검색하는 것은 다르다. 표는 rowspan/colspan 원형 셀과 펼친 grid를 JSON으로 함께 저장한다. 표시용 300자 snippet은 생성 입력을 자르는 길이가 아니다.',
          '## 필드와 키',
          '`document_catalog` 뷰로 document_id, service_id, category를 함께 조회한다. 문서에 service_id 하나를 강제로 넣지 않는다. raw_hash/content_hash/input_hash는 각각 HTML 바이트·정규화 본문·임베딩 입력을 구분한다. 임베딩 캐시는 chunk_id/model/encoder_fingerprint의 복합키이며 모델·입력 형식·prefix·endpoint 조건이 바뀌면 재사용하지 않는다.',
          table(['필드','의미'],[['document_id','URL과 본문 SHA-256으로 만든 문서 ID'],['service_id / category','서비스 ID와 카테고리. document_services를 통해 연결'],['alias','수집 출처 별칭. G는 안내, L은 법령, C01은 성남시 표'],['title / body','문서 제목과 정규화한 검색 본문'],['source_url / source_type','원본 URL과 law/service_guide/municipal_table'],['organization / law_name','수집 출처 기관과 법령·행정규칙명'],['article_number / parent_document_id','별도 조문 문서·부모 관계를 저장할 확장 필드. 현재 전문 저장 구조에서는 null'],['effective_date / collected_at / last_checked_at','시행일 / 원문 수집 시각 / 원격 확인 시각'],['content_hash / raw_hash','본문 UTF-8 SHA-256 / HTML 원본 바이트 SHA-256'],['raw_path','프로젝트 루트 기준 원문 보존 경로'],['acquisition_status / metadata_json','수집 성공 상태와 수집 파일·색인 범위 등 보조 정보'],['chunk_id / ordinal / locator','청크 ID / 문서 안의 0부터 시작하는 순서 / 조문·섹션·표 위치'],['start_char / end_char','정규화 본문 안의 문자 구간. 끝 위치는 미포함'],['grid_json / cells_json','행·열로 펼친 표 / 원형 셀과 rowspan·colspan'],['dimension / vector_json','벡터 차원 / L2 정규화 벡터 배열'],['encoder_fingerprint / input_hash','인코더 설정 해시 / 해당 청크 임베딩 입력 해시'],['checked_at / http_status / reason','수집 시도 시각 / HTTP 응답 / 제외 또는 실패 사유']])]
    meanings={'metadata':'코퍼스 설정과 해시','raw_sources':'원문 HTML 바이트','services':'서비스·분류·진입 문서·관측 hop','documents':'수집 원문과 정규화 본문·출처·날짜','document_services':'문서와 서비스 연결','source_checks':'수집 시도와 실패 이력','chunks':'검색 단위·위치·본문 해시','document_tables':'표의 원형 셀과 펼친 grid','embeddings':'정규화 벡터와 인코더 식별자'}
    fields={'key':'DB 설정 이름','value':'JSON으로 직렬화한 설정 값',
            'raw_hash':'원문 HTML 바이트의 SHA-256','media_type':'원문 MIME 타입. 현재 text/html','payload':'수집한 HTML 전체 바이트',
            'service_id':'서비스 식별자. SJ-SVC-001부터 SJ-SVC-040까지','name':'서비스명','category':'서비스 카테고리',
            'entry_source_id':'scope.md 출처 목록의 D01–D32 키','max_document_hops':'서비스 감사에서 기록한 최대 외부 문서 참조 깊이','audit_classification':'서비스 감사 분류',
            'document_id':'원본 URL과 정규화 본문 해시로 만든 DOC- 식별자','alias':'출처 별칭. G 안내문, L 법령, C01 성남시 표',
            'title':'문서 제목 또는 문서 제목과 청크 위치','body':'정규화한 문서 검색 본문','source_url':'공식 출처 URL',
            'source_type':'law, service_guide, municipal_table 중 하나','organization':'출처 기관','law_name':'법령·행정규칙명. 안내문은 null',
            'article_number':'별도 조문 문서용 필드. 현재 전문 저장이므로 null','effective_date':'법령 시행일. 안내문은 null',
            'collected_at':'원문 수집 시각','last_checked_at':'원격 응답을 마지막으로 확인한 시각','content_hash':'해당 정규화 본문 또는 청크 text의 SHA-256',
            'raw_path':'프로젝트 루트 기준 HTML 보존 경로','parent_document_id':'상위 문서 ID. 현재 루트 문서만 있어 null',
            'acquisition_status':'문서 적재 상태. 현재 ok','metadata_json':'수집 파일과 색인 범위를 설명하는 JSON 객체',
            'table_index':'문서 안의 0부터 시작하는 표 순서','caption':'표 제목. 없으면 빈 문자열',
            'grid_json':'병합 셀을 펼친 2차원 문자열 배열','cells_json':'원형 셀의 text·row·col·rowspan·colspan·header 배열',
            'chunk_id':'문서 ID와 순번으로 만든 청크 식별자','ordinal':'문서 안의 0부터 시작하는 청크 순서','text':'검색·임베딩에 사용하는 청크 본문',
            'locator':'조문·안내 섹션·표 행 등 문서 내 위치','start_char':'documents.body의 시작 문자 인덱스',
            'end_char':'documents.body의 끝 문자 인덱스. 끝 문자는 미포함',
            'model':'임베딩 모델명','encoder_fingerprint':'모델·엔드포인트·입력 형식 등 인코더 설정 해시',
            'dimension':'벡터 차원. 현재 4096','vector_json':'L2 정규화한 실수 벡터의 JSON 배열','input_hash':'임베딩 API에 전달한 문자열의 SHA-256',
            'created_at':'해당 임베딩 생성 시각','check_id':'수집 확인 기록의 정수 식별자','checked_at':'해당 수집 시도의 확인 시각',
            'http_status':'HTTP 응답 코드. 응답 미확보 시 null','status':'ok 또는 excluded','reason':'제외 사유 코드. 성공 시 null'}
    data += ['## 테이블별 저장 내용',table(['테이블','행 수','저장 내용'],[[name,counts[name],meaning] for name,meaning in meanings.items()]),
             '문서 91개는 법령·행정규칙 64개, 정부24 안내 26개, 성남시 발급 안내 1개다. 서비스 40개에는 생계급여 신청·주거급여 신청·긴급복지 생계지원 요청·기초연금 신청이 포함된다.',
             '임베딩은 Qwen3-VL-Embedding-8B로 계산한 청크별 4,096차원 벡터다. 모델 가중치는 DB에 저장하지 않는다. BM25는 chunks.text로 실행 시 색인을 구성하고, Hybrid는 BM25·Dense의 검색 순위를 결합하므로 별도 결과 테이블을 두지 않는다.',
             '질문·정답·통제용 가상 근거·모델 응답·평가 결과는 이 DB에 저장하지 않는다. 해당 자료는 benchmark/의 JSONL과 결과 파일에 보관한다.',
             '## 테이블 연결',
             '`documents → document_services → services`는 문서와 서비스의 다대다 관계다. `chunks.document_id`와 `document_tables.document_id`는 documents를, `embeddings.chunk_id`는 chunks를 참조한다. `documents.raw_hash`로 raw_sources의 HTML을 찾는다. 이 raw_hash 연결은 논리적 연결이며 현재 DDL의 외래키 제약은 아니다.',
             '`document_catalog`는 문서에 서비스 ID·카테고리를 붙인 뷰다. 서비스 연결이 없는 문서도 LEFT JOIN으로 남으며, 문서 하나가 여러 행으로 나올 수 있다. `source_checks`는 제외된 수집 시도까지 보존하므로 모든 행이 documents와 연결되지는 않는다.',
             '## DB 설정',table(['키','값'],[[k,json.dumps(v,ensure_ascii=False)] for k,v in metadata.items()]),
             '`value`는 JSON 문자열이다. `gold_indexed=false`는 정답 자료가 검색 코퍼스에 들어가지 않았다는 뜻이다. corpus_hash는 청크 ID와 본문 해시로 계산하며 파일 전체 해시와 구분한다.']
    for name,cols in columns.items():
        data += ['### '+name+' · '+meanings.get(name,''),table(['필드','SQLite 타입','NOT NULL','PK 순서','의미'],[[x['name'],x['type'],bool(x['notnull']),x['pk'] or '—',fields[x['name']]] for x in cols])]
    data+=['## 현재 DB의 DDL','```sql\n'+';\n\n'.join(r['sql'] for r in schema)+';\n```',
           '## 검색 결과 공통 형식',
           'JSONL 한 줄은 질문 하나이며 `query_id`, `retriever`, `hits`를 갖는다. hits는 rank 오름차순이다. 각 hit는 query_id, retriever, rank, document_id, chunk_id, score, title, snippet, source_url을 갖는다. 각 방식의 점수 척도는 다르므로 점수 자체를 서로 비교하지 않는다.',
           '```json\n'+json.dumps({k:v for k,v in cases['bm25'][qs[0]['question_id']]['top_10'][0].items()},ensure_ascii=False,indent=2)+'\n```',
           '## 출처 등록부',table(['별칭','제목','종류','시행일','수집일','최종 확인일','출처'],source_rows),
           '## 수집 시도',table(['상태','건수'],checks_summary['source_checks'].items()),
           table(['별칭','HTTP','상태','사유'],[[x['alias'],x['http_status'],x['status'],x['reason']] for x in checks if x['status']!='ok'])]
    emit('02_data_specification.md',data)
    with (DATA/'civic.sqlite').open('rb') as f:
        database_hash=hashlib.file_digest(f,'sha256').hexdigest()
    examples='''SELECT service_id, name, category FROM services ORDER BY service_id;

SELECT source_type, COUNT(*) AS documents FROM documents GROUP BY source_type;

SELECT d.alias, d.title, ch.locator, ch.text
FROM chunks ch JOIN documents d USING(document_id)
WHERE d.alias = 'L가족관계의 등록 등에 관한 법률'
ORDER BY ch.ordinal LIMIT 5;

SELECT model, dimension, COUNT(*) AS vectors FROM embeddings GROUP BY model, dimension;

SELECT d.alias, r.media_type, length(r.payload) AS html_bytes
FROM documents d JOIN raw_sources r USING(raw_hash) LIMIT 5;'''
    package=['# Civic-Link SQLite 데이터 명세',
             '[civic.sqlite](civic.sqlite)는 프로젝트의 data/processed/civic.sqlite에서 내보낸 DB 복사본이다. 원문 HTML·정규화 본문·표·청크·임베딩을 포함한다.',
             f"파일 크기: {(DATA/'civic.sqlite').stat().st_size:,}바이트. SHA-256: `{database_hash}`.",
             '`raw_path`는 원본 프로젝트에서의 보존 위치를 기록한 값이다. 이 폴더만 전달해도 HTML은 raw_sources.payload에서, 검색 본문은 documents.body와 chunks.text에서 읽을 수 있다. 검색·생성 코드는 별도로 필요하다. 문서 임베딩은 저장돼 있지만 새로운 질문의 Dense 검색에는 같은 설정의 질의 임베딩이 필요하다.',
             *data[2:],
             '## 조회 예시','```sql\n'+examples+'\n```']
    (DATA/'data_specification.md').write_text('\n\n'.join(package)+'\n')
    # Benchmark: no copied citizen statements and no invented representative weights.
    benchmark=['# 민원 질문 벤치마크',
               '공식 안내와 법령에 근거를 연결한 합성 개발 질문 100건이다. 실제 시민 발화의 수집 자료가 아니며, 독립 평가용 테스트셋으로 사용하지 않는다.',
               '## 작성 목적과 말투',
               '실제 민원은 공식 명칭을 정확히 말하지 않거나, 목적·연도·관계를 생략하고 상황부터 설명할 수 있다. 정중한 질문, 일상어, 짧은 말, 오타, 긴 설명, 짜증 섞인 표현을 함께 구성했다. 불친절함과 정보 부족은 별개다. 표현이 거칠어도 의미와 근거가 충분하면 답하도록 설계했다.',
               '말투별 문항 수는 평가 조건에 맞춰 배정했다. 실제 민원 발생 비율이나 모집단의 말투 분포를 추정한 값은 아니다.',
               '## 구성',table(['기대 행동','문항 수'],manifest['by_action'].items()),
               table(['question_type','문항 수'],Counter(q['question_type'] for q in qs).items()),
               '질문 유형과 기대 행동, 문서 hop은 별도 축이다. clarification 20건은 clarify, unanswerable 16건은 abstain으로 평가한다. 서비스 범위 40개와 질문 수 100문항을 구분한다.',
               table(['말투','문항 수'],manifest['by_style'].items()),
               table(['답변 문항의 기록 경로 hop','문항 수'],sorted(manifest['answer_hops'].items())),
               '100건 모두 검색과 응답 생성을 실행한다. 검색 정답 위치 지표의 분모만 answer 64건이다. clarify 20건은 첫 질문의 의도가 확정되지 않아 검색 정답을 강요하지 않는다. abstain 16건은 일반 유보 8건과 제공 근거 전용 8건이다. 마지막 8건은 검색 기록을 생성에 사용하지 않는다.',
               '3-hop 3문항은 같은 제적등본 면제 근거를 사용하는 표현 변형이다. 학습·평가를 분리할 때는 같은 근거와 의도를 공유하는 문항을 같은 묶음으로 배정한다.',
               '## 파일',table(['파일','용도'],[['questions.jsonl','정답·근거·경로·태그·검수 상태 100건'],['queries.jsonl','검색 입력 query_id/question만 100건'],['decision_inputs.jsonl','생성 입력 질문·context_mode·제공 근거 100건'],['benchmark/canonical_queries.jsonl','같은 64건의 기준 문장 진단'],['benchmark/clarified_queries.jsonl','20건의 미리 작성한 보충 정보 적용 진단']]),
               '## 주요 JSONL 필드',table(['필드','뜻'],[['question_id','문항 고유 ID'],['question / language_style','실험 질문과 작성 말투'],['expected_action','answer / clarify / abstain'],['expected_answerability','검색 정답 위치 채점 가능 여부'],['task_track','full_corpus 또는 provided_evidence_only'],['question_type / challenge_tags','주 유형과 복수 도전 조건'],['service_ids','평가용 서비스 ID. 모델 입력에서 제외'],['evidence_requirements','필수 위치별 출처·인용·offset·허용 chunk ID'],['gold_answer / gold_rubric','잠정 기준 답안과 평가 조건'],['document_hops / gold_document_paths','기록된 외부 문서 경로의 최장 길이와 경로'],['minimum_hops_verified','false: 최단 경로 독립 검증 전'],['abstention_basis','유보의 원문 또는 범위 정책 근거'],['clarification','필요 정보·예시 되물음·가상 보충 답변·해소 후 근거'],['provided_evidence','통제 시험에서 모델이 받는 근거'],['provenance / review_status','합성 작성 경위와 pending_human_review']]),
               '## 유보와 모호성',
               '의도가 모호하면 필요한 정보를 묻고, 제공 자료만으로 확정할 수 없으면 유보한다. 조건부 설명과 필요한 되물음을 함께 한 응답은 내용 평가에서 허용할 수 있다. 단일 action 라벨 일치율만으로 응답 전체의 적절성을 판단하지 않는다.',
               'SJ-N-070(국세 납세증명 처리기간), SJ-N-076(혼인관계증명 수수료), SJ-N-083(휴업·폐업 구분)은 되묻지 않고 조건별 설명만 제공해도 충분한 답이 될 수 있어 검수 우선 대상으로 둔다. 현재 clarify 라벨은 실험의 대화 정책이며 유일한 올바른 응답 형식이 입증된 것은 아니다.',
               '제공 근거 시험은 무근거·무관한 근거·불완전한 참조·충돌·구자료·다른 관할·미확인 인용·예외 부분 누락을 각 1건 포함한다. 충돌·구자료·다른 관할·미확인 인용의 일부 문서는 가상이며 URL은 null이다. 공식 SQLite에 삽입하지 않았다.',
               '## 문항 목록',table(['ID','행동','hop','말투','질문'],[[q['question_id'],q['expected_action'],q['document_hops'],q['language_style'],q['question']] for q in qs]),
               '## 검수 상태',
               '중복 ID, 원문 인용 offset, 청크 연결은 자동 검사한다. 정답의 충분성, 대체 답안, 최소 hop, 문장의 자연스러움은 사람 검수 전이며 `review_status`는 `pending_human_review`다. 가능한 모든 질문 조건의 조합을 포함하지는 않는다.']
    emit('03_benchmark.md',benchmark)
    # Experiment tables are generated from metrics, not copied from narrative drafts.
    experiment=['# Week 2 실험 방법과 결과',
                '실행일: 2026-09-26. 데이터: 100문항, 원문 91개, 청크 6,768개. 동일 원문·청크·질문·top-k로 BM25, Dense, Hybrid를 비교했다. 생성은 각 방식의 검색 결과를 같은 프롬프트와 모델에 전달했다.',
                '## 실행 구성',table(['항목','설정'],[['BM25','k1=1.2, b=0.75; 단어 + 한글 문자 bigram'],['Dense','회사 VPN Qwen3-VL-Embedding-8B; 4096차원; L2 정규화 후 전체 cosine 검색'],['Dense 입력','청크 제목 + 줄바꿈 + 본문; 질문은 원문 그대로. 공식 기본 instruction과 system/user/assistant 템플릿'],['Hybrid','각 방식 top-100; 동일 가중치 sum(1/(60+rank)); chunk_id 중복 병합; 최종 10개'],['검색 평가','k=1,3,5,10; 답변 가능 64건'],['생성 모델','회사 Qwen3.8-Flash-Next; temperature=0; max_tokens=1600; thinking=false; JSON 출력'],['자동 평가','같은 Qwen3.8-Flash-Next; temperature=0; max_tokens=700'],['입력 차단','Gold·유형·정답 서비스·hop·기준 질문을 검색 및 생성 입력에서 제외']]),
                '임베딩은 VPN API를 사용하고 6,768개 벡터를 SQLite에 저장했다. 같은 인코더 fingerprint의 벡터를 재사용한다. 모델명·입력 형식·코퍼스·질문·출력 SHA-256을 기록하며, 서버 가중치 revision과 정밀도는 확인되지 않았다.',
                '## 실험 수',table(['실험','방식당 입력','합계'],[['첫 질문 검색',100,300],['기준 문장 검색',64,192],['보충 정보 후 검색',20,60],['첫 응답 생성',100,300],['생성 결과 자동 평가',100,300]]),
                '기준 문장 64건과 보충 후 질문 20건은 별도 독립 문항이 아니다. 명확화 실험은 미리 작성한 보충 정보를 주는 진단이며 실제 사용자와의 후속 대화를 수행한 결과가 아니다. 생성은 첫 응답을 평가한다.',
                '생성 실험은 법령 시행일 메타데이터를 보완한 동일 DB를 사용했다. 완료된 응답 300건과 자동 평가 300건을 집계했다.',
                '## 검색 지표',
                'Locator Recall은 질문별 필수 위치 중 회수한 비율이다. Hit은 하나 이상, All locators는 모든 위치를 찾은 비율이다. MRR은 최초 정답 위치의 순위 역수다. 긴 조문에 속한 청크 하나만 검색되어도 위치 회수로 계산되므로 완전한 답변 근거 확보율과 같지 않다.',
                table(['방식','k','n','Recall','Hit','All locators','MRR'],[[LABEL[r['method']],r['k'],r['n'],pct(r['recall']),pct(r['hit']),pct(r['all_evidence']),f"{r['mrr']:.4f}"] for r in retrieval_rows if r['track']=='conversational']),
                '## 원문 범위 회수',
                '보조 지표로 Gold 인용의 [start_char,end_char) 구간과 검색 청크 구간의 합집합이 겹치는 비율을 계산했다. 중복 청크 구간을 두 번 세지 않는다. 이 지표도 주장 함의 검증은 아니며, 길게 지정된 Gold 조문 전체를 요구하므로 최소 충분 근거보다 엄격할 수 있다.',
                table(['방식','인용 범위 평균 회수','모든 인용 범위 회수'],[[LABEL[m],pct(statistics.mean(r['methods'][m]['span_macro_coverage'] for r in analysis if r['expected_action']=='answer')),f"{sum(r['methods'][m]['all_gold_spans_covered'] for r in analysis if r['expected_action']=='answer')}/64"] for m in METHODS]),
                '## 문서 hop별 검색 · k=10']
    for m in METHODS:
        experiment += ['### '+LABEL[m],table(['hop','n','Recall','All locators'],[[h,v['question_count'],pct(v['metrics']['10']['recall']),pct(v['metrics']['10']['all_evidence'])] for h,v in reports[m]['conversational']['retrieval']['by_hop'].items()])]
    experiment+=['## 유형별 검색 · k=10',table(['유형','n']+[LABEL[m]+' Recall' for m in METHODS],[[t,v['question_count']]+[pct(reports[m]['conversational']['retrieval']['by_type'][t]['metrics']['10']['recall']) for m in METHODS] for t,v in reports['bm25']['conversational']['retrieval']['by_type'].items()]),
                 '## 표현·명확화 비교 · k=10',table(['방식','입력','n','Recall','All locators'],[[LABEL[m],t,r['retrieval']['overall']['question_count'],pct(r['retrieval']['overall']['metrics']['10']['recall']),pct(r['retrieval']['overall']['metrics']['10']['all_evidence'])] for m in METHODS for t,r in reports[m].items()]),
                 '표현을 바꾸었을 때 점수가 달라져도 말투 하나만의 인과 효과로 해석하지 않는다. 어휘·길이·키워드가 함께 바뀌고 같은 부모 근거를 공유한다. 현재 개발셋을 보며 파라미터나 프롬프트를 튜닝하지 않았다.',
                 '## 생성·행동 결과',
                 '행동 일치율은 answer/clarify/abstain 라벨의 일치다. 내용의 정답률이 아니다. 내용 평가는 동일 모델의 잠정 심판 결과이며 독립 평가를 대신하지 않는다. 원문에 충실한 유보가 Gold answer와 불일치할 수도 있다.']
    if generation:
        experiment += [table(['방식','실행','API 오류','형식 정상','행동 일치','Macro F1','자동 과제 충족'],[[LABEL[m],f"{g['attempted']}/100",g['api_errors'],g['schema_valid'],pct(g['overall']['action_exact_accuracy']),f"{g['action_classification']['macro_f1']:.4f}",f"{g['overall']['judge_task_success']}/100"] for m,g in generation.items()]),
                       table(['방식','인용 수','입력에 있는 출처 ID','원문 그대로 인용'],[[LABEL[m],g['citations']['total'],g['citations']['known_source_ids'],g['citations']['exact_quotes_ignoring_whitespace']] for m,g in generation.items()]),
                       '원문 인용 일치는 공백·줄바꿈을 제거한 뒤 부분 문자열로 검사했다. 어휘를 고쳐 쓴 인용은 실패로 센다. 이 검사는 인용이 질문에 적합한지 또는 모든 답변 주장을 뒷받침하는지 판정하지 않는다.']
        for m,g in generation.items():
            experiment+=['### '+LABEL[m]+' 행동 혼동행렬',table(['기대 행동','예측 answer','예측 clarify','예측 abstain'],[[a]+[g['confusion_matrix'].get(a,{}).get(b,0) for b in ['answer','clarify','abstain']] for a in ['answer','clarify','abstain']])]
    else:experiment+=['생성 결과 집계 전. 검색 결과만 확정된 중간 문서다.']
    experiment+=['## 실패 원인 판정 기준',
                 table(['분류','판정 근거와 현재 해석'],[['DATA_MISSING','필요 원문이 DB에 없는 경우. 답변 64건의 지정 원문은 모두 있어 검색 누락을 미수집으로 처리하지 않음'],['VOCABULARY_MISMATCH','같은 Gold의 기준 문장보다 일상 문장 검색이 나쁜 사례에서 어휘 민감성 가설 제시'],['EXACT_TERM_MISS','법적 근거 질문에서 지정 조문 누락. 정확한 법률명·조문 제공 여부를 함께 검토'],['SEMANTIC_CONFUSION','비슷한 서비스 질문의 오검색 후보. 관련성 판단의 독립 검수 전'],['CHUNKING_ERROR','같은 문서의 다른 조문 회수만으로 확정하지 않음. 인용 범위·경계와 분할 변경 실험 필요'],['TABLE_PARSING_ERROR','표의 셀·주석 보존 검사와 검색 누락을 구분. 검색 누락만으로 파싱 오류를 확정하지 않음'],['MULTI_HOP_REQUIRED','연결 원문이 존재하나 일부 위치가 빠진 후보. 그래프 실행 전에는 개선 가능성만 제시'],['OUT_OF_SCOPE','현재 서비스 범위를 벗어나는 요청'],['UNANSWERABLE','질문 정보 또는 제공 근거 부족, 개별 결정 불가'],['RANKING_MISS','Top-10 누락은 확인되지만 위 원인을 특정하기 어려운 경우']]),
                 '전수 100건의 세 방식별 위치 순위, 누락 근거, 관찰·가설, 생성 응답은 05_case_review.md에 수록한다. 원인 후보를 확정 원인으로 합산하지 않는다.',
                 '## 재현',
                 '프로젝트 루트에서 기존 Python 환경과 VPN을 사용한다. `CIVIC_CHAT_URL`, `CIVIC_CHAT_API_KEY`는 로컬 환경변수로 설정한다. ',
                 '```sh\npython3 scripts/benchmark.py validate --benchmark-dir benchmark\npython3 scripts/run_week2.py --benchmark-dir benchmark --stage all\npython3 scripts/report_week2.py\npython3 -m unittest discover -s tests -v\n```',
                 '실행 파일은 같은 입력·모델·프롬프트 해시에서만 재사용한다. 다른 설정 실험은 별도 출력 경로를 사용한다. 전체 원문·SQLite는 용량이 큰 로컬 산출물이므로 이 Markdown 묶음에 들어 있지 않다.',
                 '## 해석 범위',
                 '이 결과는 고정 원문과 합성 공개 개발셋의 기준선이다. 실제 민원 빈도·독립 테스트 성능·법률적 정확성·서비스 운영 성능을 추정하지 않는다. 지연 시간은 VPN·서버 상태의 영향을 받으므로 처리 성능 순위의 근거로 사용하지 않는다. ']
    answer_ids=[q['question_id'] for q in qs if q['expected_action']=='answer']
    def recall(m,qid):return cases[m][qid]['metrics']['10']['recall']
    dense_better=sum(recall('dense',i)>recall('bm25',i) for i in answer_ids)
    bm_better=sum(recall('bm25',i)>recall('dense',i) for i in answer_ids)
    fusion_loss=sum(recall('hybrid',i)<max(recall('bm25',i),recall('dense',i)) for i in answer_ids)
    representative=[('SJ-N-003','일상어로 토지 정보를 요청한다. Dense가 토지대장 안내를 회수했고 BM25는 누락했다. 어휘 차이에 대한 Dense 이득 사례다.'),
                    ('SJ-N-029','면세사업자 수입금액과 과세표준의 구분에서 BM25는 두 안내를 회수했고 Dense는 둘 다 놓쳤다. 유사 서비스 간 구분을 검토할 사례다.'),
                    ('SJ-N-026','Dense는 국세 납부내역증명 안내를 3위로 회수했다. Hybrid에서는 10위 밖으로 밀렸다. RRF가 한쪽 검색의 정답을 반드시 보존하지 않는 사례다.'),
                    ('SJ-N-033','생기부라는 약칭에서 BM25는 출력 종류를 다룬 제5조를 놓쳤다. Dense는 3위, Hybrid는 1위로 회수했다. 검색 성공과 생성의 용어 이해는 따로 검토한다.'),
                    ('SJ-N-038','BM25는 신청서류를 정한 규칙 제34조만 회수했다. 나머지 연결 조문은 빠졌고 Dense·Hybrid는 지정 위치를 모두 놓쳤다. 진입점 검색과 참조 확장을 분리해 점검해야 한다.'),
                    ('SJ-N-040','세 방식 모두 대리신청의 지정 근거를 놓쳤다. 대리수령·미지급 연금 등 인접 내용이 상위에 나왔다. 관련 단어가 있는 문서가 곧 질문의 법적 근거는 아니다.'),
                    ('SJ-N-050','무인발급 안내를 찾아도 국세청 규정 제41조가 참조하는 민원처리법 제28조·시행령 제32조까지 자동 회수되지는 않았다. 원문 제41조의 명시적 참조를 그래프 후보로 분리한다.'),
                    ('SJ-N-023','표의 휴업사실증명 행과 본인확인 주석을 별개 근거로 평가한다. 데이터에 표와 주석이 있으므로 누락 여부와 파싱 오류를 구분한다.')]
    findings=['## 결과 해석과 대표 사례',
              f'64건 중 Dense의 Recall이 BM25보다 높은 문항은 {dense_better}건, BM25가 높은 문항은 {bm_better}건이다. Hybrid가 두 단일 검색 중 더 좋은 결과보다 낮은 문항은 {fusion_loss}건이다. 평균 점수만으로 모든 유형에서 Hybrid가 우월하다고 결론 내리지 않는다.',
              '2-hop 12건의 모든 위치 회수는 BM25 0건, Dense 2건, Hybrid 1건이다. Hybrid의 전체 평균이 높아도 연결 문서를 모두 찾는 과제는 남는다. 3-hop 세 문항은 한 근거 가족의 변형이므로 별도의 독립 성능 근거로 확대하지 않는다.',
              table(['문항','BM25 Recall','Dense Recall','Hybrid Recall','관찰·해석'],[[i]+[f'{recall(m,i):.3f}' for m in METHODS]+[note] for i,note in representative]),
              '현재 원문에는 국세청 규정 제41조의 민원처리법 참조, 기초연금 시행규칙 제6조의 민법 제777조 참조가 존재한다. Week 3에서는 이 명시적 관계를 만들고, Week 4에서는 검색된 진입 노드에서만 확장한다. 정답 경로를 시작점으로 주면 검색 실패를 인위적으로 숨기게 되므로 사용하지 않는다.']
    experiment[experiment.index('## 실패 원인 판정 기준'):experiment.index('## 실패 원인 판정 기준')]=findings
    if 'bm25' in generation:
        experiment+=['## 생성 실패와 자동 평가의 오류',
                    '응답·입력 근거·자동 평가 사유를 대조한 사례 분석이다. 아래 판정은 자동 검토 결과이며 독립적인 사람 검수 전이다.',
                    table(['방식·문항','실제 관찰','의미'],[
                        ['BM25 · SJ-N-026','국세 납부내역증명이라고 답했지만 실제 인용은 국세 납세증명 안내다.','서비스명을 맞혔어도 지정 근거를 확보하지 못한 답변이다.'],
                        ['BM25 · SJ-N-040','대리신청 질문에 대리수령인의 범위를 답했다.','신청과 수령이라는 법적 행위의 혼동이다.'],
                        ['BM25 · SJ-N-074','clarify 라벨과 되물음이 있으나 연금 종류를 기초연금으로 가정하고 근거 없이 4촌 조건을 덧붙였다.','행동 라벨 일치가 올바른 내용이나 적절한 명확화를 보장하지 않는다.'],
                        ['BM25 심판 · SJ-N-033','심판은 제공 문맥에 출력 종류를 다룬 제5조가 있다고 썼다. 실제 입력에는 같은 지침의 제9조만 있다.','Gold 근거를 실제 문맥으로 혼동한 평가 오류다.'],
                        ['BM25 심판 · SJ-N-040','심판은 오류를 지적하면서도 실제로 검색되지 않은 시행규칙 제6조·민법 제777조를 제공된 근거라고 서술했다.','결론이 타당해도 평가 이유에 근거 혼동이 섞일 수 있다.']]),
                    '따라서 자동 과제 충족 수치는 참고용이다. 모델 품질을 확정하려면 human_review.csv에 검수자·일시·근거를 기록하고 생성 주장과 인용의 실제 관계를 확인해야 한다.']
    if 'hybrid' in generation:
        experiment+=['Hybrid의 SJ-N-033은 학교생활기록 작성 및 관리지침 제5조를 1위로 검색했지만 실제 응답은 근거가 없다며 유보했다. 같은 질문에서 Dense는 해당 조문을 3위로 검색한 뒤 출력 구분을 설명했다. 검색 성공을 답변 성공으로 환산할 수 없으며, 컨텍스트의 다른 문서·순서와 생성 모델의 근거 활용을 후속 실험에서 분리해야 한다.']
    emit('04_experiments.md',experiment)
    review=['# 질문 100건의 검색·생성 비교',
            '각 문항은 동일 질문에 대한 BM25·Dense·Hybrid 결과다. 위치 회수는 자동 측정, 실패 원인은 관찰에 근거한 가설이다. Gold는 사람 검수 전이며 생성 응답은 수정하지 않은 실제 출력이다. 인용문 일치와 내용의 타당성은 별개다.']
    for q in qs:
        qid=q['question_id'];r=byid[qid]
        review += [f'## {qid}',q['question'],f"기대 행동: {q['expected_action']}. 기록 경로 hop: {q['document_hops'] if q['document_hops'] is not None else '해당 없음'}. 유형: {q['question_type']}.",
                   '기준 답안: '+q['gold_answer'],
                   table(['방식','검색 결과','최상위 제목','첫 근거 순위·인용 범위'],[[LABEL[m],STATUS[r['methods'][m]['retrieval_status']],cases[m][qid]['top_10'][0]['title'] if cases[m][qid]['top_10'] else '없음','; '.join(e['source_alias']+' '+e['locator']+f" = {e['first_rank'] or '10위 밖'} / {pct(e['gold_span_coverage'])}" for e in r['methods'][m]['evidence']) or '채점 제외'] for m in METHODS])]
        for m in METHODS:
            rm=r['methods'][m];review += [LABEL[m]+' 관찰: '+' '.join(rm['observations'])+(' 원인 후보: '+', '.join(rm['cause_hypotheses'])+'.' if rm['cause_hypotheses'] else '')]
        if q['evidence_requirements']:
            review+=['근거: '+'; '.join(f"[{e['source_alias']} {e['locator']}]({e['source_url']})" for e in q['evidence_requirements'])]
        if q['clarification']:review+=['명확화 진단의 보충 정보: '+q['clarification']['simulated_user_response']+' / 해소된 질문: '+q['clarification']['resolved_question']]
        if q['task_track']=='provided_evidence_only':review+=['제공 근거 조건: '+q['evidence_condition']+'. 공식 코퍼스에 추가하지 않은 통제 시험이다.']
        for m in METHODS:
            if m not in gencases:continue
            g=gencases[m][qid];j=g['automatic_judge'] or {};raw=predictions[m][qid].get('output') or {}
            review += ['### '+LABEL[m]+' 실제 응답',f"행동: {g['predicted_action']}. 행동 라벨 일치: {g['action_exact_match']}. 잠정 자동 과제 충족: {j.get('task_success')}.",g['response'] or '응답 없음']
            if g['follow_up_question']:review+=['되물음: '+g['follow_up_question']]
            if raw.get('citations'):review+=['인용: '+'; '.join(c['source_id']+' — '+c['quote'] for c in raw['citations'])]
            review+=['자동 평가 사유: '+str(j.get('reason','평가 없음'))]
    emit('05_case_review.md',review)
    summary=['# Civic-Link Week 2 결과',
             '공식 민원 안내와 법령을 대상으로 BM25·Dense·Hybrid(RRF)의 검색 결과와 응답 실패를 비교했다. 서비스 40개, 원문 91개, 청크 6,768개, 합성 질문 100건을 사용했다.',
             '## 구현 및 평가',
             table(['항목','결과'],[
                 ['서비스 범위','10개 카테고리·40개 서비스, 기록된 외부 문서 참조 깊이 1–3'],
                 ['SQLite','원문 91개·표 53개·청크와 임베딩 각 6,768개'],
                 ['검색','BM25·Dense·Hybrid 각각 100문항 검색'],
                 ['보조 진단','방식별 기준 문장 64건·보충 정보 후 질문 20건'],
                 ['응답 및 자동 평가','방식별 100건씩, 총 응답 300건·자동 평가 300건'],
                 ['미완료 검수','Gold Evidence의 충분성·대체 답안·최소 hop의 독립 검수']]),
             '검색 지표는 정답 위치가 지정된 64건을 대상으로 계산한다. 나머지 36건도 검색·생성을 실행하며, 되물음과 유보 행동으로 평가한다.',
             '## 검색 결과',
             table(['방식','Recall@10','All locators@10','MRR@10'],[[LABEL[m],pct(reports[m]['conversational']['retrieval']['overall']['metrics']['10']['recall']),pct(reports[m]['conversational']['retrieval']['overall']['metrics']['10']['all_evidence']),f"{reports[m]['conversational']['retrieval']['overall']['metrics']['10']['mrr']:.4f}"] for m in METHODS]),
             '## 문서 및 데이터',
             table(['파일','내용'],[
                 ['[01_scope_audit.md](01_scope_audit.md)','서비스 40개·출처·문서 참조 경로'],
                 ['[02_data_specification.md](02_data_specification.md)','DB 스키마·식별자·날짜·출처 등록부'],
                 ['[03_benchmark.md](03_benchmark.md)','질문 설계·필드·100문항 목록'],
                 ['[04_experiments.md](04_experiments.md)','실험 설정·검색 및 생성 결과·실패 분석'],
                 ['[05_case_review.md](05_case_review.md)','문항별 세 방식의 검색 결과와 실제 응답'],
                 ['questions.jsonl','기준 답안·근거를 포함한 100문항'],
                 ['queries.jsonl','정답 없는 검색 입력'],
                 ['decision_inputs.jsonl','정답 없는 생성 입력과 통제 근거'],
                 ['human_review.csv','사람 검수표'],
                 ['manifest.json','파일 해시와 실행 수']]),
             'Markdown 문서 6개와 첨부 데이터를 ZIP으로 제공한다. SQLite 복사본과 상세 데이터 명세는 docs/reports/week2/data/에 별도로 제공하며 ZIP에는 포함하지 않는다.',
             '## 해석 범위',
             '합성 개발셋의 결과이며 실제 민원 빈도나 독립 테스트 성능을 추정하지 않는다. 자동 내용 평가자는 생성 모델과 같아 평가 오류가 남아 있다. 기록된 문서 경로의 길이를 전체 그래프의 최단 경로로 해석하지 않는다.',
             '후속 실험은 명시적 참조 관계의 Graph DB 구축과 검색 결과의 그래프 확장이다. 현재 결과는 텍스트 검색 기준선이며 그래프의 개선 효과는 측정하지 않았다.']
    emit('README.md',summary)
    write_json(OUT/'manifest.json',{'created_at':now(),'question_count':len(qs),'corpus_hash':manifest['corpus_hash'],
                                  'benchmark_manifest_sha256':digest((BENCH/'manifest.json').read_bytes()),
                                  'retrieval_runs':len(retrieval_rows)//4,'generation_runs':len(generation),'human_review':'pending',
                                  'files':{p.name:digest(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json'}})
    archive=OUT.parent/'civic-link-week2.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUT.iterdir()):z.write(p,p.name)
    print(json.dumps({'submission_files':len(list(OUT.iterdir())),'retrieval_runs':len(retrieval_rows)//4,'generation_runs':len(generation),'archive_bytes':archive.stat().st_size},ensure_ascii=False))

if __name__=='__main__':main()
