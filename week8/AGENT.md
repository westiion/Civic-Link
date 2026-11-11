# Week 8 — Qwen 기반 반복 탐색 구현

[노션 전체 구조](https://app.notion.com/p/4146d1078cb3804994ccda312c40e9c2)를 실행 코드로 연결한 첫 구현이다. **첫 질문부터 Qwen이 행동을 선택**하며, 중앙 프로그램은 요청 검사·도구 실행·자료 보관·재전달을 담당한다. 첫 검색을 Hybrid+BGE로 고정하지 않는다.

이 문서는 구현과 실행 방법을 설명한다. [1,000문항 벤치마크 확장 기록](REPORT.md)과 과거 Week 4·7 보고서는 보존한다. **이번 구현의 1,000문항 검색·응답 성능 평가는 아직 수행하지 않았다.**

## 전체 흐름과 코드 위치

```text
사용자 질문 → 첫 입력 구성 → Qwen 판단 → 실행 관리
                                     ├─ search / graph → 결과 처리 → Qwen 재판단
                                     └─ respond → 사용자에게 응답
```

| 단계 | 구현한 역할 | 코드 |
|---|---|---|
| 중앙 프로그램 · 첫 입력 구성 | 질문 원문을 유지하고, 자료 범위·도구 설명·지침·초기 상태를 system/user 메시지로 구성 | [prompts.py](../src/civic_link/agent/prompts.py), [controller.py](../src/civic_link/agent/controller.py) |
| Qwen · 판단 | 서버에 메시지를 보내 확인 항목·행동·입력값을 받음 | [client.py](../src/civic_link/agent/client.py) |
| 중앙 프로그램 · 실행 관리 | JSON과 근거 ID 검사, 판단 기록, 선택된 도구 호출 | [protocol.py](../src/civic_link/agent/protocol.py), [controller.py](../src/civic_link/agent/controller.py) |
| 검색 도구 | 새 검색어로 Hybrid+BGE 검색, 또는 지정한 대상의 Graph 관계 조회 | [search.py](../src/civic_link/agent/search.py), [graph.py](../src/civic_link/agent/graph.py) |
| 중앙 프로그램 · 결과 처리 | 결과 정리·보관 후 질문·새 결과·필요한 기존 근거와 기록을 Qwen에 재전달 | [controller.py](../src/civic_link/agent/controller.py) |
| 사용자에게 응답 | Qwen의 응답 문장에 실제 저장 자료의 출처를 연결해 전달 | [controller.py](../src/civic_link/agent/controller.py), [run_agent.py](../scripts/run_agent.py) |

Qwen의 출력은 `checks / action / arguments / reason`을 가진 JSON이다. 행동은 `search`, `graph`, `respond`이며 각 입력은 다음과 같다.

| 행동 | arguments |
|---|---|
| search | `query`: 새 검색어 |
| graph | `target`: 실제 노드 ID 또는 저장된 이름, `relation`: 지원 관계 |
| respond | `text`: 응답 문장, `evidence_ids`: 사용한 근거 ID 목록 |

도구는 `tool / status / evidence / relations`를 포함한 키–값 자료를 반환한다. 프로그램이 실제 값을 정리하며, 근거의 의미와 충분성은 Qwen이 판단한다. 평가 정답이나 벤치마크의 하위 과제 주석은 입력에 넣지 않는다.

## 실행 준비

Python 3.12 환경과 복원한 코퍼스 DB가 필요하다. 저장소 루트에서 PowerShell로 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/restore_data.py restore
.\.venv\Scripts\python.exe scripts/run_agent.py --question "주민등록표 등본의 신청 서류는 무엇인가요?" --show-input
```

`--show-input`은 첫 메시지 확인용이며 모델·검색 도구를 호출하지 않는다. 실제 실행에는 다음 자원이 필요하다.

| 자원 | 설정 | 역할 |
|---|---|---|
| 생성용 Qwen | `QWEN_ENDPOINT`, `QWEN_MODEL` | 첫 판단·재판단·응답 작성. 기본 모델 ID는 `Qwen3.8-Flash-Next` |
| 임베딩용 Qwen | `QWEN_EMBEDDING_ENDPOINT`, `QWEN_EMBEDDING_MODEL` | 문서·새 검색어의 벡터 생성. 기본 모델 ID는 `Qwen3-VL-Embedding-8B` |
| 로컬 BGE | `BGE_MODEL_PATH` | Hybrid 후보의 관련성 재정렬 |
| Dense 색인 | `--index`, 기본 `outputs/agent-index/corpus.npz` | 현재 코퍼스 문서 벡터 보관 |

endpoint에는 각 서버의 **전체 API URL**을 설정한다. 인증이 필요한 환경은 `QWEN_API_KEY`, `QWEN_EMBEDDING_API_KEY`를 사용한다. 주소·키는 공유 파일에 기록하지 않는다.

```powershell
$env:QWEN_ENDPOINT = "<생성 서버의 chat/completions 전체 URL>"
$env:QWEN_EMBEDDING_ENDPOINT = "<임베딩 서버의 embeddings 전체 URL>"
$env:BGE_MODEL_PATH = "<준비한 로컬 BGE 모델 폴더>"
.\.venv\Scripts\python.exe -m pip install -r requirements-bge.txt
```

BGE 가중치는 별도로 준비해야 한다. 실행 코드가 모델을 자동 다운로드하지 않는다. 두 Qwen 모델은 역할이 다르며, 임베딩 서버를 생성용 endpoint에 설정하면 안 된다.

## 색인 생성과 한 질문 실행

기존 Week7 후보 파일은 동결된 질문별 결과다. 에이전트가 만든 새 검색어에는 현재 코퍼스의 Dense 색인이 필요하다. 아래 색인 생성은 **임베딩 서버를 실제 호출**하며, 기존 색인 파일을 덮어쓰지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts/build_agent_index.py --output outputs/agent-index/corpus.npz
.\.venv\Scripts\python.exe scripts/run_agent.py --question "주민등록표 등본의 신청 서류는 무엇인가요?" --output-dir outputs/agent-run-001
```

색인 생성과 검색 실행에서 `QWEN_EMBEDDING_MODEL`, `QWEN_EMBEDDING_REVISION`, `QWEN_QUERY_INSTRUCTION` 설정을 일치시킨다. 색인은 코퍼스·선언한 인코더 설정·벡터 차원·체크섬을 검사한다. 임베딩이나 BGE가 없으면 `search`가 오류를 반환하며 BM25 단독으로 바꾸지 않는다. Graph와 응답은 필요한 순간에 독립적으로 실행된다.

`outputs/agent-run-001`은 새 디렉터리여야 한다. 생성 파일은 다음과 같으며 Git에서 제외된다.

| 파일 | 내용 |
|---|---|
| `result.json` | 응답·실제 출처·최종 확인 항목·보관 근거·실행 이력 |
| `trace.jsonl` | 각 Qwen 입력·출력, 요청 검사, 도구 결과의 순서 |
| `config.json` | 모델 이름·설정·호출 한도·검증 범위 |

## 상의 없이 정한 초기 구현 선택

아래는 실행 가능한 코드를 만들기 위해 정한 **초기 선택**이다. 함께 합의한 전체 구조와 구분하며, 최적 성능을 확인한 설정이 아니다.

| 항목 | 현재 선택·기본값 | 이유·영향 |
|---|---|---|
| 실행 방식 | Python 제어 루프, 한 번에 행동 하나. 별도 에이전트 프레임워크 없음 | 단계별 입력과 결과를 추적하기 쉽게 구성 |
| 시스템 지침 문구 | 역할·자료 범위, 비교 대상별 확인, 근거 부족·충돌 처리, 도구 설명을 `prompts.py`에 작성 | 전체 구조를 실행하기 위한 초안이며, 세부 판단 문구의 효과는 검증 전 |
| 응답 계약 | JSON의 네 필드만 허용. 확인 상태는 `미확인/일부 확인/확인/충돌` | 프로그램이 실행 요청을 명확히 검사 |
| 호출 한도 | 모델 8회, 실제 도구 실행 6회 | 무한 반복 제한. 마지막 모델 판단은 응답에 사용 |
| 잘못된 요청 | 연속 2회면 중단. 수정 요청도 모델 호출 수에 포함 | 끝없이 형식을 수정하는 루프 방지 |
| 실패 처리 | 모델 통신 실패는 별도 오류 종료. 도구 실패는 오류 관찰값으로 Qwen에 전달. 자동 통신 재시도 없음 | 실행 실패와 자료 없음을 구분 |
| 중복 요청 | 같은 도구·입력의 오류 아닌 결과는 세션 안에서 재사용 | 중복 실행을 줄임. 모델 판단 횟수는 계속 증가 |
| 전달할 근거 | 새 결과 → 확인 항목에 연결된 기존 근거 → 나머지 보관 근거 순 | 새로 찾은 정보를 읽을 기회를 우선 확보 |
| 문맥 예산 | 근거 객체의 JSON 직렬화 길이 합계 최대 60,000자. 제외한 ID 표시 | 토큰 기준이나 전체 프롬프트 상한이 아님. 긴 자료는 일부 문장으로 자르지 않고 이번 전달에서 제외 |
| 내용 검색 | 후보 100개, 반환 10개. BM25 `k1=1.2`, `b=0.75`, RRF 상수 60 | 기존 검색 설정을 출발점으로 사용. 새 쿼리는 전체 코퍼스에서 다시 검색 |
| 색인·임베딩 | 제목+본문을 임베딩한 정규화 벡터를 NPZ로 저장. 생성 배치 16, 생성 timeout 60초 | 현재 코퍼스와 색인의 대응을 검사. query instruction 기본 빈 문자열, revision 기본 `unspecified` |
| BGE | CPU·float32, 배치 8, 입력 한도 1,024토큰 | 로컬 실행의 기본값. 장치·입력 한도는 CLI로 변경 가능 |
| Graph 탐색 | 기본 outgoing 1홉, 관계 최대 20개·근거 최대 50개 | 연결 범위를 제한. incoming/both는 실행 설정으로 변경하며 Qwen 인자는 대상·관계만 포함 |
| Graph 대상 식별 | 실제 ID 또는 Unicode·공백·대소문자를 정규화한 저장 이름·별칭의 정확 일치 | 후보가 여러 개거나 없으면 그 상태 반환. 새 관계를 생성하지 않음 |
| Graph 근거 수집 | 관계를 뒷받침하는 청크와 연결 대상의 원문을 함께 반환. 공용 서류·기관 노드의 근거는 선택한 관계의 출처 문서로 제한 | 다른 서비스의 근거가 섞이지 않게 하는 초기 정책 |
| 반환 범위 | 결과 일부만 반환하면 메타데이터에 표시. 별도 페이지 조회 도구는 미구현 | Qwen은 재검색이나 다른 대상을 요청할 수 있으나, 모든 후보를 읽었다고 간주할 수 없음 |
| Qwen 요청 | temperature 0, 출력 최대 2,000토큰, timeout 120초, 비스트리밍 | 서버의 JSON 강제 옵션은 기본 끔. 지원 확인 후 `--json-mode`로 선택 |
| 근거·인용 | 실제 `chunk_id`를 근거 ID로 사용. 한 번이라도 전달된 자료만 인용 허용. 응답 끝에 출처 목록 부착 | 별도의 E1 번호를 만들지 않으며, 문장별 인용 대응 검증은 아직 없음 |
| 검색 점수 보관 | 쿼리별 순위·점수는 도구 원결과 trace에 보관. 재전달하는 공통 근거에는 ID·제목·본문·출처를 사용 | 이전 검색어의 점수가 새 검색어의 점수처럼 전달되는 문제 방지 |
| 대화 범위 | 한 질문의 자동 탐색부터 `respond`까지 한 실행 | 추가 질문 문구는 반환할 수 있지만, 사용자 후속 답변을 기존 세션에서 재개하는 기능은 미구현 |

## 검증 범위

모델 서버를 이용한 두 실행과 색인 생성은 작업 복사본에서 수행한 뒤, 동일한 코드·실행 자원·검증 기록을 실제 프로젝트로 옮겼다. 두 모델 실행은 연결 검증용 단일 질문이며 평가용 성능 수치가 아니다.

실제 프로젝트에서도 전체 오프라인 테스트를 다시 실행해 **195 passed, 1 skipped**를 확인했다. 첫 입력 구성, 기존 DB와 새 색인의 호환, 실제 프로젝트 내부로 해석되는 실행 경로도 확인했다. 모델 서버를 이용한 두 실행은 옮겨온 기록이며, 실제 프로젝트에서 같은 질문을 다시 실행한 결과로 표시하지 않는다.

| 확인 항목 | 결과 |
|---|---|
| 전체 오프라인 테스트 | 195 passed, 1 skipped. Windows의 심볼릭 링크 생성 권한이 필요한 테스트 1개 제외 |
| Qwen 서버 | 생성 모델 `Qwen3.8-Flash-Next`, 임베딩 모델 `Qwen3-VL-Embedding-8B` 확인 |
| 문서 색인 | 실제 임베딩 서버로 6,768청크 × 4,096차원 생성, 현재 코퍼스와 대응 검증 |
| Graph 경로 | Qwen → `graph(병적증명서, REQUIRES)` → Qwen → 응답. 모델 2회·도구 1회, 관계 3개·근거 1개 반환 |
| Hybrid+BGE 경로 | Qwen이 만든 새 검색어로 검색 → Qwen → 응답. 모델 2회·도구 1회, 후보 100개·반환 10개, 실제 출처 5개 연결 |
| 기존 데이터 | 공유 파일·코퍼스 복원 검증 통과. 기존 Week4·Week7 발표 보고서와 벤치마크 원문 보존 |

두 서버 실행은 검증용으로 모델 호출 한도 4회·도구 한도 2회를 사용했다. 로컬 기록은 `outputs/agent-smoke-20261111-graph/`, `outputs/agent-smoke-hybrid/`에 있으며 Git에 포함하지 않는다. 색인은 `outputs/agent-index/corpus.npz`, 고정 BGE 가중치는 `.cache/reranker-models/`에 준비했다. 실제 프로젝트의 기존 DB·Git 설정·가상환경은 보존했다.

**CPU BGE로 후보 100개를 재정렬한 실제 실행은 지연이 컸다.** 실행 장치·배치·반환량의 성능 조정은 남아 있으며, 연결 성공을 운영 속도나 답변 정확성의 검증으로 해석하지 않는다.

[흐름 테스트](../tests/test_agent_workflow.py), [검색 도구 테스트](../tests/test_agent_search.py), [Graph 도구 테스트](../tests/test_agent_graph.py), [실행 설정 테스트](../tests/test_agent_cli.py)는 서버 없이 실행할 수 있다. 첫 Graph 선택, 새 검색어 전달, 근거 누적, 잘못된 요청·출처 거부, 문맥 예산, 도구 오류와 호출 한도를 검증한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_workflow.py tests/test_agent_search.py tests/test_agent_graph.py tests/test_agent_cli.py -q
```

형식 검사와 실제 근거 ID·출처의 연결 검사는 구현되어 있다. **답변이 근거를 의미적으로 뒷받침하는지 검증하는 절차, 사용자 후속 응답 재개, 1,000문항 성능 비교는 남아 있다.** 서버 모델 목록 확인이나 단일 실행 성공을 검색·답변 성능 개선의 근거로 해석하지 않는다.
