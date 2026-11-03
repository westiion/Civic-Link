# 유형 1 항목별 채점 실험

기존 512토큰 BGE 직접 정렬에서 실패한 유형 1의 14문항을 같은 후보로 다시 정렬한다. 민원 안내에서 원문 제목·항목명·값을 한 입력으로 만들고 항목별 BGE 원점수의 최댓값을 그 청크의 점수로 쓴다. 두 개 이상의 항목으로 나눌 수 없는 청크는 기존 원점수를 사용한다. 세 번째 조건은 청크 전체 순위와 항목별 순위의 단순 평균이다. 동점이면 기존 청크 전체 순위를 따른다. [실험 결과](../../week7/failure_analysis/ranking-cases-512.md#유형-1-항목별-채점-결과)

### 저장된 점수로 재현

저장소 루트에서 실행한다. 준비 단계에서는 기존 검색 후보와 복원된 코퍼스 `data/corpus/civic.sqlite`를 읽는다. 집계만 할 때는 모델이 필요하지 않다.

```powershell
python -B experiments/type1_fields/prepare.py --repo . --out .cache/type1-fields/inputs
python -B experiments/type1_fields/aggregate.py --repo . --candidates .cache/type1-fields/inputs/candidates.jsonl --inputs .cache/type1-fields/inputs/inputs.jsonl --scores week7/failure_analysis/results/type1-fields/scores.jsonl --out .cache/type1-fields/results
```

### BGE 점수부터 재측정

`BAAI/bge-reranker-v2-m3`의 기존 버전(`953dc6f`)이 저장된 로컬 모델 폴더를 지정한다. 원래 14문항에서 새로 필요한 항목과 비교용 전체 청크만 오프라인 CPU로 채점한다. 중간 점수는 지정한 실행 폴더에 보존된다. 아래를 실행하기 전에 위의 `prepare.py` 단계를 완료한다.

```powershell
python -B experiments/type1_fields/score.py --inputs .cache/type1-fields/inputs/inputs.jsonl --model-dir "<MODEL_DIR>" --out .cache/type1-fields/run
python -B experiments/type1_fields/aggregate.py --repo . --candidates .cache/type1-fields/inputs/candidates.jsonl --inputs .cache/type1-fields/inputs/inputs.jsonl --scores .cache/type1-fields/run/scores.jsonl --out .cache/type1-fields/results
```
