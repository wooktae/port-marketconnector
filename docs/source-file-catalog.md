# Source File Catalog

이 문서는 `port-marketconnector` repository의 주요 소스/문서 파일을 AWS Migration 전 정리 관점에서 요약한 것이다. 정적 파일 분석 기준이며, Flask app, 브로커 API, token 발급/갱신, 주문/잔고/시세/체결 조회, DB 명령은 실행하지 않았다.

## Python 소스

| 파일 경로 | 한글 제목 | 파일 내용 | 주요 역할 | 수정/운영 시 주의사항 |
|---|---|---|---|---|
| `connector_app.py` | Flask API 진입점 | 시세, 잔고, 주문, 주문/체결 조회 실행 API와 View 조회 API route를 정의한다. | 외부 요청을 각 connector 함수와 view service로 연결한다. | 실행 시 route에 따라 브로커 API 호출, token 처리, DB 쓰기가 발생할 수 있다. URL과 endpoint 의미를 변경하지 않는다. |
| `connector_balance.py` | 잔고/보유 snapshot 동기화 | KIS 잔고 API 응답을 파싱해 계좌 요약과 보유 종목 snapshot을 저장한다. | `connector_balance_snapshot`, `connector_position_snapshot`, legacy balance/holding 저장 흐름을 담당한다. | 실행 시 외부 API 호출, token 갱신, DB 쓰기가 발생할 수 있다. stale position 삭제 후 재저장 흐름을 변경하지 않는다. |
| `connector_buy.py` | 매수 주문 wrapper | 매수 입력값을 공통 주문 제출 함수로 전달하는 CLI/API helper다. | 매수 TR_ID와 요청 유형을 `connector_order_common.py`에 위임한다. | 실행 시 실제 주문 제출 가능성이 있다. 주문 함수 signature와 TR_ID 의미를 임의 변경하지 않는다. |
| `connector_cancel.py` | 주문 취소 wrapper | 원 주문 요청 ID를 기준으로 취소 요청을 제출한다. | 취소 구분 코드와 주문 context를 공통 취소/정정 흐름에 전달한다. | 실행 시 주문 취소 API 호출과 DB 상태 갱신이 발생할 수 있다. 브로커 취소 코드 확인 없이 변경하지 않는다. |
| `connector_db.py` | DB repository helper | connector 및 legacy 테이블의 저장, 갱신, 조회 helper를 모은다. | API call log, balance, position, quote, order request, event, fill, View 조회를 담당한다. | 호출 시 실제 DB 접근이 발생한다. 테이블명, column 의미, transaction 흐름, `search_path` 전제를 유지한다. |
| `connector_modify.py` | 주문 정정 wrapper | 원 주문 요청 ID를 기준으로 정정 요청을 제출한다. | 정정 구분 코드와 가격/수량 입력을 공통 취소/정정 흐름에 전달한다. | 실행 시 주문 정정 API 호출과 DB 상태 갱신이 발생할 수 있다. 브로커 정정 코드 확인 없이 변경하지 않는다. |
| `connector_order_check.py` | 주문/체결 동기화 | KIS 주문/체결 조회 결과를 order event, fill, legacy 주문 데이터로 저장한다. | direct 조회, broad search fallback, summary fallback, broker 주문번호 매핑을 담당한다. | 실행 시 외부 API 호출과 DB 쓰기가 발생한다. direct summary와 broad summary가 섞이지 않도록 fallback 순서를 유지한다. |
| `connector_order_common.py` | 주문 제출 공통 로직 | 매수/매도/취소/정정 요청의 DB 기록, 브로커 API 호출, 응답 반영을 처리한다. | order request 생성/갱신, strategy signal mapping, API call log 저장을 담당한다. | 실행 시 주문 제출/취소/정정과 DB 쓰기가 발생한다. request type, status, parent-child 주문 상태 보정 의미를 변경하지 않는다. |
| `connector_quote_closed.py` | 기간 시세 조회 | KIS 기간 시세 API를 호출하고 EOD quote를 upsert할 수 있다. | 일봉/기간 시세 파싱과 `connector_quote_eod` 저장을 담당한다. | `--no-save`는 DB 저장만 막으며 외부 API 호출은 남는다. 기간 파라미터와 upsert 의미를 유지한다. |
| `connector_quote_realtime.py` | 실시간 현재가 조회 | KIS 현재가 API를 호출하고 realtime quote를 저장할 수 있다. | 단일 종목 현재가 파싱과 `connector_quote_realtime` 저장을 담당한다. | `--no-save`는 DB 저장만 막으며 외부 API 호출은 남는다. 현재가 필드 매핑을 임의 변경하지 않는다. |
| `connector_sell.py` | 매도 주문 wrapper | 매도 입력값을 공통 주문 제출 함수로 전달하는 CLI/API helper다. | 매도 TR_ID와 요청 유형을 `connector_order_common.py`에 위임한다. | 실행 시 실제 주문 제출 가능성이 있다. 주문 함수 signature와 TR_ID 의미를 임의 변경하지 않는다. |
| `connector_view_service.py` | View API 조회 조립 | DB 조회 결과를 JSON 응답 형태로 직렬화하고 주문 timeline/tree를 구성한다. | dashboard, balance, positions, orders, events, quotes, strategy trades View 응답을 담당한다. | 브로커 API 호출은 없지만 DB 조회에 의존한다. View 응답 key와 timeline 구성 의미를 유지한다. |
| `db_config.py` | DB 설정 helper | PostgreSQL 접속 설정을 환경변수에서 읽어 dict로 반환한다. | `connector_db.py`의 공통 DB connection 설정 공급자다. | 민감정보 값은 환경변수/로컬 설정에서 주입한다. password 값을 문서나 로그에 기록하지 않는다. |
| `token_manager.py` | Token 관리 helper | access token 파일 읽기/쓰기, 신규 token 발급, 만료 감지 후 갱신을 처리한다. | KIS API 호출 전 token 확보와 갱신을 담당한다. | 실행 시 token 파일 생성/삭제/출력과 외부 token endpoint 호출이 발생할 수 있다. token 파일과 token 값은 읽거나 인용하지 않는다. |
| `config.py` | KIS 로컬 설정 | KIS app key, secret, base URL, 계좌 관련 설정을 제공하는 로컬 설정 파일이다. | 브로커 API 호출 모듈에 인증/계좌 설정을 공급한다. | 민감정보 후보 파일이다. 실제 값은 문서화하지 않고 환경변수/로컬 설정에서 주입되는 값으로만 취급한다. |

## 문서 파일

| 파일 경로 | 한글 제목 | 파일 내용 | 주요 역할 | 수정/운영 시 주의사항 |
|---|---|---|---|---|
| `AGENTS.md` | 작업 규칙 문서 | 작업 범위, 금지 실행, 민감정보 취급, 검증 규칙을 정의한다. | Codex/agent가 이 repository에서 따라야 할 안전 규칙을 제공한다. | 브로커/API/DB/token 실행 금지와 민감정보 마스킹 원칙을 약화하지 않는다. |
| `README.md` | 프로젝트 설명서 | 프로젝트 개요, 파일 구조, Flask API, 설정/실행 주의사항을 설명한다. | 운영자와 개발자가 connector 구조와 위험 entrypoint를 이해하도록 돕는다. | 실행 방법 변경, 구조 변경, 외부 의존성 변경이 있을 때만 갱신한다. 민감정보 값은 `[REDACTED]`로만 표현한다. |
| `CHANGELOG.md` | 변경 이력 | 날짜별 실제 변경 사항과 실행하지 않은 위험 작업을 기록한다. | migration 전 변경 맥락과 기능 변경 여부를 추적한다. | 기능 변경이 없으면 “기능 변경 없음”을 명시한다. 실제 수행하지 않은 작업을 기록하지 않는다. |
| `docs/source-file-catalog.md` | 전체 파일 카탈로그 | repository 주요 파일의 역할과 운영 주의사항을 표로 정리한다. | AWS Migration 전 모듈 정리와 인수인계용 파일 목록 역할을 한다. | 신규/삭제/역할 변경 파일이 생기면 함께 갱신한다. 민감정보 값은 포함하지 않는다. |
| `docs/worklog/2026-05-26.md` | 2026-05-26 작업 일지 | 초기 문서화와 legacy 파일 정리 작업 내용을 기록한다. | 날짜별 작업 계획/완료 이력을 보존한다. | 기존 들여쓰기와 상태 표기 형식을 유지한다. |
| `docs/worklog/2026-05-27.md` | 2026-05-27 작업 일지 | DB 접속 환경 외부화와 schema-per-domain 문서화 내용을 기록한다. | 어제 미커밋 변경사항의 배경과 검증 범위를 설명한다. | 실제 DB 접속정보 값과 password를 기록하지 않는다. |
| `docs/worklog/2026-05-28.md` | 2026-05-28 작업 일지 | 미커밋 변경 확인, 파일 카탈로그 작성, 설명 주석 추가 작업을 기록한다. | 오늘 문서화/주석 작업의 계획/완료 이력을 보존한다. | 기능 로직 변경 없이 문서/주석 변경만 기록한다. |

## 정리 후보

현재 정적 분석 기준으로 삭제할 파일은 표시하지 않는다. 실행 위험이 큰 entrypoint는 정리 후보가 아니라 운영 주의 대상이며, 삭제 또는 rename 없이 보존한다.
