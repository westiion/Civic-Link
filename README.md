# Civic-Link

Civic-Link는 수정구청의 무인민원발급 서비스에 적용하기 위한 행정·법률 기반 Graph RAG 시스템 구축 프로젝트입니다. 행정 서비스와 필요 서류, 근거 조문 및 관련 법령 간의 관계를 지식 그래프로 구조화하고, 이를 바탕으로 사용자의 질문에 적합한 행정 서비스를 안내하며 해당 서비스의 공식적인 법적·행정적 근거를 함께 제시하는 것을 목표로 합니다.

> 인하대학교 탄소중립 아카데미 7기 산업체 연계 프로젝트 · 마음AI

## 진행 과정

검색 결과와 실패 사례를 분석하며 검색 구조를 단계적으로 확장하고 있습니다.

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

## 저장소 구조

```text
```text
data/       행정 서비스·질문·원문 및 검색용 그래프 데이터
docs/       그래프 설계·검색 방식 및 결과 분석 문서
results/    질문별 검색 결과와 검색 방식별 평가 결과
scripts/    데이터 복원·그래프 구축·검색 및 검증 코드
weekN/      N주차 발표 및 제출 자료
```

## 실행

Python 3.12와 NetworkX 3.4.2가 필요합니다.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/submission_cli.py restore
.venv/bin/python scripts/submission_cli.py search '등본을 대신 발급받으려면 뭘 가져가야 하나요?' --hops 2
.venv/bin/python scripts/submission_cli.py verify
```

Windows PowerShell에서는 가상환경의 Python 경로를 `.venv/Scripts/python.exe`로 바꾸면 됩니다.
