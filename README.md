# Civic-Link

Civic-Link는 **수정구청의 무인민원발급 서비스에 적용하기 위한 행정·법률 기반 Graph RAG 시스템 구축 프로젝트**입니다. 행정 서비스와 필요 서류, 처리기관, 근거 조문 및 관련 법령의 관계를 지식 그래프로 구조화합니다. 이를 바탕으로 민원인의 질문에 적합한 서비스를 안내하고, 안내 내용의 공식적인 법적·행정적 근거를 함께 제시하는 것을 목표로 합니다.

> 인하대학교 탄소중립 아카데미 7기 산업체 연계 프로젝트 · 마음AI

## 주차별 발표 보고서

| 주차 | 보고서 | 보존 상태 |
|---|---|---|
| Week 4 | [Hybrid+Graph 확장 구현과 성능 분석](week4/REPORT.md) | Git에 남아 있는 2026-10-27 최종본 복원. 본문·수치는 유지하고 당시 자료 링크만 연결 |
| Week 6 | 원문 미확인 | Git 전체 이력에서 원본을 찾지 못해 별도 보고서를 만들지 않음 |
| Week 7 | [피드백 반영과 검색 개선 실험](week7/REPORT.md) | 기존 보고서와 연결된 실패 분석·실험 기록 보존 |

[1,000문항 벤치마크 확장 기록](week8/REPORT.md)은 발표 보고서와 별도로 보존합니다. 현재 구현은 [Week 8 에이전트 구조·실행·초기 선택](week8/AGENT.md)에 정리합니다. 구현 검증과 1,000문항 성능 평가는 구분합니다.

## 현재 구조 — Qwen이 첫 행동부터 선택

사용자 질문을 받으면 중앙 Python 프로그램이 `system`·`user` 입력을 구성해 Qwen을 호출합니다. Qwen은 확인 항목과 다음 행동을 반환하고, 프로그램이 요청을 검사해 **Hybrid+BGE 내용 검색**, **Graph 관계 탐색**, **사용자 응답** 중 선택된 행동을 실행합니다. 도구 결과는 보관·정리한 뒤 원래 질문과 필요한 기록을 함께 Qwen에 다시 전달합니다.

첫 검색을 고정하지 않습니다. Hybrid+BGE는 새 검색어로 저장 코퍼스를 검색하고, Graph는 대상과 관계를 지정해 기존 연결을 조회합니다. [노션의 전체 흐름](https://app.notion.com/p/4146d1078cb3804994ccda312c40e9c2)을 기준으로 구현했습니다.

## 진행 과정

#### Week 1 — 문제 및 평가 기준 정의

- 수정구청 무인민원발급을 중심으로 대상 서비스 범위 설정
- 민원 질문 유형과 평가용 데이터 구조 설계
- 질문별 행정 서비스와 공식 근거 연결

#### Week 2 — 데이터 및 텍스트 검색 기반 구축

- 행정 서비스 안내와 관련 법령 원문 수집
- BM25, Dense, Hybrid 검색 파이프라인 구현
- 검색 방식별 성능 비교 및 실패 사례 분석

#### Week 3 — 지식 그래프 및 관계 검색 구축

- 행정 서비스, 필요 서류, 처리기관, 법령, 조문의 관계 구조화
- 그래프의 노드와 관계를 공식 원문 근거에 연결
- Graph Retriever 구현 및 텍스트 검색과 비교

#### Week 4 — Hybrid + Graph 확장 및 개선 검증

- 토큰화·법령 참조·누락 근거 연결 보완
- Hybrid 기반 그래프 확장 검색 구현
- 개선 가설 3개 실험 및 성능 비교

#### Week 7 — 벤치마크 검수와 검색 개선

- 608문항에서 coverage 수식과 BGE 재정렬 비교
- 입력 길이·순위 탈락·후보 누락 사례를 구분하고 개선 효과 검증
- 생성 응답의 행동 선택과 근거 충실성을 검색 성적과 구분해 기록

#### 별도 작업 — 벤치마크 1,000문항 확장

- 기존 608문항을 보존하고, 기존 근거에 기반한 복합 문의 392문항 추가
- 답변 가능 800·추가 확인 100·답변 보류 100문항의 별도 개발용 버전 제공
- 문항별 출처·하위 문의 정답·누수 연결 그룹과 재생성/검증 코드 제공
- 이번 범위는 벤치마크 확장이다. [확장 기록](week8/REPORT.md) · [1,000문항 CSV](data/benchmark/week8/questions.csv)

## 저장소 구조

```text
Civic-Link/
├─ data/                         # 검색·평가 데이터
│  ├─ services.jsonl             # 행정 서비스 목록
│  ├─ corpus/                    # 원문 DB·복원용 압축본
│  ├─ graph/                     # 지식 그래프·근거 연결 데이터
│  ├─ retrieval/                 # 고정 Hybrid 후보·실행 정보
│  └─ benchmark/                 # 평가 질문·정답 근거
│     ├─ diagnostic480/          # 유형 균형 진단용 480문항
│     ├─ week8/                  # 기존 608 + 복합 392 = 개발용 1,000문항
│     ├─ exports/                # 질문 목록의 CSV·Markdown 보기
│     └─ reviews/                # 벤치마크 검수·수정 기록
│
├─ src/civic_link/               # 핵심 구현 코드
│  ├─ common.py                  # 공통 데이터·DB 처리
│  ├─ tokenization.py            # 텍스트 정규화·토큰화
│  ├─ agent/                     # Qwen 판단·중앙 제어·실시간 검색 도구
│  ├─ graph/                     # 그래프 구축·탐색
│  ├─ retrieval/                 # Hybrid 기반 Graph 확장 검색
│  ├─ reranking/                 # 점수식·후보 재정렬
│  └─ evaluation/                # 검색·행동 평가·벤치마크 검증
├─ scripts/                      # 복원·검색·평가·실험 실행 명령
│
├─ week4/REPORT.md               # 당시 보고서 원문·과거 자료 링크
├─ week7/                        # Week 7 보고서·실험 자료
│  ├─ REPORT.md                  # 피드백 반영·개선·수행 기록
│  ├─ failure_analysis/          # 순위 탈락·후보 누락 사례와 입력 보정 결과
│  ├─ experiments/               # Week 7 실험 자료·재현 코드
│  │  ├─ score_comparison/results/ # 수식 비교·정책 선택·문항별 변화
│  │  └─ type1_fields/           # 유형 1 항목별 BGE 실험 재현 코드
│  ├─ EXPERIMENT2.md             # 실험 2 결과
│  ├─ SHARING.md                 # 재집계 안내
│  ├─ docs/                      # 실험 2 문항별 기록
│  ├─ evaluation/                # 저장된 평가 결과
│  └─ results/                   # 검색 순위·실험 입력 및 출력
├─ outputs/                      # 실행 시 생성되는 결과 (Git 제외)
├─ week8/                        # 현재 구현과 벤치마크 확장 기록
│  ├─ AGENT.md                   # 노션 구조 대응·실행·미합의 초기 선택
│  └─ REPORT.md                  # 기존 벤치마크 확장·검증·한계 기록
├─ tests/                        # 테스트 코드·테스트용 데이터
│
├─ README.md                     # 프로젝트 소개·실행·평가 안내
├─ pyproject.toml                # Python 패키지·테스트 설정
├─ requirements.txt              # 실행 의존성
├─ requirements-dev.txt          # 개발·테스트 의존성
└─ manifest.json                 # 공유 파일 목록·입력 데이터 검증 정보
```

## 실행

### Qwen 에이전트

Python 환경과 원문 DB를 아래 설치·복원 절차로 준비한 뒤 실행합니다. 첫 입력만 확인하는 명령은 모델 서버나 도구를 호출하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts/run_agent.py --question "대리 신청에 필요한 서류는 무엇인가요?" --show-input
```

실제 실행에는 생성 서버 `QWEN_ENDPOINT`가 필요합니다. 내용 검색에는 임베딩 서버 `QWEN_EMBEDDING_ENDPOINT`, 문서 Dense 색인, 로컬 BGE 모델 `BGE_MODEL_PATH`도 필요합니다. 서버 주소·인증 정보는 환경 변수로 설정하며 저장소에 넣지 않습니다. Graph는 저장된 로컬 Graph와 원문 DB를 조회합니다.

```powershell
# 최초 검색 준비: 기존 선택 의존성·고정 BGE 모델 준비 후 실제 모델 폴더를 BGE_MODEL_PATH로 설정
.\.venv\Scripts\python.exe -m pip install -r requirements-bge.txt
.\.venv\Scripts\python.exe scripts/download_bge.py

# 명시적으로 문서 색인을 생성하며, 기존 출력은 덮어쓰지 않음
.\.venv\Scripts\python.exe scripts/build_agent_index.py --output outputs/agent-index/corpus.npz

# Qwen의 첫 판단부터 실행. 새 출력 폴더를 사용
.\.venv\Scripts\python.exe scripts/run_agent.py --question "대리 신청에 필요한 서류는 무엇인가요?" --output-dir outputs/agent-run
```

`result.json`은 응답·근거·실행 상태, `trace.jsonl`은 각 Qwen 입력·출력과 도구 결과, `config.json`은 실행 설정을 남깁니다. 자료·색인·모델이 없으면 해당 도구의 오류를 전달하며 BM25만 실행해 Hybrid라고 표시하지 않습니다. 새 검색기는 과거 고정 후보의 순위 재현을 보장하지 않습니다. [단계별 구현과 아직 합의하지 않은 초기값](week8/AGENT.md)을 확인하세요.

### 기존 Week 7 검색·평가 재현

[Week 7 보고서](week7/REPORT.md), [실패 사례 분석 및 입력 길이 보정 결과](week7/failure_analysis/README.md), [실험 2 최종 결과](week7/EXPERIMENT2.md)와 [공유본·BGE/Qwen 재집계 안내](week7/SHARING.md)를 함께 제공합니다. 저장된 원점수·원응답의 재집계에는 모델이나 서버가 필요하지 않습니다.

### 설치 → 현재 DB 복원 → 검색 → 평가

Git clone 후 저장소 루트에서 실행한다. Python 3.12와 최초 패키지 설치용 인터넷이 필요하다. 이후 명령은 BGE·임베딩 서버·모델 API를 호출하지 않는다.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/restore_data.py restore

.venv/bin/python scripts/run_retrieval.py \
  --queries data/benchmark/queries.jsonl \
  --hybrid-results data/retrieval/hybrid_candidates.jsonl.gz \
  --hybrid-run data/retrieval/hybrid_candidates.run.json \
  --rerank coverage --output outputs/team-run/predictions.jsonl

.venv/bin/python scripts/evaluate.py \
  --questions data/benchmark/questions.jsonl \
  --benchmark-version week7-master608 \
  --retrieval-predictions outputs/team-run/predictions.jsonl \
  --retrieval-format h3 --prediction-queries data/benchmark/queries.jsonl \
  --output-dir outputs/team-run/evaluation

.venv/bin/python -m pytest -q
.venv/bin/python scripts/restore_data.py verify
```

출력 경로가 이미 있으면 새 이름을 사용한다. Windows에서는 가상환경을 `py -3.12 -m venv .venv`로 만들고, `.venv/bin/python`을 `.venv/Scripts/python.exe`로 바꾼다. 위 예시는 Bash의 줄 이어쓰기(`\`)를 사용하므로, PowerShell에서는 각 명령을 한 줄로 입력하거나 줄 끝의 `\`를 백틱으로 바꾼다. 기대 결과는 Complete@10 **411/488 (84.22%)**다. 기본 행동 라벨과 허용 행동 대안은 별도로 평가하며, 검색 점수로 행동·답변 품질을 추정하지 않는다.

### 별도 벤치마크 1,000문항 확장

`data/benchmark/week8/questions.jsonl`은 기존 608문항과 복합 문의 392문항을 합친 **1,000문항**이다. `questions.csv`는 검토용, `queries.jsonl`은 정답을 제외한 입력용이다. [벤치마크 확장 기록](week8/REPORT.md)에 구성과 한계를 정리했다. 이 기록은 발표 보고서와 별개이며 기존 `week8/` 경로를 유지한다. 아래 생성·검증은 네트워크와 모델 호출 없이 실행한다.

```sh
.venv/bin/python scripts/build_week8_benchmark.py verify
.venv/bin/python scripts/build_week8_benchmark.py build --output-dir outputs/week8-rebuilt
```

DB가 없다면 위 설치·복원 절차를 먼저 실행한다. 기존 출력 디렉터리는 덮어쓰지 않는다. 기존 608문항용 Hybrid 후보·임베딩·BGE 결과를 새 392문항에 재사용할 수 없다. 이 명령은 데이터 확장·검증이며 에이전트 모델 성능 평가를 수행하지 않는다.

### Week 7 보존 데이터

608문항(기존 204 + 신규 404)과 균형 진단 부분집합 480개를 제공한다. 신규 404개·근거 590건 검토 후 21문항의 보조 주석과 확인 정책을 수정했다. 질문 ID·원문·검색 정답은 유지했다.

[주차 수행 보고서](week7/REPORT.md) · [전체 질문](data/benchmark/exports/questions.md) · [신규 질문](data/benchmark/exports/new_questions.md) · [AI 검수](data/benchmark/reviews/agent_review.csv)

### 남긴 입력과 복원 범위

- `data/corpus/corpus.sqlite.gz`: 약 3.1MB. 원문 91개·청크 6,768개·서비스 연결을 담은 현재 검색 DB 약 14.9MB만 복원한다. 임베딩·과거 실행 결과·환경은 복원하지 않는다.
- `data/retrieval/hybrid_candidates.jsonl.gz`: 약 10.6MB. 고정 Hybrid 후보를 압축 상태로 직접 읽는다. 55.9MB의 별도 복원본을 만들지 않는다.
- `data/retrieval/hybrid_candidates.run.json`: 입력 질의와 후보의 일치 검사용 메타데이터다. 다른 질문이나 후보를 섞으면 실행을 거부한다.
- `data/benchmark/`: 현재 정답·질의와 균형 480개 부분집합. `data/benchmark/reviews/`와 작은 검수 전 fixture는 수정 이력 검증에 필요하다.
- `week7/experiments/score_comparison/results/`: 개발·검증 분리, 정책 선택, 항별 비교, 개선·퇴보 및 재현 결과다.
- `week7/experiments/type1_fields/`: 당시 유형 1의 14문항을 원래 후보와 저장된 항목별 BGE 점수로 다시 집계하는 코드다. [재현 방법](week7/experiments/type1_fields/README.md)을 따른다.

Week 7 보고서의 고정 실험과 그 재현 자료는 보존한다. 별도 팀원 가설 H1~H5의 실험 묶음·실행기·전용 테스트는 현재 공유본에서 제외했으며, 과거 기록은 Git 이력에 남아 있다. 이는 Week 4 보고서 안의 H1·H2·H3 실험이나 Week 7의 coverage·BGE·Graph 가중치 실험을 제거했다는 뜻이 아니다.

압축 입력 두 개는 전송용 ZIP이 아니라 현재 실행에 필요한 동결 데이터다. SQLite는 파일로 열어야 하므로 현재 DB만 압축본과 함께 존재한다. 복원된 DB와 실행 결과는 Git에서 제외한다. 원본과 다른 기존 DB는 덮어쓰지 않는다.

### 기준선과 수식 비교

위 검색 명령에서 `--rerank feature`와 `--output outputs/baseline/h3.jsonl`을 사용하면 기존 H3 기준선을 생성한다. 동일 질문·후보·정답에서 기존 H3는 391/488이고 새 수식은 30개 개선·10개 퇴보다.

```sh
.venv/bin/python scripts/run_score_comparison.py --output-dir outputs/new-ablation
.venv/bin/python scripts/analyze_score_comparison.py --output-dir outputs/new-ablation
```

위 Week7 명령은 저장된 Hybrid 후보 이후를 재현한다. 새 질문 검색은 별도의 에이전트 검색 도구를 사용한다. 과거 Week3/4 제출과 벤치마크 초안 생성은 해당 재현 명령의 범위 밖이다. 실험 2의 BGE 원점수·Qwen 원응답·원채점과 최종 사례는 손실 없는 캐시로 별도 보존하며 [공유본·재집계 안내](week7/SHARING.md)의 명령으로 재집계한다. 재집계에는 BGE 실행 환경이나 원격 서버가 필요하지 않다. 선택적인 새 BGE 실행 의존성은 `requirements-bge.txt`에 분리되어 있다.

### Git 관리

공유 파일 목록과 체크섬은 `manifest.json`의 `files`에 함께 기록한다. `manifest.json` 자체는 체크섬 대상에서 제외한다. 의도적으로 공유 파일을 수정한 경우 `scripts/refresh_manifest.py`를 실행해 갱신한다. Git 정보가 없는 배포본에서도 기존 manifest의 파일 목록을 사용한다. 해당 도구는 stage·commit·push하지 않는다.

과거 이력은 Git에 남아 있으므로 전체 clone 크기는 현재 파일 합계보다 크다. 최신 상태만 받으려면 shallow clone을 사용한다. 로컬 복구 기록과 개인 환경은 공유하지 않는다.
