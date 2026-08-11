# port-marketconnector 작업 규칙

이 문서는 `port-marketconnector` 마이크로서비스의 Python 코드, Flask API, 설정, 테스트, 스크립트와 문서를 수정할 때 적용하는 기준이다.

`port-marketconnector` 관련 작업은 이 문서만 읽어도 작업 범위, 실행 위험, 브로커 주문 gate, DB 책임, 운영 환경과 검증 원칙을 이해할 수 있어야 한다.

## 0. 최우선 문서 가독성 규칙

모든 문서 작업에서 본 섹션을 최우선으로 적용한다.

### 0.0 적용 범위 제한

가독성 규칙은 이번 작업에서 새로 작성하거나 직접 수정하는 부분에만 적용한다.

사용자가 문서 전체 정리나 전수 점검을 명시하지 않은 경우 아래 작업은 수행하지 않는다.

| 항목 | 기본 처리 |
| --- | --- |
| 기존 문서 전체 전수 스캔 | 수행하지 않음 |
| README 전체 재구성 | 수행하지 않음 |
| CHANGELOG 과거 이력 대량 정리 | 수행하지 않음 |
| 신규 scanner · audit 도구 작성 | 수행하지 않음 |
| sub-agent · orchestrator 생성 | 수행하지 않음 |

변경 인접부는 같은 표 행, 같은 bullet 묶음, 같은 짧은 문단까지만 본다.

범위 밖의 기존 위반은 원본을 유지하고 필요 시 후속 후보로만 남긴다.

### 0.1 표 작성 규칙

새로 만드는 독립 요약 표는 기본적으로 2컬럼으로 작성한다.

기본 헤더는 `항목 / 값`이다.

아래 상황에서는 더 구체적인 2컬럼 헤더를 사용할 수 있다.

| 상황 | 우선 헤더 |
| --- | --- |
| 검증 결과 | `항목 / 결과` |
| 파일별 변경 | `파일 / 변경` |
| endpoint 설명 | `경로 / 역할` |
| entrypoint 설명 | `파일 / 역할` |
| 설정 정리 | `설정 / 값` |
| 위험 정리 | `위험 / 처리` |
| 테스트 결과 | `테스트 / 결과` |

기존 표에 행을 추가하는 경우 기존 컬럼 구조를 유지한다.

3컬럼 이상 표는 다음 경우에만 허용한다.

| 조건 | 처리 |
| --- | --- |
| 사용자가 명시적으로 요청 | 요청 구조 사용 |
| 기존 표 보존이 더 안전 | 기존 구조 유지 |
| 비교 구조상 2컬럼 변환 시 의미 손실 | 예외 허용 |

### 0.2 표 셀과 문장 길이

- 표 셀은 2문장 이하로 유지한다.
- 한 셀에 여러 값이 있으면 `<br>`로 나눈다.
- 한 셀에 3개 이상의 사실을 장문으로 넣지 않는다.
- 긴 근거는 표 밖 설명이나 관련 문서 링크로 분리한다.
- 300자 초과 셀과 500자 초과 라인을 만들지 않는다.
- raw log, 전체 API 응답, 전체 SQL 출력과 AWS 응답을 문서에 붙이지 않는다.

### 0.3 문서 밀도

문서 작성 우선순위는 아래를 따른다.

1. 짧은 Summary
2. 짧은 2컬럼 표
3. 짧은 bullet
4. 상세 문서 링크
5. 긴 본문

같은 사실을 README, CHANGELOG, worklog와 상세 문서에 장문으로 반복하지 않는다.

### 0.4 상태 표시

상태 배지는 아래 5종만 사용한다.

| 배지 | 의미 |
| --- | --- |
| 🔴 | 금지 · live · 고위험 |
| 🟠 | 대기 · 관찰 · 미확정 |
| 🟢 | 완료 · 성공 · ENABLED |
| 🔵 | 참고 · 정보 · evidence |
| ⚫ | 해당 없음 |

상태 배지로 충분하면 HTML 색상을 추가하지 않는다.

### 0.5 작업 방식 제한

문서 작업은 아래 순서로 진행한다.

1. 요청 범위 확인
2. 대상 파일 직접 읽기
3. 필요한 부분만 수정
4. UTF-8 No BOM 저장
5. 짧은 after-check
6. 변경 요약 보고

사용자가 명시적으로 요청하지 않는 한 아래 방식은 사용하지 않는다.

- 전체 workspace 전수 스캔
- sub-agent
- orchestrator
- 신규 scanner
- content hash matrix
- workspace 밖 임시 파일
- 과도한 자동화 스크립트

## 1. Scope

### 1.1 기본 작업 디렉터리

`C:\Workspaces\port-marketconnector`

### 1.2 프로젝트 역할

port-marketconnector는 한국투자증권 국내 주식 API와 PostgreSQL을 연결하는 Python 기반 MarketConnector 마이크로서비스다.

주요 책임은 아래와 같다.

| 영역 | 역할 |
| --- | --- |
| Token | KIS access token 발급 · 갱신 · 파일 관리 |
| Quote | 현재가 · 실시간 · 기간 시세 조회 |
| Balance | 계좌 잔고와 보유 종목 Snapshot |
| Order | 매수 · 매도 · 취소 · 정정 |
| Order Sync | 주문 이벤트와 체결 동기화 |
| Strategy Order | 승인된 전략 주문 제출 |
| Intraday | Snapshot Refresh와 hard stop 판단 |
| View API | port-view용 조회 API |
| Persistence | connector · execution · legacy DB 연동 |

### 1.3 기본 수정 대상

| 구분 | 대상 |
| --- | --- |
| Python | 루트 `*.py`와 package Python 파일 |
| Flask | `connector_app.py`와 route 관련 코드 |
| Script | `scripts` |
| 테스트 | `tests` 또는 `test_*.py` |
| 설정 | 환경변수 loader · config 관련 코드 |
| 문서 | `README.md` · `CHANGELOG.md` · `docs` |
| 의존성 | requirements · lock · build 관련 파일 |
| 운영 | EC2 · SSM 실행 wrapper와 관련 문서 |

현재 요청에 포함되지 않은 파일은 수정하지 않는다.

### 1.4 다른 마이크로서비스

아래 프로젝트는 외부 의존 모듈이다.

- `port-view`
- `port-interest-crawler`
- `port-interest-preprocessor`
- `port_strategy_common`
- `port_strategy_decision`
- `port_strategy_research`
- `port_strategy_execution`

현재 작업이 명시적으로 요구하지 않는 한 다른 MS의 코드와 문서는 수정하지 않는다.

`.kiro`의 cross-service spec도 port-marketconnector 작업 범위에 자동 포함하지 않는다.

## 2. 실행 위험 등급

이 저장소는 정적 확인만으로도 실제 token, 브로커 API, 주문과 DB 쓰기로 이어질 수 있다.

모든 Python entrypoint를 일반적인 로컬 도구처럼 실행하지 않는다.

### 2.1 최고 위험 entrypoint

| 파일 | 위험 |
| --- | --- |
| `connector_buy.py` | 실제 매수 주문 제출 가능 |
| `connector_sell.py` | 실제 매도 주문 제출 가능 |
| `connector_cancel.py` | 주문 취소 가능 |
| `connector_modify.py` | 주문 정정 가능 |
| `connector_strategy_order_execute.py` | `--execute` 사용 시 전략 주문 제출 가능 |
| `connector_app.py` | route 호출에 따라 주문 · 조회 · DB 쓰기 가능 |

위 파일은 사용자의 명시적 실행 요청과 대상 환경 확인 없이 실행하지 않는다.

### 2.2 외부 API와 DB 쓰기 위험

| 파일 | 위험 |
| --- | --- |
| `token_manager.py` | token 발급 · 갱신 · 파일 생성 · 삭제 |
| `connector_balance.py` | 잔고 API 호출과 Snapshot 저장 |
| `connector_order_check.py` | 주문 · 체결 조회와 DB 저장 |
| `connector_quote_realtime.py` | 시세 API 호출과 선택적 저장 |
| `connector_quote_closed.py` | 기간 시세 API 호출과 upsert |
| `connector_intraday_snapshot_refresh.py` | 장중 잔고 API 호출과 Snapshot 저장 |
| `connector_intraday_position_evaluate.py` | 점검 기록과 READY 매도 주문 row 생성 가능 |
| `scripts/run_connector_balance_daily.sh` | 잔고 갱신 entrypoint 실행 |

`--no-save`, dry run 또는 조회 목적 옵션이 있어도 token 발급과 외부 API 호출 가능성이 남을 수 있다.

옵션 이름만 보고 안전하다고 판단하지 않는다.

### 2.3 정적 작업 기본값

사용자가 실행을 명시하지 않은 경우 아래 작업만 수행한다.

- 파일 직접 읽기
- 안전한 텍스트 검색
- 코드 정적 분석
- 문서 수정
- 테스트 코드 작성
- 실행되지 않는 syntax와 import 검토
- 읽기 전용 Git 상태 확인

## 3. 브로커 주문 안전 기준

### 3.1 Paper와 live 구분

- paper와 live base URL, 계좌와 설정을 혼동하지 않는다.
- 환경이 명확하지 않으면 주문 관련 실행을 하지 않는다.
- aws-live 자동 BUY/SELL은 별도 승인과 cutover 범위다.
- paper라는 이유만으로 주문 실행을 일반 검증으로 취급하지 않는다.

### 3.2 Daily Step 12 주문

`connector_strategy_order_execute.py --execute`는 승인된 Paper 주문 구간에서만 실행하는 것을 전제로 한다.

| 항목 | 기준 |
| --- | --- |
| 대상 | `strategy_execution_order`의 실행 대상 주문 |
| 기본 동작 | dry run |
| 실제 제출 | 명시적 `--execute` |
| 승인 gate | `portfolio-paper-daily-step12-17-approval` |
| 후속 동기화 | `connector_order_check.py` |

승인 gate를 우회하는 기본값 변경, hidden fallback과 자동 `--execute` 추가를 금지한다.

### 3.3 Intraday hard stop

| 단계 | 책임 |
| --- | --- |
| Snapshot | `connector_intraday_snapshot_refresh.py` |
| 판단 | `connector_intraday_position_evaluate.py` |
| 주문 후보 | READY 매도 execution order 생성 |
| 알림 | event notifier Lambda 호출 가능 |
| 실제 주문 | 별도 승인 workflow 이후 처리 |

`connector_intraday_position_evaluate.py`는 판단과 주문 후보 생성을 담당하며 broker 매도 제출 책임을 갖지 않는다.

판단 entrypoint 안에 broker 주문 제출 로직을 추가하지 않는다.

실제 매도는 `portfolio-paper-intraday-stop-sell-approval` 승인 gate 이후에만 이어져야 한다.

## 4. Flask API 기준

`connector_app.py`는 실행 API와 View 조회 API를 함께 제공한다.

### 4.1 실행 API

| 경로 | 위험 |
| --- | --- |
| `/api/v1/quotes/realtime` | 외부 시세 API · DB 저장 가능 |
| `/api/v1/quotes/eod` | 외부 기간 시세 API · DB 저장 가능 |
| `/api/v1/accounts/balance` | 잔고 API · Snapshot 저장 |
| `/api/v1/orders/buy` | 매수 주문 |
| `/api/v1/orders/sell` | 매도 주문 |
| `/api/v1/orders/cancel` | 주문 취소 |
| `/api/v1/orders/modify` | 주문 정정 |
| `/api/v1/orders/history` | 주문 · 체결 조회와 DB 저장 |
| `/api/v1/price` | legacy 시세 alias |

route 이름이나 HTTP method를 명시 요청 없이 변경하지 않는다.

실행 API를 health check 또는 smoke test로 호출하지 않는다.

### 4.2 View API

View API는 조회 중심이지만 DB 연결과 데이터 노출 범위를 확인해야 한다.

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

View API 변경 시 port-view의 DTO, endpoint 계약과 하위 호환성을 확인한다.

## 5. Python 코드 작성 규칙

### 5.1 공통 원칙

- 기존 함수명, CLI option, API 경로와 DB 의미를 우선 유지한다.
- 브로커 요청 의미를 숨기는 wrapper를 추가하지 않는다.
- side effect가 있는 함수와 순수 조립 함수를 구분한다.
- module import만으로 token 발급, API 호출, 주문 또는 DB 쓰기가 일어나지 않게 한다.
- entrypoint 실행은 `if __name__ == "__main__":` 경계를 유지한다.
- 실패를 성공 또는 정상 무주문으로 변환하지 않는다.

### 5.2 Token

- token 값을 출력하거나 문서화하지 않는다.
- token 파일을 테스트 fixture로 읽지 않는다.
- token 발급과 갱신은 명시적인 함수 호출에서만 수행한다.
- import 시 token 생성이나 파일 삭제가 발생하지 않게 한다.
- 만료 감지와 재발급 실패를 성공으로 처리하지 않는다.

### 5.3 KIS API 요청

- timeout을 명시한다.
- HTTP status와 KIS 응답 코드를 모두 확인한다.
- API 실패 응답을 빈 정상 결과로 해석하지 않는다.
- 응답 필드가 없거나 형식이 다른 경우 명시적으로 실패한다.
- retry는 주문 제출과 조회 API를 동일하게 적용하지 않는다.
- 주문 요청은 자동 retry로 중복 제출하지 않는다.

### 5.4 주문 처리

- idempotency와 기존 order request 상태를 먼저 확인한다.
- 주문 전 DB 상태와 broker 응답을 분리해서 기록한다.
- broker 주문번호 전체를 로그와 문서에 노출하지 않는다.
- 부분 체결, 미체결, 거절과 취소 상태를 하나의 성공 상태로 합치지 않는다.
- 취소와 정정은 원주문 context를 확인한 뒤 수행한다.

주문 중복 제출 차단을 위해 아래 Claim 원칙을 지킨다.

- 동일 `strategy_execution_order`는 Broker 제출 전에 원자적 Claim을 획득한다.
- Claim 대상 상태는 `REQUESTED`이며 성공 시 `SUBMITTING`으로 전환한다.
- Claim 결과가 0행이면 중복 또는 선점된 주문으로 판단하고 Broker를 호출하지 않는다.
- Claim DB 오류가 발생하면 Broker를 호출하지 않는다.
- Claim 실패를 주문 제출 성공으로 변환하지 않는다.
- 한 주문의 Claim 또는 상태 기록 실패가 후속 주문 Batch 전체를 비정상 종료시키지 않도록 단위 실패로 격리한다.
- 실제 Broker 호출 횟수는 성공한 Claim당 최대 1회여야 한다.
- 자동 retry로 동일 Execution Order를 재제출하지 않는다.

### 5.4.1 수량 검증

- BUY와 SELL 수량은 Broker 호출 전에 공통 정규화 함수를 거친다.
- 허용 수량은 1 이상의 정수다.
- 0과 음수는 거부한다.
- 정수와 정확히 같은 숫자 문자열 또는 Decimal은 정수로 변환할 수 있다.
- 소수 수량은 절사하거나 반올림하지 않고 거부한다.
- Boolean은 정수처럼 취급하지 않고 거부한다.
- 숫자로 해석할 수 없는 값은 거부한다.
- 정규화된 수량은 Broker 호출까지 값 변경 없이 전달한다.
- 수량 검증 실패 시 Broker 호출, token 호출과 운영 DB 후속 처리를 수행하지 않는다.

비상 최대 수량 차단 기준은 아래와 같다.

- `STEP12_FATAL_MAX_ORDER_QTY`는 주문 직전 비상 최대 수량 차단 설정이다.
- 미설정 또는 0이면 검사를 비활성화한다.
- 양의 정수이면 해당 수량을 초과하는 주문을 차단한다.
- 초과 수량을 최대값으로 자동 축소하지 않는다.
- 음수, 소수, 비숫자 설정은 명시적 구성 오류로 처리한다.
- 일반 수량 검증을 Fatal Max 검사보다 먼저 수행한다.
- `connector_order_common.py`의 순수 검증 함수 정의와 실제 주문 제출 경계의 적용 책임을 구분한다.
- 공통 함수가 존재한다는 이유만으로 모든 주문 경로에 자동 적용됐다고 가정하지 않는다.

### 5.4.2 취소·정정 Payload 계약

| 항목 | 기준 |
| --- | --- |
| 전량 취소·정정 | `ORD_QTY=0` · `QTY_ALL_ORD_YN=Y` |
| 부분 취소·정정 | 요청 수량 · `QTY_ALL_ORD_YN=N` |
| 수량 검증 | Broker 호출 전 순수 resolver에서 검증 |
| 오류 처리 | Payload를 만들지 않고 Broker를 호출하지 않음 |

- 전량 취소에 기존 주문수량을 전달하지 않는다.
- 부분 취소 수량은 1 이상의 유효한 정수여야 한다.
- 전량·부분 분기 계약을 Wrapper와 공통 함수에서 다르게 해석하지 않는다.

### 5.4.3 Terminal 상태 단조성

Terminal 상태는 `SUBMITTED`, `FAILED`, `CANCELED`다.

- Terminal 상태에서 비Terminal 또는 다른 Terminal 상태로 임의 역행하지 않는다.
- 상태 UPDATE는 허용된 이전 상태를 조건으로 수행한다.
- UPDATE 결과가 0행이면 성공으로 간주하지 않는다.
- Broker 제출 성공 후 `SUBMITTED` 상태 반영이 실패하면 주문 성공 로그를 남기지 않는다.
- 이 경우 `SELL_ORDERED` 같은 후속 상태도 기록하지 않는다.
- Broker 성공 후 DB 상태 동기화 실패를 `FAILED`로 덮어써 Broker 결과를 왜곡하지 않는다.
- `SUBMITTED_STATE_SYNC_FAILED`와 같은 명시적 운영 오류로 남기고 Fail-closed 처리한다.
- 상태 기록 중 추가 오류가 발생해도 Batch의 나머지 주문 처리를 계속할 수 있도록 오류를 격리한다.

### 5.5 주문 · 체결 동기화

`connector_order_check.py`의 direct 조회 우선 의미를 유지한다.

direct 응답의 상세 내역이 비어도 summary가 있으면 broad search보다 direct fallback을 먼저 처리한다.

broad summary를 특정 주문의 event 또는 fill로 잘못 귀속하지 않는다.

### 5.6 Balance와 Position Snapshot

- balance snapshot과 position snapshot의 기준 시각을 함께 보존한다.
- 동일 계좌와 기준일의 stale position 정리 범위를 확인한다.
- 빈 보유 목록이 정상 청산인지 API 이상인지 구분한다.
- OPEN 전략 포지션이 있는데 KIS 보유 결과가 비면 mismatch로 처리한다.
- `view_app` 같은 조회 전용 DB user로 Connector 쓰기를 수행하지 않는다.

### 5.7 Quote

- `--no-save`는 DB 저장만 차단할 수 있음을 전제로 한다.
- 외부 API 호출까지 차단한다고 문서화하지 않는다.
- 거래일, 시간대와 종목 코드를 검증한다.
- EOD upsert key와 realtime 저장 의미를 변경하지 않는다.

## 6. Database 기준

### 6.1 연결

| 항목 | 값 |
| --- | --- |
| Database | `portfolio` |
| 기본 설정 loader | `db_config.py`의 `get_db_config()` |
| 환경변수 | `INTEREST_DB_*` |
| Password | 기본값 없음 |
| search path | `connector, execution, legacy, reference, public` |

실제 환경에서는 전용 Connector DB user를 사용한다.

`postgres` 기본값이 코드나 문서에 남아 있더라도 운영 권한 기준으로 해석하지 않는다.

### 6.2 주요 schema 책임

| Schema | 역할 |
| --- | --- |
| `connector` | 계좌 · Snapshot · 시세 · 주문 · 체결 |
| `execution` | 전략 주문 · position state · intraday check |
| `legacy` | 과거 호환 테이블 |
| `reference` | 종목과 공통 기준정보 |
| `public` | fallback search path |

### 6.3 주요 쓰기 대상

- `connector.connector_api_call_log`
- `connector.connector_balance_snapshot`
- `connector.connector_position_snapshot`
- `connector.connector_quote_realtime`
- `connector.connector_quote_eod`
- `connector.connector_order_request`
- `connector.connector_order_event`
- `connector.connector_fill`
- `execution.strategy_execution_order`
- `execution.strategy_intraday_position_check`
- legacy 호환 테이블

실제 schema 이름은 코드와 DB 계약을 확인하고 사용한다.

추정 컬럼명과 추정 table name으로 SQL을 작성하지 않는다.

### 6.4 SQL과 transaction

- 신규 SQL은 가능한 한 schema-qualified 이름을 사용한다.
- 기존 unqualified SQL은 connection `search_path`와 정합성을 확인한다.
- SQL 수정 전 실제 코드, migration 또는 `information_schema.columns`로 컬럼을 확인한다.
- 주문과 체결 관련 쓰기는 transaction 경계를 확인한다.
- commit 전후 예외 처리와 rollback 의미를 유지한다.
- 실패한 DB 작업 뒤에 성공 marker를 출력하지 않는다.

## 7. 설정과 민감정보

### 7.1 민감정보

아래 값은 코드, 문서, 예시, 로그에 원문으로 기록하지 않는다.

- access token
- refresh 또는 token 관련 파일 내용
- KIS app key · app secret
- 계좌번호와 상품 코드
- DB password와 전체 connection string
- Slack webhook URL
- AWS account-id
- 실제 ARN
- public IP와 RDS hostname
- broker 주문번호 전체
- command id
- image digest full SHA256

필요한 경우 아래 placeholder를 사용한다.

| Placeholder | 용도 |
| --- | --- |
| `[REDACTED]` | 일반 민감정보 |
| `[REDACTED_ACCOUNT_NO]` | 계좌번호 |
| `[REDACTED_BROKER_ORDER_NO]` | broker 주문번호 |
| `[REDACTED_ARN]` | ARN |
| `[REDACTED_SECRET_ARN]` | secret ARN |
| `[REDACTED_PUBLIC_IP]` | public IP |
| `[REDACTED_RDS_HOST]` | RDS hostname |
| `[REDACTED_COMMAND_ID]` | SSM command id |

### 7.2 설정 변경

설정 key나 loader를 변경하면 아래를 함께 확인한다.

1. `config.py`
2. `db_config.py`
3. 해당 entrypoint
4. Flask route
5. README
6. EC2 · SSM 환경변수 주입
7. token과 secret 보관 위치

실제 `config.py`, token 파일과 local secret 파일의 값을 읽어 문서에 옮기지 않는다.

## 8. EC2 · SSM 운영 기준

MarketConnector 운영 경로는 EC2와 SSM RunCommand를 기준으로 한다.

### 8.1 현재 운영 구조

| 항목 | 값 |
| --- | --- |
| Compute | MarketConnector EC2 |
| 원격 실행 | SSM RunCommand |
| Daily order | Step Functions 승인 후 실행 |
| Intraday | Scheduler → SSM |
| 알림 | event notifier Lambda |
| EC2 lifecycle | Scheduler start · stop |

IAM Role, policy와 Scheduler 이름은 운영 식별 정보이며 변경 시 cross-service 문서와 정합성을 확인한다.

### 8.2 운영 명령 작성

- 동적 식별자는 `list/describe → 변수 추출 → 후속 검증` 흐름을 사용한다.
- 실제 instance id, ARN, command id와 account-id를 문서에 남기지 않는다.
- multiline SSM command는 UTF-8 No BOM 파일과 `--parameters file://...` 방식을 사용한다.
- Windows와 Linux shell 문법을 혼합하지 않는다.
- stdout에 token, 계좌번호와 broker 번호가 포함되지 않게 한다.
- SSM status와 프로세스 exit code를 함께 확인한다.

실제 SSM RunCommand는 사용자 명시 요청 없이 발행하지 않는다.

### 8.3 DevOps Artifact 기준

- 배포 Artifact는 Git SHA 기반 Versioned ZIP이다.
- Bundle Source는 committed Git blob이다.
- Working Tree 파일을 직접 ZIP으로 묶지 않는다.
- Detached Head에서도 resolved Source SHA를 기준으로 한다.
- Bundle 포함 목록은 `.devops/bundle/include.txt`에서 관리한다.
- Bundle 생성기는 `.devops/scripts/build_bundle.py`다.
- 신규 운영 파일은 include manifest와 contract test를 함께 갱신한다.
- token, secret, cache, runtime 파일과 build output은 Bundle에 넣지 않는다.
- Local Bundle과 S3 Artifact의 SHA-256 정합성을 검증한다.

### 8.4 CodeDeploy 안전 기준

- EC2 In-place 배포는 CodeDeploy Lifecycle Hook을 통해서만 수행한다.
- 운영 Application 경로를 수동 복사로 덮어쓰지 않는다.
- 배포 전 기존 Source Backup을 생성한다.
- 배포 중 Deployment Lock을 유지한다.
- Connector 실행 프로세스가 존재하면 안전 기준에 따라 배포를 차단한다.
- 중복 Connector 프로세스를 허용하지 않는다.
- ApplicationStart에서 Connector를 자동 실행하지 않는다.
- ValidateService 성공 전 Deployment Lock을 해제하지 않는다.
- 실패 시 신규 Application 프로세스를 기동하지 않는다.
- 배포 검증을 위해 Flask 실행 API나 주문 entrypoint를 호출하지 않는다.

### 8.5 Runtime과 Secret 보호 기준

- `config.py`에는 KIS App Key, Secret과 계좌번호를 하드코딩하지 않는다.
- Runtime 환경은 환경변수와 AWS Secret 주입 구조를 사용한다.
- Runtime token 파일은 Bundle에 포함하지 않는다.
- 배포, Rollback과 재배포 중 기존 token 파일을 보존한다.
- `config.py`는 배포 후 권한 600을 유지한다.
- Secret 값은 Build, Hook, SSM과 검증 출력에 기록하지 않는다.
- Secret Rotation은 별도 명시적 작업 범위이며 이번 완료 기준에 포함하지 않는다.

### 8.6 Wrapper 기준

- Daily Wrapper는 `scripts/run_connector_balance_daily.sh`다.
- Intraday Wrapper는 `scripts/run_intraday_snapshot_and_evaluate.sh`다.
- Intraday 순서는 Snapshot Refresh → Position Evaluate다.
- Wrapper는 Strict Shell 설정을 유지한다.
- Shell 파일은 LF로 관리한다.
- 배포 검증에서는 `bash -n`과 구조 검증을 우선한다.
- 명시적 운영 실행 요청 없이 Wrapper를 실제 실행하지 않는다.

### 8.7 Rollback 기준

- 직전 성공 상태는 배포 전 Backup과 S3 Versioned Artifact로 식별한다.
- Backup Rollback은 Deployment Lock과 프로세스 0개 상태에서 수행한다.
- 복원 후 주요 Source SHA-256, Python Compile과 Wrapper Syntax를 확인한다.
- 검증된 동일 S3 Revision을 재배포할 수 있어야 한다.
- Rollback과 재배포 중 token, secret과 runtime 파일을 보존한다.
- Rollback 검증에서도 주문 API를 호출하지 않는다.

### 8.8 안전한 검증 범위

허용:

- Python Compile
- Ruff
- Mock 기반 Pytest
- Bundle Contract Test
- ZIP 구조 검사
- Shell Syntax 검사
- 파일 존재와 권한 확인
- SHA-256 정합성 확인
- CodeDeploy Hook 상태 확인
- Connector 프로세스 수 확인
- Deployment Lock 상태 확인

명시적 승인 없이 금지:

- Flask 주문 API 호출
- BUY, SELL, 취소와 정정 실행
- `connector_strategy_order_execute.py --execute`
- Daily Balance Wrapper 실제 운영 실행
- Intraday Wrapper 실제 운영 실행
- token 신규 발급과 강제 Rotation
- 운영 DB 쓰기를 동반하는 Smoke Test

### 8.9 main Push 자동 Release 기준

main Push는 GitHub Actions Release Trigger이며 `workflow_dispatch` 수동 실행 경로도 유지한다.

전체 자동 흐름은 아래와 같다.

| 단계 | 내용 |
| --- | --- |
| Trigger | main Push 또는 수동 실행 |
| Build | CodeBuild Quality Gate와 Git SHA Versioned ZIP |
| Artifact | 전용 Private Versioned S3 · S3 Version ID 고정 |
| Compute 준비 | EC2 상태 확인 · SSM Online 확인 |
| 배포 | S3 Versioned Revision 기반 CodeDeploy In-place |

EC2 상태 보존 원칙은 아래와 같다.

- 배포 전 EC2 상태를 확인한다.
- stopped 상태이면 Release를 위해 일시적으로 시작할 수 있다.
- 이미 running이면 기존 상태를 그대로 유지한다.
- Workflow가 직접 시작한 EC2만 Release 종료 후 원래 stopped 상태로 복원한다.
- 이 lifecycle은 배포용 Compute 준비이며 Connector Application 실행이나 주문 실행을 의미하지 않는다.

No-Order Release Boundary를 유지한다.

- ApplicationStart에서 Connector를 자동 실행하지 않는 기존 원칙을 유지한다.
- Release Workflow에서 SSM SendCommand로 주문을 실행하지 않는다.
- BUY · SELL · CANCEL · MODIFY entrypoint와 `--execute`를 Release smoke로 실행하지 않는다.
- 실제 Broker 주문 API 호출 없이 배포를 검증한다.
- 기존 SSM · Scheduler 기반 Runtime 실행 책임은 유지한다.

OIDC · IAM Action 목록과 AWS 정책 구현 상세는 port-devops 범위이며 이 문서에 기록하지 않는다.

## 9. 실행 제한

사용자가 명시적으로 요청하지 않는 한 아래 작업을 수행하지 않는다.

| 구분 | 금지 작업 |
| --- | --- |
| Flask | connector server 실행 |
| Token | 발급 · 갱신 · 삭제 · 출력 |
| KIS | 시세 · 잔고 · 주문 · 체결 API 호출 |
| Broker | 매수 · 매도 · 취소 · 정정 |
| DB | DDL · DML · migration · psql |
| AWS | EC2 · SSM · Lambda · Scheduler 실행 또는 변경 |
| Slack | webhook 또는 notifier 실제 호출 |
| 운영 | Daily · Intraday entrypoint 실행 |
| Git | add · commit · push · reset · restore |

읽기 전용 검증도 사용자의 요청 범위에서만 수행한다.

## 10. 테스트와 검증

### 10.1 기본 원칙

- 실제 KIS와 DB 없이 가능한 unit test를 우선한다.
- `requests`, token loader, DB connection과 Lambda 호출은 mock 또는 stub으로 격리한다.
- 주문 테스트는 실제 base URL과 계좌를 사용하지 않는다.
- 테스트 중 token 파일을 생성하거나 삭제하지 않는다.
- 실제 운영 환경변수를 테스트에 주입하지 않는다.

### 10.2 변경별 최소 검증

| 변경 대상 | 최소 검증 |
| --- | --- |
| Python 문법 | `py_compile` 또는 안전한 compile check |
| 순수 함수 | 관련 unit test |
| Flask route | test client + 외부 의존 mock |
| KIS client | request · response parsing mock |
| 주문 로직 | dry-run · idempotency · 원자적 Claim · 중복 제출 차단 test |
| 주문 수량 | 수량 Property Test · Fatal Max 경계 Test |
| 취소·정정 | 전량 0/Y · 부분 수량/N Payload Contract Test |
| 주문 상태 | Terminal 상태 단조성 Test |
| 주문 회귀 | Broker 성공 후 상태 동기화 실패 · Claim과 상태 기록 이중 실패 회귀 Test |
| 호출 횟수 | Broker·token·운영 DB 호출 0건과 성공 Claim당 Broker Mock 1회 검증 |
| Repository | SQL · parameter · transaction test |
| Token | 파일과 HTTP mock |
| 문서 | 링크 · 사실 · 가독성 |
| Shell | syntax와 인자 전달 정적 확인 |

주문 경계 안전 기능에서 Property Test는 선택 사항이 아니라 필수 검증이다.

실행 위험 import가 있는 파일은 compile 과정에서도 side effect 여부를 먼저 확인한다.

실행하지 못한 검증은 완료로 기록하지 않고 사유를 남긴다.

## 11. 문서 관리 규칙

### 11.1 README.md

README는 port-marketconnector의 현재 구조와 운영 AS-IS를 설명한다.

README에 포함할 내용:

- 프로젝트 역할
- 실행 위험
- Flask API
- 주요 entrypoint
- Token · Quote · Balance · Order 흐름
- Daily와 Intraday 운영 경계
- DB와 schema
- EC2 · SSM 구조
- 설정과 보안
- 상세 문서 링크

다른 MS의 내부 구현과 Step Functions 전체 정의를 장문으로 복사하지 않는다.

### 11.2 CHANGELOG.md

CHANGELOG는 port-marketconnector 코드와 문서의 주요 변경만 기록한다.

- 최신 날짜를 상단에 추가한다.
- `Added`, `Changed`, `Fixed`, `Removed`, `Security`를 필요에 따라 사용한다.
- 실제 Connector 변경만 기록한다.
- 일회성 command id, execution id와 raw log를 기록하지 않는다.
- 신규 섹션부터 2컬럼 표 중심으로 작성한다.
- 과거 이력은 별도 요청이 없으면 원본을 유지한다.

### 11.3 docs

상세 설명은 현재 유지 중인 `docs` 문서로 분리한다.

새 문서를 만들기 전에 기존 README, CHANGELOG와 docs에 흡수 가능한지 먼저 확인한다.

날짜별 `docs/worklog/*.md`는 신규 생성하지 않는다.

코드와 문서 변경 이력은 `CHANGELOG.md`에 기록한다.

### 11.4 source-file-catalog.md 자동 갱신

`docs/source-file-catalog.md`가 존재하는 경우 아래 변경이 발생하면 같은 작업에서 갱신 여부를 반드시 확인한다.

| 변경 | 처리 |
| --- | --- |
| 주요 Python 파일 생성 · 삭제 · 이름 변경 | 카탈로그 갱신 |
| Flask route와 entrypoint 책임 변경 | 역할과 위험 갱신 |
| package · scripts 디렉터리 변경 | 경로와 구조 갱신 |
| 설정 · token · DB loader 역할 변경 | 설정 항목 갱신 |
| EC2 · SSM wrapper 역할 변경 | 운영 항목 갱신 |
| 문서 생성 · 삭제 · 역할 변경 | Documents 항목 갱신 |
| 내부 구현만 변경 · 책임 동일 | 생략 가능 |

카탈로그는 전체 파일 inventory가 아니다.

운영과 유지보수에 의미 있는 파일, 묶음 경로와 책임만 기록한다.

## 12. Git 규칙

기본적으로 읽기 전용 상태 확인만 허용한다.

| 허용 | 금지 |
| --- | --- |
| `git status --short`<br>`git diff --stat`<br>`git diff --check` | `git add`<br>`git commit`<br>`git push`<br>`git reset`<br>`git restore`<br>`git checkout`<br>`git stash` |

사용자가 명시적으로 요청하지 않는 한 commit을 생성하지 않는다.

## 13. 완료 보고

작업 완료 시 아래만 짧게 보고한다.

| 항목 | 내용 |
| --- | --- |
| 변경 파일 | 실제 수정한 파일 |
| 핵심 변경 | 기능 또는 문서 변경 요약 |
| 위험 경로 | 실행하지 않은 API · 주문 · DB 경로 |
| 검증 | 수행한 정적 검사와 test |
| 미수행 | 실행하지 못한 검증 |
| 보안 | 민감정보 원문 기록 여부 |
| 후속 | 실제로 남은 항목만 기록 |

운영자가 수행한 작업과 Kiro가 수행한 작업을 구분한다.

## 14. 완료 체크리스트

- [ ] 요청된 port-marketconnector 파일만 수정했는가?
- [ ] 다른 MS와 `.kiro` 파일을 불필요하게 수정하지 않았는가?
- [ ] 최우선 문서 가독성 규칙을 적용했는가?
- [ ] 신규 독립 표를 기본 2컬럼으로 작성했는가?
- [ ] 긴 셀과 긴 라인을 만들지 않았는가?
- [ ] token · KIS · broker · DB side effect를 구분했는가?
- [ ] paper와 live 환경을 혼동하지 않았는가?
- [ ] Daily Step 12 approval gate를 우회하지 않았는가?
- [ ] Intraday 판단과 실제 매도 제출 책임을 분리했는가?
- [ ] Flask 실행 API와 View API를 구분했는가?
- [ ] 주문 retry로 중복 주문 가능성을 만들지 않았는가?
- [ ] DB schema와 transaction 정합을 확인했는가?
- [ ] 민감정보 원문을 기록하지 않았는가?
- [ ] 실패를 정상 또는 무주문 성공으로 기록하지 않았는가?
- [ ] 변경 범위에 맞는 안전한 검증을 수행했는가?
- [ ] 파일 구조나 책임이 바뀌면 `docs/source-file-catalog.md`를 확인했는가?
- [ ] 날짜별 `docs/worklog/*.md`를 새로 만들지 않았는가?
- [ ] UTF-8 No BOM으로 저장했는가?
- [ ] 실제 수정 내용만 README와 CHANGELOG에 반영했는가?
