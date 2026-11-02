# Week7 새 가설 1: 출처·내용 편중 완화

이 실험은 회사 임베딩 API를 사용하는 새 다섯 가설 중 1번이다. 이전 질문 분해·규칙 기반 참조 선택·CPU 문맥 실험은 현재 작업 경로에서 제외하고 `week7/local/retired_hypotheses_20261103/`에 보관했다.
실행 스크립트: `scripts/run_week7_diversity.py`.

## 가설과 통제

질문 전체와 관련된 비슷한 청크가 상위를 독점할 때 출처·내용의 중복을 감점하면 다른 필수 근거가 top10에 남는가?
대표 관찰은 SJ-A-003의 top10이 성남시 표 행들에 집중되고 정부24 근거가 63위인 현상이다.
출처가 다르다는 것과 질문의 다른 요구를 충족한다는 것은 다르다. 다양성을 높였다는 이유만으로 가설을 지지하지 않는다.

608문항의 기존 후보 68,430쌍과 BGE 원점수를 고정한다. 답변 가능 488문항 전체를 평가하고 원 기준선 437/488과 비교한다.
고유 후보 청크는 5,474개다. 현재 공유 DB에는 벡터가 없어 회사 API로 각 청크를 한 번만 계산하고 SQLite에 저장한다.
질문 분해·재검색·BGE 추론·최종 답변 생성은 하지 않는다. 정답표는 `evaluate`에서만 읽는다.

| 조건 | 적용 |
|---|---|
| bge_direct | 기존 BGE 점수순, 동점은 chunk_id |
| source_penalty | 같은 document_id가 이미 선택됐으면 출처 감점 |
| semantic_penalty | 선택한 청크와의 최대 코사인 유사도에 따른 내용 감점 |
| source_semantic_penalty | 출처·내용 감점 모두 적용 |

후보별 BGE 점수를 질문 내 min-max로 0~1 정규화한다(모두 같으면 1).
선택 점수 = 정규화 BGE − 0.15 × 출처 중복 여부 − 0.15 × 의미 중복도.
의미 중복도 = max(0, (선택한 청크와의 최대 코사인 유사도 − 0.8) / 0.2).
각 조건에서 해당하지 않는 감점은 0이다. 아직 선택한 청크가 없으면 감점도 0이다.
이를 재계산하며 10개를 순차 선택하고 나머지는 기존 BGE 순위로 둔다. 후보를 삭제하거나 반환 수를 늘리지 않는다.
가중치 0.15·문턱 0.8은 사전 고정한 첫 비교안이지 최적값이 아니다. `prepare --strength ... --threshold ...`로 바꾸면 새 폴더에서 별도 개발 실험으로 기록한다.
같은 문서의 예외·조건이 함께 필요한 경우 또는 유사한 문장이 다른 조건을 다루는 경우 감점이 악영향을 줄 수 있다. 개선·회귀를 같이 평가한다.

## 실행 — 기존 .venv 사용, GPU/torch 불필요

VPN 연결 후 회사 API 설정을 같은 PowerShell 창에 둔다. 실제 값으로 설정되어 있으면 다시 입력하지 않는다.
URL은 `http://실제IP:포트/v1` 형태의 순수 문자열이어야 한다. Markdown 링크를 붙이지 않는다.

```powershell
cd C:\Develop\Civic-Link
$env:CIVIC_EMBEDDING_URL = "http://실제IP:포트/v1"
$env:CIVIC_EMBEDDING_MODEL = "Qwen3-VL-Embedding-8B"
```

입력 준비(첫 실행만):

```powershell
.\.venv\Scripts\python.exe scripts\run_week7_diversity.py prepare
```

**먼저 5개 요청만 실행한다.** 전체 임베딩 계산은 서버 속도에 따라 길어질 수 있으므로 이 출력의 예상 시간과 오류 유무를 먼저 확인한다.

```powershell
.\.venv\Scripts\python.exe scripts\run_week7_diversity.py embed --limit 5
```

정상 연결과 예상 시간을 확인한 뒤 전체 벡터를 확보한다. 앞서 성공한 5개는 재요청하지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\run_week7_diversity.py embed
.\.venv\Scripts\python.exe scripts\run_week7_diversity.py rank
.\.venv\Scripts\python.exe scripts\run_week7_diversity.py evaluate
```

임베딩 API는 동시 요청 없이 청크당 한 요청이다. 25개마다 진행률·예상 잔여 시간을 표시한다.
`Ctrl+C` 후 같은 embed 명령을 실행하면 완료된 청크는 건너뛴다. 요청 중이던 청크만 다시 요청될 수 있다.
중단 이후 서버 모델 배포를 바꾸지 않는다. 서버 내부 revision·잘림 설정은 이 클라이언트가 확인하지 못하므로 별도 운영 정보가 있으면 함께 기록한다.
클라이언트는 원문을 자르지 않는다. 너무 긴 입력/요청 형식 오류는 멈추며, 조용히 다른 방식으로 전환하지 않는다.

Qwen3-VL 기본 요청은 `/embeddings`에 `messages`(system 지시 + user 텍스트)를 보내는 방식이다.
[vLLM 공식 Embedding API 설명](https://docs.vllm.ai/en/v0.31.0/models/pooling_models/embed/)에 따른 형식이며 실제 회사 서버에서는 5개 사전 확인이 필요하다.
서버가 `input` 문자열만 받도록 구성되어 있다면 서버 안내를 확인한 뒤 `--format text`로 **새 출력 폴더**에서 시작한다. 형식을 섞어 캐시를 재사용하지 않는다.
인증이 필요하면 `CIVIC_EMBEDDING_API_KEY`를 환경변수로 지정한다. API 키는 결과 파일에 저장하지 않는다.

## 분석할 파일

기본 출력: `week7/results/ranking_cases_hypotheses/h1_diversity/`

- `report.md`: 4개 조건 전체 지표.
- `summary.json`: 전체·질문 유형별 지표, 개선/악화 ID.
- `per_question.jsonl`: 정답 근거별 최초 순위. 정답표를 사용하는 사후 평가 자료.
- `rankings.jsonl`: 전체 순위, top10 선택 시 BGE 점수·감점·코사인 유사도·출처, 출처 편중 통계.
- `protocol.json`, `rank.run.json`, `embedding.run.json`: 설정·입력·출력 지문과 실행 기록.
- `vectors.sqlite`: 재시작 가능한 벡터 캐시. 크기가 커질 수 있어 Git에서 제외했다. 분석 공유에 필수 아님.

완료되면 report.md·summary.json을 먼저 공유하고 사례 검증에는 per_question.jsonl·rankings.jsonl을 사용한다.
기준선 재현, 전체 완전회수/Recall/MRR, 기존 성공 상실, 대표 비교 질문의 반대쪽 근거 순위를 함께 확인한다.
현 벤치마크의 실패를 보고 만든 정책이므로 개발셋 개선 결과로 보고한다.
