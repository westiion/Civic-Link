# Hybrid+Graph → BGE 직접 정렬

실험 2의 후보·원점수 캐시를 재사용한 2026-11-01 추가 평가다. 원본 H3·coverage·BGE 결과와 Qwen 생성·채점 결과는 수정하지 않았다.

| 기준 | 완전회수 | Recall | MRR |
|---|---:|---:|---:|
| @10 | 437/488 · 89.54918032786885% | 0.9325819672131148 | 0.8336098100442363 |
| @100 | 481/488 · 98.5655737704918% | 0.9904371584699453 | 0.8354044364674488 |

488개의 `expected_action=answer` 문항을 평가한다. 608문항 전체의 순위는 저장하며, clarify·abstain 각 60개는 지표 분모에서 제외한다. 기존 근거 묶음 정의를 사용해 문항별 Recall·MRR·완전회수의 평균을 계산한다. @100은 전체 후보를 정렬한 뒤 평가한다.

## 후보와 원점수

- 원본: `week7/results/experiment2_20261031/{h3,coverage,bge}.jsonl` 및 실행 메타데이터.
- Hybrid 상위 100개와 허용 Graph 상위 최대 100개의 `chunk_id` 합집합: 608문항, 총 68,430쌍, 질문당 100~184개.
- 후보 목록과 Hybrid 동결 캐시를 대조하고, H3·coverage·BGE의 후보 집합 동일성, query/chunk 중복 없음, 유한 원점수·캐시 전량성을 확인했다.
- 모델: `BAAI/bge-reranker-v2-m3`, revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`.
- 질문과 `title + '\n' + text`를 쌍으로 입력, 최대 512토큰, `only_second` 절단, PyTorch MPS float16, batch 32, SDPA. 원 실행의 torch 2.9.0·transformers 4.57.1 등 설정은 `metrics.json`에 보존했다.
- 원점수 전부 재사용, 새 추론·네트워크·모델 로딩 0회.

정렬 키는 `(-bge_raw_score, chunk_id)`다. 원점수를 반올림하거나 점수를 혼합하지 않는다. 정확한 동점에서만 Python 문자열 오름차순을 적용하며, 저장된 H3·coverage·역순 입력을 각각 사용해 전량 재현성을 검증했다. 587문항에 정확한 동점이 있으며 동점 그룹은 2,838개, 동점 후보는 5,951개다.

기존 H3→BGE 대비 top-10 순서 17문항, coverage→BGE 대비 18문항이 다르다. @10의 세 지표와 @100의 완전회수·Recall은 같다. `W7V2-LA046`의 첫 근거만 31→32위가 되어 MRR@100이 `0.8354065021734245`에서 `0.8354044364674488`로 낮아졌다. 생성 답변의 품질 변화는 이 평가에서 측정하지 않았다.

## 파일과 재집계

이 폴더의 `rankings.csv`는 헤더 외 68,430행이다. `query_id, chunk_id, bge_raw_score, final_rank`를 저장하며, 순위는 1부터 시작한다. 원점수는 `repr(float)`로 출력해 캐시의 이진 부동소수 값을 정확히 다시 읽을 수 있다. CSV를 다시 읽어 전량 원점수·순위 일치도 검증했다. `metrics.json`은 정밀 집계값·실행 설정, `per_question.jsonl`은 488개 문항별 지표, `manifest.json`은 입력·출력 파일의 지문이다.

저장소 루트에서, 현재 Python 의존성과 복원된 `data/corpus/civic.sqlite`가 있는 환경에서 실행한다. 재집계는 BGE·PyTorch·원본 추론 캐시를 필요로 하지 않는다.

```sh
.venv/bin/python scripts/evaluate_week7_bge_direct.py \
  --rankings week7/results/experiment2_bge_direct_20261101/rankings.csv \
  --output-dir outputs/bge-direct-recheck
```

원본 후보와 원점수의 전체 검증까지 다시 하려면 다음과 같이 실행한다. 두 명령 모두 새 출력 폴더를 지정하며 기존 결과를 덮어쓰지 않는다.

```sh
.venv/bin/python scripts/evaluate_week7_bge_direct.py \
  --output-dir outputs/bge-direct-from-cache
```

## 검증 범위

원본 캐시와 현재 질문·정답의 지문, 608개 질문 텍스트, 전체 후보 합집합과 원점수의 대응, 정확한 동점 처리, 전량 CSV 왕복, CSV 단독 재집계 일치를 확인했다. 기존 평가 함수의 488×6개 지표를 별도의 근거 묶음 최소 순위 계산과 대조했다.

최초 직접 정렬 검증에서는 관련 테스트 32개, 구형 실험 테스트 두 모듈을 제외한 84개가 통과했다. 이후 Git 공유본 정리에서 구형 import와 입력 경로를 현재 `civic_link` 구조로 옮기고 전체 테스트 범위를 복구했다. 최종 현재 실행 수와 깨끗한 공유본 검증은 [공유 안내](../../SHARING.md) 및 `week7/evaluation/experiment2/sharing_verification.json`을 참고한다. 루트 공유 manifest도 현재 필수 결과 전체에 맞춰 갱신한다. 과거 104개 테스트 기록과 부분 실행 84개를 현재 전체 통과 수로 바꾸어 기록하지 않는다.
