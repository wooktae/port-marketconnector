# CHANGELOG

## 2026-07-01

### Added

- README에 신규 entrypoint 3종(`connector_strategy_order_execute.py`, `connector_intraday_snapshot_refresh.py`, `connector_intraday_position_evaluate.py`) 전용 섹션을 추가했다.
- README에 EC2 + SSM RunCommand 운영 구조 섹션을 추가했다. IAM Role `portfolio-paper-marketconnector-ec2-role`, inline policy `portfolio-paper-marketconnector-event-notifier-invoke`, EC2 lifecycle Scheduler `portfolio-paper-ec2-start-0750-kst`, `portfolio-paper-marketconnector-stop-1550-kst`, 장중 10분 주기 Scheduler `portfolio-paper-intraday-snapshot-evaluate-10min-kst`를 서술했다.
- README에 장중 stop-loss 흐름과 승인 gate 연계를 서술했다. `strategy_execution_order` READY 생성 및 `portfolio-event-notifier` Lambda로의 `INTRADAY_STOP_LOSS` Slack 통지까지는 자동으로 진행되고, broker 주문 제출은 `portfolio-paper-intraday-stop-sell-approval` Step Functions 승인 후에 이어진다는 점을 명시했다.
- README에 Step 12 KIS paper 주문 제출이 `portfolio-paper-daily-step12-17-approval` 승인 gate를 전제로 한다는 점을 명시했다.
- README 실행 위험 목록에 신규 entrypoint 3종을 추가했다.
- `docs/worklog/2026-07-01.md` 작업 일지를 추가했다.

### Changed

- README 파일 구조 요약에 신규 entrypoint 3종과 `scripts/run_connector_balance_daily.sh` 항목을 반영했다.
- 주문 및 체결 동기화 섹션에 `connector_order_check.py`의 direct/summary fallback 우선 처리 취지를 짧게 병기했다.

### Notes

- 기능 변경 없음. 이번 변경은 md 문서에 한정된 최신화 작업이다.
- Flask app 실행, KIS/브로커 API 호출, token 발급/갱신, 잔고/보유/시세/주문/체결 조회, 주문 제출/취소/정정, DB DDL/DML, AWS API 호출, SSM RunCommand 발행, Slack webhook 호출은 실행하지 않았다.
- 다른 마이크로서비스(port-view, StrategyExecution, StrategyDecision, StrategyResearch, Crawler, Preprocessor) 내부 상세, Step Functions state machine 전체 step, EventBridge Scheduler 전체 라인업, Lambda 내부 구현, command id, 실행 시간, 일회성 검증 로그는 반영 범위에서 제외했다.
- IAM Role ARN, Lambda ARN, secret ARN, account-id, KIS app key/secret, token 값, 계좌번호 전체, broker order number 전체, DB password, Slack webhook URL, RDS endpoint hostname은 문서에 기록하지 않았다.

## 2026-05-28

### Added

- `docs/source-file-catalog.md`를 추가해 Python 소스와 문서 파일의 역할, 주요 책임, 운영 주의사항을 한글로 정리했다.
- 주요 Python connector 파일에 module docstring을 추가했다.
- 잔고/시세/주문/체결/View 조립 등 운영상 중요한 함수에 짧은 function docstring을 추가했다.
- `docs/worklog/2026-05-28.md` 작업 일지를 추가했다.

### Changed

- README에 파일 카탈로그와 설명 주석 정리 산출물을 반영했다.
- 미커밋 상태였던 `connector_order_check.py` 변경의 의미를 문서화했다.
  - direct 조회에서 `output1`이 비어도 `output2` summary가 있으면 broad search보다 direct fallback을 먼저 처리해 broad summary가 특정 주문 event/fill에 섞일 위험을 줄이는 변경이다.

### Notes

- 기능 변경 없음. 이번 작업의 신규 변경은 문서와 설명 주석 정리다.
- 실제 Flask app 실행, KIS/브로커 API 호출, token 발급/갱신, 주문/잔고/시세/체결 조회, DB DDL/DML은 실행하지 않았다.
- 민감정보 값은 문서에 기록하지 않았다.

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
