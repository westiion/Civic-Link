## Civic-Link

Civic-Link는 수정구청의 무인민원발급 서비스에 적용하기 위한 행정·법률 기반 Graph RAG 시스템 구축 프로젝트입니다. 행정 서비스와 필요 서류, 근거 조문 및 관련 법령 간의 관계를 지식 그래프로 구조화하고, 이를 바탕으로 사용자의 질문에 적합한 행정 서비스를 안내하며 해당 서비스의 공식적인 법적·행정적 근거를 함께 제시하는 것을 목표로 합니다.

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
weekN/      N주차 발표 및 제출 자료
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

실행 결과는 `results/runs/<실행시각>/`에 방법별 예측, 지표, 실패 사례와 실행 설정으로 저장됩니다. 현재 공유 기준 실행은 `results/runs/20261026_232637/`입니다.

#### 개발 검증

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe scripts\submission_cli.py verify
```
