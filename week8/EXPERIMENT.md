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

BGE CLI 기본값은 입력 1,024토큰·배치 4·CPU 스레드 4·float32다. Colab에서는 `--bge-device cuda`로 명시하며 CPU로 자동 전환하지 않는다. Qwen thinking은 기본적으로 비활성화한다. A/B/C의 모델·정밀도·배치·입력 한도를 동일하게 유지한다.

결과물: 문항별 응답·출처·전체 도구 및 Qwen 입력 이력, 고정 실행 설정, `report.json`, `summary.csv`, `REPORT.md`. 실제 성능 수치는 모델 실행과 평가 후 이 결과에서 작성한다.
