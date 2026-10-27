# Week3 피드백 기반 Week4 코드 병합

현재 Kiwi 토큰화, ‘같은 법·이 법·조문 범위’ 연결, 명칭 불일치·부정문 evidence 보완을 유지한 상태에 팀원의 Hybrid+Graph 확장과 세 가지 통제 실험을 추가했다. 이 문서는 구현 안내이며 새 성능 결과는 아니다. 병합 과정에서 회사 모델 호출이나 전체 질문 성능 실험은 수행하지 않는다.

## 검색 흐름

```text
질문 → BM25(Kiwi) + Dense → RRF → Hybrid 후보 100개
  → 상위 10개 청크의 evidence 바인딩 역조회
  → Service/Article 시작점 최대 5개(청크당 2개)
  → GROUNDED_BY·REFERENCES·REQUIRES·HANDLED_BY·PART_OF 정방향 탐색, 최대 2-hop
  → Kiwi IDF 겹침·시작점 점수·hop 감쇠로 Graph 후보 정렬
  → Graph100 + Hybrid100의 RRF → 최종 10개
```

초기 보고서는 GROUNDED_BY·REFERENCES 두 관계만 사용했다. 현재 기본값은 다섯 관계 전체이며, 나머지 예산·점수 설정은 유지한다. 관계 목록은 바인딩 역조회와 탐색 양쪽에 적용된다. 시작 노드 유형은 Service/Article로 유지한다. `hybrid_graph.py`와 `run_week4.py`에서 `--relations GROUNDED_BY REFERENCES`를 지정하면 기존 두 관계 조건을 실행할 수 있다. 다섯 관계의 성능은 별도 통제 실험으로 평가한다.

질문·청크(제목+본문)·인용문은 모두 현재 `tokenization.py`의 Kiwi 토큰을 사용한다. Graph 관련도는 현재 corpus의 IDF를 이용한다. 기본 Graph 단독 검색의 토큰화·점수 코드는 바꾸지 않았다.

## 세 가설의 통제 조건

| 가설 | 이전 조건 | 이후 조건 | 유지하는 것 |
|---|---|---|---|
| H1 시작점 | Kiwi 명칭 매칭(`lexical`) | Hybrid 청크의 바인딩 역조회(`hybrid`) | 둘 다 적격 Service/Article, 시작점 가중치 1, 같은 그래프·후속 정렬 |
| H2 evidence | 검수 범위의 별칭·부정문 보완 꺼짐 | 해당 보완 켜짐 | 최신 법령 참조 파서·노드·엣지·Hybrid·검색 설정 |
| H3 정렬 | 동일 가중치 RRF | 0.5×정규화 RRF + 0.3×청크 관련도 + 0.2×인용문 관련도 | 시작점·경로·실제 최종 후보 풀 |

H1의 `lexical`은 예전 bigram 비교가 아니다. 현재 Kiwi 명칭 선택과 Hybrid 기반 선택을 비교한다. 후보 노드 집합은 두 조건 모두 같은 적격 Service/Article이며, 기존 Graph 단독의 Service/Law 정책 전체를 재현하는 실험도 아니다.

H2는 이미 존재하는 세 evidence 보완의 기여를 분리하는 실험이다. 같은 법·조문 범위를 끄지 않으며 도메인 엣지 변경을 허용하지 않는다. 기본 그래프는 보완이 켜진 상태로 유지하고, 실험용 그래프는 실행 결과 안에 따로 만든다. H2의 바인딩 변경은 시작점 선택에도 영향을 줄 수 있다.

H3는 학습형 모델이 아닌 규칙 기반 재정렬이다. 청크·인용문 관련도는 Kiwi 토큰의 IDF 겹침을 사용한다. 후보 제한으로 제외된 Graph 청크는 후보 풀 Recall에서 제외한다. Hybrid만 있는 결과도 후보로 유지하지만 재정렬 후 Top-10에 항상 남는 것은 아니다.

세 가설은 각각 한 요인만 비교한다. H1→H2→H3를 순차 누적한 성능표로 해석하지 않는다. 후속 조합 실험은 동일한 핵심 모듈에서 옵션을 조합할 수 있다.

## 파일 역할

| 파일 | 역할 |
|---|---|
| [hybrid_graph.py](../scripts/hybrid_graph.py) | 확장·시작점 선택·RRF·재정렬 공통 구현, 입력 검증, 추적 로그 |
| [hybrid_graph_h1.py](../scripts/hybrid_graph_h1.py), [hybrid_graph_h3.py](../scripts/hybrid_graph_h3.py) | 동일 구현을 호출하는 실험 진입점. 복사된 검색 코드 없음 |
| [prepare_h2.py](../scripts/prepare_h2.py) | 최신 빌더로 evidence 전후 그래프 생성·변경 범위 검증 |
| [compare_extension.py](../scripts/compare_extension.py) | 공통 평가·유형별 지표·개선/악화·근거별 후보 탈락 집계 |
| `compare_h1.py`, `compare_h2.py`, `compare_h3.py`, `compare_hybrid_graph.py` | 비교 종류를 고정하는 명령 진입점 |
| [run_week4.py](../scripts/run_week4.py) | 저장된 현재 Hybrid 후보로 확장 및 세 가설 실행·평가 |

## 나중에 실행할 명령

회사 모델 설정 및 문서 임베딩 준비는 기존 실행 방법을 따른다. 아래 첫 명령은 모델 검색을 실행하고, 두 번째는 저장된 후보만 사용한다. 이 문서의 명령을 실행해야 새 성능 결과가 생성된다.

```powershell
$run = Join-Path "results\week4_run" (Get-Date -Format "yyyyMMdd_HHmmss")
.\.venv\Scripts\python.exe scripts\run_models.py --output-dir $run
.\.venv\Scripts\python.exe scripts\run_week4.py --hybrid-results "$run\hybrid_candidates.jsonl" --hybrid-run "$run\hybrid_candidates.run.json" --output-dir "$run\week4"
```

원하는 실험만 실행하려면 두 번째 명령에 `--experiments baseline h1`처럼 지정한다. 동일 출력 디렉터리를 덮어쓰지 않으므로 재실행에는 새 경로를 사용한다.

단일 옵션 조합 실행 예:

```powershell
.\.venv\Scripts\python.exe scripts\hybrid_graph.py --queries data\queries.jsonl --hybrid-results "$run\hybrid_candidates.jsonl" --hybrid-run "$run\hybrid_candidates.run.json" --seed-mode hybrid --seed-weight uniform --rerank feature --output "$run\custom_extension.jsonl"
```

개별 비교 명령은 `--before`, `--after`, `--output-dir`로 파일을 명시한다. H2 비교에는 `--before-graph`, `--after-graph`도 필요하다. 파일명을 하드코딩해 다른 실험을 잘못 읽는 일을 피하도록 변경했다.

## 결과 구조와 해석

```text
<run>/
  hybrid.predictions.jsonl           기존 Top-10 평가 결과
  hybrid_candidates.jsonl            동일 검색의 Hybrid 후보 100개
  hybrid_candidates.run.json         입력 질문·corpus·토큰화 등 실행 기록
  week4/
    week4_manifest.json             완료/실패와 실험 산출물
    predictions/                    조건별 결과와 .run.json
    h2_graphs/before/, after/        H2 전용 그래프, 원본 그래프 유지
    h2_graphs/repair_diff.json       추가 바인딩별 원문·출처
    comparisons/baseline/            Hybrid 대 기본 확장
    comparisons/h1/, h2/, h3/        summary, metrics, cases, per_question, report
```

`report.md`는 자동 집계다. 사람이 원문·경로를 확인한 실패 원인 분석을 대신하지 않는다. 개선·악화 질문, 후보 밖의 근거, 후보 안에서 순위 탈락한 근거를 함께 확인한다. 확장 실행에는 Gold를 전달하지 않고 비교 단계에서만 평가 질문을 읽는다.

실행 기록에는 Kiwi 패키지 버전·점수 버전·공통 소스 해시·그래프/evidence 해시·Hybrid 입력 해시를 남긴다. 현재 토큰화와 맞지 않는 과거 bigram Hybrid 입력은 거절한다. 팀원의 ZIP 결과는 과거 실험 자료로 취급하며 현재 결과 폴더에 덮어쓰지 않았다.

## 검증 범위

테스트는 Kiwi 입력 일관성, 시작점 통제, H3 후보·경로 보존, 후보 제한 처리, 잘못된 입력 거절, Hybrid 후보 내보내기, H2의 법령 참조 보존을 확인한다. 제출 검증은 새 소스도 해시·문법 검사를 하도록 갱신한다. 과거의 주석/docstring 금지 검사는 제거하고 실제 파일·데이터·실험 무결성 검사는 유지한다.
