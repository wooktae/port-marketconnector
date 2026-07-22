# port-marketconnector

KIS 국내 주식 API 연동을 위한 Python 기반 market connector 마이크로서비스다.

이 저장소에는 Flask API entrypoint, 브로커 API client script, token 처리, 주문 제출 helper, 잔고/보유/시세 조회, PostgreSQL repository helper가 포함되어 있다.

이 문서는 정적 파일 분석만 기준으로 작성했다. 브로커 API, Flask app, token 발급/갱신, 잔고 조회, 주문 조회, 주문 제출, DB 명령은 실행하지 않았다.

## 기술 스택

- Python
- Flask
- Requests
- psycopg v3
- PostgreSQL
- KIS 국내 주식 API 연동

## 파일 구조 요약

- `connector_app.py`: 메인 Flask connector API. 실행 API와 View 조회 API를 함께 제공한다.
- `token_manager.py`: access token 파일 처리, token 발급, token 갱신 helper.
- `config.py`: KIS 및 계좌 관련 로컬 설정. 모든 값은 민감정보로 취급한다.
- `connector_db.py`: connector/legacy 테이블용 PostgreSQL repository helper.
- `connector_view_service.py`: dashboard, balance, positions, orders, order events, quote, strategy trades 조회 응답 조립.
- `connector_order_common.py`: 주문 제출 공통 로직, order request 저장, broker 응답 반영, 취소/정정 공통 helper.
- `connector_buy.py`, `connector_sell.py`: 매수/매도 주문 wrapper.
- `connector_cancel.py`, `connector_modify.py`: 주문 취소/정정 wrapper.
- `connector_balance.py`: 잔고/보유 조회 및 connector snapshot 저장 흐름.
- `connector_order_check.py`: 주문/체결 조회 및 order event/fill 저장 흐름.
- `connector_quote_realtime.py`, `connector_quote_closed.py`: 실시간 시세 및 기간 시세 조회 흐름. 옵션에 따라 DB 저장 가능.
- `connector_strategy_order_execute.py`: Step 12 전략 execution order 제출 entrypoint. 기본은 dry run이며 `--execute` 사용 시 브로커 주문 제출과 DB 반영이 이어질 수 있다.
- `connector_intraday_snapshot_refresh.py`: 장중 잔고/보유 snapshot 재조회 entrypoint. connector snapshot 저장 흐름을 담당한다.
- `connector_intraday_position_evaluate.py`: 장중 보유 포지션 hard stop 판단 entrypoint. 조건 충족 시 READY execution order 생성과 Slack 통지가 발생할 수 있다.
- `scripts/run_connector_balance_daily.sh`: 일일 잔고 snapshot 갱신 shell wrapper.
- `docs/source-file-catalog.md`: AWS Migration 전 파일별 역할, 책임, 운영 주의사항을 정리한 파일 카탈로그.

## Flask API 요약

`connector_app.py`는 두 종류의 route를 제공한다.

실행 API는 브로커 API 호출 또는 DB 쓰기를 수행할 수 있다.

- `GET /api/v1/quotes/realtime`: 실시간 시세 조회 및 DB 저장
- `GET /api/v1/quotes/eod`: 기간 시세 조회 및 DB 저장
- `GET /api/v1/accounts/balance`: 잔고/보유 조회 및 DB 저장
- `POST /api/v1/orders/buy`: 매수 주문 흐름
- `POST /api/v1/orders/sell`: 매도 주문 흐름
- `POST /api/v1/orders/cancel`: 주문 취소 흐름
- `POST /api/v1/orders/modify`: 주문 정정 흐름
- `GET /api/v1/orders/history`: 주문/체결 조회 및 DB 저장
- `GET /api/v1/price`: 실시간 시세 legacy alias

View API는 조회 중심이지만 DB 접근에 의존한다.

- `GET /api/v1/view/account-summary`
- `GET /api/v1/view/dashboard`
- `GET /api/v1/view/balance/latest`
- `GET /api/v1/view/positions/latest`
- `GET /api/v1/view/orders`
- `GET /api/v1/view/orders/<order_request_id>`
- `GET /api/v1/view/order-events`
- `GET /api/v1/view/quotes/realtime/latest`
- `GET /api/v1/view/quotes/eod`
- `GET /api/v1/view/strategy/trades/recent`

`connector_app.py`는 `GET /api/v1/price` legacy alias도 제공한다.

## 주요 기능 영역

### Token Manager

`token_manager.py`는 아래 동작이 가능하다.

- 기존 access token 파일 읽기
- KIS token endpoint를 통한 새 token 발급
- token 파일 삭제
- API 응답에서 token 만료를 감지한 경우 token 갱신

문서 작업 또는 정적 분석 중에는 이 모듈을 실행하지 않는다. token 파일을 만들거나 삭제하거나 token 관련 값을 출력할 수 있다.

### 잔고 및 보유 Snapshot

`connector_balance.py`는 KIS 잔고 API를 호출하고 계좌 요약 및 보유 종목을 파싱한 뒤 아래 테이블에 저장할 수 있다.

- `connector_balance_snapshot`
- `connector_position_snapshot`
- legacy `balance_summary`
- legacy `holdings`
- `connector_api_call_log`

같은 계좌/일자의 stale `connector_position_snapshot` row를 삭제한 뒤 현재 보유 종목 기준으로 다시 저장할 수 있다.

### 시세 조회

`connector_quote_realtime.py`는 실시간/현재가 API를 호출하고 `connector_quote_realtime`에 저장할 수 있다.

`connector_quote_closed.py`는 기간 시세 API를 호출하고 `connector_quote_eod`에 upsert할 수 있다.

시세 script의 `--no-save` option은 DB 저장만 막는다. 외부 브로커 API 호출과 token 발급/갱신 가능성은 남아 있다.

### 주문 제출

`connector_buy.py`와 `connector_sell.py`는 `connector_order_common.py`의 공통 주문 로직을 사용한다.

주문 흐름은 아래 작업을 수행할 수 있다.

- `connector_order_request` 생성
- 브로커 주문 제출
- 브로커 응답을 order request에 반영
- strategy signal과 order mapping 저장
- API call log 저장

`connector_cancel.py`와 `connector_modify.py`는 기존 broker order context를 조회한 뒤 취소/정정 요청을 제출할 수 있다.

### 주문 및 체결 동기화

`connector_order_check.py`는 KIS 주문/체결 내역을 조회하고 아래 테이블에 저장할 수 있다.

- `connector_order_event`
- `connector_fill`
- legacy `trade_orders`
- `connector_api_call_log`

브로커 주문번호를 `connector_order_request`와 매핑하는 흐름도 포함한다. direct 조회에서 `output1`이 비어도 `output2` summary가 있으면 broad search보다 direct fallback을 먼저 처리해 broad summary가 특정 주문 event/fill에 섞이지 않도록 순서를 유지한다.

### 전략 주문 실행 및 장중 운영 흐름

2026-07-01 기준으로 아래 세 개의 신규 entrypoint가 운영에 사용된다. 세 파일 모두 실행 위험 파일로 취급한다.

- `connector_strategy_order_execute.py`
  - 역할: 전략 daily execution order 중 `REQUESTED` 상태 주문을 KIS paper 계정으로 제출한다.
  - 기본 동작은 dry run이며 `--execute` 옵션을 사용해야 실제 broker 호출 경로가 열린다.
  - Step Functions 승인 gate `portfolio-paper-daily-step12-17-approval` 통과 후에만 `--execute`로 실행하는 것을 전제로 한다.
  - 저장 흐름: `connector_order_request` 생성/갱신 및 `connector_api_call_log` 기록. 이후 후속 Step에서 `connector_order_check.py`의 direct/summary fallback 우선 경로로 `connector_order_event`, `connector_fill`이 채워진다.
- `connector_intraday_snapshot_refresh.py`
  - 역할: 장중 시간대에 KIS 잔고 API를 호출해 `connector_balance_snapshot`과 `connector_position_snapshot`을 갱신한다.
  - `connector_balance.py`와 분리된 이유는 daily Step1 잔고 확정과 장중 monitor의 안전 요건이 다르기 때문이다.
  - KIS `output1`이 비어 있을 때 `execution.strategy_position_state`에 OPEN 포지션이 남아 있으면 mismatch로 종료하고, OPEN이 없으면 정상 종료로 처리한다.
  - 이 entrypoint는 전략 판단이나 broker 주문 제출을 수행하지 않는다.
- `connector_intraday_position_evaluate.py`
  - 역할: `strategy_position_state`에서 OPEN 포지션과 최신 connector snapshot을 조회해 hard stop 조건 여부를 판단한다.
  - 판단 결과는 `execution.strategy_intraday_position_check`에 기록된다.
  - hard stop 조건이 충족되면 `strategy_execution_order`에 READY 상태 매도 주문 row를 생성하고, `--notify-slack` 옵션이 있을 때 `portfolio-event-notifier` Lambda로 `INTRADAY_STOP_LOSS` 알림을 보낸다.
  - 이 파일은 broker 주문 제출 경로를 포함하지 않는다. 실제 매도 주문 제출은 Step Functions 승인 gate `portfolio-paper-intraday-stop-sell-approval` 통과 후 후속 실행에서 처리된다.

장중 자동화는 EventBridge Scheduler `portfolio-paper-intraday-snapshot-evaluate-10min-kst`가 09:10 KST부터 15:50 KST 사이에 10분 주기로 SSM RunCommand를 트리거하고, MarketConnector EC2에서 `connector_intraday_snapshot_refresh.py` → `connector_intraday_position_evaluate.py` 순서로 실행된다. 두 번째 실행은 `--create-order --notify-slack` 옵션과 함께 호출된다는 것을 전제로 한다.

### EC2 및 SSM 운영 구조

MarketConnector는 EC2 인스턴스에서 SSM RunCommand로 실행되는 것을 기준으로 운영한다.

- IAM Role: `portfolio-paper-marketconnector-ec2-role`.
- Inline policy: `portfolio-paper-marketconnector-event-notifier-invoke`. `lambda:InvokeFunction` 권한을 `portfolio-event-notifier` Lambda에만 부여한다.
- EC2 lifecycle Scheduler
  - `portfolio-paper-ec2-start-0750-kst`: 07:50 KST에 EC2 start
  - `portfolio-paper-marketconnector-stop-1550-kst`: 15:50 KST에 EC2 stop
- 장중 실행 Scheduler
  - `portfolio-paper-intraday-snapshot-evaluate-10min-kst`: 09:10 KST~15:50 KST 사이 10분 주기 실행 트리거
- Slack notify 옵션은 MarketConnector 실행 결과를 `portfolio-event-notifier` Lambda로 위임하는 형태이며, 개별 스크립트가 Slack webhook을 직접 호출하지 않는다.
- Step Functions 승인 gate
  - `portfolio-paper-daily-step12-17-approval`: Step 12(`connector_strategy_order_execute.py --execute`) 승인 후 실행 gate
  - `portfolio-paper-intraday-stop-sell-approval`: 장중 hard stop 매도 주문 제출 승인 gate

실제 IAM Role ARN, Lambda ARN, secret ARN, account-id, KIS app key/secret, token, 계좌번호, broker order number, DB password, Slack webhook URL, RDS endpoint hostname은 문서에 기록하지 않는다. 필요 시 `[REDACTED]`로만 표시한다.

### DB Repository 및 Config

`connector_db.py`는 connector account, API call log, balance snapshot, position snapshot, quote, order request, order event, fill, legacy balance/holding/trade order, View 조회 helper를 포함한다.

DB 접속정보는 `db_config.py`의 `get_db_config()`를 통해 `INTEREST_DB_*` 환경변수에서 읽는다. `INTEREST_DB_PASSWORD`는 기본값이 없으며 비어 있으면 실행 시 `RuntimeError`가 발생한다.

로컬 PostgreSQL 기본 DB name은 `portfolio`다. AWS Migration 준비 관점에서도 단일 PostgreSQL DB `portfolio` 안에 domain별 schema를 두는 schema-per-domain 구조를 기준으로 문서화한다.

이 모듈의 DB connection `search_path`는 아래 순서를 기준으로 적용한다.

```text
connector, execution, legacy, reference, public
```

public에 있던 테이블은 domain schema로 이동되었지만, 이 모듈의 기존 SQL은 schema-qualified table name으로 바꾸지 않고 connection `search_path` 기반으로 동작한다. 따라서 기존 API 경로, 함수명, 테이블명, 브로커 요청 의미는 유지한다.

`config.py`와 DB password는 민감정보로 취급한다. 실제 값을 문서에 기록하지 않는다.

## 설정 방법

Python 가상환경을 만들고 필요한 package를 설치한다. 현재 루트에는 dependency lock 파일이 확인되지 않았다.

예시 package:

```powershell
pip install flask requests psycopg
```

설정값은 로컬 Python config 변수와 DB 연결 설정에서 주입되는 구조다. 민감정보는 source control 밖에서 관리하는 것이 적합하다.

- KIS app key: `[REDACTED]`
- KIS app secret: `[REDACTED]`
- KIS base URL: `[REDACTED]`
- 계좌번호: `[REDACTED]`
- 계좌 상품 코드: `[REDACTED]`
- `INTEREST_DB_HOST`: PostgreSQL host. 기본값은 `localhost`.
- `INTEREST_DB_PORT`: PostgreSQL port. 기본값은 `5433`.
- `INTEREST_DB_NAME`: PostgreSQL database name. 기본값은 `portfolio`.
- `PORTFOLIO_DB_NAME`: 포트폴리오 공통 DB name을 별도 환경변수로 설명하거나 사용하는 경우에도 기본값은 `portfolio`로 맞춘다.
- `INTEREST_DB_USER`: PostgreSQL user. 기본값은 `postgres`.
- `INTEREST_DB_PASSWORD`: PostgreSQL password. 기본값 없음. 예시는 `[REDACTED]`.

## 실행 방법

운영자가 브로커 API 호출 또는 DB 쓰기를 의도한 경우가 아니라면 connector app과 script를 실행하지 않는다.

아래 명령은 구조 이해를 위한 예시일 뿐이다.

```powershell
python connector_app.py
```

위 명령은 route 사용 방식에 따라 브로커 API 접근으로 이어질 수 있으므로 실행 위험 명령으로 취급한다.

## 실행 위험 목록

문서 작업 중에는 아래 명령을 실행하지 않는다.

- `python token_manager.py`
- `python connector_app.py`
- `python connector_buy.py`
- `python connector_sell.py`
- `python connector_cancel.py`
- `python connector_modify.py`
- `python connector_balance.py`
- `python connector_order_check.py`
- `python connector_quote_realtime.py`
- `python connector_quote_closed.py`
- `python connector_strategy_order_execute.py`
- `python connector_intraday_snapshot_refresh.py`
- `python connector_intraday_position_evaluate.py`

위 entrypoint는 token 발급/갱신, KIS/브로커 API 호출, 주문 제출, 잔고/보유/체결/시세 조회, DB 저장, 장중 stop-loss 판단, `strategy_execution_order` READY 생성, `portfolio-event-notifier` Lambda 호출을 수행할 수 있다.

## 민감정보 규칙

- token 값, app key, app secret, 계좌번호, DB 접속정보, 원본 connection string을 출력하지 않는다.
- `access_token.txt` 같은 token 파일을 읽거나 인용하지 않는다.
- 민감정보는 환경변수 또는 source control 밖의 로컬 설정으로 관리하고, 실제 password/token/account/webhook 값을 문서에 쓰지 않는다.
- 문서에 필요한 예시는 `[REDACTED]`로 마스킹한다.
- debug/output dump 파일은 운영 소스로 단정하지 않고 로컬 산출물 또는 후보로만 표현한다.

## 문서 변경 검증

문서만 수정한 경우 아래 명령만 사용한다.

```powershell
git status --short
git diff --stat
```

문서 검증을 위해 Flask, KIS, 브로커, token, 잔고, 보유, 시세, 주문, 체결, 크롤러, DB 명령을 실행하지 않는다.

## 문서화 산출물

2026-05-28 기준으로 `docs/source-file-catalog.md`를 추가해 repository의 주요 Python 소스와 문서 파일을 한글로 정리했다.

Python 파일에는 module docstring과 운영상 중요한 핵심 함수 docstring을 추가했다. 이 변경은 파일 역할과 실행 위험 설명을 위한 주석 정리이며, API 경로, 함수명, DB 테이블명, 브로커 요청 의미, 실행 순서는 변경하지 않았다.

2026-07-01 기준으로 신규 entrypoint 3종(`connector_strategy_order_execute.py`, `connector_intraday_snapshot_refresh.py`, `connector_intraday_position_evaluate.py`)과 EC2/SSM 운영 구조, 장중 10분 주기 snapshot refresh 및 stop-loss 승인 gate 연계를 README에 반영했다. 다른 마이크로서비스(port-view, StrategyExecution, StrategyDecision, StrategyResearch, Crawler, Preprocessor)의 내부 로직과 Step Functions/EventBridge/Lambda 내부 구현 상세는 반영 범위에서 제외했다.
