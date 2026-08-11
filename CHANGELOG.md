# CHANGELOG

port-marketconnector 코드와 문서의 주요 변경 이력을 기록한다.

## 작성 원칙

| 항목 | 값 |
| --- | --- |
| 기록 범위 | Connector 코드 · Flask API · 스크립트 · 설정 · 테스트 · 문서 |
| 제외 범위 | 다른 MS 내부 구현 · orchestration 전체 정의 · 일회성 운영 로그 |
| 정렬 | 최신 날짜를 상단에 추가 |
| 분류 | Added · Changed · Fixed · Removed · Security |
| 실행 기록 | 실제 수행한 검증만 기록 |
| 민감정보 | token · 계좌 · 주문번호 · secret · ARN · endpoint 원문 금지 |

## 2026-08-11 — main Push 기반 MarketConnector 자동 Release 완성

### Added

| 항목 | 값 |
| --- | --- |
| 자동 Release | main Push GitHub Actions Release Trigger |
| 수동 경로 | `workflow_dispatch` 실행 유지 |
| EC2 준비 | 배포 전 EC2 상태 확인 · 필요 시 일시 시작 |
| EC2 복원 | Workflow가 시작한 EC2만 stopped로 복원 |
| 자동 배포 연결 | S3 Versioned Revision 기반 CodeDeploy 자동 생성·대기 |

### Changed

| 항목 | 값 |
| --- | --- |
| CI Contract | 자동 Release 구조에 맞게 갱신 |
| Release 흐름 | main Push → CodeBuild → Versioned Artifact → EC2/SSM 준비 → CodeDeploy |

### Security

| 항목 | 결과 |
| --- | --- |
| 전체 Pytest | 56개 성공 |
| CodeBuild | 성공 |
| Versioned Artifact | 생성·조회 성공 |
| EC2 상태·SSM Online | 확인 성공 |
| CodeDeploy In-place | 성공 |
| No-Order Release Boundary | 유지 |
| Connector 자동 실행 | 0건 |
| Broker 주문 API 호출 | 0건 |

## 2026-08-04 — 주문 경계 안전 강화와 Versioned 운영 배포

### Added

| 항목 | 값 |
| --- | --- |
| Claim | Execution Order 원자적 Claim |
| 수량 정규화 | 공통 주문 수량 정규화 |
| Fatal Max | 환경변수 기반 비상 최대 수량 검사 |
| Cancel/Modify | 취소·정정 수량 resolver |
| 상태 Guard | Terminal 상태 전이 Guard |
| Test | Property Test와 회귀 Test |
| 동기화 실패 | Broker 성공 후 상태 동기화 실패 처리 |
| 배포 증적 | MarketConnector 전용 Versioned Artifact 배포 |

### Changed

| 항목 | 값 |
| --- | --- |
| 제출 순서 | 수량 검증 → Fatal Max → Claim → Broker 호출 |
| Broker 호출 | Claim 성공 주문에서만 수행 |
| 전량 취소 Payload | `ORD_QTY=0` · `QTY_ALL_ORD_YN=Y` 고정 |
| 부분 취소 Payload | 요청 수량 · `QTY_ALL_ORD_YN=N` 고정 |
| 상태 UPDATE | 허용된 이전 상태 조건부 전이 |
| Artifact 목적지 | MarketConnector 전용 Versioned S3로 통일 |
| Artifact Prefix | 기존 성공 배포 기준과 일치 |

### Fixed

| 문제 | 해결 |
| --- | --- |
| 동일 Execution Order 재제출 가능성 | 원자적 Claim으로 중복 차단 |
| 0·음수·소수·Boolean 수량 | Broker 호출 전 명시적 거부 |
| 비상 이상치 수량 | Fatal Max 초과 시 자동 축소 없이 차단 |
| 전량 취소 수량 오류 | 전량 0/Y 계약 적용 |
| Terminal 상태 역행 가능성 | 조건부 상태 전이 Guard 적용 |
| Broker 성공 후 DB 동기화 실패 오판정 | 성공 출력과 후속 상태 기록 차단 |
| Claim 실패 상태 기록의 추가 예외 | Batch 중단 없이 별도 오류 처리 |
| CodeBuild의 Migration Artifact 경로 잔존 | 전용 Versioned Artifact 경로로 전환 |

### Security

| 항목 | 결과 |
| --- | --- |
| 전체 Pytest | 55개 성공 |
| Ruff | 성공 |
| Python Compile | 성공 |
| CodeBuild | 성공 |
| Versioned S3 Artifact | 생성 성공 |
| CodeDeploy In-place | 성공 |
| Lifecycle Hook | 전체 성공 |
| Manifest Source SHA | 일치 |
| 핵심 운영 파일 SHA-256 | 3개 일치 |
| `config.py` 권한 · Runtime token | 보존 |
| Connector 프로세스 | 0개 |
| Broker 주문 API 호출 | 0건 |
| BUY·SELL·취소·정정 실행 | 0건 |
| Secret 값 출력 | 0건 |

## 2026-08-01 — MarketConnector DevOps와 CodeDeploy In-place 배포

### Added

| 항목 | 값 |
| --- | --- |
| CI | GitHub Actions와 CodeBuild |
| Artifact | Git SHA 기반 Versioned ZIP Bundle |
| Bundle 생성 | committed Git blob 기반 결정적 생성 |
| 포함 Manifest | `.devops/bundle/include.txt` |
| Bundle 검증 | Bundle Contract Test |
| Artifact 저장 | Private Versioned S3 |
| 배포 정의 | `appspec.yml` |
| Lifecycle Hook | CodeDeploy Hook 5종 |
| 단일 실행 보호 | Deployment Lock |
| 배포 전 Backup | 운영 Source Backup |
| Rollback | Backup Source 복원 |
| 재배포 | 동일 S3 Revision 재사용 |
| Intraday Wrapper | Repository와 Bundle 반영 |
| Agent | CodeDeploy Agent 설치와 자동 시작 |

### Changed

| 항목 | 값 |
| --- | --- |
| `config.py` | 환경변수 기반 구조 전환 |
| 민감정보 | KIS App Key·Secret·Paper 계좌 하드코딩 제거 |
| Source 정합 | EC2 운영 Source와 Git Repository 정합화 |
| Build Source | Detached Head 처리 |
| Bundle 기준 | Working Tree에서 committed Git blob으로 변경 |
| Intraday | 실행 순서를 Wrapper로 명확화 |
| 배포 | 파일 권한과 소유자 적용 |
| Application 실행 | 자동 시작 대신 기존 SSM·Scheduler 유지 |

### Fixed

| 문제 | 해결 |
| --- | --- |
| GitHub Repository 접근 | 접근 설정 문제 해결 |
| Detached Head | Branch 이름 가정 Bundle 생성 문제 해결 |
| Bundle Hash | 줄바꿈 변환에 따른 Hash 변동 문제 해결 |
| Manifest 누락 | Intraday Wrapper 누락 해결 |
| Runtime token | 배포 중 token 파일 손실 가능성 차단 |
| 프로세스 안전 | 중복 Connector 실행과 자동 실행 위험 차단 |

### Security

| 항목 | 결과 |
| --- | --- |
| App Key·Secret 하드코딩 | 0건 |
| Paper 계좌번호 하드코딩 | 0건 |
| Bundle 내 token·secret 파일 | 0건 |
| Runtime token 파일 | 보존 |
| Secret 출력 | 0건 |
| 주문 API 호출 | 0건 |
| BUY·SELL·취소·정정 실행 | 0건 |
| Connector 자동 시작 | 0건 |
| 최종 Connector 프로세스 | 0개 |
| S3 Public Access | 차단 |
| S3 암호화 | AES256 |
| S3 Versioning | 활성 |
| Python Compile | 25개 성공 |
| Pytest | 21개 성공 |
| Ruff | 성공 |
| Bundle 파일 | 28개 확인 |
| 첫 배포·Rollback·재배포 | 검증 성공 |

## 2026-07-22 — MarketConnector 문서 기준 재정비

### Changed

| 항목 | 값 |
| --- | --- |
| `AGENTS.md` | MarketConnector 전용 작업 규칙으로 전면 재작성 |
| 최우선 규칙 | 신규 독립 표 2컬럼 · 국소 수정 · 긴 셀 금지 |
| 실행 위험 | token · KIS API · 주문 · DB 쓰기 기준 강화 |
| 주문 안전 | Daily Step 12와 Intraday 매도 approval gate 명확화 |
| Flask 기준 | 실행 API와 View API 책임 분리 |
| DB 기준 | `connector` · `execution` schema와 transaction 주의사항 정리 |
| 운영 기준 | EC2 · SSM RunCommand · Scheduler 책임 정리 |
| 테스트 기준 | 외부 API · token · DB mock 우선 |
| `README.md` | 현재 상태 · 책임 경계 · 위험 경로 중심으로 전면 재구성 |
| `docs/source-file-catalog.md` | 5열 장문 구조를 2열 중심으로 재구성 |
| 문서 체계 | README · CHANGELOG · source catalog 중심으로 단순화 |

### Security

| 항목 | 결과 |
| --- | --- |
| Python 코드 변경 | 없음 |
| Flask · KIS · broker 실행 | 0건 |
| Token 발급 · 갱신 · 파일 접근 | 0건 |
| DB · AWS · Slack 실행 | 0건 |
| Git write 명령 | 0건 |
| 민감정보 원문 신규 기록 | 0건 |

## 2026-07-01 — 전략 주문과 Intraday 운영 문서화

### Added

| 항목 | 값 |
| --- | --- |
| 전략 주문 entrypoint | `connector_strategy_order_execute.py` |
| Intraday Snapshot | `connector_intraday_snapshot_refresh.py` |
| Intraday 판단 | `connector_intraday_position_evaluate.py` |
| 운영 구조 | EC2 · SSM RunCommand · Scheduler |
| Daily approval | `portfolio-paper-daily-step12-17-approval` |
| Intraday approval | `portfolio-paper-intraday-stop-sell-approval` |
| Slack 위임 | `portfolio-event-notifier` Lambda |
| Worklog | `docs/worklog/2026-07-01.md` |

### Changed

| 항목 | 값 |
| --- | --- |
| README 파일 구조 | 신규 entrypoint 3종과 Daily balance wrapper 반영 |
| 장중 흐름 | Snapshot Refresh → Position Evaluate 순서 문서화 |
| hard stop | READY 매도 후보 생성과 실제 broker 제출 책임 분리 |
| 주문 동기화 | direct · summary fallback 우선 의미 설명 |
| 실행 위험 | 신규 entrypoint 3종 추가 |

### Security

| 항목 | 결과 |
| --- | --- |
| 기능 변경 | 없음 |
| Connector · KIS · 주문 실행 | 0건 |
| SSM · Lambda · Slack 실행 | 0건 |
| 민감정보 원문 신규 기록 | 0건 |

## 2026-05-28 — 소스 카탈로그와 설명 주석

### Added

| 항목 | 값 |
| --- | --- |
| `docs/source-file-catalog.md` | 주요 Python 파일의 역할과 운영 위험 정리 |
| Module docstring | 주요 Connector 파일 설명 |
| Function docstring | 잔고 · 시세 · 주문 · 체결 · View 조립 함수 설명 |
| Worklog | `docs/worklog/2026-05-28.md` |

### Changed

| 항목 | 값 |
| --- | --- |
| README | 소스 카탈로그와 주석 정리 결과 반영 |
| `connector_order_check.py` | direct fallback 우선 처리 의미 문서화 |

### Fixed

| 문제 | 해결 |
| --- | --- |
| broad summary 오귀속 위험 | direct `output2` summary를 broad search보다 우선 처리 |

### Security

| 항목 | 결과 |
| --- | --- |
| 기능 실행 | 0건 |
| DB · KIS · broker 호출 | 0건 |
| 민감정보 원문 신규 기록 | 0건 |

## 2026-05-27 — DB 설정 외부화와 schema-per-domain

### Changed

| 항목 | 값 |
| --- | --- |
| DB 설정 | `INTEREST_DB_*` 환경변수 기반 |
| Password | `connector_db.py` 하드코딩 제거 |
| 공통 loader | `db_config.py`의 `get_db_config()` 사용 |
| Database | `portfolio` |
| Schema 구조 | 단일 DB · domain별 schema |
| search path | `connector, execution, legacy, reference, public` |
| 기존 SQL | connection `search_path` 기반 유지 |

### Security

| 항목 | 결과 |
| --- | --- |
| 실제 DB 접속 | 0건 |
| Flask · KIS · broker 실행 | 0건 |
| DB password 원문 기록 | 0건 |

## 2026-05-26 — 초기 문서와 legacy 파일 정리

### Added

| 항목 | 값 |
| --- | --- |
| README | Python MarketConnector 프로젝트 초안 |
| AGENTS | token · 주문 · DB 실행 위험 기준 |
| Worklog | 2026-05-26 문서화 작업 일지 |

### Changed

| 항목 | 값 |
| --- | --- |
| 문서 언어 | 한국어 기준으로 정리 |
| Entrypoint 목록 | legacy 파일 정리 결과 반영 |

### Removed

| 항목 | 값 |
| --- | --- |
| Legacy script | `app.py` · `buy.py` · `balance.py` |
| Legacy script | `order_check.py` · `get_price_realtime.py` · `get_price_closed.py` |
| Cache | `__pycache__` |

### Security

| 항목 | 결과 |
| --- | --- |
| Flask · KIS · broker 실행 | 0건 |
| DB DDL · DML | 0건 |
| 민감정보 원문 신규 기록 | 0건 |
