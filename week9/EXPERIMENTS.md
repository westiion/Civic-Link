# Week 9 — 실험 재현

원본 호출 기록은 `outputs/week8-608-analysis/feedback/`에 있고, 이 폴더에는 요약만 복사했다. 아래 명령은 모두 `scripts/`에서 실행한다.

| 보고서 절 | 실험 | 호출 | 명령 | 결과 |
|---|---|---|---|---|
| 1·2·4·6 | 저장된 기록 분석(단계 집계, 첫 검색 재정렬, 역참조) | CPU | `python analyze_week9_feedback.py` | [reranking/](results/reranking/) |
| 1 | 확인 질문 지침 비교 | Qwen 16회 | `python compare_clarification_prompt.py` | [review/](review/CLARIFICATION_PROMPT_RESULT.md) |
| 1·5 | 확인 질문 점검 단계와 재개 | Qwen 약 14회 | `python run_week9_clarify_gate.py` | [clarify-gate/](results/clarify-gate/) |
| 1·5 | 확인 질문 점검 60문항 확대 | Qwen 약 180회 | `..\.venv\Scripts\python.exe run_week9_gate_scale.py gate` | [clarify-gate/scale/](results/clarify-gate/scale/) |
| 5 | 정답이 있는 5문항 재개(Qwen 사용자) | Qwen 약 15회 | `..\.venv\Scripts\python.exe run_week9_gate_scale.py resume` | [clarify-gate/scale/](results/clarify-gate/scale/) |
| 3 | Planning 첫 비교·보정 비교 | Qwen 36회 | `python run_week9_probes.py planning` / `planning-staged` | [planning/](results/planning/) |
| 3 | Planning 38문항 확대 | Qwen 114회 | `python run_week9_probes.py planning-staged --per-action 6 --extra-originals 14 --output-dir ..\outputs\week8-608-analysis\feedback\planning-staged-38` | [planning/](results/planning/) |
| 3 | Planning 38문항 채점 | Qwen 76회 | `python judge_week9_planning.py` | [judge-38-summary.json](results/planning/judge-38-summary.json) |
| 4 | Graph 도달 점검 | CPU | `..\.venv\Scripts\python.exe analyze_week9_graph_reach.py` | [graph/](results/graph/) |
| 4 | Graph 선택 재생 | Qwen 84회 | `..\.venv\Scripts\python.exe run_week9_graph_choice.py` | [graph/](results/graph/) |
| 5 | SQLite 상태 저장·복원 사례 | CPU | `python probe_week9_resume.py` | [resume/](results/resume/) |
| 6 | 조기 종료 규칙 재현 | CPU | `python simulate_week9_cost_policies.py` | [cost/](results/cost/) |
| 2·6 | 재정렬 결과 답변 비교 | Qwen 16회 | `python run_week9_probes.py reranking` | [reranking/](results/reranking/) |

- `networkx`가 필요한 스크립트는 프로젝트 `.venv`로 실행한다.
- 확인 질문 점검은 지침을 세 번 바꿔 실행했고 결과는 `results/clarify-gate/v1`, `v2`, `v3`에 있다. 스크립트에는 마지막 지침만 남아 있다. 3차 이후 개인정보(주민등록번호 등) 입력을 요구하지 않는 규칙을 추가했으며(4차), 60문항 확대 결과(`results/clarify-gate/scale/`)는 이 규칙이 없는 3차 지침으로 얻은 것이다. 1·2차 지침의 차이는 1·2차 지침의 차이는 [보고서](REPORT.md)의 설명으로 확인한다.
- 정식 반영하지 않은 프롬프트 변경은 [review/prompt-candidate-unverified.patch](review/prompt-candidate-unverified.patch)로 보존했다.
- Qwen 판정과 정규식 점검은 초안이다. 보고서에는 직접 검수한 결과를 구분해 적었다.

## LangGraph 프로토타입 (`feat/langgraph-prototype` 브랜치)

코드는 `main`에 병합하지 않았다. 아래 명령은 해당 브랜치로 전환한 뒤 `scripts/`에서 실행한다. 패키지는 `pip install -r requirements-langgraph.txt`로 설치한다.

| 보고서 절 | 실험 | 호출 | 명령 | 결과 |
|---|---|---|---|---|
| 5 | 기존 컨트롤러와의 동일성 재생(608문항 × 3조건) | CPU | `..\.venv\Scripts\python.exe replay_langgraph_parity.py --limit 0` | 터미널 출력 |
| 5 | 재질문 흐름 5문항(점검 → 멈춤 → 재개 → 재검색) | Qwen 약 25회 | `..\.venv\Scripts\python.exe run_week9_langgraph_resume.py` | [resume/](results/resume/langgraph-resume-summary.json) |

단위 테스트: `..\.venv\Scripts\python.exe -m pytest ..\tests\test_langgraph_agent.py`. 재검색은 정답 근거를 돌려주는 대체 검색이라 결과는 상한값이다.

