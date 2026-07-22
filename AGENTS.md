# AGENTS.md - port-marketconnector

## 프로젝트
- 이 프로젝트는 한국투자증권(KIS) 국내 주식 API 연동용 Python Connector 마이크로서비스다.
- Flask API로 시세, 잔고 스냅샷, 매수/매도 주문, 주문 취소/정정, 주문/체결 조회, View용 조회 API를 제공한다.
- CLI 실행 파일도 포함되어 있으며, 일부 파일은 브로커 API 호출, access token 발급/갱신, 주문 제출, 잔고/보유/체결 조회, DB 저장을 수행할 수 있다.
- 기존 API 경로, 함수명, 테이블명, 설정 key, 브로커 요청 의미는 명시 요청 없이 변경하지 않는다.

## 작업 범위
- 현재 `port-marketconnector` 디렉터리 안에서만 작업한다.
- 현재 루트 밖 파일은 수정하지 않는다.
- 변경 전에는 읽기 전용 정적 분석을 우선한다.
- 변경은 요청받은 파일에 한정하고 작고 검증 가능한 단위로 진행한다.

## 허용 작업
- README, CHANGELOG, worklog 등 문서 작성 및 수정
- 파일 구조 정적 분석
- 애플리케이션 코드를 실행하지 않는 소스 정적 확인
- `rg` 같은 안전한 텍스트 검색
- `git status --short`, `git diff --stat` 같은 Git 상태 확인

## 명시 요청과 확인 없이는 금지
- Flask app 또는 connector server 실행
- KIS 또는 브로커 API 호출
- access token 발급, 갱신, 삭제, 출력
- 잔고, 보유 종목, 현재가, 실시간 시세, 기간 시세, 주문 내역, 체결 동기화 스크립트 실행
- 매수, 매도, 주문 취소, 주문 정정 실행
- DB DDL/DML 실행
- 크롤러 또는 외부 API 호출
- 민감정보 값 출력 또는 문서 기록
- `access_token.txt`, token 파일, app key, app secret, 계좌번호, DB 비밀번호, 전체 DB 연결 문자열 읽기 또는 인용

## 민감정보 취급
- 아래 항목은 민감정보 후보로 취급한다.
  - `access_token.txt` 및 모든 token 파일
  - KIS app key, app secret
  - 계좌번호, 계좌 상품 코드
  - DB host, port, database, user, password, connection string
  - 작업에 꼭 필요하지 않은 브로커 주문번호
- 문서에 값이 필요하면 `[REDACTED]`로 표시한다.
- 원본 설정값을 채팅, 문서, 로그, 예시에 붙여 넣지 않는다.

## 실행 위험 entrypoint
- `token_manager.py`: token 파일 읽기/쓰기/삭제, token 발급/갱신 가능
- `connector_app.py`: 메인 Flask connector API 실행, 브로커 API 호출 및 DB 저장 route 포함
- `connector_buy.py`: 매수 주문 제출 가능
- `connector_sell.py`: 매도 주문 제출 가능
- `connector_cancel.py`: 주문 취소 요청 가능
- `connector_modify.py`: 주문 정정 요청 가능
- `connector_balance.py`: 잔고/보유 조회 및 잔고/포지션 snapshot 저장 가능
- `connector_order_check.py`: 주문/체결 조회 및 주문 이벤트/체결 저장 가능
- `connector_quote_realtime.py`: 실시간/현재가 조회 가능
- `connector_quote_closed.py`: 기간 시세 조회 가능

## DB 쓰기 위험
- `connector_db.py`는 connector 및 legacy 테이블에 쓰는 repository helper를 포함한다.
- `connector_order_request`, `connector_order_event`, `connector_fill`, `connector_balance_snapshot`, `connector_position_snapshot`에 쓰기 가능한 파일은 실행 위험 파일로 취급한다.
- insert, update, upsert, delete, commit helper를 호출하는 코드 경로는 실행하지 않는다.

## 문서화 규칙
- README.md는 프로젝트 이해, 실행 방법, 설정 방법, 주요 기능, 폴더 구조, 외부 의존성이 바뀐 경우에만 갱신한다.
- CHANGELOG.md에는 실제 변경된 주요 내용만 짧게 기록한다.
- docs/worklog/YYYY-MM-DD.md에는 작업 내용을 계획/완료 처리 형식으로 정리한다.
- worklog 작성 시 아래 들여쓰기 형식을 유지한다.
  - ` 1) 큰 작업 명` (앞 공백 1칸)
  - `   (1) 세부 작업` (앞 공백 3칸)
  - `       - 추가 설명` (앞 공백 7칸)
- `1.` 형식은 사용하지 않는다. markdown 자동 번호 목록으로 렌더링되어 들여쓰기와 상태 표기가 깨질 수 있다.
- 탭 문자를 섞지 않는다. 공백만 사용한다.
- 완료/예정/보류/실패/확인 상태를 작업 명 끝에 `:완료`, `:예정`, `:보류`, `:실패`, `:확인`으로 명확히 표시한다.
- 상태값은 `작업 명:완료`처럼 작업 명과 콜론 사이에 공백을 두지 않는다.
- 이미 작성된 과거 날짜 worklog의 기존 형식은 유지하고, 신규 날짜 worklog부터 위 규칙을 적용한다.

## Git 규칙
- 작업 후 `git status --short`를 확인한다.
- 작업 후 `git diff --stat`를 확인한다.
- 명시 요청 없이 commit하지 않는다.

## 검증
문서만 수정한 경우:

```powershell
git status --short
git diff --stat
```

문서 작업 검증을 위해 Flask, KIS, 브로커, token, 주문, 잔고, 시세, 체결, 크롤러, DB 명령을 실행하지 않는다.

## 완료 보고
작업 완료 후 아래 항목만 짧게 보고한다.

- 변경 파일
- 변경 요약
- 검증 결과
- 남은 위험 또는 후속 작업
