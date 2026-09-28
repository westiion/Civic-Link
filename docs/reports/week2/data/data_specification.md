# Civic-Link SQLite 데이터 명세

[civic.sqlite](civic.sqlite)는 프로젝트의 data/processed/civic.sqlite에서 내보낸 DB 복사본이다. 원문 HTML·정규화 본문·표·청크·임베딩을 포함한다.

파일 크기: 689,188,864바이트. SHA-256: `324b4c9f735339b942007ae14835f538680efd74cd2525319a562369bcf00f56`.

`raw_path`는 원본 프로젝트에서의 보존 위치를 기록한 값이다. 이 폴더만 전달해도 HTML은 raw_sources.payload에서, 검색 본문은 documents.body와 chunks.text에서 읽을 수 있다. 검색·생성 코드는 별도로 필요하다. 문서 임베딩은 저장돼 있지만 새로운 질문의 Dense 검색에는 같은 설정의 질의 임베딩이 필요하다.

## 적재 현황

| 테이블 | 행 수 |
| --- | --- |
| chunks | 6768 |
| document_services | 191 |
| document_tables | 53 |
| documents | 91 |
| embeddings | 6768 |
| metadata | 5 |
| raw_sources | 91 |
| services | 40 |
| source_checks | 112 |

무결성 검사: ok. 외래키 오류: 0건. Qwen3-VL-Embedding-8B 4,096차원 벡터 6,768개.

## 날짜와 자료 기준

`effective_date`는 법령 시행일, `collected_at`은 원문 수집 시각, `last_checked_at`은 원격 응답을 마지막으로 확인한 시각이다. 법령은 2026-09-24 기준 시행본을 고정했다. 성남시 안내는 2026-09-26 수집본이며 과거 상태라고 소급하지 않는다. 미기재 시행일은 null이다. 수집 실패 시 기존 문서의 수집일을 갱신하지 않는다.

법령·행정규칙 64건은 모두 시행일을 갖는다. 안내 27건은 적용할 법정 시행일이 없어 null이다. 원문 머리말과 날짜 필드의 대조 기록은 `benchmark/results/source_date_repairs.json`에 저장한다.

| 필드 | null 문서 수 | 해석 |
| --- | --- | --- |
| effective_date | 27 | 일반 안내 또는 시행일 미기재. 수집일로 대체하지 않음 |
| law_name | 27 | 비법령 문서 |
| article_number | 91 | 문서 행은 전문 단위. 조문은 chunks.locator |
| parent_document_id | 91 | 현재 문서는 원문 스냅샷의 루트 |

## 원문과 검색 단위

`raw_sources.payload`에 HTML 전체 바이트를 보존한다. `documents.body`는 검색용 정규화 본문이다. 법령은 조문, 안내는 섹션, 표는 행과 주석으로 구분한다. 긴 단위는 최대 1,200자, 겹침 150자로 분할하며 가능하면 줄바꿈에서 자른다. offset은 정규화 본문의 Python 문자 인덱스로 `[start_char,end_char)`를 사용한다.

법령의 첫 부칙 이후와 별표·서식 다운로드 영역은 raw에 보존하지만 본문 검색에는 포함하지 않는다. 양식 번호를 설명하는 조문을 찾는 것과 첨부 양식 자체를 검색하는 것은 다르다. 표는 rowspan/colspan 원형 셀과 펼친 grid를 JSON으로 함께 저장한다. 표시용 300자 snippet은 생성 입력을 자르는 길이가 아니다.

## 필드와 키

`document_catalog` 뷰로 document_id, service_id, category를 함께 조회한다. 문서에 service_id 하나를 강제로 넣지 않는다. raw_hash/content_hash/input_hash는 각각 HTML 바이트·정규화 본문·임베딩 입력을 구분한다. 임베딩 캐시는 chunk_id/model/encoder_fingerprint의 복합키이며 모델·입력 형식·prefix·endpoint 조건이 바뀌면 재사용하지 않는다.

| 필드 | 의미 |
| --- | --- |
| document_id | URL과 본문 SHA-256으로 만든 문서 ID |
| service_id / category | 서비스 ID와 카테고리. document_services를 통해 연결 |
| alias | 수집 출처 별칭. G는 안내, L은 법령, C01은 성남시 표 |
| title / body | 문서 제목과 정규화한 검색 본문 |
| source_url / source_type | 원본 URL과 law/service_guide/municipal_table |
| organization / law_name | 수집 출처 기관과 법령·행정규칙명 |
| article_number / parent_document_id | 별도 조문 문서·부모 관계를 저장할 확장 필드. 현재 전문 저장 구조에서는 null |
| effective_date / collected_at / last_checked_at | 시행일 / 원문 수집 시각 / 원격 확인 시각 |
| content_hash / raw_hash | 본문 UTF-8 SHA-256 / HTML 원본 바이트 SHA-256 |
| raw_path | 프로젝트 루트 기준 원문 보존 경로 |
| acquisition_status / metadata_json | 수집 성공 상태와 수집 파일·색인 범위 등 보조 정보 |
| chunk_id / ordinal / locator | 청크 ID / 문서 안의 0부터 시작하는 순서 / 조문·섹션·표 위치 |
| start_char / end_char | 정규화 본문 안의 문자 구간. 끝 위치는 미포함 |
| grid_json / cells_json | 행·열로 펼친 표 / 원형 셀과 rowspan·colspan |
| dimension / vector_json | 벡터 차원 / L2 정규화 벡터 배열 |
| encoder_fingerprint / input_hash | 인코더 설정 해시 / 해당 청크 임베딩 입력 해시 |
| checked_at / http_status / reason | 수집 시도 시각 / HTTP 응답 / 제외 또는 실패 사유 |

## 테이블별 저장 내용

| 테이블 | 행 수 | 저장 내용 |
| --- | --- | --- |
| metadata | 5 | 코퍼스 설정과 해시 |
| raw_sources | 91 | 원문 HTML 바이트 |
| services | 40 | 서비스·분류·진입 문서·관측 hop |
| documents | 91 | 수집 원문과 정규화 본문·출처·날짜 |
| document_services | 191 | 문서와 서비스 연결 |
| source_checks | 112 | 수집 시도와 실패 이력 |
| chunks | 6768 | 검색 단위·위치·본문 해시 |
| document_tables | 53 | 표의 원형 셀과 펼친 grid |
| embeddings | 6768 | 정규화 벡터와 인코더 식별자 |

문서 91개는 법령·행정규칙 64개, 정부24 안내 26개, 성남시 발급 안내 1개다. 서비스 40개에는 생계급여 신청·주거급여 신청·긴급복지 생계지원 요청·기초연금 신청이 포함된다.

임베딩은 Qwen3-VL-Embedding-8B로 계산한 청크별 4,096차원 벡터다. 모델 가중치는 DB에 저장하지 않는다. BM25는 chunks.text로 실행 시 색인을 구성하고, Hybrid는 BM25·Dense의 검색 순위를 결합하므로 별도 결과 테이블을 두지 않는다.

질문·정답·통제용 가상 근거·모델 응답·평가 결과는 이 DB에 저장하지 않는다. 해당 자료는 benchmark/의 JSONL과 결과 파일에 보관한다.

## 테이블 연결

`documents → document_services → services`는 문서와 서비스의 다대다 관계다. `chunks.document_id`와 `document_tables.document_id`는 documents를, `embeddings.chunk_id`는 chunks를 참조한다. `documents.raw_hash`로 raw_sources의 HTML을 찾는다. 이 raw_hash 연결은 논리적 연결이며 현재 DDL의 외래키 제약은 아니다.

`document_catalog`는 문서에 서비스 ID·카테고리를 붙인 뷰다. 서비스 연결이 없는 문서도 LEFT JOIN으로 남으며, 문서 하나가 여러 행으로 나올 수 있다. `source_checks`는 제외된 수집 시도까지 보존하므로 모든 행이 documents와 연결되지는 않는다.

## DB 설정

| 키 | 값 |
| --- | --- |
| as_of_date | "2026-09-24" |
| chunking | "article/section; 1200 chars; 150 overlap" |
| corpus_hash | "e55238fddf3882dd3dab97cce5faa8c6b136f2dd89934cc3ff2f445abda59863" |
| created_at | "2026-09-26T10:36:50.199040+00:00" |
| gold_indexed | false |

`value`는 JSON 문자열이다. `gold_indexed=false`는 정답 자료가 검색 코퍼스에 들어가지 않았다는 뜻이다. corpus_hash는 청크 ID와 본문 해시로 계산하며 파일 전체 해시와 구분한다.

### chunks · 검색 단위·위치·본문 해시

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| chunk_id | TEXT | False | 1 | 문서 ID와 순번으로 만든 청크 식별자 |
| document_id | TEXT | True | — | 원본 URL과 정규화 본문 해시로 만든 DOC- 식별자 |
| ordinal | INTEGER | True | — | 문서 안의 0부터 시작하는 청크 순서 |
| title | TEXT | True | — | 문서 제목 또는 문서 제목과 청크 위치 |
| text | TEXT | True | — | 검색·임베딩에 사용하는 청크 본문 |
| locator | TEXT | True | — | 조문·안내 섹션·표 행 등 문서 내 위치 |
| start_char | INTEGER | True | — | documents.body의 시작 문자 인덱스 |
| end_char | INTEGER | True | — | documents.body의 끝 문자 인덱스. 끝 문자는 미포함 |
| content_hash | TEXT | True | — | 해당 정규화 본문 또는 청크 text의 SHA-256 |

### document_services · 문서와 서비스 연결

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| document_id | TEXT | False | 1 | 원본 URL과 정규화 본문 해시로 만든 DOC- 식별자 |
| service_id | TEXT | False | 2 | 서비스 식별자. SJ-SVC-001부터 SJ-SVC-040까지 |

### document_tables · 표의 원형 셀과 펼친 grid

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| document_id | TEXT | False | 1 | 원본 URL과 정규화 본문 해시로 만든 DOC- 식별자 |
| table_index | INTEGER | False | 2 | 문서 안의 0부터 시작하는 표 순서 |
| caption | TEXT | False | — | 표 제목. 없으면 빈 문자열 |
| grid_json | TEXT | False | — | 병합 셀을 펼친 2차원 문자열 배열 |
| cells_json | TEXT | False | — | 원형 셀의 text·row·col·rowspan·colspan·header 배열 |

### documents · 수집 원문과 정규화 본문·출처·날짜

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| document_id | TEXT | False | 1 | 원본 URL과 정규화 본문 해시로 만든 DOC- 식별자 |
| alias | TEXT | True | — | 출처 별칭. G 안내문, L 법령, C01 성남시 표 |
| title | TEXT | True | — | 문서 제목 또는 문서 제목과 청크 위치 |
| body | TEXT | True | — | 정규화한 문서 검색 본문 |
| source_url | TEXT | True | — | 공식 출처 URL |
| source_type | TEXT | True | — | law, service_guide, municipal_table 중 하나 |
| organization | TEXT | False | — | 출처 기관 |
| law_name | TEXT | False | — | 법령·행정규칙명. 안내문은 null |
| article_number | TEXT | False | — | 별도 조문 문서용 필드. 현재 전문 저장이므로 null |
| effective_date | TEXT | False | — | 법령 시행일. 안내문은 null |
| collected_at | TEXT | True | — | 원문 수집 시각 |
| last_checked_at | TEXT | True | — | 원격 응답을 마지막으로 확인한 시각 |
| content_hash | TEXT | True | — | 해당 정규화 본문 또는 청크 text의 SHA-256 |
| raw_hash | TEXT | True | — | 원문 HTML 바이트의 SHA-256 |
| raw_path | TEXT | True | — | 프로젝트 루트 기준 HTML 보존 경로 |
| parent_document_id | TEXT | False | — | 상위 문서 ID. 현재 루트 문서만 있어 null |
| acquisition_status | TEXT | True | — | 문서 적재 상태. 현재 ok |
| metadata_json | TEXT | True | — | 수집 파일과 색인 범위를 설명하는 JSON 객체 |

### embeddings · 정규화 벡터와 인코더 식별자

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| chunk_id | TEXT | False | 1 | 문서 ID와 순번으로 만든 청크 식별자 |
| model | TEXT | False | 2 | 임베딩 모델명 |
| encoder_fingerprint | TEXT | False | 3 | 모델·엔드포인트·입력 형식 등 인코더 설정 해시 |
| dimension | INTEGER | True | — | 벡터 차원. 현재 4096 |
| vector_json | TEXT | True | — | L2 정규화한 실수 벡터의 JSON 배열 |
| input_hash | TEXT | True | — | 임베딩 API에 전달한 문자열의 SHA-256 |
| created_at | TEXT | True | — | 해당 임베딩 생성 시각 |

### metadata · 코퍼스 설정과 해시

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| key | TEXT | False | 1 | DB 설정 이름 |
| value | TEXT | True | — | JSON으로 직렬화한 설정 값 |

### raw_sources · 원문 HTML 바이트

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| raw_hash | TEXT | False | 1 | 원문 HTML 바이트의 SHA-256 |
| media_type | TEXT | True | — | 원문 MIME 타입. 현재 text/html |
| payload | BLOB | True | — | 수집한 HTML 전체 바이트 |

### services · 서비스·분류·진입 문서·관측 hop

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| service_id | TEXT | False | 1 | 서비스 식별자. SJ-SVC-001부터 SJ-SVC-040까지 |
| name | TEXT | True | — | 서비스명 |
| category | TEXT | True | — | 서비스 카테고리 |
| entry_source_id | TEXT | False | — | scope.md 출처 목록의 D01–D32 키 |
| max_document_hops | INTEGER | True | — | 서비스 감사에서 기록한 최대 외부 문서 참조 깊이 |
| audit_classification | TEXT | True | — | 서비스 감사 분류 |

### source_checks · 수집 시도와 실패 이력

| 필드 | SQLite 타입 | NOT NULL | PK 순서 | 의미 |
| --- | --- | --- | --- | --- |
| check_id | INTEGER | False | 1 | 수집 확인 기록의 정수 식별자 |
| alias | TEXT | False | — | 출처 별칭. G 안내문, L 법령, C01 성남시 표 |
| source_url | TEXT | False | — | 공식 출처 URL |
| checked_at | TEXT | False | — | 해당 수집 시도의 확인 시각 |
| http_status | INTEGER | False | — | HTTP 응답 코드. 응답 미확보 시 null |
| status | TEXT | False | — | ok 또는 excluded |
| reason | TEXT | False | — | 제외 사유 코드. 성공 시 null |
| raw_hash | TEXT | False | — | 원문 HTML 바이트의 SHA-256 |

## 현재 DB의 DDL

```sql
CREATE INDEX chunks_document ON chunks(document_id);

CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(document_id),ordinal INTEGER NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,locator TEXT NOT NULL,start_char INTEGER NOT NULL,end_char INTEGER NOT NULL,content_hash TEXT NOT NULL,UNIQUE(document_id,ordinal),CHECK(end_char>start_char));

CREATE TABLE document_services(document_id TEXT REFERENCES documents(document_id),service_id TEXT REFERENCES services(service_id),PRIMARY KEY(document_id,service_id));

CREATE TABLE document_tables(document_id TEXT REFERENCES documents(document_id),table_index INTEGER,caption TEXT,grid_json TEXT,cells_json TEXT,PRIMARY KEY(document_id,table_index));

CREATE TABLE documents(document_id TEXT PRIMARY KEY,alias TEXT UNIQUE NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,source_url TEXT NOT NULL,source_type TEXT NOT NULL,organization TEXT,law_name TEXT,article_number TEXT,effective_date TEXT,collected_at TEXT NOT NULL,last_checked_at TEXT NOT NULL,content_hash TEXT NOT NULL,raw_hash TEXT NOT NULL,raw_path TEXT NOT NULL,parent_document_id TEXT REFERENCES documents(document_id),acquisition_status TEXT NOT NULL,metadata_json TEXT NOT NULL);

CREATE TABLE embeddings(chunk_id TEXT REFERENCES chunks(chunk_id),model TEXT,encoder_fingerprint TEXT,dimension INTEGER NOT NULL,vector_json TEXT NOT NULL,input_hash TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(chunk_id,model,encoder_fingerprint));

CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);

CREATE TABLE raw_sources(raw_hash TEXT PRIMARY KEY,media_type TEXT NOT NULL,payload BLOB NOT NULL);

CREATE TABLE services(service_id TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT NOT NULL,entry_source_id TEXT,max_document_hops INTEGER NOT NULL,audit_classification TEXT NOT NULL);

CREATE TABLE source_checks(check_id INTEGER PRIMARY KEY,alias TEXT,source_url TEXT,checked_at TEXT,http_status INTEGER,status TEXT,reason TEXT,raw_hash TEXT);

CREATE VIEW document_catalog AS SELECT d.*,s.service_id,s.category FROM documents d LEFT JOIN document_services ds USING(document_id) LEFT JOIN services s USING(service_id);
```

## 검색 결과 공통 형식

JSONL 한 줄은 질문 하나이며 `query_id`, `retriever`, `hits`를 갖는다. hits는 rank 오름차순이다. 각 hit는 query_id, retriever, rank, document_id, chunk_id, score, title, snippet, source_url을 갖는다. 각 방식의 점수 척도는 다르므로 점수 자체를 서로 비교하지 않는다.

```json
{
  "query_id": "SJ-N-001",
  "retriever": "bm25",
  "rank": 1,
  "document_id": "DOC-39d8d4ed529804eda5dc",
  "chunk_id": "DOC-39d8d4ed529804eda5dc-C0005",
  "score": 42.917374448550504,
  "title": "주민등록표 등본(초본) 발급 / 부가정보",
  "snippet": "부가정보\n근거법령\n주민등록법\n(제29조)\n주민등록법 시행령\n(제47조)\n주민등록법 시행규칙\n(제13조 ,제15조)\n제도를 담당하는 기관 :\n행정안전부\n주민과\n위 담당부서와 전화번호는 이 민원의 제도를 담당하고 있는 (중앙)행정기관입니다.\n개별 민원에 대한 문의 사항은 접수·처리기관(관할처리기관)과 연락하시기 바랍니다.\n자주묻는 질문\n주민등록표등(초)본은 무료인가요?\n주민등록표등본 발급물을 팩스나 파일로 받을 수 있나요?\n정보 변경 내역\n최근 내용 변경일 :\n2026-09-02\n최근 내용 확인일 :\n2026-09-02",
  "source_url": "https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000015&HighCtgCD=A01010001&Mcode=1020&tp_seq=01"
}
```

## 출처 등록부

| 별칭 | 제목 | 종류 | 시행일 | 수집일 | 최종 확인일 | 출처 |
| --- | --- | --- | --- | --- | --- | --- |
| C01 | 성남시 무인민원발급 — 발급 민원과 본인확인 | municipal_table | — | 2026-09-26T10:16:34.445130+00:00 | 2026-09-26T10:16:34.445130+00:00 | [원문](https://www.seongnam.go.kr/cn020403) |
| G004 | 주민등록표 등본(초본) 발급 | service_guide | — | 2026-09-23T14:53:54.622324+00:00 | 2026-09-23T14:53:54.622324+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000015&HighCtgCD=A01010001&Mcode=1020&tp_seq=01) |
| G006 | 토지(임야)대장 등본 발급(열람) | service_guide | — | 2026-09-23T14:53:54.693465+00:00 | 2026-09-23T14:53:54.693465+00:00 | [원문](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000026&HighCtgCD=A02001001&Mcode=10207&tp_seq=01) |
| G010 | 개별공시지가 확인 | service_guide | — | 2026-09-23T14:53:54.973858+00:00 | 2026-09-23T14:53:54.973858+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000012) |
| G015 | 제적부의 등본(초본) 발급 | service_guide | — | 2026-09-23T14:53:55.243964+00:00 | 2026-09-23T14:53:55.243964+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12700000044) |
| G016 | 국민기초생활수급자 증명서 발급 | service_guide | — | 2026-09-23T14:53:55.272562+00:00 | 2026-09-23T14:53:55.272562+00:00 | [원문](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=14600000280&Mcode=10020) |
| G017 | 장애인증명서 발급 | service_guide | — | 2026-09-23T14:53:55.480204+00:00 | 2026-09-23T14:53:55.480204+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=14600000273&HighCtgCD=A05001) |
| G018 | 한부모가족 증명서 발급 | service_guide | — | 2026-09-23T14:53:55.531124+00:00 | 2026-09-23T14:53:55.531124+00:00 | [원문](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=10601000001) |
| G021 | 지방세 세목별 과세증명서 발급 | service_guide | — | 2026-09-23T14:53:55.827422+00:00 | 2026-09-23T14:53:55.827422+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000084) |
| G022 | 지방세 납세증명서 발급 | service_guide | — | 2026-09-23T14:53:56.122253+00:00 | 2026-09-23T14:53:56.122253+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13100000056) |
| G023 | 사업자등록증명 발급 | service_guide | — | 2026-09-23T14:53:56.222549+00:00 | 2026-09-23T14:53:56.222549+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000016) |
| G024 | 휴업사실증명 발급 | service_guide | — | 2026-09-23T14:53:56.418661+00:00 | 2026-09-23T14:53:56.418661+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000017) |
| G025 | 폐업사실증명 발급 | service_guide | — | 2026-09-23T14:53:56.450662+00:00 | 2026-09-23T14:53:56.450662+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000019) |
| G026 | 납세증명서 발급 | service_guide | — | 2026-09-23T14:53:56.510794+00:00 | 2026-09-23T14:53:56.510794+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000011) |
| G027 | 국세 납부내역증명 발급 | service_guide | — | 2026-09-23T14:53:56.529552+00:00 | 2026-09-23T14:53:56.529552+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000018) |
| G030 | 부가가치세과세표준증명 발급 | service_guide | — | 2026-09-23T14:53:56.781341+00:00 | 2026-09-23T14:53:56.781341+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000331) |
| G031 | 부가가치세면세사업자수입금액증명 발급 | service_guide | — | 2026-09-23T14:53:56.787144+00:00 | 2026-09-23T14:53:56.787144+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000329&HighCtgCD=) |
| G032 | 유치원 및 초중등학교 졸업(예정)증명 | service_guide | — | 2026-09-23T14:53:56.795268+00:00 | 2026-09-23T14:53:56.795268+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13410000020&HighCtgCD=A04001%3BA04007&tp_seq=01) |
| G033 | 중등학교 성적증명 | service_guide | — | 2026-09-23T14:53:56.837679+00:00 | 2026-09-23T14:53:56.837679+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13410000016&tp_seq=02) |
| G034 | 유치원 및 초중등학교 학교(유치원)생활기록부 증명 | service_guide | — | 2026-09-23T14:53:57.058368+00:00 | 2026-09-23T14:53:57.058368+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13410000019) |
| G035 | 검정고시 합격증명 | service_guide | — | 2026-09-23T14:53:57.076284+00:00 | 2026-09-23T14:53:57.076284+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13404000021&HighCtgCD=A04001&Mcode=10020&srhQuery=2022&tp_seq=02) |
| G036 | 병적증명서 발급 | service_guide | — | 2026-09-23T14:53:57.107589+00:00 | 2026-09-23T14:53:57.107589+00:00 | [원문](https://m.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13000000016) |
| G037 | 농지대장 등본발급 | service_guide | — | 2026-09-23T14:53:57.113619+00:00 | 2026-09-23T14:53:57.113619+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=13800000014) |
| G043 | 토지이용계획확인신청 | service_guide | — | 2026-09-23T15:08:47.771004+00:00 | 2026-09-23T15:08:47.771004+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000013) |
| G044 | 소득금액증명 발급 | service_guide | — | 2026-09-23T15:08:48.071849+00:00 | 2026-09-23T15:08:48.071849+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000021) |
| G045 | 건축물대장 등본(초본) 발급(열람) | service_guide | — | 2026-09-23T15:18:38.085215+00:00 | 2026-09-23T15:18:38.085215+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000098) |
| G047 | 자동차 등록원부등본(초본) 발급(열람) | service_guide | — | 2026-09-23T15:22:33.103940+00:00 | 2026-09-23T15:22:33.103940+00:00 | [원문](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=15000000334) |
| L2026년 한부모가족 지원대상자의 범위 | 2026년 한부모가족 지원대상자의 범위 | law | 2026-01-01 | 2026-09-23T15:33:53.855815+00:00 | 2026-09-23T15:33:53.855815+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000270856&admRulId=2045426&joTpYn=N&languageType=KO&chrClsCd=010202) |
| L가족관계의 등록 등에 관한 규칙 | 가족관계의 등록 등에 관한 규칙 | law | 2025-07-19 | 2026-09-23T15:04:59.276581+00:00 | 2026-09-23T15:04:59.276581+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=272659&efYd=20250719&chrClsCd=010202) |
| L가족관계의 등록 등에 관한 법률 | 가족관계의 등록 등에 관한 법률 | law | 2024-12-27 | 2026-09-23T15:04:59.205147+00:00 | 2026-09-23T15:04:59.205147+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=257203&efYd=20241227&chrClsCd=010202) |
| L건축물대장의 기재 및 관리 등에 관한 규칙 | 건축물대장의 기재 및 관리 등에 관한 규칙 | law | 2025-07-31 | 2026-09-23T15:04:56.380796+00:00 | 2026-09-23T15:04:56.380796+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=273103&efYd=20250731&chrClsCd=010202) |
| L건축법 | 건축법 | law | 2026-02-27 | 2026-09-23T15:48:39.075545+00:00 | 2026-09-23T15:48:39.075545+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=273437&efYd=20260227&chrClsCd=010202) |
| L건축법 시행령 | 건축법 시행령 | law | 2026-09-18 | 2026-09-23T15:04:57.271129+00:00 | 2026-09-23T15:04:57.271129+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=288849&efYd=20260918&chrClsCd=010202) |
| L공간정보의 구축 및 관리 등에 관한 법률 | 공간정보의 구축 및 관리 등에 관한 법률 | law | 2026-07-01 | 2026-09-23T15:04:54.909731+00:00 | 2026-09-23T15:04:54.909731+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284007&efYd=20260701&chrClsCd=010202) |
| L공간정보의 구축 및 관리 등에 관한 법률 시행규칙 | 공간정보의 구축 및 관리 등에 관한 법률 시행규칙 | law | 2026-01-02 | 2026-09-23T15:04:55.919787+00:00 | 2026-09-23T15:04:55.919787+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=282377&efYd=20260102&chrClsCd=010202) |
| L국립학교의 각종 증명 발급 등에 관한 규칙 | 국립학교의 각종 증명 발급 등에 관한 규칙 | law | 2023-04-12 | 2026-09-23T15:05:04.174885+00:00 | 2026-09-23T15:05:04.174885+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=249913&efYd=20230412&chrClsCd=010202) |
| L국민기초생활 보장법 | 국민기초생활 보장법 | law | 2025-10-01 | 2026-09-23T15:04:59.821442+00:00 | 2026-09-23T15:04:59.821442+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=276653&efYd=20251001&chrClsCd=010202) |
| L국민기초생활 보장법 시행규칙 | 국민기초생활 보장법 시행규칙 | law | 2025-03-21 | 2026-09-23T15:05:00.074501+00:00 | 2026-09-23T15:05:00.074501+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=269175&efYd=20250321&chrClsCd=010202) |
| L국민기초생활 보장법 시행령 | 국민기초생활 보장법 시행령 | law | 2026-01-02 | 2026-09-23T15:50:23.641715+00:00 | 2026-09-23T15:50:23.641715+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=281585&efYd=20260102&chrClsCd=010202) |
| L국세징수법 | 국세징수법 | law | 2026-06-02 | 2026-09-23T15:05:02.688449+00:00 | 2026-09-23T15:05:02.688449+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=286427&efYd=20260602&chrClsCd=010202) |
| L국세징수법 시행규칙 | 국세징수법 시행규칙 | law | 2026-03-20 | 2026-09-23T15:05:03.674890+00:00 | 2026-09-23T15:05:03.674890+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284983&efYd=20260320&chrClsCd=010202) |
| L국세징수법 시행령 | 국세징수법 시행령 | law | 2026-02-27 | 2026-09-23T15:05:03.203152+00:00 | 2026-09-23T15:05:03.203152+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=283487&efYd=20260227&chrClsCd=010202) |
| L국세청민원사무처리규정 | 국세청민원사무처리규정 | law | 2025-08-12 | 2026-09-26T10:23:42.727656+00:00 | 2026-09-26T10:23:42.727656+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000263130&joTpYn=N&languageType=KO&chrClsCd=010202) |
| L기초연금법 | 기초연금법 | law | 2025-10-01 | 2026-09-23T15:08:43.197340+00:00 | 2026-09-23T15:08:43.197340+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=276657&efYd=20251001&chrClsCd=010202) |
| L기초연금법 시행규칙 | 기초연금법 시행규칙 | law | 2026-07-30 | 2026-09-23T15:50:24.574920+00:00 | 2026-09-23T15:50:24.574920+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=287361&efYd=20260730&chrClsCd=010202) |
| L기초연금법 시행령 | 기초연금법 시행령 | law | 2026-07-30 | 2026-09-23T15:50:23.642198+00:00 | 2026-09-23T15:50:23.642198+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=286645&efYd=20260730&chrClsCd=010202) |
| L긴급복지지원법 | 긴급복지지원법 | law | 2025-10-23 | 2026-09-23T15:08:45.435314+00:00 | 2026-09-23T15:08:45.435314+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=270789&efYd=20251023&chrClsCd=010202) |
| L긴급복지지원법 시행령 | 긴급복지지원법 시행령 | law | 2022-05-03 | 2026-09-23T15:50:23.642124+00:00 | 2026-09-23T15:50:23.642124+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=242023&efYd=20220503&chrClsCd=010202) |
| L농지법 | 농지법 | law | 2026-09-18 | 2026-09-23T15:05:04.959033+00:00 | 2026-09-23T15:05:04.959033+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284301&efYd=20260918&chrClsCd=010202) |
| L농지법 시행규칙 | 농지법 시행규칙 | law | 2026-09-22 | 2026-09-23T15:05:06.038447+00:00 | 2026-09-23T15:05:06.038447+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=289987&efYd=20260922&chrClsCd=010202) |
| L농지법 시행령 | 농지법 시행령 | law | 2026-09-18 | 2026-09-23T15:05:05.460560+00:00 | 2026-09-23T15:05:05.460560+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=288811&efYd=20260918&chrClsCd=010202) |
| L등기사항증명서 등 수수료규칙 | 등기사항증명서 등 수수료규칙 | law | 2025-08-01 | 2026-09-23T15:04:58.264767+00:00 | 2026-09-23T15:04:58.264767+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=272961&efYd=20250801&chrClsCd=010202) |
| L민법 | 민법 | law | 2026-03-17 | 2026-09-23T15:55:03.140857+00:00 | 2026-09-23T15:55:03.140857+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284415&efYd=20260317&chrClsCd=010202) |
| L민원 처리에 관한 법률 | 민원 처리에 관한 법률 | law | 2022-07-12 | 2026-09-23T15:05:06.345026+00:00 | 2026-09-23T15:05:06.345026+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=239293&efYd=20220712&chrClsCd=010202) |
| L민원 처리에 관한 법률 시행령 | 민원 처리에 관한 법률 시행령 | law | 2026-05-06 | 2026-09-23T15:08:38.425848+00:00 | 2026-09-23T15:08:38.425848+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=285815&efYd=20260506&chrClsCd=010202) |
| L병역법 시행규칙 | 병역법 시행규칙 | law | 2026-01-01 | 2026-09-23T15:05:04.852246+00:00 | 2026-09-23T15:05:04.852246+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=272775&efYd=20260101&chrClsCd=010202) |
| L병적증명서 발급 규정 | 병적증명서 발급 규정 | law | 2025-12-12 | 2026-09-23T15:46:58.757180+00:00 | 2026-09-23T15:46:58.757180+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000269680&admRulId=36857&joTpYn=Y&languageType=KO&chrClsCd=010202) |
| L부가가치세법 | 부가가치세법 | law | 2026-01-02 | 2026-09-23T15:05:03.817822+00:00 | 2026-09-23T15:05:03.817822+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=276117&efYd=20260102&chrClsCd=010202) |
| L부동산 가격공시에 관한 법률 | 부동산 가격공시에 관한 법률 | law | 2020-12-10 | 2026-09-23T15:04:57.299207+00:00 | 2026-09-23T15:04:57.299207+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=219361&efYd=20201210&chrClsCd=010202) |
| L부동산 가격공시에 관한 법률 시행규칙 | 부동산 가격공시에 관한 법률 시행규칙 | law | 2022-03-30 | 2026-09-23T15:04:57.301455+00:00 | 2026-09-23T15:04:57.301455+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=241621&efYd=20220330&chrClsCd=010202) |
| L부동산 가격공시에 관한 법률 시행령 | 부동산 가격공시에 관한 법률 시행령 | law | 2026-01-02 | 2026-09-23T15:48:39.075387+00:00 | 2026-09-23T15:48:39.075387+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=281273&efYd=20260102&chrClsCd=010202) |
| L부동산등기규칙 | 부동산등기규칙 | law | 2025-08-01 | 2026-09-23T15:04:58.188101+00:00 | 2026-09-23T15:04:58.188101+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=266847&efYd=20250801&chrClsCd=010202) |
| L부동산등기법 | 부동산등기법 | law | 2025-01-31 | 2026-09-23T15:04:57.351890+00:00 | 2026-09-23T15:04:57.351890+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=265377&efYd=20250131&chrClsCd=010202) |
| L부동산등기사항증명서 발급처리지침 | 부동산등기사항증명서 발급처리지침 | law | 2025-01-31 | 2026-09-23T15:46:58.757549+00:00 | 2026-09-23T15:46:58.757549+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2200000106175&admRulId=2111627&joTpYn=N&languageType=KO&chrClsCd=010202) |
| L성남시 제증명 등 수수료 징수 조례 | 성남시 제증명 등 수수료 징수 조례 | law | 2026-04-30 | 2026-09-23T15:33:53.450417+00:00 | 2026-09-23T15:33:53.450417+00:00 | [원문](https://www.law.go.kr/LSW/ordinInfoR.do?ordinSeq=2126623&ordinId=2146203&chrClsCd=010202&gubun=ELIS) |
| L소득세법 | 소득세법 | law | 2026-07-01 | 2026-09-23T15:05:03.822017+00:00 | 2026-09-23T15:05:03.822017+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=280405&efYd=20260701&chrClsCd=010202) |
| L어디서나 민원처리제 운영지침 | 어디서나 민원처리제 운영지침 | law | 2024-11-07 | 2026-09-23T15:46:58.757410+00:00 | 2026-09-23T15:46:58.757410+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000249132&admRulId=29521&joTpYn=Y&languageType=KO&chrClsCd=010202) |
| L인터넷에 의한 등기기록의 열람 등에 관한 업무처리지침 | 인터넷에 의한 등기기록의 열람 등에 관한 업무처리지침 | law | 2025-01-31 | 2026-09-23T15:33:53.953103+00:00 | 2026-09-23T15:33:53.953103+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2200000106133&admRulId=2111479&joTpYn=N&languageType=KO&chrClsCd=010202) |
| L자동차관리법 | 자동차관리법 | law | 2026-06-16 | 2026-09-23T15:04:58.409848+00:00 | 2026-09-23T15:04:58.409848+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=286989&efYd=20260616&chrClsCd=010202) |
| L자동차등록규칙 | 자동차등록규칙 | law | 2026-06-03 | 2026-09-23T15:04:58.678907+00:00 | 2026-09-23T15:04:58.678907+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=286693&efYd=20260603&chrClsCd=010202) |
| L장애인복지법 시행규칙 | 장애인복지법 시행규칙 | law | 2026-07-01 | 2026-09-23T15:05:00.080572+00:00 | 2026-09-23T15:05:00.080572+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=282417&efYd=20260701&chrClsCd=010202) |
| L전자정부법 | 전자정부법 | law | 2026-08-28 | 2026-09-23T15:48:39.075056+00:00 | 2026-09-23T15:48:39.075056+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=283701&efYd=20260828&chrClsCd=010202) |
| L주거급여법 | 주거급여법 | law | 2023-10-19 | 2026-09-23T15:08:41.715875+00:00 | 2026-09-23T15:08:41.715875+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=250087&efYd=20231019&chrClsCd=010202) |
| L주거급여법 시행규칙 | 주거급여법 시행규칙 | law | 2015-12-29 | 2026-09-23T15:50:23.642047+00:00 | 2026-09-23T15:50:23.642047+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=178495&efYd=20151229&chrClsCd=010202) |
| L주민등록법 | 주민등록법 | law | 2025-07-22 | 2026-09-23T15:04:54.909286+00:00 | 2026-09-23T15:04:54.909286+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=268555&efYd=20250722&chrClsCd=010202) |
| L주민등록법 시행규칙 | 주민등록법 시행규칙 | law | 2026-04-28 | 2026-09-23T15:04:54.909664+00:00 | 2026-09-23T15:04:54.909664+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=285757&efYd=20260428&chrClsCd=010202) |
| L주민등록법 시행령 | 주민등록법 시행령 | law | 2026-04-28 | 2026-09-23T15:04:54.909584+00:00 | 2026-09-23T15:04:54.909584+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=285733&efYd=20260428&chrClsCd=010202) |
| L지방세기본법 | 지방세기본법 | law | 2026-02-05 | 2026-09-23T15:05:01.027136+00:00 | 2026-09-23T15:05:01.027136+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=283257&efYd=20260205&chrClsCd=010202) |
| L지방세기본법 시행규칙 | 지방세기본법 시행규칙 | law | 2026-07-01 | 2026-09-23T15:05:01.607359+00:00 | 2026-09-23T15:05:01.607359+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=286871&efYd=20260701&chrClsCd=010202) |
| L지방세기본법 시행령 | 지방세기본법 시행령 | law | 2026-07-01 | 2026-09-23T15:05:01.352024+00:00 | 2026-09-23T15:05:01.352024+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=286471&efYd=20260701&chrClsCd=010202) |
| L지방세징수법 | 지방세징수법 | law | 2026-02-05 | 2026-09-23T15:05:02.000327+00:00 | 2026-09-23T15:05:02.000327+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=283259&efYd=20260205&chrClsCd=010202) |
| L지방세징수법 시행규칙 | 지방세징수법 시행규칙 | law | 2026-07-01 | 2026-09-23T15:48:39.075484+00:00 | 2026-09-23T15:48:39.075484+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=283319&efYd=20260701&chrClsCd=010202) |
| L지방세징수법 시행령 | 지방세징수법 시행령 | law | 2026-02-05 | 2026-09-23T15:05:02.432652+00:00 | 2026-09-23T15:05:02.432652+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=283265&efYd=20260205&chrClsCd=010202) |
| L지적업무처리규정 | 지적업무처리규정 | law | 2025-08-29 | 2026-09-23T15:47:01.617979+00:00 | 2026-09-23T15:47:01.617979+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000263422&admRulId=2043750&joTpYn=Y&languageType=KO&chrClsCd=010202) |
| L초ㆍ중등교육법 시행규칙 | 초ㆍ중등교육법 시행규칙 | law | 2026-03-11 | 2026-09-23T15:05:04.781787+00:00 | 2026-09-23T15:05:04.781787+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=284493&efYd=20260311&chrClsCd=010202) |
| L토지이용규제 기본법 | 토지이용규제 기본법 | law | 2026-02-27 | 2026-09-23T15:04:56.088395+00:00 | 2026-09-23T15:04:56.088395+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=283679&efYd=20260227&chrClsCd=010202) |
| L토지이용규제 기본법 시행규칙 | 토지이용규제 기본법 시행규칙 | law | 2022-03-30 | 2026-09-23T15:08:40.279960+00:00 | 2026-09-23T15:08:40.279960+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=241603&efYd=20220330&chrClsCd=010202) |
| L토지이용규제 기본법 시행령 | 토지이용규제 기본법 시행령 | law | 2026-01-02 | 2026-09-23T15:04:56.145499+00:00 | 2026-09-23T15:04:56.145499+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=281297&efYd=20260102&chrClsCd=010202) |
| L학교생활기록 작성 및 관리지침 | 학교생활기록 작성 및 관리지침 | law | 2026-03-01 | 2026-09-23T15:46:58.757496+00:00 | 2026-09-23T15:46:58.757496+00:00 | [원문](https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq=2100000274694&admRulId=37596&joTpYn=Y&languageType=KO&chrClsCd=010202) |
| L한부모가족지원법 | 한부모가족지원법 | law | 2025-12-04 | 2026-09-23T15:05:00.130123+00:00 | 2026-09-23T15:05:00.130123+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=266687&efYd=20251204&chrClsCd=010202) |
| L한부모가족지원법 시행규칙 | 한부모가족지원법 시행규칙 | law | 2025-10-01 | 2026-09-23T15:05:00.764428+00:00 | 2026-09-23T15:05:00.764428+00:00 | [원문](https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=278861&efYd=20251001&chrClsCd=010202) |

## 수집 시도

| 상태 | 건수 |
| --- | --- |
| excluded | 21 |
| ok | 91 |

| 별칭 | HTTP | 상태 | 사유 |
| --- | --- | --- | --- |
| G001 | — | excluded | acquisition_failed_or_missing_raw |
| G002 | — | excluded | acquisition_failed_or_missing_raw |
| G003 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G005 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G007 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G008 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G009 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G011 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G012 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G013 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G014 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G019 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G020 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G028 | — | excluded | acquisition_failed_or_missing_raw |
| G029 | — | excluded | acquisition_failed_or_missing_raw |
| G038 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G039 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G040 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G041 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G042 | 200 | excluded | auxiliary_or_legal_shell_replaced_by_full_law |
| G046 | 200 | excluded | error_page_http_200 |

## 조회 예시

```sql
SELECT service_id, name, category FROM services ORDER BY service_id;

SELECT source_type, COUNT(*) AS documents FROM documents GROUP BY source_type;

SELECT d.alias, d.title, ch.locator, ch.text
FROM chunks ch JOIN documents d USING(document_id)
WHERE d.alias = 'L가족관계의 등록 등에 관한 법률'
ORDER BY ch.ordinal LIMIT 5;

SELECT model, dimension, COUNT(*) AS vectors FROM embeddings GROUP BY model, dimension;

SELECT d.alias, r.media_type, length(r.payload) AS html_bytes
FROM documents d JOIN raw_sources r USING(raw_hash) LIMIT 5;
```
