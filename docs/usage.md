# Week 7 · Git 실행 안내

608문항(기존 204 + 신규 404)과 균형 진단 부분집합 480개를 제공한다. 신규 404개·근거 590건 검토 후 21문항의 보조 주석과 확인 정책을 수정했다. 질문 ID·원문·검색 정답은 유지했다.

[결과 보고서](../experiments/score_comparison/report.md) · [전체 질문](../data/benchmark/exports/questions.md) · [신규 질문](../data/benchmark/exports/new_questions.md) · [AI 검수](../data/benchmark/reviews/agent_review.csv)

## 설치 → 현재 DB 복원 → 검색 → 평가

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

출력 경로가 이미 있으면 새 이름을 사용한다. Windows에서는 `.venv/bin/python`을 `.venv/Scripts/python.exe`로 바꾼다. 기대 결과는 Complete@10 **411/488 (84.22%)**다. 기본 행동 라벨과 허용 행동 대안은 별도로 평가하며, 검색 점수로 행동·답변 품질을 추정하지 않는다.

## 남긴 입력과 복원 범위

- `data/corpus/corpus.sqlite.gz`: 약 3.1MB. 원문 91개·청크 6,768개·서비스 연결을 담은 현재 검색 DB 약 14.9MB만 복원한다. 임베딩·과거 실행 결과·환경은 복원하지 않는다.
- `data/retrieval/hybrid_candidates.jsonl.gz`: 약 10.6MB. 고정 Hybrid 후보를 압축 상태로 직접 읽는다. 55.9MB의 별도 복원본을 만들지 않는다.
- `data/retrieval/hybrid_candidates.run.json`: 입력 질의와 후보의 일치 검사용 메타데이터다. 다른 질문이나 후보를 섞으면 실행을 거부한다.
- `data/benchmark/`: 현재 정답·질의와 균형 480개 부분집합. `data/benchmark/reviews/`와 작은 검수 전 fixture는 수정 이력 검증에 필요하다.
- `experiments/score_comparison/results/`: 개발·검증 분리, 정책 선택, 항별 비교, 개선·퇴보 및 재현 결과다.

압축 입력 두 개는 전송용 ZIP이 아니라 현재 실행에 필요한 동결 데이터다. SQLite는 파일로 열어야 하므로 현재 DB만 압축본과 함께 존재한다. 복원된 DB와 실행 결과는 Git에서 제외한다. 원본과 다른 기존 DB는 덮어쓰지 않는다.

## 기준선과 수식 비교

위 검색 명령에서 `--rerank feature`와 `--output outputs/baseline/h3.jsonl`을 사용하면 기존 H3 기준선을 생성한다. 동일 질문·후보·정답에서 기존 H3는 391/488이고 새 수식은 30개 개선·10개 퇴보다.

```sh
.venv/bin/python scripts/run_score_comparison.py --output-dir outputs/new-ablation
.venv/bin/python scripts/analyze_score_comparison.py --output-dir outputs/new-ablation
```

저장된 Hybrid 후보 이후만 재현한다. 새 Hybrid 후보 생성, 과거 Week3/4 제출, 벤치마크 초안 생성, BGE 실행 환경은 현재 지원 범위에서 제거했다. 관련 구형 테스트·제출 manifest도 제거했고, 현재 수식·입력 검증·근거·행동·평가 테스트는 유지했다.

## Git 관리

`file_inventory.txt`는 현재 공유 파일의 전체 목록이다. ZIP 생성이나 별도 업로드는 없다. 의도적으로 공유 파일을 수정한 경우 `scripts/refresh_manifest.py`를 실행해 목록과 검증 manifest를 갱신한다. 해당 도구는 stage·commit·push하지 않는다.

과거 이력은 Git에 남아 있으므로 전체 clone 크기는 현재 파일 합계보다 크다. 최신 상태만 받으려면 shallow clone을 사용한다. 로컬 복구 기록과 개인 환경은 공유하지 않는다.
