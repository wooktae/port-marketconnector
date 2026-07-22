# port-marketconnector

한국투자증권 국내 주식 API와 PostgreSQL을 연결하는 Python 기반 MarketConnector 마이크로서비스다.

시세, 잔고, 포지션, 주문, 체결, 전략 주문과 장중 점검 데이터를 처리하고, port-view가 사용할 조회 API를 제공한다.

이 저장소의 실행 경로는 token 발급, 외부 API 호출, 주문 제출과 DB 쓰기로 이어질 수 있으므로 정적 분석과 문서 작업을 기본값으로 한다.

## 현재 상태

| 항목 | 값 |
| --- | --- |
| 운영 환경 | AWS Paper |
| Compute | MarketConnector EC2 |
| 원격 실행 | SSM RunCommand |
| Daily 주문 | Step Functions approval 이후 실행 |
| Intraday | Scheduler 기반 Snapshot Refresh · hard stop 판단 |
| broker 주문 | 승인된 경로에서만 제출 |
| View API | port-view 조회 연동 |
| aws-live BUY/SELL | 🔴 미진행 |
| 문서 기본 원칙 | token · 주문 · DB 실행 없이 정적 확인 |

> Paper 환경도 실제 주문이 제출될 수 있다. 주문 관련 entrypoint와 Flask 실행 API를 일반 smoke test로 호출하지 않는다.

## 기술 스택

| 항목 | 값 |
| --- | --- |
| Language | Python |
| API | Flask |
| HTTP | Requests |
| Database Driver | psycopg v3 |
| Database | PostgreSQL |
| Broker | KIS 국내 주식 API |
| Compute | EC2 |
| Remote Execution | SSM RunCommand |

## 책임 경계

### 담당 범위

| 영역 | 역할 |
| --- | --- |
| Token | KIS access token 발급 · 갱신 · 파일 관리 |
| Quote | 현재가 · 실시간 · 기간 시세 조회 |
| Balance | 계좌 잔고와 보유 Snapshot |
| Order | 매수 · 매도 · 취소 · 정정 |
| Order Sync | 주문 이벤트와 체결 동기화 |
| Strategy Order | 승인된 전략 주문 제출 |
| Intraday | Snapshot Refresh와 hard stop 판단 |
| View API | port-view용 조회 API |
| Persistence | connector · execution · legacy DB 연동 |

### 직접 담당하지 않는 범위

| 항목 | 실제 책임 영역 |
| --- | --- |
| 전략 생성 | Strategy Research |
| 전략 판단 | Strategy Decision |
| 주문 계획 | Strategy Execution |
| Daily orchestration | Step Functions |
| 자동 실행 시각 | EventBridge Scheduler |
| View 화면 | port-view |
| 데이터 수집 · 전처리 | Crawler · Preprocessor |
| aws-live cutover | 별도 승인 범위 |

## 주요 파일

| 파일 | 역할 |
| --- | --- |
| `connector_app.py` | Flask 실행 API와 View API |
| `token_manager.py` | token 파일 · 발급 · 갱신 |
| `config.py` | KIS와 계좌 로컬 설정 |
| `db_config.py` | DB 환경변수 loader |
| `connector_db.py` | connector · execution · legacy repository helper |
| `connector_view_service.py` | View API 응답 조립 |
| `connector_order_common.py` | 주문 공통 처리 |
| `connector_buy.py` | 매수 주문 wrapper |
| `connector_sell.py` | 매도 주문 wrapper |
| `connector_cancel.py` | 주문 취소 wrapper |
| `connector_modify.py` | 주문 정정 wrapper |
| `connector_balance.py` | Daily 잔고와 포지션 Snapshot |
| `connector_order_check.py` | 주문 이벤트와 체결 동기화 |
| `connector_quote_realtime.py` | 실시간 시세 조회 |
| `connector_quote_closed.py` | 기간 시세 조회 |
| `connector_strategy_order_execute.py` | Daily 전략 주문 제출 |
| `connector_intraday_snapshot_refresh.py` | 장중 Snapshot Refresh |
| `connector_intraday_position_evaluate.py` | 장중 hard stop 판단 |
| `scripts/run_connector_balance_daily.sh` | Daily 잔고 Snapshot wrapper |
| `docs/source-file-catalog.md` | 주요 파일과 책임 |

상세 역할은 [소스 파일 카탈로그](docs/source-file-catalog.md)를 참고한다.

## 실행 위험 등급

### 최고 위험

| 파일 | 위험 |
| --- | --- |
| `connector_buy.py` | 실제 매수 주문 가능 |
| `connector_sell.py` | 실제 매도 주문 가능 |
| `connector_cancel.py` | 주문 취소 가능 |
| `connector_modify.py` | 주문 정정 가능 |
| `connector_strategy_order_execute.py` | `--execute` 사용 시 전략 주문 가능 |
| `connector_app.py` | route에 따라 주문 · API · DB 쓰기 가능 |

### 외부 API와 DB 쓰기 위험

| 파일 | 위험 |
| --- | --- |
| `token_manager.py` | token 발급 · 갱신 · 파일 변경 |
| `connector_balance.py` | 잔고 API · Snapshot 저장 |
| `connector_order_check.py` | 주문 · 체결 조회와 저장 |
| `connector_quote_realtime.py` | 시세 API · 선택적 저장 |
| `connector_quote_closed.py` | 기간 시세 API · upsert |
| `connector_intraday_snapshot_refresh.py` | 장중 잔고 API · Snapshot 저장 |
| `connector_intraday_position_evaluate.py` | 점검 기록 · READY 주문 row 생성 가능 |

`--no-save` 또는 dry run은 모든 side effect를 차단한다는 뜻이 아니다.

token 발급과 외부 API 호출 가능성은 별도로 확인해야 한다.

## Flask API

`connector_app.py`는 실행 API와 View API를 함께 제공한다.

### 실행 API

| 경로 | 역할 · 위험 |
| --- | --- |
| `GET /api/v1/quotes/realtime` | 실시간 시세 조회 · DB 저장 가능 |
| `GET /api/v1/quotes/eod` | 기간 시세 조회 · DB 저장 가능 |
| `GET /api/v1/accounts/balance` | 잔고 조회 · Snapshot 저장 |
| `POST /api/v1/orders/buy` | 매수 주문 |
| `POST /api/v1/orders/sell` | 매도 주문 |
| `POST /api/v1/orders/cancel` | 주문 취소 |
| `POST /api/v1/orders/modify` | 주문 정정 |
| `GET /api/v1/orders/history` | 주문 · 체결 조회와 저장 |
| `GET /api/v1/price` | legacy 시세 alias |

실행 API를 health check나 문서 검증 목적으로 호출하지 않는다.

### View API

| 경로 | 역할 |
| --- | --- |
| `GET /api/v1/view/account-summary` | 계좌 요약 |
| `GET /api/v1/view/dashboard` | Dashboard |
| `GET /api/v1/view/balance/latest` | 최신 잔고 |
| `GET /api/v1/view/positions/latest` | 최신 포지션 |
| `GET /api/v1/view/orders` | 주문 목록 |
| `GET /api/v1/view/orders/<order_request_id>` | 주문 상세 |
| `GET /api/v1/view/order-events` | 주문 이벤트 |
| `GET /api/v1/view/quotes/realtime/latest` | 최신 시세 |
| `GET /api/v1/view/quotes/eod` | 기간 시세 |
| `GET /api/v1/view/strategy/trades/recent` | 최근 전략 거래 |

View API는 조회 중심이지만 DB 연결과 민감정보 노출 범위를 확인해야 한다.

## Token

`token_manager.py`는 다음 동작을 수행할 수 있다.

| 항목 | 내용 |
| --- | --- |
| 읽기 | 기존 token 파일 |
| 발급 | KIS token endpoint |
| 갱신 | 만료 감지 후 재발급 |
| 삭제 | token 파일 제거 |
| 저장 | 신규 token 파일 |

문서 작업과 정적 분석에서는 실행하지 않는다.

token 값과 token 파일 내용은 출력하거나 문서화하지 않는다.

## Balance와 Position Snapshot

### Daily Snapshot

`connector_balance.py`는 KIS 잔고 API를 호출하고 계좌와 보유 데이터를 저장할 수 있다.

| 대상 | 역할 |
| --- | --- |
| `connector.connector_balance_snapshot` | 계좌 요약 |
| `connector.connector_position_snapshot` | 보유 포지션 |
| `connector.connector_api_call_log` | API 호출 기록 |
| legacy balance · holdings | 과거 호환 |

동일 계좌와 기준일의 stale position row를 현재 보유 기준으로 정리할 수 있다.

빈 보유 결과가 정상 청산인지 KIS 응답 이상인지 구분해야 한다.

### Intraday Snapshot

`connector_intraday_snapshot_refresh.py`는 장중 Snapshot을 갱신한다.

| 상황 | 처리 |
| --- | --- |
| KIS 보유 있음 | Snapshot 저장 |
| KIS 보유 없음 · OPEN 포지션 없음 | 정상 종료 가능 |
| KIS 보유 없음 · OPEN 포지션 있음 | mismatch 실패 |

이 entrypoint는 전략 판단과 broker 주문 제출을 담당하지 않는다.

## Quote

| 파일 | 역할 |
| --- | --- |
| `connector_quote_realtime.py` | 현재가 · 실시간 시세 |
| `connector_quote_closed.py` | 기간 시세 · EOD upsert |

`--no-save`는 DB 저장만 차단할 수 있다.

외부 KIS 호출과 token 발급 가능성은 남아 있다.

## 주문 처리

### 매수 · 매도

`connector_buy.py`와 `connector_sell.py`는 `connector_order_common.py`의 공통 로직을 사용한다.

주문 흐름:

1. 주문 요청 상태 확인
2. `connector_order_request` 생성 또는 갱신
3. KIS 주문 제출
4. broker 응답 반영
5. API call log 저장
6. 후속 주문 · 체결 동기화

주문 retry는 조회 API와 동일하게 다루지 않는다.

자동 retry로 중복 주문을 만들지 않는다.

### 취소 · 정정

| 파일 | 역할 |
| --- | --- |
| `connector_cancel.py` | 기존 주문 취소 |
| `connector_modify.py` | 기존 주문 정정 |

원주문 context와 broker 주문 상태를 확인한 뒤 수행한다.

### 주문 · 체결 동기화

`connector_order_check.py`는 다음 데이터를 저장할 수 있다.

| 대상 | 역할 |
| --- | --- |
| `connector.connector_order_event` | 주문 상태 이벤트 |
| `connector.connector_fill` | 체결 |
| `connector.connector_api_call_log` | API 호출 기록 |
| legacy trade orders | 과거 호환 |

direct 조회에서 상세 `output1`이 비어도 `output2` summary가 있으면 broad search보다 direct fallback을 먼저 처리한다.

broad summary를 특정 주문의 event나 fill로 잘못 귀속하지 않는다.

## Daily 전략 주문

`connector_strategy_order_execute.py`는 승인된 전략 주문을 KIS Paper 계정으로 제출한다.

| 항목 | 값 |
| --- | --- |
| 대상 | 전략 실행 주문 |
| 기본 동작 | dry run |
| 실제 제출 | `--execute` |
| 승인 gate | `portfolio-paper-daily-step12-17-approval` |
| 저장 | order request · API call log |
| 후속 | `connector_order_check.py` |

approval workflow를 통과하지 않은 상태에서 `--execute`를 사용하지 않는다.

## Intraday hard stop

장중 흐름은 판단과 실제 주문 제출을 분리한다.

| 단계 | 책임 |
| --- | --- |
| Snapshot | `connector_intraday_snapshot_refresh.py` |
| 판단 | `connector_intraday_position_evaluate.py` |
| 결과 기록 | `execution.strategy_intraday_position_check` |
| 주문 후보 | `execution.strategy_execution_order` READY 매도 |
| 알림 | event notifier Lambda |
| 실제 매도 | 별도 approval workflow 이후 |

hard stop 조건이 충족되어도 판단 entrypoint 자체는 broker 주문을 제출하지 않는다.

실제 매도 제출은 `portfolio-paper-intraday-stop-sell-approval` 이후 처리한다.

## EC2와 SSM 운영

MarketConnector는 EC2에서 SSM RunCommand로 실행되는 구조다.

### 운영 흐름

| 항목 | 값 |
| --- | --- |
| EC2 start | 07:50 KST Scheduler |
| Daily 실행 | Step Functions → SSM |
| Intraday 실행 | 09:10~15:50 KST · 10분 주기 |
| 실행 순서 | Snapshot Refresh → Position Evaluate |
| 알림 | event notifier Lambda |
| EC2 stop | 15:50 KST Scheduler |

### 운영 식별자

| 항목 | 이름 |
| --- | --- |
| EC2 Role | `portfolio-paper-marketconnector-ec2-role` |
| Lambda invoke policy | `portfolio-paper-marketconnector-event-notifier-invoke` |
| EC2 start Scheduler | `portfolio-paper-ec2-start-0750-kst` |
| EC2 stop Scheduler | `portfolio-paper-marketconnector-stop-1550-kst` |
| Intraday Scheduler | `portfolio-paper-intraday-snapshot-evaluate-10min-kst` |

실제 ARN, instance id, command id, account-id와 public IP는 문서에 기록하지 않는다.

## Database

### 연결 기준

| 항목 | 값 |
| --- | --- |
| Database | `portfolio` |
| Config loader | `db_config.py` · `get_db_config()` |
| 환경변수 | `INTEREST_DB_*` |
| Password | 기본값 없음 |
| search path | `connector, execution, legacy, reference, public` |

운영 환경에서는 Connector 전용 DB user를 사용한다.

문서의 예시 기본 user를 운영 권한 기준으로 해석하지 않는다.

### Schema 책임

| Schema | 역할 |
| --- | --- |
| `connector` | 계좌 · Snapshot · 시세 · 주문 · 체결 |
| `execution` | 전략 주문 · position state · intraday check |
| `legacy` | 과거 호환 |
| `reference` | 종목과 공통 기준정보 |
| `public` | fallback search path |

신규 SQL은 가능한 한 schema-qualified 이름을 사용한다.

기존 unqualified SQL은 connection `search_path` 기준으로 동작한다.

### 주요 쓰기 테이블

| 영역 | 테이블 |
| --- | --- |
| API Log | `connector.connector_api_call_log` |
| Balance | `connector.connector_balance_snapshot` |
| Position | `connector.connector_position_snapshot` |
| Quote | `connector.connector_quote_realtime` · `connector.connector_quote_eod` |
| Order | `connector.connector_order_request` |
| Order Event | `connector.connector_order_event` |
| Fill | `connector.connector_fill` |
| Strategy Order | `execution.strategy_execution_order` |
| Intraday Check | `execution.strategy_intraday_position_check` |

실제 컬럼과 table은 코드와 DB 계약을 확인한다.

추정 컬럼명으로 SQL을 작성하지 않는다.

## 설정

현재 dependency lock 파일은 확인되지 않았다.

예시 package 설치:

```powershell
pip install flask requests psycopg
```

민감정보는 source control 밖에서 관리한다.

### DB 환경변수

| 환경변수 | 기본값 · 역할 |
| --- | --- |
| `INTEREST_DB_HOST` | `localhost` |
| `INTEREST_DB_PORT` | `5433` |
| `INTEREST_DB_NAME` | `portfolio` |
| `PORTFOLIO_DB_NAME` | `portfolio` |
| `INTEREST_DB_USER` | 환경별 Connector DB user |
| `INTEREST_DB_PASSWORD` | 기본값 없음 |

### KIS 설정

| 항목 | 처리 |
| --- | --- |
| App key | `[REDACTED]` |
| App secret | `[REDACTED]` |
| Base URL | 환경별 설정 |
| 계좌번호 | `[REDACTED_ACCOUNT_NO]` |
| 상품 코드 | `[REDACTED]` |
| Token | local file 또는 외부 관리 |

`config.py`, token 파일과 local secret 값을 문서에 옮기지 않는다.

## 실행

운영자가 외부 API 호출, 주문 또는 DB 쓰기를 명시적으로 의도한 경우가 아니라면 실행하지 않는다.

아래 명령은 실행 위험 예시다.

```powershell
python connector_app.py
```

Flask 실행 후 route 호출에 따라 KIS API와 DB 쓰기로 이어질 수 있다.

## 실행 금지 목록

문서 작업과 정적 분석 중에는 아래 entrypoint를 실행하지 않는다.

- `token_manager.py`
- `connector_app.py`
- `connector_buy.py`
- `connector_sell.py`
- `connector_cancel.py`
- `connector_modify.py`
- `connector_balance.py`
- `connector_order_check.py`
- `connector_quote_realtime.py`
- `connector_quote_closed.py`
- `connector_strategy_order_execute.py`
- `connector_intraday_snapshot_refresh.py`
- `connector_intraday_position_evaluate.py`

## 보안

다음 값은 코드, 문서와 로그에 원문으로 기록하지 않는다.

- access token
- KIS app key · app secret
- 실제 계좌번호
- DB password와 connection string
- broker 주문번호 전체
- Slack webhook URL
- AWS account-id
- 실제 ARN
- public IP
- RDS hostname
- SSM command id

Placeholder:

| 값 | Placeholder |
| --- | --- |
| 일반 민감정보 | `[REDACTED]` |
| 계좌번호 | `[REDACTED_ACCOUNT_NO]` |
| broker 주문번호 | `[REDACTED_BROKER_ORDER_NO]` |
| ARN | `[REDACTED_ARN]` |
| secret ARN | `[REDACTED_SECRET_ARN]` |
| public IP | `[REDACTED_PUBLIC_IP]` |
| RDS hostname | `[REDACTED_RDS_HOST]` |
| command id | `[REDACTED_COMMAND_ID]` |

## 문서

| 문서 | 역할 |
| --- | --- |
| `AGENTS.md` | port-marketconnector 작업 규칙 |
| `README.md` | 현재 구조와 운영 AS-IS |
| `CHANGELOG.md` | 주요 변경 이력 |
| `docs/source-file-catalog.md` | 주요 파일과 책임 |

날짜별 `docs/worklog/*.md`는 신규 생성하지 않는다.

문서 변경 이력은 `CHANGELOG.md`에 기록한다.
