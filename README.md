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

브로커 주문번호를 `connector_order_request`와 매핑하는 흐름도 포함한다.

### DB Repository 및 Config

`connector_db.py`는 connector account, API call log, balance snapshot, position snapshot, quote, order request, order event, fill, legacy balance/holding/trade order, View 조회 helper를 포함한다.

`config.py`와 DB connection string은 민감정보로 취급한다. 실제 값을 문서에 기록하지 않는다. 운영 환경에서는 환경변수나 로컬 전용 설정으로 분리하는 방향이 적합하다.

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
- PostgreSQL connection string: `[REDACTED]`

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

위 entrypoint는 token 발급/갱신, KIS/브로커 API 호출, 주문 제출, 잔고/보유/체결/시세 조회, DB 저장을 수행할 수 있다.

## 민감정보 규칙

- token 값, app key, app secret, 계좌번호, DB 접속정보, 원본 connection string을 출력하지 않는다.
- `access_token.txt` 같은 token 파일을 읽거나 인용하지 않는다.
- 문서에 필요한 예시는 `[REDACTED]`로 마스킹한다.
- debug/output dump 파일은 운영 소스로 단정하지 않고 로컬 산출물 또는 후보로만 표현한다.

## 문서 변경 검증

문서만 수정한 경우 아래 명령만 사용한다.

```powershell
git status --short
git diff --stat
```

문서 검증을 위해 Flask, KIS, 브로커, token, 잔고, 보유, 시세, 주문, 체결, 크롤러, DB 명령을 실행하지 않는다.
