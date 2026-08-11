# Source File Catalog

port-marketconnector의 주요 파일과 디렉터리 역할을 빠르게 확인하기 위한 문서다.

모든 파일을 나열하는 inventory가 아니라, 구조 이해와 변경 영향 판단에 필요한 항목만 기록한다.

repository root 기준 상대 경로를 사용하며 cache, token 파일과 일회성 산출물은 제외한다.

## 사용 원칙

| 항목 | 값 |
| --- | --- |
| 기준 | 현재 port-marketconnector 코드와 운영 구조 |
| 포함 | 주요 entrypoint · 공통 helper · 운영 영향 파일 |
| 제외 | cache · token · debug dump · 일회성 output |
| 갱신 | 파일의 경로 · 책임 · 실행 위험이 바뀔 때 |
| 생략 | 내부 구현만 바뀌고 파일 책임이 동일한 경우 |
| 민감정보 | token · 계좌 · secret · ARN · endpoint 원문 금지 |

## Root

| 파일 | 역할 |
| --- | --- |
| `AGENTS.md` | MarketConnector 코드와 문서 작업 규칙 |
| `README.md` | 현재 구조 · 실행 위험 · 운영 AS-IS |
| `CHANGELOG.md` | 주요 변경 이력 |
| `config.py` | KIS App Key·Secret·Base URL·Paper 계좌 환경변수 계약 |
| `db_config.py` | PostgreSQL 환경변수 loader |
| `connector_db.py` | DB repository helper |

### 변경 시 확인

| 대상 | 확인 |
| --- | --- |
| `AGENTS.md` | 주문 gate · 실행 제한 · 문서 갱신 규칙 |
| `README.md` | 현재 entrypoint와 운영 구조 |
| `CHANGELOG.md` | 실제 Connector 변경만 기록 |
| `config.py` | 민감정보 하드코딩 금지 · Runtime 주입 대상 · 배포 후 권한 600 |
| `db_config.py` | `INTEREST_DB_*`와 password 기본값 |
| `connector_db.py` | schema · SQL · transaction · mapping |

## Flask

| 파일 | 역할 |
| --- | --- |
| `connector_app.py` | 실행 API와 View API route |
| `connector_view_service.py` | View API 응답 조립 |

### 실행 API

| 경로 | 역할 |
| --- | --- |
| `/api/v1/quotes/realtime` | 실시간 시세 조회 · 저장 가능 |
| `/api/v1/quotes/eod` | 기간 시세 조회 · 저장 가능 |
| `/api/v1/accounts/balance` | 잔고 조회 · Snapshot 저장 |
| `/api/v1/orders/buy` | 매수 주문 |
| `/api/v1/orders/sell` | 매도 주문 |
| `/api/v1/orders/cancel` | 주문 취소 |
| `/api/v1/orders/modify` | 주문 정정 |
| `/api/v1/orders/history` | 주문 · 체결 동기화 |
| `/api/v1/price` | legacy 시세 alias |

### View API

| 경로 | 역할 |
| --- | --- |
| `/api/v1/view/account-summary` | 계좌 요약 |
| `/api/v1/view/dashboard` | Dashboard |
| `/api/v1/view/balance/latest` | 최신 잔고 |
| `/api/v1/view/positions/latest` | 최신 포지션 |
| `/api/v1/view/orders` | 주문 목록 |
| `/api/v1/view/orders/<order_request_id>` | 주문 상세 |
| `/api/v1/view/order-events` | 주문 이벤트 |
| `/api/v1/view/quotes/realtime/latest` | 최신 시세 |
| `/api/v1/view/quotes/eod` | 기간 시세 |
| `/api/v1/view/strategy/trades/recent` | 최근 전략 거래 |

### 변경 시 확인

| 항목 | 값 |
| --- | --- |
| Route | URL · HTTP method · response contract |
| 실행 API | token · 외부 API · DB side effect |
| View API | port-view DTO와 하위 호환성 |
| 보안 | 계좌 · token · 주문번호 노출 여부 |
| 검증 | 실제 route 호출보다 mock test 우선 |

## Token

| 파일 | 역할 |
| --- | --- |
| `token_manager.py` | token 파일 · 발급 · 갱신 · 삭제 |

### 변경 시 확인

| 항목 | 값 |
| --- | --- |
| Import | import만으로 발급이나 파일 변경 금지 |
| Log | token 원문 출력 금지 |
| Failure | 갱신 실패를 성공으로 처리하지 않음 |
| Test | HTTP와 파일 시스템 mock |
| 보관 | token 파일 source control 제외 |

## Balance와 Position

| 파일 | 역할 |
| --- | --- |
| `connector_balance.py` | Daily 잔고와 포지션 Snapshot |
| `connector_intraday_snapshot_refresh.py` | Intraday Snapshot Refresh |
| `scripts/run_connector_balance_daily.sh` | Daily 잔고 실행 wrapper · Bundle·CodeDeploy 배포 대상 |
| `scripts/run_intraday_snapshot_and_evaluate.sh` | Snapshot Refresh 이후 Position Evaluate 순차 실행 Intraday wrapper |

### 주요 저장 대상

| 테이블 | 역할 |
| --- | --- |
| `connector.connector_balance_snapshot` | 계좌 Snapshot |
| `connector.connector_position_snapshot` | 보유 포지션 Snapshot |
| `connector.connector_api_call_log` | KIS 호출 기록 |
| legacy balance · holdings | 과거 호환 |

### 변경 시 확인

| 항목 | 값 |
| --- | --- |
| 빈 보유 | 정상 청산과 API 이상 구분 |
| OPEN mismatch | 전략 OPEN과 KIS 보유 불일치 |
| stale 정리 | 계좌 · 기준일 범위 |
| 기준 시각 | balance와 position 정합 |
| DB user | Connector 쓰기 권한 사용 |

## Quote

| 파일 | 역할 |
| --- | --- |
| `connector_quote_realtime.py` | 현재가 · 실시간 시세 |
| `connector_quote_closed.py` | 기간 시세 · EOD upsert |

### 변경 시 확인

| 항목 | 값 |
| --- | --- |
| `--no-save` | DB 저장만 차단 가능 |
| KIS 호출 | 옵션과 별개로 발생 가능 |
| Token | 발급 · 갱신 가능성 |
| Mapping | 현재가와 EOD 필드 의미 |
| Upsert | key와 거래일 기준 |

## 주문 공통

| 파일 | 역할 |
| --- | --- |
| `connector_order_common.py` | 주문 수량 정규화 · Fatal Max · 취소/정정 resolver · 상태 전이 Guard · broker 공통 처리 |
| `connector_buy.py` | 매수 주문 wrapper |
| `connector_sell.py` | 매도 주문 wrapper |
| `connector_cancel.py` | 전량 0/Y · 부분 수량/N 계약을 적용하는 취소 wrapper |
| `connector_modify.py` | 전량·부분 수량 계약을 적용하는 정정 wrapper |

### 변경 시 확인

| 항목 | 값 |
| --- | --- |
| Idempotency | 기존 order request 상태 확인 |
| Retry | 주문 자동 retry 금지 |
| TR ID | paper/live와 요청 유형 정합 |
| Status | 요청 · 접수 · 체결 · 거절 구분 |
| Log | broker 주문번호 전체 노출 금지 |
| Quantity | 1 이상 정수 · Boolean·소수 차단 |
| Fatal Max | 초과 수량 자동 축소 금지 |
| Cancel/Modify | 전량 0/Y · 부분 수량/N |
| Terminal Status | 허용된 이전 상태 조건부 UPDATE |
| Side Effect | 검증 실패 시 Broker·token·DB 후속 호출 차단 |
| Test | Property · 경계 · 호출 횟수 검증 |

## 주문과 체결 동기화

| 파일 | 역할 |
| --- | --- |
| `connector_order_check.py` | 주문 이벤트 · 체결 · legacy 주문 동기화 |

### 주요 저장 대상

| 테이블 | 역할 |
| --- | --- |
| `connector.connector_order_event` | 주문 상태 이벤트 |
| `connector.connector_fill` | 체결 |
| `connector.connector_api_call_log` | API 호출 기록 |
| legacy trade orders | 과거 호환 |

### 변경 시 확인

| 항목 | 값 |
| --- | --- |
| Direct | 특정 주문 조회 우선 |
| Summary | direct `output2` fallback |
| Broad | direct 처리 후 검색 |
| Mapping | broker 번호와 order request |
| 오귀속 | broad summary를 특정 주문에 연결 금지 |

## Daily 전략 주문

| 파일 | 역할 |
| --- | --- |
| `connector_strategy_order_execute.py` | 승인된 전략 주문 제출 · Execution Order 원자적 Claim · 중복 Broker 제출 차단 · 수량과 Fatal Max 검증 · Broker 결과와 상태 동기화 · 단위 실패 격리 |

### 운영 기준

| 항목 | 값 |
| --- | --- |
| 기본 | dry run |
| 실제 주문 | `--execute` |
| 승인 gate | `portfolio-paper-daily-step12-17-approval` |
| Claim | `REQUESTED → SUBMITTING` |
| Broker 호출 | Claim 성공 주문만 |
| 중복 주문 | Claim 0행이면 skip |
| 상태 실패 | Broker 성공으로 출력하지 않음 |
| Batch | 단위 실패 후 후속 대상 계속 |
| 저장 | order request · API call log |
| 후속 | `connector_order_check.py` |

approval gate를 우회하는 기본값이나 자동 실행 fallback을 추가하지 않는다.

## Intraday hard stop

| 파일 | 역할 |
| --- | --- |
| `connector_intraday_position_evaluate.py` | OPEN 포지션 hard stop 판단 |

### 책임 경계

| 단계 | 책임 |
| --- | --- |
| Snapshot | `connector_intraday_snapshot_refresh.py` |
| 판단 | `connector_intraday_position_evaluate.py` |
| 결과 | `execution.strategy_intraday_position_check` |
| 주문 후보 | `execution.strategy_execution_order` READY 매도 |
| 알림 | event notifier Lambda |
| 실제 매도 | approval workflow 이후 |

판단 entrypoint에 broker 주문 제출 로직을 추가하지 않는다.

## Database Contract

| 항목 | 값 |
| --- | --- |
| Database | `portfolio` |
| Config loader | `db_config.py` · `get_db_config()` |
| 환경변수 | `INTEREST_DB_*` |
| Password | 기본값 없음 |
| search path | `connector, execution, legacy, reference, public` |

### Schema 책임

| Schema | 역할 |
| --- | --- |
| `connector` | 계좌 · Snapshot · 시세 · 주문 · 체결 |
| `execution` | 전략 주문 · position · intraday check |
| `legacy` | 과거 호환 |
| `reference` | 종목과 공통 기준정보 |
| `public` | fallback search path |

### 주요 쓰기 대상

| 영역 | 테이블 |
| --- | --- |
| API Log | `connector.connector_api_call_log` |
| Balance | `connector.connector_balance_snapshot` |
| Position | `connector.connector_position_snapshot` |
| Quote | `connector.connector_quote_realtime` · `connector.connector_quote_eod` |
| Order | `connector.connector_order_request` |
| Event | `connector.connector_order_event` |
| Fill | `connector.connector_fill` |
| Strategy Order | `execution.strategy_execution_order` |
| Intraday Check | `execution.strategy_intraday_position_check` |

추정 table과 column으로 SQL을 작성하지 않는다.

기존 unqualified SQL은 connection `search_path`와 정합성을 확인한다.

## EC2와 SSM

| 항목 | 역할 |
| --- | --- |
| MarketConnector EC2 | Python entrypoint 실행 |
| SSM RunCommand | Daily · Intraday 원격 실행 |
| EventBridge Scheduler | EC2 lifecycle · 장중 주기 |
| Step Functions | Daily와 매도 approval |
| Event Notifier Lambda | 운영 알림 |

### 운영 식별자

| 항목 | 이름 |
| --- | --- |
| EC2 Role | `portfolio-paper-marketconnector-ec2-role` |
| Lambda policy | `portfolio-paper-marketconnector-event-notifier-invoke` |
| Start Scheduler | `portfolio-paper-ec2-start-0750-kst` |
| Stop Scheduler | `portfolio-paper-marketconnector-stop-1550-kst` |
| Intraday Scheduler | `portfolio-paper-intraday-snapshot-evaluate-10min-kst` |

실제 ARN, instance id, command id, account-id와 endpoint는 기록하지 않는다.

## DevOps와 배포

### Root 배포 파일

| 파일 | 역할 |
| --- | --- |
| `appspec.yml` | CodeDeploy EC2 In-place 배포와 Lifecycle Hook 연결 |
| `requirements.txt` | EC2 Runtime Dependency 기준 |
| `.github/workflows/marketconnector-codebuild.yml` | main Push · 수동 실행 자동 Release · CodeBuild · EC2 상태 준비/복원 · CodeDeploy 연결 |

### Bundle

| 파일 | 역할 |
| --- | --- |
| `.devops/bundle/include.txt` | Versioned ZIP에 포함할 배포 파일 Manifest |
| `.devops/scripts/build_bundle.py` | committed Git blob 기반 Versioned ZIP과 Manifest 생성 |
| `.devops/codebuild/buildspec.yml` | Compile · Test · Ruff · Bundle · S3 업로드 Phase |
| `.devops/scripts/compile_check.py` | 안전한 Python Compile 검사 |

`.devops/artifacts` 산출물과 일회성 Artifact는 카탈로그에 나열하지 않는다.

### CodeDeploy Hook

`appspec.yml`이 참조하는 상대 경로 기준이다.

| 파일 | 역할 |
| --- | --- |
| `codedeploy/application_stop.sh` | 실행 프로세스와 배포 안전 상태 확인 |
| `codedeploy/before_install.sh` | Deployment Lock과 기존 Source Backup |
| `codedeploy/after_install.sh` | Bundle 설치 · 권한 적용 · Runtime 파일 보존 |
| `codedeploy/application_start.sh` | 자동 시작 없이 안전 상태 유지 |
| `codedeploy/validate_service.sh` | Compile · Wrapper Syntax · 필수 파일 · 단일 실행 · Lock 해제 검증 |

### 변경 시 확인

| 항목 | 값 |
| --- | --- |
| Bundle Include 변경 | Contract Test 갱신 |
| Hook 변경 | `appspec.yml` 참조 정합성 확인 |
| Shell 변경 | LF와 `bash -n` 확인 |
| Runtime 파일 | Bundle 미포함 확인 |
| Source·Bundle | Source SHA와 Bundle Version 정합성 확인 |
| Hook 실행 | Connector·주문 자동 실행 없음 확인 |
| Rollback | token 파일 보존 확인 |
| Artifact Store | MarketConnector 전용 Versioned S3 |
| Revision | Bucket · Key · Version ID 고정 |
| Manifest | Source SHA 확인 |
| 운영 Source | Manifest SHA-256과 실제 파일 비교 |
| Runtime 보호 | `config.py` · token 파일 보존 |
| 무주문 검증 | Connector 프로세스와 Broker 호출 0건 |

## Tests

| 변경 | 검증 |
| --- | --- |
| Python 문법 | 안전한 compile check |
| 순수 함수 | unit test |
| Flask route | test client · 외부 의존 mock |
| KIS parsing | HTTP response mock |
| Token | HTTP · 파일 mock |
| 주문 | dry-run · idempotency · 원자적 Claim · 중복 차단 |
| 주문 수량 | 수량 Property Test · Fatal Max |
| 취소·정정 | 전량·부분 취소 Contract |
| 주문 상태 | Terminal 상태 단조성 |
| 호출 횟수 | Broker·token·DB 호출 횟수 |
| Repository | SQL · parameter · transaction |
| Shell | syntax · 인자 전달 정적 확인 |
| 문서 | 링크 · 사실 · 가독성 |

실제 KIS, token, 계좌, DB와 Lambda를 테스트에 사용하지 않는다.

## Documents

| 문서 | 역할 |
| --- | --- |
| `AGENTS.md` | MarketConnector 작업 규칙 |
| `README.md` | 현재 구조와 운영 AS-IS |
| `CHANGELOG.md` | 주요 변경 이력 |
| `docs/source-file-catalog.md` | 주요 파일과 책임 |

날짜별 `docs/worklog/*.md`는 신규 생성하지 않는다.

과거 worklog 파일을 유지하는 경우 역사 기록으로만 취급하고 신규 작업 기준으로 사용하지 않는다.

## 외부 의존 모듈

| 모듈 | 관계 |
| --- | --- |
| `port-view` | Connector 조회 API 소비 |
| `port-interest-crawler` | 원천 데이터 수집 |
| `port-interest-preprocessor` | 전처리 |
| `port_strategy_common` | 공통 전략 모델 |
| `port_strategy_research` | 전략 연구 |
| `port_strategy_decision` | 전략 판단 |
| `port_strategy_execution` | 주문 계획과 실행 상태 |

다른 MS의 내부 코드와 문서는 MarketConnector 변경 범위에 자동 포함하지 않는다.

## 카탈로그 갱신 조건

| 변경 | 처리 |
| --- | --- |
| 주요 Python 파일 생성 · 삭제 · 이름 변경 | 갱신 |
| Flask route와 entrypoint 책임 변경 | 갱신 |
| package · scripts 구조 변경 | 갱신 |
| token · 설정 · DB loader 역할 변경 | 갱신 |
| EC2 · SSM wrapper 역할 변경 | 갱신 |
| 문서 생성 · 삭제 · 역할 변경 | 갱신 |
| 내부 구현만 변경 · 책임 동일 | 생략 가능 |

카탈로그 갱신 시 전체 repository inventory를 새로 만들지 않는다.

변경된 영역과 인접 항목만 확인한다.
