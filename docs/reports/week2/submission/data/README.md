# Week 2 corpus 및 SQLite

`corpus-sqlite.zip`에 아래 경로를 보존해 원문과 실행 DB를 함께 묶음.

- `data/graphability-audit/2026-09-24/`: 최초 원문 스냅샷, 본문, 수집 메타데이터, manifest
- `data/raw/2026-09-26/`: 추가 수집 원문 및 메타데이터
- `data/services.jsonl`: 서비스 등록부
- `data/processed/civic.sqlite`: 원문·정규화 문서·표·청크·문서 임베딩을 포함한 SQLite
- `data/processed/civic.build.json`: DB 구축 기록

[재현](../reproducibility/README.md)에 따라 `reproducibility/`에서 `python reproduce.py`를 실행하면 해시 확인과 압축 해제가 자동으로 진행됨. 
별도로 해제할 경우 ZIP 내부의 `data/` 경로를 유지하여 `reproducibility/data/processed/civic.sqlite`가 되도록 해야함.
