# Week 2 개발 환경·배포·benchmark 재현 안내

이 폴더는 코드·테스트·질문·기존 실행 결과를 모은 독립 실행 패키지다. 형제 폴더의 `../data/corpus-sqlite.zip`과 `archive_manifest.json`을 함께 전달하면 원래 레포 없이 실행할 수 있다. 모든 명령은 **이 README가 있는 `reproducibility/` 디렉터리**에서 실행한다.

## 1. 제출 파일과 재현 범위

| 경로 | 역할 |
| --- | --- |
| `../data/corpus-sqlite.zip` | 수집 원문·메타데이터·서비스 등록부·SQLite 및 문서 임베딩 |
| `../data/archive_manifest.json` | ZIP 및 압축 내부 각 파일의 SHA-256, DB 행 수 |
| `scripts/collect_sources.py` | 성남시 무인민원발급 안내·국세청 규정 2개 출처의 추가 수집 코드 |
| `scripts/civic_data.py` | 기존 원문 스냅샷에서 정규화 문서·표·청크·SQLite 생성 |
| `scripts/retrieval.py`, `tokenization.py` | BM25·Dense·Hybrid 검색 및 임베딩 |
| `scripts/benchmark.py`, `evaluate.py` | 질문 검증·검색 평가 |
| `scripts/generation.py`, `run_week2.py` | 응답 생성·자동 판정·전체 실험 실행 |
| `scripts/validate_corpus.py`, `tests/` | DB·원문·청크 일치 검증 및 단위 테스트 |
| `benchmark/` | 100개 질문, 입력 전용 파일, Gold, 기존 검색 9회·생성 3회 결과 |
| `docs/` | 범위·데이터 명세·기존 실행 안내의 스냅샷 |
| `reproduce.py` | 데이터 복원·무결성 확인·오프라인 재평가·BM25 신규 실행 |
| `environment.json`, `requirements.txt`, `.env.example` | 검증 환경·필수 의존성·모델 API 설정 예시 |
| `files.sha256.json` | 배포한 코드·질문·기존 결과·문서의 파일별 해시 |
| `VERIFICATION.md`, `verification.json` | 이 패키지에 대해 실제 수행한 검증과 결과 |

원본 기준 커밋은 `environment.json`에 기록했지만, 커밋 이후의 미커밋 작업을 포함한다. 정확한 제출 버전은 이 폴더와 파일별 SHA-256으로 식별한다. 기존 사용자 변경을 포함한 현재 작업 트리의 스냅샷이며 새 커밋이나 원격 업로드는 수행하지 않았다.

## 2. 개발 환경 관리

Python 3.10 이상과 SQLite를 포함한 Python 표준 라이브러리가 필요하다. 필수 외부 패키지는 없다. 검증은 macOS arm64, Python 3.12.14, SQLite 3.53.1에서 수행했다. 이 값은 **이번 제출 패키지 검증 환경**이며, 최초 모델 추론 시점의 런타임 버전은 별도로 기록되어 있지 않다.

```sh
python3 -m venv .venv
. .venv/bin/activate
python --version
python -m pip install -r requirements.txt
```

Windows에서는 가상환경 활성화에 `.venv\Scripts\Activate.ps1`을 사용한다. 이하 Python 명령은 동일하다. 시스템 Python이 Xcode 라이선스 오류를 내면 정상 설치된 Python 3.10 이상을 사용한다.

NumPy는 Dense 계산의 선택적 가속기다. 없어도 순수 Python으로 동작한다. 이번 오프라인 검증은 새 가상환경에서 외부 패키지 없이 수행한다. 원본 실행 메타데이터의 `dense_backend`는 방식별 `benchmark/results/*.run.json`에서 확인할 수 있다. 모델 재실행 속도를 위해 NumPy를 추가한다면 사용 버전을 별도로 기록한다.

비밀키는 환경변수로만 전달한다. `.env.example`에는 키 값이 없다. 프로그램이 `.env` 파일을 자동으로 읽지는 않는다. 모델 서버는 Python 환경과 분리되어 있으며, 아래 환경변수를 실행하는 쉘에 설정해야 한다.

## 3. API 없이 결과 재현 — 권장 시작점

```sh
python reproduce.py
```

이 명령은 다음을 순서대로 실행한다.

1. 코드·질문·기존 결과와 데이터 ZIP의 SHA-256을 검증한다.
2. ZIP을 이 폴더 아래 `data/`로 복원하고 파일별 해시를 확인한다. 다른 내용의 기존 파일은 덮어쓰지 않는다.
3. SQLite integrity/외래키, 원문 해시, 문서 본문, 청크 오프셋과 corpus hash를 검증한다.
4. 동봉한 단위 테스트를 실행한다.
5. 기존 검색 9개 실행의 지표를 다시 계산하여 동봉 지표와 정확히 비교한다.
6. BM25를 원질문 100건·기준 문장 64건·명확화 질문 20건에서 새로 실행하여 검색 결과 전체를 비교한다.
7. 저장된 생성 응답·자동 판정 3개 방식의 지표를 다시 계산한다. 실행 시각 `created_at`만 비교에서 제외한다.

`runs/offline/verification.json`에 결과가 저장된다. 원래 `benchmark/results/`는 유지하고, 재평가 결과는 `runs/offline/benchmark/`, 새 BM25 결과는 `runs/offline/fresh_bm25/`에 쓴다. 재실행할 때는 새 출력 경로를 지정한다.

```sh
python reproduce.py --output-dir runs/offline-second
```

이는 **저장된 Dense/Hybrid 순위와 모델 응답의 재채점**, 그리고 **BM25 검색 자체의 신규 재실행**이다. Dense/Hybrid의 질의 벡터 신규 계산, 답변 생성, LLM 판정 모델을 오프라인에서 다시 추론하는 것은 아니다. 원래 질의 벡터와 모델 가중치·서버 이미지가 제공되지 않아 전체 모델 추론의 완전한 독립 재현은 아직 불가능하다.

원질문 검색의 기대 결과는 다음과 같다. 지표 분모는 정답 근거 위치가 있는 64건이다.

| 방식 | Recall@10 | All locators@10 | MRR@10 | 행동 일치/100 |
| --- | --- | --- | --- | --- |
| BM25 | 0.5807 | 0.4688 | 0.3473 | 77 |
| Dense | 0.6615 | 0.5469 | 0.3739 | 74 |
| Hybrid | 0.6745 | 0.5625 | 0.4301 | 77 |

질문은 합성 개발셋이며 독립 테스트셋이 아니다. 행동 라벨 일치는 답변 정답률이 아니다. 생성 모델과 자동 평가 모델이 같고 사람 검수가 남아 있다. 검색의 동점 정렬은 청크 ID를 사용한다. 새 실행의 시간·지연과 생성 문장은 정확히 같을 것으로 가정하지 않는다.

## 4. VPN 모델 서버에서 전체 실험 새로 실행

먼저 3절로 데이터를 복원한다. 실행 PC에서 기존 모델 서버에 접속 가능하고 인증 정보를 제공받아야 한다. 모델 서버를 새로 배포하는 명령이나 가중치는 이 레포에 없다.

```sh
export CIVIC_EMBEDDING_URL=http://10.50.0.50:30004/v1
export CIVIC_EMBEDDING_MODEL=Qwen3-VL-Embedding-8B
export CIVIC_CHAT_URL=http://10.50.0.50:30001/v1
export CIVIC_CHAT_MODEL=Qwen3.8-Flash-Next
# 필요한 경우 CIVIC_EMBEDDING_API_KEY와 CIVIC_CHAT_API_KEY를 별도로 설정한다.
```

저장 결과를 재사용하지 않도록 **결과가 없는 새 benchmark 디렉터리**를 만든다. 아래는 POSIX 쉘 기준이며 `runs/fresh`가 있으면 중단된다.

```sh
python - <<'PY'
from pathlib import Path
import shutil
out = Path('runs/fresh')
out.mkdir(parents=True, exist_ok=False)
for name in ['questions.jsonl', 'queries.jsonl', 'canonical_queries.jsonl',
             'clarified_queries.jsonl', 'decision_inputs.jsonl', 'manifest.json']:
    shutil.copy2(Path('benchmark') / name, out / name)
PY
python scripts/run_week2.py --benchmark-dir runs/fresh --stage all
```

검색만 실행하려면 `--stage retrieval`, 생성·자동 평가만 실행하려면 검색 완료 후 `--stage generation`을 사용한다. `--benchmark-dir benchmark`를 사용하면 이미 저장된 결과를 재사용하므로 신규 추론 재현으로 간주하면 안 된다. 새 실행에는 임베딩 API 호출, 답변 생성 300건, 자동 판정 300건이 필요하다.

BM25 설정은 `k1=1.2`, `b=0.75`, 한국어 단어+문자 bigram이다. Dense는 Qwen3-VL 입력 템플릿, 4,096차원 전체 cosine 검색이다. Hybrid는 후보 100개에 동일 가중치 RRF `1/(60+rank)`를 적용해 상위 10개를 반환한다. 생성에는 질문·서비스 범위·검색 근거만 전달하고 Gold는 사후 평가에만 사용한다. 제공 근거만 사용하는 8개 문항은 일반 검색 근거를 넣지 않는다.

기존 문서 임베딩의 fingerprint에는 모델명·URL·입력 형식이 포함된다. URL이나 인코더 설정을 바꾸면 같은 모델명이라도 DB의 임베딩과 불일치할 수 있다. 별도 패키지 복사본에서 해당 서버 설정으로 `scripts/retrieval.py embed --db data/processed/civic.sqlite --embedding-format qwen3-vl`을 실행하고 별도 실험으로 기록한다. 같은 이름의 서버 가중치 변경은 fingerprint가 감지하지 못한다. 서버 가중치 revision·서빙 이미지·seed·정밀도 설정은 최초 기록에 없어 문장 단위 동일 결과를 보장할 수 없다.

## 5. Crawler와 corpus/DB 재구축

원래 데이터가 보이지 않았던 이유는 `.gitignore`가 `data/graphability-audit/`, 대형 DB 및 `*.sqlite`를 제외하기 때문이다. 이제 해당 원문과 DB를 별도 ZIP으로 전달한다.

`collect_sources.py`는 공개 출처 2개를 수집하고 HTTP 상태·수집 시각·SHA-256·본문 존재 여부를 기록한다. **전체 91문서를 처음부터 수집하는 범용 crawler는 아니다.** 기존 2026-09-24 아카이브 전체의 최초 수집 자동화 코드는 현재 레포에서 확인되지 않는다. 91개 원문의 결과 스냅샷과 출처 메타데이터를 동봉해 DB 입력의 동일성을 확보했다.

새 원문 수집은 인터넷 연결이 필요하며, 기존 스냅샷을 보존하도록 새 디렉터리를 쓴다.

```sh
python scripts/collect_sources.py --output-dir data/raw/new-collection
```

수집 결과의 각 `status`를 확인한다. 이 명령의 새 폴더는 DB 구축 코드에 자동 반영되지 않는다. 구축 코드는 고정된 `data/graphability-audit/2026-09-24`와 `data/raw/2026-09-26`을 사용한다. 원문 재수집은 사이트 변경으로 과거 benchmark 입력과 달라질 수 있다.

동봉한 스냅샷으로 DB를 재구축할 때는 새 파일을 사용한다.

```sh
python scripts/civic_data.py --db data/processed/rebuilt.sqlite
python scripts/validate_corpus.py --db data/processed/rebuilt.sqlite
python scripts/benchmark.py validate --db data/processed/rebuilt.sqlite --benchmark-dir benchmark
```

재구축 DB에는 문서 임베딩이 없다. Dense 실행에는 `scripts/retrieval.py embed --db data/processed/rebuilt.sqlite --embedding-format qwen3-vl`과 VPN 서버가 필요하다. `run_week2.py`와 생성 코드는 `data/processed/civic.sqlite` 경로를 사용하므로, 새 DB를 전체 실험에 쓰려면 별도 패키지 복사본에서 해당 경로로 배치한다. 원문·청킹을 변경한 경우 기존 Gold 위치와 corpus hash가 맞는지 다시 검토한다.

## 6. 배포 현황과 전달 방법

현재 레포에서 확인되는 실행 형태는 **로컬 Python CLI + 별도 운영 VPN 모델 API**다. 애플리케이션 웹 서버, Dockerfile/Compose, CI/CD workflow, 운영 배포 manifest는 없다. 모델 서버의 구축·운영 설정도 레포에 없다. 따라서 자동 배포 또는 컨테이너 환경이 갖춰져 있다고 설명하지 않는다.

이번 제출은 파일 전달 방식이다. `submission/` 전체 또는 새 `civic-link-week2.zip`을 전달하고, 수신자는 압축 해제 후 이 폴더에서 2~3절을 실행한다. 데이터 ZIP은 상대 경로 보존을 위해 `data/`와 `reproducibility/`를 나란히 둔다. 충분한 디스크 공간(압축 파일과 해제된 DB, 선택적 재구축 DB 포함 약 3GB 이상 권장)을 확보한다.

운영 배포 자동화와 전체 수집 자동화, 모델 가중치/이미지 고정, 독립적인 사람 검수는 후속 보완 사항이다. 이 문서는 현재 확인된 상태와 새 제출 패키지의 실행 절차를 구분해 기록한다.
