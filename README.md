# Civic-Link

Civic-Link는 행정·법률 민원 질문에 대해 관련 서비스와 공식 근거를 함께 찾기 위한 Graph RAG 프로젝트입니다. 성남시 수정구 민원 사례를 중심으로, 행정 서비스·법령·조문·필요 서류 사이의 관계를 구조화하고 검색 품질을 검증합니다.

이 저장소의 `data`, `docs`, `scripts`, `results`는 특정 주차별 복사본이 아니라 매주 피드백을 반영하며 계속 발전시키는 중심 프로젝트입니다. 주차별 발표 자료나 별도로 보여 줄 결과물은 `week3`, `week4`와 같은 폴더에 구분해 보존합니다.

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
