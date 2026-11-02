# 새 가설 2: 질문과 관련된 참조 근거 선택

기준은 기존 BGE 직접 정렬 437/488이다. 1번 내용 감점을 누적하지 않는다.
1번의 청크 벡터 5,474개만 재사용하고, 원질문 608개의 벡터를 같은 회사 API로 추가 계산한다.
PC에서 BGE나 생성형 모델을 실행하지 않는다. 모델·주소·입력 형식이 1번과 다르면 중단한다.
질문에도 1번과 같은 표현 지시를 사용한다. 서버 내부 모델 버전은 클라이언트가 확인할 수 없으므로 같은 배포를 유지한다.

## 비교 조건

| 조건 | 정책 |
|---|---|
| bge_direct | 기존 BGE 순위 유지 |
| references_bge | 보호된 상위 청크의 명시적 참조 후보를 BGE 점수순으로 선택 |
| references_semantic | 동일한 참조 후보를 질문-청크 코사인 유사도순으로 선택 |

보호할 청크는 BGE 상위 7개, 참조 슬롯은 최대 3개로 고정한다. 출발 근거가 최종 결과에서 사라지지 않도록 보호된 청크에서만 출발한다.
REFERENCES 간선의 원문 evidence에 해당 출발 청크가 연결돼 있어야 한다. 대상 Article의 article_context 청크 중 **기존 후보에 들어 있는 청크만** 선택한다.
동일 대상 조항에서는 최대 한 청크만 선택하며, 해당 조항이 보호된 상위 7개에 이미 포함돼 있으면 추가하지 않는다.
남은 자리는 BGE 순위로 채운다. 상위 10개 이후는 선택되지 않은 기존 BGE 순서다. 전체 후보·정답·반환 수는 유지한다.
두 참조 조건의 차이는 대상의 우선순위뿐이다. 코사인 동점은 BGE 점수, chunk_id, 출발 순위·간선 ID로 결정한다.
질문 유형 정답이나 정답 근거 ID는 선택에 사용하지 않는다. 키워드 gate도 넣지 않아 의미 점수 효과와 혼동하지 않는다.

이 정책은 의미 유사도를 이용한 **참조 후보 간 우선순위 가설**이다. 유사도가 높다는 이유만으로 참조가 답변에 필수라고 확정하지 않는다. 모든 질문에 같은 정책을 적용해 부적절한 참조로 인한 회귀도 확인한다.
3개 슬롯은 사전 고정한 첫 비교안이며 최적값이 아니다. 대표 SJ-N-050·W7V2-MS041과 전체 488개 answer 문항을 함께 평가한다.

## 실행

1번과 같은 VPN·CIVIC_EMBEDDING_URL·CIVIC_EMBEDDING_MODEL 설정을 유지한다.

```powershell
cd C:\Develop\Civic-Link
.\.venv\Scripts\python.exe scripts\run_week7_references.py prepare
.\.venv\Scripts\python.exe scripts\run_week7_references.py embed --limit 5
```

5개 질문의 요청 성공·예상 시간을 확인한 뒤 다음을 한 줄씩 실행한다.

```powershell
.\.venv\Scripts\python.exe scripts\run_week7_references.py embed
.\.venv\Scripts\python.exe scripts\run_week7_references.py rank
.\.venv\Scripts\python.exe scripts\run_week7_references.py evaluate
```

공통 임베딩 모듈을 사용하므로 진행 메시지는 `unique chunks`라고 표시되지만 이번 입력은 질문 ID와 원질문이다.
첫 5개는 재요청하지 않는다. 중단 후 embed를 재실행하면 완료된 질문을 건너뛴다.
prepare/rank는 기존 출력을 덮어쓰지 않는다. 설정 변경 실험은 새 --out 폴더를 사용한다.

결과: `week7/results/ranking_cases_hypotheses/h2_references/`
- report.md, summary.json: 세 조건 전체·유형별 지표와 개선/악화.
- rankings.jsonl: 참조 후보 목록, 선택된 경로·원문 인용·BGE 순위·질문 유사도.
- per_question.jsonl: 필수 그룹별 순위와 실패 단계.
- protocol.json, rank.run.json, embedding.run.json: 고정 입력·실행 출처.
- vectors.sqlite: 이번 질문 벡터만 저장. 1번의 청크 캐시는 읽기 전용으로 유지.

결과를 받은 뒤 통합 해석은 기존 `week7/results/ranking_cases_hypotheses/analysis.md`에 **2번 실험 절을 추가**한다. 평가 명령은 기존 1번 분석 문서를 덮어쓰지 않는다.
