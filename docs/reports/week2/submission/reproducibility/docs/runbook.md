# Civic-Link 실행 방법

현재 실험은 `benchmark`, 제출 문서는 `docs/reports/week2/submission`에 있다.

## 실행 환경

Python 3.10 이상을 사용. 표준 라이브러리로 동작하며, 설치된 NumPy가 있으면 Dense 전체 cosine 계산을 가속. 추가 모델 다운로드는 필요 없다.

```sh
CIVIC_PYTHON=python3
```

## 현재 결과 확인

```sh
"$CIVIC_PYTHON" scripts/benchmark.py validate --benchmark-dir benchmark
"$CIVIC_PYTHON" -m unittest discover -s tests -v
"$CIVIC_PYTHON" scripts/report_week2.py
```

`report_week2.py`는 저장된 실행 결과에서 제출 문서와 ZIP을 만든다.
`data/processed/civic.sqlite`의 일관된 복사본과 상세 데이터 명세도 `docs/reports/week2/data/`에 갱신.

## VPN Setting

임베딩은 `Qwen3-VL-Embedding-8B`, 채팅 모델은 `Qwen3.8-Flash-Next`을 사용.

```sh
export CIVIC_EMBEDDING_URL=(임베딩 모델 Endpoint)
export CIVIC_EMBEDDING_MODEL=Qwen3-VL-Embedding-8B
export CIVIC_CHAT_URL=(채팅 모델 Endpoint)
export CIVIC_CHAT_MODEL=Qwen3.8-Flash-Next
```

## 전체 실험

```sh
"$CIVIC_PYTHON" scripts/run_week2.py --benchmark-dir benchmark --stage all
```

검색은 세 방식 × 원질문 100건·기준 문장 64건·명확화 후 20건으로 총 9개 실행이다. 
생성은 세 방식마다 원질문 100건, 사후 자동 평가 100건을 실행한다. 
`--stage retrieval` 또는 `--stage generation`으로 나눌 수 있다. 생성 요청 동시 실행 수는 2다.

검색은 코퍼스·질문·설정·인코더 fingerprint와 결과 해시가 맞는 완료 파일을 재사용한다. 
생성은 입력·프롬프트·모델·설정 fingerprint가 같은 응답만 재사용한다. 다른 조건이면 중단한다. 
실패 응답을 성공할 때까지 반복해 좋은 응답만 남기지 않는다. 
서버 장애로 실행이 끊기면 원인을 해결한 뒤 같은 명령으로 완료분을 재사용한다.

`questions.jsonl`이 질문 원본이다. 
`scripts/benchmark.py prepare`는 이 파일에서 입력 전용 JSONL과 manifest를 만든다. 
완료 결과와 입력이 달라지면 덮어쓰지 않고 중단한다. 질문·조건을 바꾸는 실험은 별도 출력 경로에서 수행한다. 
같은 모델명으로 서버 가중치가 바뀌는 경우는 API 설정 해시로 감지할 수 없으므로 별도 실험으로 구분한다.

## 개별 검색

```sh
"$CIVIC_PYTHON" scripts/retrieval.py search --db data/processed/civic.sqlite --query '주거급여 신청 서류는 무엇인가요?' --method bm25
"$CIVIC_PYTHON" scripts/retrieval.py search --db data/processed/civic.sqlite --queries benchmark/queries.jsonl --method dense --embedding-format qwen3-vl --output /tmp/civic-dense-new.jsonl
```

Dense는 4,096차원 벡터의 전체 cosine 검색이다. 
공식 기본 instruction `Represent the user's input.`과 Qwen3-VL의 system/user/assistant 템플릿을 사용한다. 
입력 문자열을 임의로 자르지 않는다. 
Hybrid는 두 검색의 상위 100개를 동일 가중치 `sum(1/(60+rank))`로 결합한다.

## 개별 생성과 평가

```sh
"$CIVIC_PYTHON" scripts/generation.py run --benchmark-dir benchmark --method bm25
"$CIVIC_PYTHON" scripts/generation.py judge --benchmark-dir benchmark --method bm25
"$CIVIC_PYTHON" scripts/generation.py evaluate --benchmark-dir benchmark --method bm25
```

`--method dense`, `--method hybrid`도 같다. 
기본 결과 폴더는 `results/generation_bm25`, `generation_dense`, `generation_hybrid`다. 
입력은 질문·서비스 범위·검색된 청크 본문 또는 제공 근거뿐이다. Gold는 생성이 끝난 뒤 평가에만 사용한다. 
`provided_evidence_only` 8건에는 검색 결과를 넣지 않는다.

## 원문 DB 재구축

`data/graphability-audit/`, `data/raw/`, `data/services.jsonl`이 필요하다. 
대형 원문·SQLite는 로컬에 보존하며 Git에 포함되지 않는다.

```sh
"$CIVIC_PYTHON" scripts/civic_data.py --db data/processed/rebuilt.sqlite
"$CIVIC_PYTHON" scripts/retrieval.py embed --db data/processed/rebuilt.sqlite --embedding-format qwen3-vl
```

문서·청크 내용이 같은지 확인한 뒤 새 DB로 평가한다. 
원문 버전이나 청킹이 달라지면 Gold chunk ID와 코퍼스 해시도 다시 검토해야 한다. 
임베딩은 배치마다 저장하며 같은 입력 해시·설정의 완료 벡터는 재사용한다.

기존 DB의 누락 시행일을 원문 머리말에서 보완한 명령은 다음과 같다. 
이미 보완된 DB에서 다시 실행하면 수정 0건이다. 수정 기록에는 이전 값·새 값·원문 머리말이 있다.

```sh
"$CIVIC_PYTHON" scripts/civic_data.py --db data/processed/civic.sqlite --repair-source-dates benchmark/results/source_date_repairs.json
```

이 보완은 effective_date 8개만 바꿨다. 원문·청크·벡터는 유지했다.