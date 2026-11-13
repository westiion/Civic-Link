# Week 8 — 반복 탐색 가설과 비교 실험

## 가설과 비교 조건

| 가설 | 비교 | 기대하는 변화 | 함께 확인할 반례·비용 |
|---|---|---|---|
| **① 미확인 항목을 기준으로 재검색하면 한 번 검색할 때 놓친 내용을 보완한다.** | A → B | 비교 대상·조건별 필수 근거와 답변 충족 증가 | 불필요한 재검색, 기존 성공의 퇴보, 호출·시간 증가 |
| **② 대상·인용 단서가 보이면 Graph의 직접 조회·관계 탐색이 재검색만 하는 것보다 필요한 본문을 안정적으로 확보한다.** | B → C | 같은 문서의 세부 항목·인용 조문·조건의 회수와 인용 충족 증가 | 잘못된 대상·관계 선택, 불필요한 본문, 커서로 예산 소진 |

| 조건 | 실행 방식 | 검증하는 역할 |
|---|---|---|
| **A · `single_search`** | 원래 질문으로 Hybrid+BGE 한 번 검색 → Qwen 응답 | 현재 환경에서 다시 실행하는 정적 검색 기준선 |
| **B · `iterative_search`** | 같은 첫 결과 → Qwen이 `search`·`respond` 선택 | 반복 판단·검색의 효과 |
| **C · `iterative_graph`** | 같은 첫 결과 → Qwen이 `search`·`graph`·`respond` 선택 | 후보 제공·직접 조회·관계 탐색·이어 읽기를 포함한 Graph 기능의 추가 효과 |

세 조건의 첫 검색 결과를 한 번 생성해 그대로 공유한다. 실제 모델·생성 설정·코퍼스·색인·검색 설정·코드 해시를 고정하고, B와 C는 **Qwen 최대 8회·도구 최대 6회**를 사용한다. 공통 첫 검색도 도구 1회로 계산한다. A는 첫 검색 이후 응답만 허용하며 오류 수정도 같은 모델 호출 한도 안에서 처리한다. 조건 순서는 문항별로 순환한다.

이 실험은 **첫 검색 이후의 보완 효과**를 비교한다. 일반 `run_agent.py`는 Qwen이 첫 행동부터 고르는 설계대로 실행한다. Week7의 H3·Coverage·BGE 수치를 새 모델·검색 환경의 성능과 직접 차감하지 않고, 당시 결과는 참고로 두며 A를 같은 환경에서 재측정한다. C의 효과가 확인되면 직접 조회와 관계 탐색의 효과를 나누는 추가 조건을 검토한다.

## 평가 기준

| 구분 | 집계 |
|---|---|
| 근거 회수 | 수집 / Qwen에 전달 / 최종 호출에 전달 / 실제 인용한 근거를 각각 집계. 요구 근거 완전회수율·요구 묶음 Recall |
| 내용 품질 | 문의별 필수 처리·사실 정확성·제공 본문의 뒷받침·실제 인용의 뒷받침·근거 없는 단정. 복합 문항은 A/B 모두 충족해야 전체 통과 |
| 추가 질문·보류 | 최상위 행동 하나로 합치지 않고 문의별 기대 행동과 실제 응답 내용을 평가 |
| 비용·안정성 | 문항당 시간, 모델·도구 호출, 서버가 반환한 토큰 사용량, 입력 문자 수, 요청 오류·도구 오류·실행 중단 |
| 조건 간 차이 | 동일 문항에서 개선 / 퇴보 / 유지. 전체 및 질문 유형별 요약 |

Graph가 반환한 일부 청크 범위에 청크 ID가 연결돼 있다는 이유만으로 해당 청크 전체를 회수한 것으로 계산하지 않는다. 실제 전달한 원문 범위를 합쳐 요구 구간 전체를 포함하는지 확인한다. 지정 근거 회수는 답변의 정확성과 별개다.

내용 평가는 저장된 응답에 대한 사후 모델 판정이며 조건 이름을 숨긴다. 가능하면 생성 모델과 다른 평가 모델을 사용하고, 개선·퇴보 사례는 사람이 표본 검수한다. 미평가·판정 오류를 성공 처리하지 않으며, 실행 오류를 비교 대상에서 빼지 않는다. 모든 조건의 결과가 있는 문항만 쌍으로 비교하고 미완료 목록을 보고한다.

## 데이터와 실행 순서

1. 대표 문항을 별도 `queries.jsonl`에 고정해 실행·출처·커서·종료를 점검한다. 아래 `--limit 20`은 원본의 앞 20개를 사용하는 연결 점검용이며 유형별 표본을 뜻하지 않는다.
2. 실패 사례 외의 기존 성공·추가 질문·보류·복합 문항을 포함한 동일 질문 세트로 A/B/C를 실행한다.
3. 근거 회수와 비용을 먼저 확인하고 내용 평가를 연결한다. 개선뿐 아니라 기존 성공의 퇴보도 읽는다.

Week8 1,000문항은 기존 문항을 재조합한 **개발 벤치마크**다. 설계에 사용한 44문항만의 개선을 전체 일반화 성능으로 표현하지 않으며, 이 실험을 독립 홀드아웃 평가라고 부르지 않는다. 모델 입력 파일은 `query_id`·`question`만 허용하고 정답·주석은 평가기에만 전달한다.

## PowerShell 실행

Colab T4 실행은 [전용 안내](COLAB.md)와 [노트북](run_comparison_colab.ipynb)을 사용한다. 기본값은 전체 1,000문항 × A/B/C이며, 결과와 색인을 Drive에 저장한다. Colab에서 생성·임베딩 서버까지 연결돼야 실행할 수 있다.

전제: `QWEN_ENDPOINT`, `QWEN_EMBEDDING_ENDPOINT`, `BGE_MODEL_PATH`를 현재 실행 환경에 설정한다. 필요한 경우 `QWEN_API_KEY`, `QWEN_EMBEDDING_API_KEY`도 설정한다. 서버 주소·키를 결과 설정 파일에 평문으로 저장하지 않는다. Dense 색인은 사용한 임베딩 모델·revision·query instruction과 일치해야 한다.

```powershell
# 첫 연결 점검. --model로 실제 서버의 모델 ID를 지정한다.
.\.venv\Scripts\python.exe scripts/run_agent_comparison.py --queries data/benchmark/week8/queries.jsonl --output-dir outputs/week8-agent-smoke --limit 20 --model YOUR_MODEL_ID

# 같은 명령을 재실행하면 저장된 문항을 건너뛴다. 전체 실험에는 새 폴더 사용.
.\.venv\Scripts\python.exe scripts/run_agent_comparison.py --queries data/benchmark/week8/queries.jsonl --output-dir outputs/week8-agent-comparison --model YOUR_MODEL_ID

# 근거 회수·비용 먼저 집계. 완료된 공통 문항과 미완료 문항을 구분한다.
.\.venv\Scripts\python.exe scripts/evaluate_agent_comparison.py --run-dir outputs/week8-agent-comparison --questions data/benchmark/week8/questions.jsonl --output-dir outputs/week8-agent-evidence-eval

# 문의별 사후 내용 평가. QWEN_JUDGE_ENDPOINT/API_KEY 또는 생성 서버 설정 사용.
.\.venv\Scripts\python.exe scripts/judge_agent_comparison.py --run-dir outputs/week8-agent-comparison --questions data/benchmark/week8/questions.jsonl --output-dir outputs/week8-agent-judgments --model YOUR_JUDGE_MODEL_ID

# 내용 판정을 합쳐 새 보고서 생성.
.\.venv\Scripts\python.exe scripts/evaluate_agent_comparison.py --run-dir outputs/week8-agent-comparison --questions data/benchmark/week8/questions.jsonl --judgments outputs/week8-agent-judgments --output-dir outputs/week8-agent-full-eval
```

`--conditions single_search iterative_graph`처럼 일부 조건만 먼저 돌릴 수 있다. 조건·질문·코드·모델·데이터가 바뀌면 새 출력 폴더를 사용한다. 결과는 문항 단위 원자적 저장이므로 중간에 중단하면 완료된 문항은 보존되고, 진행 중이던 문항은 다시 실행한다. 비정상 종료로 `.running`이 남으면 이전 프로세스가 종료됐는지 확인한 뒤 그 잠금 파일만 제거한다.

초기 검색·응답 결과에는 해시를 기록해 재개 시 변경을 거부한다. 최초 검색 시간은 각 조건의 시간에 동일하게 합산한다. 모델·색인 적재와 캐시의 영향을 받으므로 조건 순환 및 반복 측정으로 확인한다. 내용 판정 오류는 미평가로 남으며 같은 평가 명령 재실행으로 재시도한다.

내용 평가기에 `--workers 8`을 추가하면 최대 8개 응답을 동시에 평가한다. 기본값은 1이다. 요청마다 별도 클라이언트를 사용해 토큰 사용량이 섞이지 않으며, 성공한 판정은 재사용하고 실패한 판정만 다시 시도한다. 동시 실행 수를 바꾸면 새 평가 출력 폴더를 사용한다. 내용 평가는 로컬 GPU가 필요 없으므로 원결과를 PC에 백업하고 대여 GPU를 반환한 뒤 실행할 수 있다. 생성 실험과 평가 요청을 동시에 보내면 처리 시간에 영향을 주므로 시간 비교 시에는 분리한다.

BGE CLI 기본값은 입력 1,024토큰·배치 4·CPU 스레드 4·float32다. Colab에서는 `--bge-device cuda`로 명시하며 CPU로 자동 전환하지 않는다. Qwen thinking은 기본적으로 비활성화한다. A/B/C의 모델·정밀도·배치·입력 한도를 동일하게 유지한다.

결과물: 문항별 응답·출처·전체 도구 및 Qwen 입력 이력, 고정 실행 설정, `report.json`, `summary.csv`, `REPORT.md`. 실제 성능 수치는 모델 실행과 평가 후 이 결과에서 작성한다.

## 여러 GPU로 병렬 실행

폴더와 입력·출력 형식은 동일하다. `--bge-devices`에 지정한 GPU마다 독립 프로세스와 BGE 모델 하나를 만들고, 준비된 작업자에게 **문항 단위**로 일을 나눠 준다. 같은 문항의 A/B/C는 한 작업자에서 실행하므로 첫 검색 결과를 공유하며, 조건 순환은 원본 문항 순서를 따른다. 작업자는 처리 후 다음 문항을 가져가므로 어려운 문항이 특정 GPU에 몰려도 남은 일을 나눌 수 있다.

`--api-concurrency 4`는 전체 작업자의 **생성·임베딩 요청을 합쳐 최대 4개**로 제한한다. 서버가 동시 요청 4개를 감당하기 어렵다면 첫 점검부터 2로 정한다. 동시 요청 수를 포함한 설정은 재개 시 고정하며, 변경하려면 새 출력 폴더를 사용한다. GPU가 4장이어도 Qwen 서버 응답 대기 때문에 전체 속도가 정확히 4배가 되지는 않는다.

GPU 메모리에 여유가 있는데 Qwen 대기로 GPU가 쉬는 경우 `--workers-per-gpu 2 --api-concurrency 8`로 GPU 4대에 작업자 8개를 띄울 수 있다. 각 작업자가 별도의 BGE 모델과 검색 자료를 적재하므로 GPU·CPU 메모리 사용이 늘어난다. 기본값은 GPU당 작업자 1개다. 동시 요청을 늘린 별도 예비 실험에서 실제 처리량·응답 지연·오류·메모리를 확인한 뒤 전체 실행에 적용한다. A/B/C의 모델·정밀도·검색 설정은 그대로 유지하며 작업자 수는 실행 설정에 기록한다. 작업자 수 변경도 새 출력 폴더를 사용한다.

### V100 × 4 설치

Python 3.12 이상인 Linux GPU 서버에서 저장소를 받은 뒤 실행한다. V100은 Volta(`sm_70`)이므로 **PyTorch 2.9.0의 CUDA 12.6 빌드**를 사용한다. CUDA 12.8/12.9 빌드에서 Volta 지원이 제외된 점은 [PyTorch 공식 안내](https://github.com/pytorch/pytorch/issues/157517), 설치 명령은 [이전 버전 설치 안내](https://pytorch.org/get-started/previous-versions/)를 참고한다.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install torch==2.9.0 --index-url https://download.pytorch.org/whl/cu126
.venv/bin/python -m pip install -r requirements-bge.txt
.venv/bin/python scripts/restore_data.py restore
.venv/bin/python scripts/download_bge.py
export BGE_MODEL_PATH="$PWD/.cache/reranker-models/models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
```

`QWEN_ENDPOINT`, `QWEN_EMBEDDING_ENDPOINT`와 필요한 키는 서버의 환경 변수로 설정한다. **대여 서버에서 해당 API에 접근할 수 있어야 한다.** PC의 VPN 연결이 자동으로 대여 서버에 적용되지는 않는다. 이 실행기는 내부 서버를 외부에 공개하거나 VPN을 구성하지 않는다. 문서 색인이 없다면 같은 임베딩 설정으로 한 번 생성한다.

```bash
.venv/bin/python scripts/build_agent_index.py --output outputs/agent-index/corpus.npz --timeout 180
```

기존 색인이 있으면 위 생성 명령은 생략한다. 재개 중 색인을 새로 만들지 않는다. 생성 도중 중단돼 불완전한 파일이 남았다면 해당 파일을 확인하고 새 출력 경로로 색인을 완성한 뒤 `--index`로 지정한다.

### 실행과 평가

```bash
# 실제 GPU 연산, 생성·임베딩 연결, BGE 최대 입력·배치 적재를 점검하고 앞 20문항 실행
.venv/bin/python -u scripts/run_agent_comparison.py \
  --queries data/benchmark/week8/queries.jsonl \
  --output-dir outputs/week8-gpu-smoke20 --limit 20 \
  --bge-devices cuda:0 cuda:1 cuda:2 cuda:3 --api-concurrency 4 \
  --bge-batch-size 4 --bge-max-length 1024 --timeout 180 --json-mode

# 전체 1,000문항 × A/B/C. 같은 명령으로 재개
.venv/bin/python -u scripts/run_agent_comparison.py \
  --queries data/benchmark/week8/queries.jsonl \
  --output-dir outputs/week8-gpu-1000 \
  --bge-devices cuda:0 cuda:1 cuda:2 cuda:3 --api-concurrency 4 \
  --bge-batch-size 4 --bge-max-length 1024 --timeout 180 --json-mode

# 별도 결과 병합 없이 기존 평가기를 그대로 사용
.venv/bin/python scripts/evaluate_agent_comparison.py \
  --run-dir outputs/week8-gpu-1000 --questions data/benchmark/week8/questions.jsonl \
  --output-dir outputs/week8-gpu-evaluation
```

`cuda:0` 등의 번호는 `CUDA_VISIBLE_DEVICES`가 적용된 뒤 프로세스에서 보이는 GPU 순서다. 각 GPU가 같은 모델인지, CUDA 커널이 실행되는지, API 응답과 색인이 일치하는지 확인한 뒤 작업자를 시작한다. 시작 점검의 소량 API 요청은 성능 집계에서 제외한다. 네 작업자의 BGE·검색 색인 준비를 마친 뒤 측정 문항을 배분한다. GPU는 기본 float32를 유지하며 이 변경에서 BF16·FP16으로 바꾸지 않는다.

완료한 조건별 결과는 같은 폴더에 즉시 저장한다. 중단 후 재개할 때 완료된 결과를 검사해 건너뛰고, 일부 조건만 끝난 문항은 저장된 첫 검색을 재사용해 남은 조건만 실행한다. 워커 오류나 Ctrl+C가 발생하면 이 실행기가 만든 작업 프로세스를 종료한 후 잠금을 해제한다. **프로그램 종료는 GPU 대여 반납이 아니므로 대여 종료는 별도로 해야 한다.** 강제 종료로 `.running`이 남으면 `workers.json`의 프로세스들이 모두 종료됐는지 확인한 뒤 잠금 파일만 제거한다.

원결과의 `execution`에 GPU와 프로세스 ID, `config.json`에 GPU·PyTorch·CUDA·동시 요청 설정을 남긴다. `run.json`의 `attempt_wall_seconds`는 마지막 실행 시도의 작업자 준비·처리 시간을 포함하며, 과거 실행 시간이나 실행기 시작 전 설치·다운로드·API 점검 시간은 합산하지 않는다. 조건별 평균 응답 시간은 병렬 부하 상태의 지연 시간이며, 전체 대여 시간과 구분한다. 내용 평가기는 기존과 같이 별도 실행한다.
