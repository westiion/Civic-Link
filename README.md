# Civic-Link

Civic-Link는 **수정구청의 무인민원발급 서비스에 적용하기 위한 행정·법률 기반 Graph RAG 시스템 구축 프로젝트**입니다. 행정 서비스와 필요 서류, 처리기관, 근거 조문 및 관련 법령의 관계를 지식 그래프로 구조화합니다. 이를 바탕으로 민원인의 질문에 적합한 서비스를 안내하고, 안내 내용의 공식적인 법적·행정적 근거를 함께 제시하는 것을 목표로 합니다.

> 인하대학교 탄소중립 아카데미 7기 산업체 연계 프로젝트 · 마음AI

## 주차별 발표 보고서

| 주차 | 보고서 | 보존 상태 |
|---|---|---|
| Week 4 | [Hybrid+Graph 확장 구현과 성능 분석](week4/REPORT.md) | Git에 남아 있는 2026-10-27 최종본 복원. 본문·수치는 유지하고 당시 자료 링크만 연결 |
| Week 6 | 원문 미확인 | Git 전체 이력에서 원본을 찾지 못해 별도 보고서를 만들지 않음 |
| Week 7 | [피드백 반영과 검색 개선 실험](week7/REPORT.md) | 기존 보고서와 연결된 실패 분석·실험 기록 보존 |

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

Graph 직접 조회·후보 제공·이어 읽기를 포함한 설계는 [Week8 AGENT](week8/AGENT.md), 한 번 검색 / 검색 반복 / 검색+Graph 반복의 가설과 실행·평가 명령은 [비교 실험 계획](week8/EXPERIMENT.md)을 참고합니다. `scripts/run_agent_comparison.py`는 문항별 결과를 저장하고 같은 설정으로 재개합니다.

**Colab T4에서 전체 1,000문항 A/B/C 실행:** [Colab 실행 안내](week8/COLAB.md) · [실행 노트북 열기](https://colab.research.google.com/github/westiion/Civic-Link/blob/main/week8/run_comparison_colab.ipynb). BGE는 Colab GPU를 사용하고 Qwen 생성·임베딩은 설정한 서버를 호출합니다. 서버 연결 확인이 먼저 필요합니다.

**V100 4장 등 여러 GPU 사용:** 같은 실행기에 `--bge-devices cuda:0 cuda:1 cuda:2 cuda:3`를 지정합니다. 기본적으로 GPU마다 BGE를 한 번 적재하고 서로 다른 문항을 병렬 처리합니다. API 대기가 길고 메모리에 여유가 있으면 `--workers-per-gpu 2 --api-concurrency 8`로 GPU당 작업자를 늘릴 수 있습니다. [설치·실행·재개 안내](week8/EXPERIMENT.md#여러-gpu로-병렬-실행)를 참고합니다.

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

### Git 관리

공유 파일 목록과 체크섬은 `manifest.json`의 `files`에 함께 기록한다. `manifest.json` 자체는 체크섬 대상에서 제외한다. 의도적으로 공유 파일을 수정한 경우 `scripts/refresh_manifest.py`를 실행해 갱신한다. Git 정보가 없는 배포본에서도 기존 manifest의 파일 목록을 사용한다. 해당 도구는 stage·commit·push하지 않는다.

과거 이력은 Git에 남아 있으므로 전체 clone 크기는 현재 파일 합계보다 크다. 최신 상태만 받으려면 shallow clone을 사용한다. 로컬 복구 기록과 개인 환경은 공유하지 않는다.
