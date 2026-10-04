# 02 · NetworkX Graph Construct

[01 Schema](01_graph_schema.md) · [02 Construct](02_graph_construct.md) · [03 Retrieval](03_graph_retrieval.md) · [04 Fail Case](04_fail_case_analysis.md)

서비스 등록부와 원문 안내에서 관계를 만들고, 각 연결의 근거를 추적할 수 있게 저장한다.

**목차** · [입력과 구축 흐름](#section-02-1) · [대리 신청 안내가 위임장 관계가 되는 과정](#section-02-2) · [처리 단계](#section-02-3) · [저장 전에 확인하는 것](#section-02-4) · [현재 산출물](#section-02-5) · [실행](#section-02-6) · [저장 형식 상세](#section-02-7)

<a id="section-02-1"></a>

## 입력과 구축 흐름

[서비스 등록부](../data/services.jsonl)와 원문 DB에서 서비스를 확인하고, 실제로 수집한 법령·안내문을 관계로 연결한다. **질문·평가용 정답 근거(Gold)·검색 결과는 구축 입력으로 사용하지 않는다.** 기존 서비스·문서·원문 조각의 ID를 유지한다.

법령과 조항을 먼저 만든 뒤 서비스 안내의 인용을 연결한다. 제출서류와 기관은 안내문에 적힌 범위에서만 추출하고, 신청 조건이 다르면 별도 관계로 남긴다. 마지막으로 원문 인용과 출처를 검증해 그래프와 근거 파일을 저장한다. [구축 규칙](01_graph_schema.md)과 [구현 코드](../scripts/graph_construct.py)를 함께 확인할 수 있다.

<a id="construct-example"></a>

<a id="section-02-2"></a>

## 대리 신청 안내가 위임장 관계가 되는 과정

정부24 주민등록표 등본·초본 안내에는 다음 문장이 이어져 있다. 등록부의 주민등록표 등본 서비스와 원문 DB의 이름·분류가 일치하는지 확인한 뒤 이 안내를 읽는다.

```text
2. 대리인이 신청하는 경우
대리인의 신분증 제시
위임장(주민등록법 시행규칙 별지 제9호서식) 제출
```

**‘대리인이 신청하는 경우’는 조건이고, ‘위임장 제출’은 제출 진술이다.** 제출서류 구간부터 공무원 확인·QR코드·부가정보 전까지 읽으며 각 줄의 문자 위치를 유지한다. ‘경우’로 끝나는 제목은 조건으로 보관한다. 서류명에서는 번호·기호와 괄호 뒤 설명·제출 표현을 분리해 ‘위임장’을 얻지만, 원문 진술에는 서식 번호까지 남긴다.

상·하위 조건이 함께 있으면 모두 보존한다. ‘없음’, ‘서식없음’, 공동조회 항목을 제출 의무로 만들지 않는다. 이렇게 얻은 위임장 서류 유형은 여러 서비스가 공유할 수 있지만, 신청 조건이 다른 제출 관계는 각각 기록한다. **이 관계 하나가 모든 신청인에게 위임장 제출을 요구한다는 뜻은 아니다.** 신분증 대안·유효기간·자격도 원문과 함께 읽어야 한다.

조건 구절과 제출 구절은 같은 원문 조각 안에 있어도 역할이 달라 근거를 따로 기록한다. 위임장 노드에는 제출 진술을, 이 서비스의 제출 관계에는 조건과 제출 진술을 함께 연결한다. 각 인용의 정확한 문자 범위와 겹치는 원문 조각을 보존하므로 검색 결과가 어느 근거를 선택했는지 추적할 수 있다.

다른 서비스의 위임장 조건이 섞이지 않도록 검색 시에는 들어온 관계와 출처가 같은 노드 근거만 선택한다. [실제 관계 JSON과 근거 위치](#construct-storage-example)는 문서 뒤에 모았다.

<a id="section-02-3"></a>

## 처리 단계

| 단계 | 역할 | 결과 |
| --- | --- | --- |
| 입력 대조 | 등록부와 원문 서비스가 일치하는지 확인 | 입력 식별값과 기존 ID 유지 |
| 법령 정리 | 수집한 법령의 제목·시행일 확인 | 법령 노드와 맥락 근거 |
| 조문 정리 | 같은 문서의 조문번호별로 묶기 | 조항 노드와 소속 법령 연결 |
| 인용 연결 | 명시한 법령명·조문번호 찾기 | 서비스의 법적 근거와 조항 간 참조 |
| 서비스 정리 | 공식 이름과 허용된 문자열 별칭 구성 | 서비스 노드와 안내 근거 |
| 제출서류 구분 | 서류명과 신청 조건을 구분 | 조건이 있는 제출 관계 |
| 기관 연결 | 접수·처리 항목의 원문 명칭 확인 | 기관 범주와 접수·처리 역할 |
| 근거 연결 | 인용 위치와 겹치는 원문 조각 기록 | 노드·관계에서 원문으로 가는 연결 |
| 검증·저장 | 구조·출처·인용을 대조 | 그래프·근거 파일과 미해결 참조 |

안내문이 조항을 인용한 경우와 법령 본문이 서비스를 직접 언급한 경우는 구별해서 기록한다. 어느 쪽도 특정 개인에게 해당 조항이 적용된다는 판단은 아니다. 기관도 ‘시군구 및 읍면동 출장소’처럼 원문에 적힌 범주를 유지하며 특정 주소나 관할 사무소를 채워 넣지 않는다.

명시적인 인용이라도 대상 법령·조항이 수집 자료에 없으면 임의 노드를 만들지 않는다. ‘같은 법’, 범위형 인용에서 생략된 중간 조항, 별표·서식 전체를 모두 해석하는 파서도 아니다. 연결하지 못한 참조 수와 이 제한을 함께 제시한다.

<a id="section-02-4"></a>

## 저장 전에 확인하는 것

노드의 필수 정보, 허용된 다섯 관계의 출발·도착 유형, 관계 ID의 중복, 모든 노드·관계의 원문 근거를 검사한다. 관계의 근거가 출발 쪽 문서에 속하는지, 조항과 소속 법령의 출처가 같은지, 문서 이동 수가 실제 출처 이동과 맞는지도 확인한다.

인용문은 정규화된 원문 본문의 해당 문자 범위와 일치해야 한다. 연결된 원문 조각이 같은 문서에 속하고 그 범위를 빈틈없이 덮는지, 겹치는 부분의 문자까지 같은지 대조한다. 주소·본문 해시·시행일·수집일·확인일도 원문 정보와 비교한다.

검증을 통과한 그래프와 근거는 두 JSON 파일로 저장하고, 연결하지 못한 참조는 별도 진단값으로 반환한다. 이후 원문 DB의 내용 식별값(fingerprint)이 달라지면 읽기 단계에서 불일치를 알린다. 검증 통과는 구조와 인용의 일치를 뜻하며 법적 해석·개인 자격·실제 처리 가능성을 확정하지 않는다.

<a id="section-02-5"></a>

## 현재 산출물

노드 6,182개, 관계 9,207개, 근거 기록(Evidence record) 10,895개다. 미해결 참조는 3,022개이며, 자료의 관계를 전부 포착했다는 뜻은 아니다.

| 노드 유형 | 수 |
| --- | --- |
| Service | 40 |
| Document | 24 |
| Agency | 8 |
| Article | 6046 |
| Law | 64 |

| 관계 | 수 |
| --- | --- |
| REQUIRES | 84 |
| HANDLED_BY | 62 |
| GROUNDED_BY | 155 |
| PART_OF | 6046 |
| REFERENCES | 2860 |

| 파일 | 내용 |
| --- | --- |
| [graph.json](../data/processed/graph.json) | NetworkX MultiDiGraph의 노드·관계·스키마와 코퍼스 내용 식별값 |
| [graph.evidence.json](../data/processed/graph.evidence.json) | 원문 구절·청크·출처와 노드·관계의 근거 연결 |
| [source_index.json](../data/source_index.json) | 원문 91개의 ID·URL·원문 해시 |
| [실행 설정](../results/current204/run_manifest.json) | 현재 검색 입력·코퍼스·코드·설정의 식별 정보 |

<a id="section-02-6"></a>

## 실행

Python 3.12와 NetworkX 3.4.2 환경에서 submission/week3를 현재 디렉터리로 사용한다.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/submission_cli.py restore
.venv/bin/python scripts/submission_cli.py verify
```

구축 자체를 확인할 때는 아래 명령으로 같은 입력을 읽고 임시 폴더에 그래프를 만든다. 임시 출력은 검증 후 정리된다.

```sh
PYTHONPATH=scripts .venv/bin/python - <<'CHECK_GRAPH'
from pathlib import Path
from tempfile import TemporaryDirectory
from graph_construct import build_graph, save_graph, validate_graph
graph, evidence, unresolved = build_graph(
    Path('data/processed/civic.sqlite'), Path('data/services.jsonl'))
with TemporaryDirectory(prefix='civic-link-graph-') as directory:
    save_graph(graph, evidence, Path(directory) / 'graph.json')
    print(validate_graph(graph, evidence))
print({'unresolved_references': len(unresolved)})
CHECK_GRAPH
```

검증 통과는 구조와 원문 인용의 일치 확인이다. 관계의 법적 해석, 개인의 자격 또는 실제 민원 처리 가능성을 확정하지 않는다.

<a id="construct-storage"></a>

<a id="section-02-7"></a>

## 저장 형식 상세

처리 단계와 실제 구현의 대응, 저장 키, 예시의 식별자를 모았다.

<details>
<summary>처리 단계와 구현 함수의 대응</summary>

- **입력 대조** — `build_graph` · `corpus_identity`: services·documents·document_services·chunks를 읽고 등록부 ID·이름·분류를 대조. 결과: 네 테이블의 내용 식별값, 등록부 해시, 원문·청크 ID 유지.

- **법령 정리** — Law 생성 · `stable`: source_type=law인 수집 문서. 결과: 문서별 Law, 제목·시행일, 문서 앞부분의 식별 맥락 근거(identity_context).

- **조문 정리** — Article 생성 · `article_id`: 법령 청크를 document_id/locator로 묶고 ‘제N조’ 형식 확인. 결과: Article, 조문 제목·원문 ID·locator, 청크별 앞 최대 200자의 article_context; 같은 법령으로 PART_OF.

- **인용 연결** — `citations`: 명시된 법령명과 조항, 문서 안에서 선언한 약칭. 결과: 존재하는 대상 Article에 REFERENCES; 안내의 기본정보·부가정보에서는 GROUNDED_BY.

- **서비스 정리** — Service 생성 · `service_aliases`: 서비스 등록부의 이름과 연결된 안내문. 결과: 공식 이름, 이름 끝의 신청/요청 제거·문자열 변형·긴 단어에서 파생한 별칭, 안내·지역 표의 노드 맥락 근거.

- **제출서류 구분** — 제출서류 · `add_edge`: 안내문의 제출서류 줄과 상·하위 조건. 결과: Document와 REQUIRES, 조건·제출 진술 전체·원문 근거.

- **기관 연결** — 기관 · `add_edge`: 원문의 ‘접수’ 또는 ‘처리’ 바로 다음 줄. 결과: Agency와 HANDLED_BY; 시군구 등 원문 범주, 접수/처리 역할.

- **근거 연결** — `EvidenceBuilder.span` / `bind`: 출처 문서·문자 구간·역할·근거가 연결되는 노드 또는 관계(owner). 결과: records, node_bindings, edge_bindings; 구절을 덮는 실제 chunk_ids.

- **검증·저장** — `validate_graph` / `save_graph`: 완성된 그래프와 Evidence. 결과: 검증된 MultiDiGraph JSON과 Evidence JSON; 미해결 참조는 별도 반환값.

</details>

<details>
<summary>입력·저장 구조와 검증 규칙</summary>

[구축 코드](../scripts/graph_construct.py)의 build_graph는 [서비스 등록부](../data/services.jsonl)와 복원된 data/processed/civic.sqlite의 services·documents·document_services·chunks를 읽는다. 질문·Gold·검색 결과는 노드나 관계 생성 입력으로 사용하지 않는다.

1. 등록부의 서비스 ID와 SQLite의 서비스 ID 집합을 대조하고, 네 테이블의 내용 식별값을 계산한다.
2. 실제 수집한 법령과 조문에서 Law·Article을 만들고, 등록부에서 Service를 만든다. 기존 service_id·document_id·chunk_id를 유지한다.
3. 민원 안내의 제출서류·접수처 구간에서 Document·Agency를 추출한다. 명시적 법령·조항 인용으로 GROUNDED_BY·REFERENCES를, 조문의 소속으로 PART_OF를 만든다.
4. 같은 노드 쌍이라도 조건이 다르면 networkx.MultiDiGraph의 별도 edge로 기록한다. 제출 조건·대안은 qualifiers에 남긴다.
5. EvidenceBuilder가 원문 문자 구간과 겹치는 청크를 찾아 quote·출처·시행일·수집일을 records에 기록하고 node_bindings·edge_bindings로 연결한다.
6. validate_graph로 속성·허용 관계·출처·원문 구간을 검증한 뒤 Domain Graph와 Evidence Layer를 JSON으로 저장한다. 미수집 조항이나 해석이 불명확한 참조는 관계로 추정하지 않는다.

[01의 속성·관계·근거 정책](01_graph_schema.md)이 구축 규칙이다. Domain의 Document는 제출서류 유형이며 SQLite의 원문 documents와 다르다.

먼저 스키마 검사는 노드 필수 속성, 허용 관계와 출발·도착 유형, 관계 ID의 유일성, 모든 노드·관계의 근거 연결을 확인한다. 이어 출처 검사는 각 관계의 근거가 출발 노드의 원문 문서에 속하는지, PART_OF의 조항과 법령이 같은 원문인지, document_hop이 출처 이동과 일치하는지 확인한다.

SQLite를 함께 전달한 검사는 quote와 `documents.body[start:end]`를 비교한다. 청크가 같은 문서에 속하고 인용 구간과 겹치는지, 여러 청크에 걸친 인용을 끊김 없이 덮는지, 겹치는 문자 내용까지 같은지 검사한다. URL·본문 해시·시행일·수집일·확인일도 원문 메타데이터와 대조한다. 이 과정을 통과한 뒤 `save_graph`가 두 JSON을 저장하고, 이후 `load_graph`는 원문 DB의 내용 식별값이 달라졌으면 불일치를 알린다.

아래 실행 절의 build_graph 반환값은 `(graph, evidence, unresolved)`다. `save_graph`의 두 파일은 검색 입력이고, unresolved는 연결하지 못한 참조의 진단 자료다. 제출된 graph와 Evidence는 이 고정 원문에 대한 산출물이며, 문서의 예시는 저장된 ID와 인용을 그대로 사용한다.

필수 노드 속성과 5가지 관계의 출발·도착 노드 유형, 관계 ID 중복, 모든 노드·관계의 근거 연결, 근거가 연결된 노드·관계, 출처 문서, PART_OF의 소속 일치, document_hop 값을 검사한다. SQLite를 함께 읽으면 quote가 documents.body의 [start_char,end_char)와 같은지, 청크들이 구간을 빈틈없이 덮는지, 출처 본문의 해시와 날짜 메타데이터가 같은지도 검사한다. load_graph는 SQLite의 내용 식별값이 다르면 중단한다.

</details>

| 저장 용어 | 뜻 |
| --- | --- |
| owner | 근거가 연결되는 노드 또는 관계 |
| records | 인용문·출처·위치를 담은 근거 기록 |
| node_bindings / edge_bindings | 노드·관계 ID에서 근거 ID로 가는 연결 |

<a id="construct-storage-example"></a>

<details>
<summary>위임장 사례의 관계 JSON과 근거 위치</summary>

출발 자료는 정부24 주민등록표 등본·초본 안내 G004, 원문 ID **DOC-39d8d4ed529804eda5dc**다. `build_graph`는 등록부의 entry_alias로 이 문서를 찾는다. Service **SJ-SVC-001**은 등록부의 ‘주민등록표 등본’이며, 이름·분류가 SQLite services와 일치하는지 확인한다. 아래는 정규화된 원문에 실제로 이어져 있는 문장이다.

```text
2. 대리인이 신청하는 경우
대리인의 신분증 제시
위임장(주민등록법 시행규칙 별지 제9호서식) 제출
```

**1. 제출서류 구간과 신청인 조건을 구분한다**

`build_graph`는 ‘민원인이 제출해야하는 서류’부터 공무원 확인 구간·QR코드·부가정보가 시작되기 전까지를 읽는다. 각 줄의 본문 문자 위치를 유지하고, ‘경우’로 끝나는 제목을 현재 조건으로 보관한다. 위 예시에서는 ‘2. 대리인이 신청하는 경우’가 조건이다. 제목을 제출서류 노드로 만들지 않는다.

‘위임장(주민등록법 시행규칙 별지 제9호서식) 제출’은 제출 진술이다. 서류 이름을 뽑을 때 앞의 번호·기호와 괄호 뒤 설명·제출 표현을 분리하여 ‘위임장’을 얻지만, 원문 진술에는 괄호와 서식 번호까지 남긴다. 번호가 `2-1`처럼 하위 조건을 나타내면 상위 조건과 하위 조건을 함께 보존한다. ‘없음’, ‘서식없음’, 공동조회 구간처럼 제출 의무로 읽으면 안 되는 항목은 관계 생성에서 제외한다.

**2. Document와 조건 있는 REQUIRES를 만든다**

Document **REQ-40551e07dde24c67b13f**의 label은 ‘위임장’, document_kind는 `application_requirement`다. 같은 서류명에는 같은 ID가 생성되므로 다른 서비스에서도 같은 이름의 노드를 참조할 수 있다. 관계 ID는 출발 노드·도착 노드·관계 종류(relation)와 조건(qualifiers)을 함께 해시한 값이므로 신청 조건이 다르면 별도 관계가 된다.

```json
{
  "relation": "REQUIRES",
  "document_hop": 0,
  "qualifiers": {
    "condition": "2. 대리인이 신청하는 경우",
    "statement": "위임장(주민등록법 시행규칙 별지 제9호서식) 제출",
    "requirement_mode": "conditional_statement_not_atomic_obligation"
  },
  "review_status": "machine_extracted_pending_human_review",
  "source": "SJ-SVC-001",
  "target": "REQ-40551e07dde24c67b13f",
  "key": "EDGE-32e472844df15eef1c47"
}
```

`requirement_mode=conditional_statement_not_atomic_obligation`은 이 관계가 원문 조건을 가진 제출 진술임을 나타낸다. ‘위임장’이라는 노드만 보고 모든 신청인이 위임장을 내야 한다고 읽으면 안 된다. 원문의 신분증 대안·유효기간·신청 자격 자료도 관계 하나가 대신하지 않는다.

**3. 조건 구절과 제출 구절을 각각 Evidence로 연결한다**

`EvidenceBuilder.span`은 문서 본문(body)의 정확한 문자 구간을 잘라 인용문(quote)을 만들고, 그 구간과 겹치는 기존 청크들을 찾는다. 이 예시에서는 조건과 제출 진술이 같은 청크 안에 있지만 역할이 달라 두 근거 기록(Evidence record)이 만들어진다.

**G004 · condition**

> 2. 대리인이 신청하는 경우

```text
Evidence: EV-34c7b1c6d2faf475faef
Role: condition
Rule: applicant_condition
Document: DOC-39d8d4ed529804eda5dc
Span: [1811, 1826)
Chunks:
  DOC-39d8d4ed529804eda5dc-C0004
```

**G004 · source_statement**

> 위임장(주민등록법 시행규칙 별지 제9호서식) 제출

```text
Evidence: EV-d3cee161bb66c66f2ed7
Role: source_statement
Rule: applicant_required_document
Document: DOC-39d8d4ed529804eda5dc
Span: [1839, 1866)
Chunks:
  DOC-39d8d4ed529804eda5dc-C0004
```

`edge_bindings['EDGE-32e472844df15eef1c47']`에는 두 Evidence ID가 모두 들어간다. 위임장 Document의 노드 근거 연결(node binding)에는 제출 진술이 들어가며, 대리인 조건은 이 서비스에서 위임장으로 가는 관계에 붙는다. 기록과 연결을 구분해 보존하므로 검색 결과에서 노드 근거를 골랐는지, REQUIRES 관계의 조건(condition) 또는 진술(statement) 근거를 골랐는지 확인할 수 있다.

이름이 같은 위임장 노드에는 다른 서비스의 노드 근거도 연결될 수 있다. Graph 검색의 `collect`는 Document·Agency에 도달할 때 해당 노드로 들어온 관계와 출처가 일치하는 노드 근거만 받는다. 위임장 노드를 공유한다는 이유로 서로 다른 서비스의 제출 조건을 합치지 않는다.

</details>
