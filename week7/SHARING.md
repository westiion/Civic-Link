# 실험 2 공유본과 재집계

전체 608문항 사례, 14개 검색 조건의 지표, 실제 Qwen 원응답 1,826개와 원채점 1,825개, BGE 직접 정렬 결과를 보존한다. 같은 모델의 자기채점은 독립적으로 검증된 답변 정확도가 아니다. 잘린 원응답과 필드 누락·발췌 불일치도 원형대로 남겼다.

## Git에 포함하는 자료

- `week7/EXPERIMENT2.md`: 최종 보고서.
- `week7/docs/experiment2_cases/`: 고유 질문 608개를 한 번씩 담은 12개 사례 문서와 구조화 상세.
- `week7/evaluation/experiment2/`: 최종 검색·행동·내용 지표와 검수 기록. 기존 `verification.json`의 104개 테스트는 과거 실행 기록이며 현재 공유본 검증과 구분한다.
- `week7/results/experiment2_20261031/`: H3·coverage·BGE, 독립 Graph 0~3-hop, 전체 진단 순위·지표의 **손실 없는 gzip**, 원 실행 메타데이터와 불변 추론 코드 스냅샷.
- 같은 폴더의 `behavior_qwen/`: 원응답 gzip, 조건별 입력 매핑, 실행 설정·기록·시간 구간. `content_judge/`에는 원채점 gzip과 매핑·설정·실행 기록을 담는다. 사설 endpoint는 `${QWEN_ENDPOINT}`로 바꾸었고 응답·채점·수치는 변경하지 않았다.
- `week7/results/experiment2_bge_direct_20261101/`: 원점수·최종 순위 CSV, 지표·문항별 결과·재집계 안내. CSV 경로는 기존 노션 기록과 같다.

gzip을 풀면 기존 원본과 바이트 단위로 같다. 로더는 논리적 `*.jsonl`·`*.json` 경로에 파일이 없으면 `*.gz`를 직접 읽는다. 입력 지문은 압축 해제한 원본 바이트로 확인하며, Git 파일 검증 manifest는 실제 압축 파일의 지문을 기록한다. 원 실행 코드 스냅샷은 출처 확인용 기록이고, 실행에는 현재 `scripts/`와 `src/civic_link/`를 사용한다.

## 로컬에 보존하고 Git에서 제외하는 자료

`.gitignore`에서 원본 비압축 캐시, smoke·float32 pilot·partial 실행, Gemma 시도, 작업 로그·편집 메모, `cases_before_*`, `editor_reference`, 중복 `evaluation_*`·`expanded_summary`·`final_summary`를 제외한다. `data/processed/`의 기존 DB도 이미 공유하는 corpus 압축본으로 복원할 수 있어 제외한다. 원본 파일은 삭제하거나 별도 Recovery/ZIP 사본으로 옮기지 않았다.

공유 파일은 루트 `file_inventory.txt`와 `manifest.json`에 명시한다. 파일을 수정하면 `scripts/refresh_manifest.py`를 다시 실행한다. 이 도구는 개인 경로·사설 endpoint·주요 비밀키 패턴과 파일 크기를 검사하며 stage·commit·push하지 않는다.

## 모델 호출 없이 재집계

Python 3.12와 `requirements-dev.txt` 의존성을 설치한 뒤 저장소 루트에서 실행한다. BGE·Qwen 모델 설치나 서버 연결은 아래 명령에 필요하지 않다. 모든 출력 폴더는 새 경로를 사용한다.

```sh
.venv/bin/python scripts/restore_data.py restore
.venv/bin/python -m pytest -q

# 전체 원후보와 원점수 검증 + 독립 정렬
.venv/bin/python scripts/evaluate_week7_bge_direct.py \
  --output-dir outputs/bge-direct-from-cache

# 배포한 CSV만으로 직접 정렬 지표 재집계
.venv/bin/python scripts/evaluate_week7_bge_direct.py \
  --rankings week7/results/experiment2_bge_direct_20261101/rankings.csv \
  --output-dir outputs/bge-direct-from-csv

# 과거 14개 검색 조건 × 5개 부분집합 재집계
.venv/bin/python scripts/compare_week7_experiment2.py \
  --output-dir week7/results/experiment2_20261031 \
  --summary-dir outputs/experiment2-recheck

# 고정 Qwen 원응답·원채점으로 행동·내용 진단 재집계
.venv/bin/python scripts/summarize_week7_behavior.py \
  --behavior-dir week7/results/experiment2_20261031/behavior_qwen \
  --summary-dir outputs/qwen-recheck

.venv/bin/python scripts/restore_data.py verify
```

검색 비교 스크립트는 원 캐시를 검증하고 진단 순위·지표를 재생성한다. 공유본에서 새로 생기는 비압축 진단 파일은 로컬 출력으로 제외한다. 출력이 달라지지 않았는지는 기존 최종 `summary.json`과 비교한다. Qwen 재집계는 생성이나 채점을 다시 요청하지 않는다. 새 원격 실험을 명시적으로 실행할 경우에만 `QWEN_ENDPOINT` 환경변수 또는 `--endpoint`로 주소를 제공한다. 원래 서버·가중치가 계속 사용 가능하다는 보장은 없다.
