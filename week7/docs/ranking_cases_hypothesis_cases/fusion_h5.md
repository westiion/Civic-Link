# 가설 5: BGE와 의미 유사도 순위 결합

같은 질문·후보 집합에서 기존 BGE 순위, 임베딩 유사도 단독 순위, 두 순위의 RRF 결합을 비교한다. H1~H4 변경은 누적하지 않는다. 후보를 추가하거나 BGE를 다시 실행하지 않는다.

H1 청크 벡터(제목+본문)와 H2 질문 벡터를 읽기 전용으로 재사용한다. 모델·엔드포인트 식별 해시·입력 형식·instruction·차원·캐시 해시·질문 원문을 확인한다. 회사 API 호출은 없다. 실제 서버의 내부 revision과 잘림 설정까지 검증한 것은 아니다.

## 고정 설정

청크와 질문의 정규화 벡터 내적을 코사인 유사도로 사용한다. 모든 기존 후보에 대해 유사도 순위를 구하고 다음 점수를 계산한다.

`RRF = 1/(60 + BGE 순위) + 1/(60 + 의미 유사도 순위)`

기본 동일 가중치, k=60은 실험 전 고정 설정이며 최적값으로 주장하지 않는다. 원점수의 척도가 다르므로 원점수를 더하지 않는다. 동점은 기존 BGE 순위로 결정한다. 모든 조건은 전체 후보를 보존하고 상위 10개를 평가한다. 기존 후보 수는 문항마다 다르며 100개로 자르지 않는다.

임베딩 단독 대조군은 결합 효과와 임베딩 순위 자체의 효과를 구분하는 진단용이다. BGE·임베딩은 독립적인 정보원이라고 가정하지 않는다. 순위 결합은 점수 차이의 크기를 버리고 두 순위를 동일하게 반영하므로, BGE가 정확하게 평가한 근거도 밀릴 수 있다.

## 실행

```powershell
.\.venv\Scripts\python.exe scripts\run_week7_fusion.py prepare
.\.venv\Scripts\python.exe scripts\run_week7_fusion.py rank
.\.venv\Scripts\python.exe scripts\run_week7_fusion.py evaluate
```

결과: week7/results/ranking_cases_hypotheses/h5_fusion. prepare는 캐시와 입력을 확인하고 설정을 고정한다. rank는 모든 후보의 BGE 점수·순위, 코사인 유사도·순위, 두 RRF 기여분과 최종 점수를 rankings.jsonl에 남긴다. evaluate만 정답표를 읽는다. 실행 완료 후 기존 ranking_cases_hypotheses/analysis.md에 분석을 추가한다.

488개 answer 문항의 완전회수@10, Recall@10, MRR, 유형별 개선·악화를 비교한다. 회복된 근거가 BGE 몇 위였고 의미 유사도 몇 위였는지, 기존 정답이 결합으로 밀린 사례도 함께 확인한다. 전체 후보가 같으므로 후보 자체의 회수 범위 확대를 주장하지 않는다. 개발셋 결과이며 독립 검증이나 생성 답변 정확도 평가는 아니다.
