# 가설 3: 질문 목적과 근거 역할 정렬

기존 BGE 후보·점수를 고정하고, 질문과 청크의 역할 분포가 맞을수록 점수를 보완한다. H1/H2 순위 조정을 누적하지 않는다. 정답과 question_type은 순위 계산에 사용하지 않는다.

## 역할과 점수

서비스 선택, 준비물, 신청 절차·기관, 자격·대리 조건, 기간, 수수료, 법적 근거·참조, 기재·공개 범위의 8가지 한국어 설명문을 사용한다. 설명문 원문은 protocol.json에 저장한다.

질문과 청크 각각에 대해 8개 설명문과 코사인 유사도를 계산하고 temperature=0.05의 softmax로 역할 비중을 만든다. 단일 유형으로 강제 분류하지 않는다. 이 비중은 검증된 분류 확률이 아닌 임베딩 기반 휴리스틱이다. 청크는 H1에서 임베딩한 제목+본문 전체를 사용하며, 세부 문장 추출이나 실제 섹션 라벨 검수는 하지 않는다.

점수 = 문항 내 min-max 정규화 BGE + 0.15 × 질문 역할 비중과 청크 역할 비중의 내적.

BGE 점수가 모두 같으면 정규화 점수는 1이다. 동점은 원래 BGE 점수와 청크 ID로 결정한다. 전체 후보를 보존하고 상위 10개를 평가한다. 계수와 설명문은 이번 실행 전 고정하며 최적값으로 주장하지 않는다.

## 세 조건

- bge_direct: 기존 BGE 기준선.
- role_prior_control: 개별 질문 대신 608개 질문의 평균 역할 비중을 사용한다. 질문별 정렬이 아니라 특정 역할 자체를 선호하는 효과를 비교하는 대조군이다. 평가 질문의 텍스트로 평균을 만들므로 이 대조군은 개발셋 진단용이다.
- role_alignment: 현재 질문의 역할 비중을 사용한다.

## 실행

저장소 루트 PowerShell에서 같은 회사 API 환경변수를 설정한 뒤 순서대로 실행한다.

```powershell
.\.venv\Scripts\python.exe scripts\run_week7_roles.py prepare
.\.venv\Scripts\python.exe scripts\run_week7_roles.py embed
.\.venv\Scripts\python.exe scripts\run_week7_roles.py rank
.\.venv\Scripts\python.exe scripts\run_week7_roles.py evaluate
```

prepare는 H1 청크 벡터와 H2 질문 벡터의 해시·입력·모델 정보를 검증한다. embed만 API를 호출하며 새 입력은 역할 설명문 8개다. 중단 시 embed를 다시 실행하면 완료된 요청을 재사용한다. BGE 로컬 추론은 없다. 기존 prepare 출력이 있으면 덮어쓰지 않는다. 새 설정은 전역 --out 인자로 새 경로를 지정해야 한다.

## 결과와 분석

결과는 week7/results/ranking_cases_hypotheses/h3_roles에 저장한다. role_profiles.json은 질문·청크의 역할 비중, rankings.jsonl은 모든 후보의 BGE 점수·역할 보너스·최종 점수와 조건별 순위, protocol.json은 고정 설정, rank.run.json은 해시와 실행 시간이다. evaluate만 정답표를 읽는다.

488개 answer 문항의 완전회수@10, Recall@10, MRR 및 유형별 개선·악화를 비교한다. 역할 정렬이 기준선뿐 아니라 평균 역할 대조군보다 좋은지 확인한다. 개선/악화 문항에서는 추정 역할이 질문 의도에 맞는지, 역할 보너스로 어떤 필수 근거가 들어오거나 밀렸는지 직접 확인한다. 결과 분석은 기존 week7/results/ranking_cases_hypotheses/analysis.md에 이어서 기록한다.

질문과 문서에 같은 설명문을 사용한 휴리스틱이며, 여러 역할을 담은 긴 청크와 복합 질문은 제대로 구분하지 못할 수 있다. 서버 내부 revision/잘림 설정은 확인되지 않았으므로 H1/H2와 같은 배포를 사용한다. 결과는 개발셋 근거 회수 평가이며 독립 검증이나 답변 정확도 평가가 아니다.
