# 성남시 수정구 민원 · Week 3

[01 Graph Schema](docs/01_graph_schema.md) · [02 Graph Construct](docs/02_graph_construct.md) · [03 Graph Retrieval](docs/03_graph_retrieval.md) · [04 Fail Case 분석](docs/04_fail_case_analysis.md)

[질문 204개](questions.md) · [질문 데이터](questions.jsonl) · [서비스 40개](data/services.jsonl) · [관계 그림](figures/index.html)

민원 질문 204개, 잠정 의도군 179개, 원문 91개와 청크 6,768개를 담은 제출 자료다. 보조 회귀와 fixture 문항은 질문 수에 포함하지 않는다. 일반·일상·화남·재촉·불신 표현을 포함한다. 말투 라벨은 작성·검토 과정의 분류이며 실제 주민 발화 통계를 뜻하지 않는다.

질문과 Gold는 같은 확보 자료에 기반한 합성 개발 데이터다. 사람 검수와 독립 Gold, 법령 현행성 확인은 없다. 204문항을 7가지 검색 방식으로 실행했으며, 검색 정답이 있는 177문항에서 필수 근거 회수를 평가했다. 추가 확인 17개와 응답 보류 10개는 행동 미평가 진단이다. Hybrid는 139/177개에서 필수 근거를 모두 찾았다. 이 수치는 답변 생성의 정확도나 결론의 의미적 타당성을 뜻하지 않는다. 출처 기준일은 2026-09-24다. 성남시 안내와 전국 공통 규정을 구분하며 수정구의 특정 창구·기기 운영을 확정하지 않는다.

## 실행

Python 3.12와 NetworkX 3.4.2가 필요하다. 이 폴더에서 다음 명령을 실행한다.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/submission_cli.py restore
.venv/bin/python scripts/submission_cli.py question SJ-A-071
.venv/bin/python scripts/submission_cli.py family SJ-A-033
.venv/bin/python scripts/submission_cli.py service SJ-SVC-001
.venv/bin/python scripts/submission_cli.py document G004
.venv/bin/python scripts/submission_cli.py search '등본을 대신 발급받으려면 뭘 가져가야 하나요?' --hops 2
.venv/bin/python scripts/submission_cli.py verify
```

원문 DB와 HTML은 `data/corpus-sqlite.zip.001`부터 이어지는 세 조각에서 폴더 안으로 복원된다. 세 파일은 하나의 원문 ZIP을 나눈 자산이다. 기존 파일이 다른 내용이면 덮어쓰지 않는다. `questions`는 204문항 전체, `documents`는 원문 목록, `chunk`와 `evidence`는 청크와 인용을 반환한다. `inventory`는 제출 파일의 해시와 자료 수를 반환한다. 제공된 Graph 검색과 검증에는 네트워크 연결이나 모델 API가 필요하지 않다. `results/current204/predictions.jsonl`에 저장된 7가지 방식의 상위 10개로 문항별 판정과 집계 지표를 검증한다. Dense·Hybrid의 저장 결과를 읽는 검증은 임베딩을 다시 생성하지 않는다.

`data/questions.sqlite`의 질문·서비스·Gold·의도군은 JSONL과 같은 내용이다. 같은 의도군은 평가 분할에서 함께 취급하며 말투 변화만으로 독립 의도 수를 늘리지 않는다.
