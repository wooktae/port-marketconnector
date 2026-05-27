# CHANGELOG

## 2026-05-27

### Changed

- DB 접속정보를 `INTEREST_DB_*` 환경변수 기반으로 외부화했다.
- `connector_db.py`의 password 하드코딩을 제거하고 `db_config.py`의 공통 `get_db_config()`를 사용하도록 변경했다.
- README에 DB 접속 환경변수 설명을 추가했다.
- PostgreSQL 기본 DB name을 `portfolio`로 정리하고, AWS Migration 준비 관점의 단일 DB `portfolio` + schema-per-domain 구조를 README에 반영했다.
- 이 모듈의 DB connection `search_path`를 `connector, execution, legacy, reference, public`으로 문서화했다.
- schema-per-domain 전환 후에도 기존 SQL은 connection `search_path` 기반으로 동작한다는 설명을 추가했다.

### Notes

- 실제 DB 접속, Flask app 실행, KIS/브로커 API 호출, token 발급/갱신, 주문/잔고/시세/체결 조회는 실행하지 않았다.
- DB password 실제 값은 문서에 기록하지 않았다.

## 2026-05-26

### Added

- Python market connector 마이크로서비스의 루트 문서 초안을 추가했다.
- 브로커 API, token, 주문, 잔고, 시세, DB 실행 위험에 대한 agent 작업 규칙을 추가했다.
- 2026-05-26 문서화 작업 일지 초안을 추가했다.

### Changed

- 초기 문서 초안을 한국어 기준으로 정리했다.
- legacy/단순 실행용 Python 파일 정리 결과에 맞춰 README와 AGENTS의 entrypoint 목록을 갱신했다.

### Removed

- legacy/단순 실행용 후보였던 `app.py`, `buy.py`, `balance.py`, `order_check.py`, `get_price_realtime.py`, `get_price_closed.py`를 제거했다.
- Python bytecode cache인 `__pycache__/`를 제거했다.

### Notes

- Flask app, KIS/브로커 API, token 발급/갱신, 잔고/보유/시세/주문/체결 조회, 주문 제출, DB DDL/DML, 크롤러는 실행하지 않았다.
- 민감정보 값은 문서에 기록하지 않았고 필요한 예시는 `[REDACTED]`로 표시했다.
