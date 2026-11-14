# Civic-Link

Civic-Link는 **수정구청의 무인민원발급 서비스에 적용하기 위한 행정·법률 기반 Graph RAG 프로젝트**입니다. 행정 서비스, 필요 서류, 처리기관, 근거 조문과 관련 법령을 연결하고, 민원 질문에 맞는 안내와 공식 원문 근거를 함께 제공하는 것을 목표로 합니다.

> 인하대학교 탄소중립 아카데미 7기 산업체 연계 프로젝트 · 마음AI

## 프로젝트 진행 과정

| 주차 | 주요 내용 | 자료 |
|---|---|---|
| Week 1 | 대상 서비스와 민원 질문 범위, 평가 데이터 구조 정의 | — |
| Week 2 | 행정 안내·법령 수집, BM25·Dense·Hybrid 검색 기반 구축 | — |
| Week 3 | 서비스·서류·기관·조문 관계 구조화, Graph Retriever 구현 | — |
| Week 4 | Hybrid 기반 Graph 확장, 토큰화·참조·누락 근거 보완 | [보고서](week4/REPORT.md) |
| Week 7 | 벤치마크 검수, coverage·BGE 비교, 검색 실패 원인 분석 | [보고서](week7/REPORT.md) · [실험 2](week7/EXPERIMENT2.md) |
| Week 8 | Qwen 에이전트와 Graph 도구 구현, 탐색 방식 비교, 복합 벤치마크 확장 | [에이전트](week8/AGENT.md) · [비교 실험](week8/EXPERIMENT.md) · [벤치마크](week8/REPORT.md) |

주차별 설계, 실험 결과와 분석은 각 보고서에 정리합니다.

## 저장소 안내

| 위치 | 내용 |
|---|---|
| [data/](data/) | 행정 서비스 목록, 원문 코퍼스, 지식 그래프, 검색 후보와 평가 데이터 |
| [src/civic_link/](src/civic_link/) | Graph 구축, 검색·재정렬, 벤치마크·평가 공통 코드 |
| [src/civic_link/agent/](src/civic_link/agent/) | Qwen 에이전트 제어, 내용 검색, Graph 조회와 비교 평가 |
| [scripts/](scripts/) | 데이터 처리, 검색·실험·평가 도구 |
| [tests/](tests/) | 기능별 테스트와 테스트 데이터 |
| [week4/REPORT.md](week4/REPORT.md) | Hybrid+Graph 확장 구현과 성능 분석 보고서 |
| [week7/](week7/) | 벤치마크 검수, 검색 개선 실험과 실패 사례 분석 |
| [week8/](week8/) | 에이전트 설계, 탐색 방식 비교, 복합 벤치마크와 사례 검토 |
