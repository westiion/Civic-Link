# Colab T4 · 전체 1,000문항 비교

[Colab에서 실행 노트북 열기](https://colab.research.google.com/github/westiion/Civic-Link/blob/main/week8/run_comparison_colab.ipynb)

노트북을 Drive에 복사하고 GPU 런타임(T4)을 선택한 뒤 위에서부터 실행한다. **1,000문항 × A/B/C = 3,000개 조건별 결과**를 생성한다. BGE 재정렬은 Colab의 CUDA GPU, Qwen 생성·임베딩은 별도 서버를 사용한다. 생성·평가 모델을 T4에 함께 적재하는 구성은 아니다.

## 먼저 필요한 것

- **Colab에서 접근할 수 있는** 생성 API와 임베딩 API 주소. 노트북의 Colab Secrets에 `QWEN_ENDPOINT`(전체 `/v1/chat/completions` URL), `QWEN_EMBEDDING_ENDPOINT`(전체 `/v1/embeddings` URL)를 설정하고 노트북 접근을 허용한다. 인증이 있으면 `QWEN_API_KEY`, `QWEN_EMBEDDING_API_KEY`도 추가한다. 주소·키를 코드에 적지 않는다.
- Python 3.12 이상과 CUDA 사용 가능한 PyTorch. 노트북은 Colab에 설치된 CUDA PyTorch를 사용하는 별도 가상환경에 프로젝트 의존성을 설치한다. Mac용 `requirements-bge-lock.txt`는 사용하지 않는다.
- 결과·색인을 저장할 Drive 공간. BGE는 고정 revision을 명시적으로 다운로드하며 런타임이 새로 만들어지면 다시 준비한다.

**PC의 VPN 연결이 Colab에 자동으로 적용되지는 않는다.** Colab 코드는 별도 VM에서 실행되므로, 내부망 주소만 제공된 현재 조건에서는 연결 가능 여부부터 확인해야 한다. 연결 점검이 실패하면 1,000문항 실행을 시작하지 말고 서버 담당자에게 Colab에서 사용할 수 있는 승인된 접속 경로를 확인한다. 이 저장소는 서버를 외부에 공개하거나 VPN을 구성하지 않는다. Colab의 별도 VM·런타임 제한은 [공식 FAQ](https://research.google.com/colaboratory/faq.html)를 참고한다.

## 실행 순서

1. Drive 연결 → 코드 커밋 고정 → 가상환경 설치.
2. GPU 확인 → Secrets 입력 → 생성·임베딩 API 각각 실제 소량 요청. 실패하면 중단.
3. 원문 DB 복원 → 고정 BGE 준비 → GPU 재정렬 점검 → 문서 색인 준비.
4. 전체 1,000문항 A/B/C 실행. `--limit`을 사용하지 않는다. 첫 검색은 문항마다 한 번 계산해 세 조건이 공유한다.
5. 근거 회수·호출·시간 보고서 생성. 내용 평가는 선택 셀로 별도 실행하며 추가 모델 호출이 발생한다.

결과는 Drive의 `Civic-Link/week8-t4-1000-v1/` 아래에 저장한다. `comparison/`에 조건별 응답·전체 이력, `evidence-eval-*`에 근거 평가, 선택 실행한 `judgments/`와 `full-eval-*`에 내용 평가가 남는다. 정답은 평가 단계에서만 읽는다. 실험 가설과 해석 기준은 [EXPERIMENT.md](EXPERIMENT.md)에 정리했다.

## 중단 후 재개

같은 `RUN_NAME`으로 위에서부터 다시 실행한다. 처음 저장한 커밋과 색인을 재사용하고 완료된 조건별 결과를 건너뛴다. 진행 중이던 조건은 다시 실행한다. 실험 중 코드를 `pull`하거나 모델·검색 설정을 바꾸지 않는다. 변경한 조건으로 다시 비교하려면 새로운 `RUN_NAME`을 사용한다.

런타임이 강제 종료되면 `comparison/.running` 또는 `judgments/.running` 잠금 파일이 남을 수 있다. **이전 런타임·프로세스가 종료됐고 다른 노트북이 같은 폴더를 사용하지 않는지 확인한 뒤 해당 잠금 파일만 삭제**한다. 자동 삭제하지 않는다. 재개 시 환경 버전이 달라지면 노트북이 중단하므로 원래 환경을 맞추거나 새 실행 폴더를 사용한다.

Colab GPU 종류·런타임 지속 시간은 보장되지 않으므로 전체 1,000문항이 한 세션에 끝난다고 가정하지 않는다. Drive 입출력 오류 시에도 폴더를 지우지 말고 연결 상태를 복구한 뒤 재개한다. GPU 메모리 부족으로 배치를 바꿔야 한다면 새 `RUN_NAME`에서 A/B/C 모두 같은 배치로 실행한다.

이 안내의 노트북 구조·Python 문법과 로컬 오프라인 테스트는 점검했지만, 실제 Colab T4 실행·서버 연결·1,000문항 성능 측정은 아직 수행하지 않았다.
