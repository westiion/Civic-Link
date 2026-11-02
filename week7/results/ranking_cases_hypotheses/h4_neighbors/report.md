# Week7 가설 실험 결과

같은 후보·청크 ID, 상위 10개. 답변 생성 평가는 아님. 분석에 사용한 개발셋 결과.

| 조건 | 완전회수@10 | Recall@10 | MRR@10 | 완전회수@100 |
|---|---:|---:|---:|---:|
| bge_direct | 437/488 | 93.26% | 0.8336 | 481 |
| same_article_control | 437/488 | 93.39% | 0.8338 | 481 |
| adjacent_article | 437/488 | 93.39% | 0.8338 | 481 |

문항별 순위는 rankings.jsonl, 근거 그룹별 순위는 per_question.jsonl, 유형별 지표·개선/악화 ID는 summary.json에 기록한다.