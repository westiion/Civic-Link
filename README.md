# Civic-Link

Civic-Link는 **수정구청의 무인민원발급 서비스에 적용하기 위한 행정·법률 기반 Graph RAG 시스템 구축 프로젝트**입니다. 행정 서비스와 필요 서류, 처리기관, 근거 조문 및 관련 법령의 관계를 지식 그래프로 구조화합니다. 이를 바탕으로 민원인의 질문에 적합한 서비스를 안내하고, 안내 내용의 공식적인 법적·행정적 근거를 함께 제시하는 것을 목표로 합니다.

> 인하대학교 탄소중립 아카데미 7기 산업체 연계 프로젝트 · 마음AI

## 진행 과정

### Week 1 — 문제 및 평가 기준 정의

- 수정구청 무인민원발급을 중심으로 대상 서비스 범위 설정
- 민원 질문 유형과 평가용 데이터 구조 설계
- 질문별 행정 서비스와 공식 근거 연결

### Week 2 — 데이터 및 텍스트 검색 기반 구축

- 행정 서비스 안내와 관련 법령 원문 수집
- BM25, Dense, Hybrid 검색 파이프라인 구현
- 검색 방식별 성능 비교 및 실패 사례 분석

### Week 3 — 지식 그래프 및 관계 검색 구축

- 행정 서비스, 필요 서류, 처리기관, 법령, 조문의 관계 구조화
- 그래프의 노드와 관계를 공식 원문 근거에 연결
- Graph Retriever 구현 및 텍스트 검색과 비교

### Week 4 — Hybrid + Graph 확장 및 개선 검증

- 토큰화·법령 참조·누락 근거 연결 보완
- Hybrid 기반 그래프 확장 검색 구현
- 개선 가설 3개 실험 및 성능 비교

## 저장소 구조

```text
data/
  services.jsonl
  processed/
scripts/
tests/
week7/
  assets/
  inputs/
  data/benchmark/
  reviews/
  evaluation/
  docs/assets/
  README.md
  REPORT.md
```

## 실행

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/restore_week7.py restore

.venv/bin/python scripts/hybrid_graph.py \
  --queries week7/data/benchmark/queries.jsonl \
  --hybrid-results week7/assets/hybrid_candidates.jsonl.gz \
  --hybrid-run week7/inputs/hybrid_candidates.run.json \
  --rerank coverage \
  --output week7/results/team-run/predictions.jsonl
```

Windows에서는 `.venv/bin/python`을 `.venv/Scripts/python.exe`로 바꿉니다.
