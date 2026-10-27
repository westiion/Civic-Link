## Civic-Link

최신 `20261027_091116`의 다섯 관계 Extension·H1·H2·H3 결과는 [통합 보고서](week4/analysis/REPORT.md)에 정리했습니다. [두 관계→다섯 관계 비교 기록](week4/analysis/RELATION_ABLATION.md)도 함께 보존합니다.

Civic-Link는 수정구청의 무인민원발급 서비스에 적용하기 위한 행정·법률 기반 Graph RAG 시스템 구축 프로젝트입니다. 행정 서비스와 필요 서류, 근거 조문 및 관련 법령 간의 관계를 지식 그래프로 구조화하고, 이를 바탕으로 사용자의 질문에 적합한 행정 서비스를 안내하며 해당 서비스의 공식적인 법적·행정적 근거를 함께 제시하는 것을 목표로 합니다.

현재 BM25와 Graph의 문자열 기반 점수는 동일한 Kiwi 형태소·표면어 토큰화를 사용합니다. Dense는 임베딩 모델의 자체 토크나이저를 사용하며, Hybrid는 BM25와 Dense 순위를 RRF로 결합합니다. Graph는 Hybrid에 자동으로 포함되지 않고 서비스·법령 시작점에서 근거가 연결된 관계를 탐색하는 별도 검색 방식입니다.

Week4의 `hybrid_graph.py`는 저장된 Hybrid 후보에서 Service·Article 시작점을 찾아 관계를 확장한 뒤 두 순위를 결합합니다. H1 시작점 비교, H2 evidence 보완 전후, H3 재정렬 비교는 동일 구현의 옵션으로 실행합니다. 기존 Kiwi·법령 참조 보완을 유지하며, 팀원의 과거 bigram 실험 결과와 구분합니다. [병합 내용과 실행 방법](docs/05_week4_integration.md)

최신 실행의 [Week4 성능·실패 분석 보고서](week4/analysis/REPORT.md), [전체 204개 질문의 검색 경로](week4/analysis/QUESTION_TRACES.md)을 확인할 수 있습니다.

> 인하대학교 탄소중립 아카데미 7기 산업체 연계 프로젝트 · 마음AI

<br>

### 진행 과정

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

<br>

### 저장소 구조

```text
data/       행정 서비스·질문·원문 및 검색용 그래프 데이터
docs/       그래프 설계·검색 방식 및 결과 분석 문서
results/    질문별 검색 결과와 검색 방식별 평가 결과
scripts/    데이터 복원·그래프 구축·검색 및 검증 코드
week4/      Week4 성능·실패 분석 보고서 및 분석 재현 코드
```

<br>

### 실행

Python 3.12와 NetworkX 3.4.2가 필요합니다.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/submission_cli.py restore
.venv/bin/python scripts/submission_cli.py search '등본을 대신 발급받으려면 뭘 가져가야 하나요?' --hops 2
.venv/bin/python scripts/submission_cli.py verify
```

Windows PowerShell에서는 가상환경의 Python 경로를 `.venv/Scripts/python.exe`로 바꾸면 됩니다.

#### 전체 검색 실험

BM25와 Graph는 로컬에서 실행되며, Dense와 Hybrid는 회사 VPN 및 임베딩 API 연결이 필요합니다. API 주소와 키는 저장소 파일에 기록하지 않고 PowerShell 환경변수로 설정합니다.

```powershell
$env:CIVIC_EMBEDDING_URL = "회사에서 전달받은 임베딩 API URL"
$env:CIVIC_EMBEDDING_MODEL = "Qwen3-VL-Embedding-8B"
# API 키가 필요한 서버에서만 설정
$env:CIVIC_EMBEDDING_API_KEY = "발급받은 API 키"

.\.venv\Scripts\python.exe scripts\run_models.py
```

실행 결과는 `results/week4_run/<실행시각>/`에 방법별 예측, 지표, 실패 사례와 실행 설정으로 저장됩니다. 최신 비교 실행은 `results/week4_run/20261027_091116/`이며, 이전 Week3 기준 결과는 `results/week3_run/`에 보관합니다. `week4_run`에는 피드백 반영 전후 실행도 비교용으로 함께 보존합니다.

새 실행은 같은 Hybrid 검색의 상위 후보를 `hybrid_candidates.jsonl`과 `hybrid_candidates.run.json`으로도 저장합니다. 기존 Hybrid 평가는 요청한 Top-10을 그대로 사용합니다. 이 후보를 이용한 Week4 확장·가설 비교는 아래처럼 별도 실행하며 회사 API를 호출하지 않습니다.

```powershell
# <실행시각>은 새 run_models.py 실행 결과 폴더로 바꿉니다.
$run = "results\week4_run\<실행시각>"
.\.venv\Scripts\python.exe scripts\run_week4.py --hybrid-results "$run\hybrid_candidates.jsonl" --hybrid-run "$run\hybrid_candidates.run.json" --output-dir "$run\week4"
```

이 명령은 기존의 Top-10 결과 파일이나 과거 bigram Hybrid 입력을 대체 입력으로 받지 않습니다. 후보 파일을 생성한 뒤 실행해야 합니다.

#### 개발 검증

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe scripts\submission_cli.py verify
```

#### 저장된 결과 복원

대용량 H1 상세 결과는 `.jsonl.gz`로 공유합니다. 원본 JSONL은 로컬에 유지하고 Git에서는 제외합니다. 새로 clone한 뒤 아래 명령으로 원래 바이트와 SHA-256을 검증하며 복원합니다. `verify`도 이 복원을 자동 수행하며 모델 API를 호출하지 않습니다. 기존 파일이 다르면 덮어쓰지 않고 중단합니다.

```powershell
.\.venv\Scripts\python.exe scripts\submission_cli.py restore-results
```

결과 파일 안의 과거 출력 경로는 실험 당시 기록으로 보존합니다. 현재 접근 경로는 위 폴더 구조를 따릅니다.

`requirements.txt`는 검색 실행용 의존성이고, `requirements-dev.txt`는 이를 포함한 뒤 테스트용 `pytest`를 추가합니다. 실행만 할 때는 전자, 개발·검증할 때는 후자를 설치합니다.
