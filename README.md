# Civic-Link

Civic-Link는 수정구청의 무인민원발급 관련 민원 사례를 바탕으로, 시민의 질문에 필요한 행정 서비스와 공식적인 법적 근거를 함께 탐색하는 행정·법률 Graph RAG 프로젝트입니다. 행정 서비스, 필요 서류, 근거 조문, 관련 법령 사이의 관계를 지식 그래프로 구조화하고, 실제 민원 질문에 적합한 서비스와 신뢰할 수 있는 근거를 정확하게 검색·제시하는 것을 목표로 합니다.

> 인하대학교 탄소중립 아카데미 7기 산업체 연계 프로젝트 · 마음AI

## 현재 범위

- 민원 질문 204개와 잠정 의도군 179개
- 행정 서비스 40개
- 원문 91개와 청크 6,768개
- Dense, Graph, Hybrid 등 7가지 검색 방식의 비교 결과
- 공식 근거 회수 여부와 실패 사례 분석

질문과 Gold는 같은 확보 자료를 바탕으로 만든 합성 개발 데이터입니다. 사람 검수, 독립 Gold, 법령 현행성 확인은 아직 완료되지 않았으므로 현재 결과는 답변의 법적 정확성을 보증하지 않습니다.

## 저장소 구조

```text
data/       데이터와 가공된 그래프
docs/       그래프 구조·구축·검색·실패 사례 문서
scripts/    데이터 복원, 검색, 검증 실행 코드
results/    현재 기준 실험 및 검증 결과
week3/      3주차에 별도로 제시한 설명과 그림
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

3주차 당시의 상세 내용과 그림은 [`week3/README.md`](week3/README.md)에서 확인할 수 있습니다.
