# 가설 4: 같은 조항의 인접 청크 선택

기존 BGE의 후보와 점수를 고정한다. H1~H3 변경은 누적하지 않는다. API·임베딩·로컬 BGE 재실행은 없다.

동일 document_id와 정확히 같은 조항 locator(제N조 또는 제N조의N)를 갖고 원래 그래프 Article에 대응되는 청크만 같은 조항으로 인정한다. 문서 ordinal 차이가 정확히 1인 청크만 인접하다고 본다. 후보에 없는 중간 청크를 건너뛰어 인접하다고 보지 않는다. 문서 머리말, 다른 조항, 다른 문서·버전은 연결하지 않는다. 청크를 병합하거나 새 내용을 생성하지 않는다.

## 고정 정책과 대조군

- bge_direct: 기존 순위.
- same_article_control: 상위 8개를 보호하고, 그 청크와 같은 조항의 기존 후보를 BGE 점수순으로 최대 2개 선택한다. 거리 제한은 없다.
- adjacent_article: 동일 정책에서 ordinal 차이 1만 허용한다.

선택한 청크를 9위부터 배치하고 남은 순서는 기존 BGE 순서를 유지한다. 중복 제거, 재귀 확장 금지, 전체 후보 보존을 적용한다. 기본 slots=2는 실험 전 고정한 설정이며 최적값이 아니다. 기존 9~10위 필수 근거가 밀리는 위험을 평가해야 한다. 여러 조항이 두 청크 이하라면 두 실험군이 같을 수 있으므로 실제 차이와 적용 가능 문항 수를 보고한다.

이 실험은 분할된 근거의 회수 여부를 평가한다. 인접성을 이용한 성공만으로 원래 실패 원인이 문맥 잘림이었다고 단정하지 않는다. 추가된 청크가 필요한 항을 담는지 원문 검수가 필요하다.

## 실행

```powershell
.\.venv\Scripts\python.exe scripts\run_week7_neighbors.py prepare
.\.venv\Scripts\python.exe scripts\run_week7_neighbors.py rank
.\.venv\Scripts\python.exe scripts\run_week7_neighbors.py evaluate
```

결과: week7/results/ranking_cases_hypotheses/h4_neighbors. prepare는 원래 코퍼스·그래프를 검증하고 neighbor_index.json에 조항 ID, 청크 ordinal, 원문 구간을 저장한다. rankings.jsonl은 선택 후보와 출발 청크·거리·원래 순위를 기록한다. evaluate만 정답표를 읽는다.

488개 answer 문항의 완전회수@10, Recall@10, MRR, 개선·악화 사례를 비교한다. 전체 608문항 중 적용 가능 문항도 구분한다. 동일 조항 대조군 대비 인접 제한의 효과와 기존 9~10위 근거 손실을 확인한다. 결과가 나오면 기존 ranking_cases_hypotheses/analysis.md에 이어서 분석한다. 독립 검증이나 답변 생성 평가는 아니다.
