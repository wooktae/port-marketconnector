# Requirements Document

## Introduction

이 기능은 MarketConnector가 브로커(KIS) 주문 제출 경계에서만 최소한의 방어 로직을 추가하는 것을 목적으로 한다.

핵심 책임 원칙은 다음과 같다.

- MarketConnector는 Execution이 결정한 주문 계획을 다시 판단하지 않는다. 종목(ticker), 매매 방향(side), 수량(quantity), 주문 순서(order sequence), 주문 방식(order method)은 Execution의 결정을 신뢰한다.
- MarketConnector는 현금 재배분, 자동 수량 축소, 시장가 버퍼 재계산, 전략 우선순위 변경을 수행하지 않는다.
- MarketConnector는 일반적인 신규 주문 금액 상한을 도입하지 않는다.
- MarketConnector는 브로커 제출 경계에서 다음 4가지 치명적 사고만 차단한다: 동일 Execution Order의 중복 제출, 명백히 잘못된 수량, Broker payload 계약 위반, Terminal 상태의 역행.

이 문서는 위 원칙을 유지하면서 4개 영역(중복 제출 차단, 최소 수량 안전 검증, 전량/부분 취소 payload 계약, 로컬 상태 단조성)의 요구사항을 정의한다.

대상 파일은 `connector_strategy_order_execute.py`, `connector_order_common.py`, `connector_cancel.py`와 이들의 직접 관련 기존 테스트 파일로 제한한다.

## Glossary

- **MarketConnector**: 전략 주문을 브로커 API로 제출하고 상태를 기록하는 시스템. 이 문서에서 SHALL의 주체가 되는 대상 시스템.
- **Execution**: 주문 계획(종목, side, 수량, 순서, 주문 방식)을 결정하는 외부 계층. MarketConnector는 Execution의 결정을 신뢰한다.
- **Broker**: 한국투자증권(KIS) 국내 주식 주문 API. 실제 BUY/SELL/취소/정정 호출 대상.
- **Execution_Order**: `strategy_execution_order` 테이블의 단일 주문 행. `execution_status` 상태값을 가진다.
- **Claim**: 브로커 호출 직전에 `strategy_execution_order`를 조건부로 원자적으로 UPDATE하여 처리 권한을 확보하는 동작.
- **SUBMITTING_STATUS**: 값이 `"SUBMITTING"`인 상수. Claim 성공 후의 `execution_status` 값.
- **REQUESTED_STATUS**: 값이 `"REQUESTED"`인 상수. Claim 이전 대상 상태.
- **SUBMITTED_STATUS**: 값이 `"SUBMITTED"`인 상수. 브로커 제출 성공 후의 상태.
- **FAILED_STATUS**: 값이 `"FAILED"`인 상수. 처리 실패 후의 상태.
- **Terminal_Status**: 더 이상 되돌리면 안 되는 최종 상태. `SUBMITTED`, `FILLED`, `CANCELED`, `REJECTED`, `FAILED`를 포함한다.
- **Normalized_Quantity**: 양의 정수(1 이상)로 정규화된 주문 수량.
- **Fatal_Max_Order_Qty**: 환경변수 `STEP12_FATAL_MAX_ORDER_QTY`로 설정하는 데이터 손상 방지용 비상 상한. 전략 상한이 아니다.
- **Dry_Run**: `--execute`가 없는 실행 모드. 계획만 출력하고 Claim과 DB 상태 변경을 수행하지 않는다.
- **Cancel_Modify_Quantity_Resolver**: CANCEL/MODIFY 요청의 수량 분기를 계산하는 순수 함수.
- **Active_Order**: `submit_rvsecncl_order()`가 정정/취소 대상으로 해석한 현재 활성 주문.
- **Order_Request_Status_Updater**: `update_order_request_status_only()` 함수. 로컬 주문 요청 상태를 갱신한다.

## Requirements

### Requirement 1: 책임 경계 유지

**User Story:** 운영자로서, MarketConnector가 Execution의 주문 결정을 재판단하지 않기를 원한다. 그래야 전략 계층과 실행 계층의 책임 경계가 유지된다.

#### Acceptance Criteria

1. WHEN 특정 Execution_Order에 대해 브로커 주문을 호출하면, THE MarketConnector SHALL Execution이 결정한 종목, side, 수량, 주문 방식과 동일한 값을 브로커 요청에 사용한다.
2. WHEN 여러 Execution_Order를 처리하면, THE MarketConnector SHALL Execution이 결정한 주문 순서와 동일한 순서로 브로커 주문을 호출한다.
3. THE MarketConnector SHALL 현금 재배분, 자동 수량 축소, 시장가 버퍼 재계산, 전략 우선순위 변경을 수행하지 않는다.
3a. WHERE Execution이 결정한 주문이 특정 계좌의 가용 현금을 초과하면, THE MarketConnector SHALL 현금 재배분으로 주문을 실행 가능하게 만들지 않고, 해당 주문을 재배분 없이 그대로 처리하여 결과(브로커 거부 포함)를 기록한다.
4. THE MarketConnector SHALL 일반적인 신규 주문 금액 상한을 도입하지 않는다.
5. THE MarketConnector SHALL 기존 Retry Normalizer, Rate Limit 재시도, 주문 간 Sleep 동작을 변경하지 않고 유지한다.

### Requirement 2: 동일 Execution Order 중복 제출 차단

**User Story:** 운영자로서, 동일한 Execution Order가 브로커에 중복 제출되지 않기를 원한다. 그래야 동일 주문의 이중 체결 사고를 방지할 수 있다.

#### Acceptance Criteria

1. THE MarketConnector SHALL `SUBMITTING_STATUS = "SUBMITTING"` 상수를 정의하고, Claim 대상 조회와 상태 전이에서 이 상수를 사용한다.
2. WHEN 특정 Execution_Order에 대해 브로커 주문을 제출하기 직전이면, THE MarketConnector SHALL 브로커 주문 호출 이전에 해당 Execution_Order에 대한 Claim을 먼저 수행한다.
3. WHERE 대상 Execution_Order의 id가 일치하고 `execution_status = 'REQUESTED'`이며 `connector_order_request_id IS NULL`인 행이 존재하면, THE MarketConnector SHALL 단일 조건부 UPDATE로 해당 행의 `execution_status`를 `'SUBMITTING'`으로 전이하고 갱신된 행을 반환한다.
4. WHEN Claim이 정확히 한 행을 `'SUBMITTING'`으로 갱신하면, THE MarketConnector SHALL 해당 Execution_Order에 대해 브로커 주문을 정확히 1회 호출한다.
5. IF Claim이 갱신한 행 수가 0이면, THEN THE MarketConnector SHALL 해당 Execution_Order에 대해 브로커 주문 API를 호출하지 않고, `execution_status`가 갱신 이전 값과 정확히 동일하게 유지되도록(`execution_status = execution_status_next`) 상태 변경 동작을 수행하지 않으며, 해당 주문을 중복 제출 차단(skip)으로 표시한다.
6. IF Claim이 갱신한 행 수가 0이면, THEN THE MarketConnector SHALL 전체 배치를 실패로 종료하지 않고 해당 주문을 중복 제출 차단 단위 결과로 기록한 뒤 나머지 대상 주문 처리를 계속한다.
7. WHEN 동일한 Execution_Order에 대해 둘 이상의 실행이 동시에 Claim을 시도하면, THE MarketConnector SHALL 정확히 하나의 실행만 조건부 UPDATE에 성공시켜 그 실행만 브로커 주문을 호출하고, 나머지 실행은 갱신 행 수 0으로 처리한다.
8. WHILE 대상 Execution_Order가 `SUBMITTING` 상태이면, WHEN `mark_strategy_execution_order_submitted()`가 호출되면, THE MarketConnector SHALL 해당 주문의 `execution_status`를 `SUBMITTED`로 전이한다.
9. IF `mark_strategy_execution_order_submitted()` 호출 시 대상 Execution_Order가 `SUBMITTING` 상태가 아니면, THEN THE MarketConnector SHALL `execution_status`를 `SUBMITTED`로 전이하지 않고 기존 상태를 유지한다.
10. WHEN `mark_strategy_execution_order_failed()`가 호출되면, THE MarketConnector SHALL 해당 호출 대상 Execution_Order의 `execution_status`를 `FAILED`로 전이한다.
11. IF `mark_strategy_execution_order_failed()` 호출 시 대상 Execution_Order가 이미 `SUBMITTED` 또는 `FAILED`(Terminal_Status)이면, THEN THE MarketConnector SHALL 그 상태를 `FAILED`로 덮어쓰지 않고 기존 상태를 유지한다.
12. WHILE 실행 모드가 Dry_Run이면, THE MarketConnector SHALL Claim 조건부 UPDATE와 모든 `execution_status` DB 변경을 수행하지 않는다.
13. IF Claim 조건부 UPDATE 실행이 DB 오류로 실패하면, THEN THE MarketConnector SHALL 해당 Execution_Order에 대해 브로커 주문을 호출하지 않고 그 주문을 실패 단위 결과로 처리하며 오류를 나타내는 결과를 반환한다.

### Requirement 3: 최소 수량 안전 검증

**User Story:** 운영자로서, 명백히 잘못된 수량이 브로커에 전달되지 않기를 원한다. 그래야 데이터 손상으로 인한 잘못된 주문을 방지할 수 있다.

#### Acceptance Criteria

1. WHERE 수량이 1 이상의 양의 정수이면, THE MarketConnector SHALL 해당 수량을 값 변경 없이 Normalized_Quantity로 허용한다.
2. IF 수량이 0이면, THEN THE MarketConnector SHALL 브로커 주문을 호출하지 않고 요청을 거부하며 수량 검증 오류를 호출자에게 반환한다.
3. IF 수량이 음수이면, THEN THE MarketConnector SHALL 브로커 주문을 호출하지 않고 요청을 거부하며 수량 검증 오류를 반환한다.
4. IF 수량이 정수가 아닌 소수 값이면, THEN THE MarketConnector SHALL 소수부를 절사하거나 반올림하지 않고 브로커 주문을 호출하지 않으며 수량 검증 오류를 반환한다.
5. IF 수량이 bool 값이면, THEN THE MarketConnector SHALL 브로커 주문을 호출하지 않고 요청을 거부하며 수량 검증 오류를 반환한다.
6. IF 수량이 숫자로 해석할 수 없는 값이면, THEN THE MarketConnector SHALL 브로커 주문을 호출하지 않고 요청을 거부하며 수량 검증 오류를 반환한다.
7. WHERE 수량이 정수와 정확히 동일한 Decimal 또는 문자열 값이면, THE MarketConnector SHALL 해당 값을 1 이상의 정수 수량으로 변환하여 값 변경 없이 허용한다.
8. THE MarketConnector SHALL Normalized_Quantity를 브로커 호출까지 동일하게 전달하고, 소수부 절사나 수량 재계산·축소를 수행하지 않는다.
9. THE MarketConnector SHALL criterion 1-8의 수량 정규화 및 검증 의미를 `connector_strategy_order_execute.py`와 `connector_order_common.py`에서 동일하게 적용한다.
10. WHERE 환경변수 `STEP12_FATAL_MAX_ORDER_QTY`가 설정되지 않았거나 값이 0이면, THE MarketConnector SHALL Fatal_Max_Order_Qty 검사를 비활성화한다.
11. IF `STEP12_FATAL_MAX_ORDER_QTY`가 양수이고 BUY/SELL 수량이 그 값을 초과하면, THEN THE MarketConnector SHALL 수량을 상한으로 조정하지 않고 브로커 주문을 호출하지 않으며 Fatal_Max 초과 오류를 반환한다.
12. IF `STEP12_FATAL_MAX_ORDER_QTY` 값이 미설정과 0을 제외하고 양의 정수로 해석할 수 없으면(음수, 소수, 비숫자), THEN THE MarketConnector SHALL 이를 명시적 구성 오류로 처리하고 조용히 무시하거나 검사를 비활성화하지 않는다.
13. THE MarketConnector SHALL criterion 1-8의 수량 검증을 Fatal_Max_Order_Qty 검사보다 먼저 수행한다.
14. IF 수량 검증(criterion 2-6)이 실패하면, THEN THE MarketConnector SHALL Fatal_Max_Order_Qty 검사를 수행하지 않고 항상 수량 검증 오류를 먼저 반환한다.

### Requirement 4: 전량/부분 취소 및 정정 payload 계약

**User Story:** 개발자로서, CANCEL과 MODIFY 요청의 수량 분기를 검증 가능한 순수 함수로 분리하기를 원한다. 그래야 Broker payload 계약 위반을 브로커 호출 전에 차단할 수 있다.

#### Acceptance Criteria

1. THE MarketConnector SHALL CANCEL/MODIFY 수량 분기를 Cancel_Modify_Quantity_Resolver 순수 함수로 분리하고, 이 함수는 브로커 호출과 DB 쓰기 같은 부작용을 수행하지 않는다.
2. WHEN action이 CANCEL이고 수량이 None이면, THE Cancel_Modify_Quantity_Resolver SHALL `ORD_QTY="0"`과 `QTY_ALL_ORD_YN="Y"`로 전량 취소 payload를 구성한다.
3. WHEN action이 CANCEL이고 수량이 Active_Order의 `order_qty` 이하인 1 이상의 정수이면, THE Cancel_Modify_Quantity_Resolver SHALL 해당 정수를 `ORD_QTY`로 하고 `QTY_ALL_ORD_YN="N"`으로 부분 취소 payload를 구성한다.
4. IF action이 CANCEL이고 지정된 수량이 0, 음수 또는 소수이면, THEN THE Cancel_Modify_Quantity_Resolver SHALL 오류를 발생시키고 payload를 구성하지 않으며 브로커 호출로 진행하지 않는다.
5. IF action이 CANCEL이고 지정된 부분 취소 수량이 Active_Order의 `order_qty`보다 크면, THEN THE Cancel_Modify_Quantity_Resolver SHALL 오류를 발생시키고 payload를 구성하지 않는다.
6. WHEN action이 MODIFY이고 수량이 1 이상의 양의 정수로 지정되면, THE Cancel_Modify_Quantity_Resolver SHALL 해당 정수 수량과 `QTY_ALL_ORD_YN="N"`으로 정정 payload를 구성한다.
7. WHEN action이 MODIFY이고 수량이 생략되면, THE Cancel_Modify_Quantity_Resolver SHALL Active_Order의 기존 수량을 사용하고 `QTY_ALL_ORD_YN="N"`을 유지한다.
8. IF action이 MODIFY이고 정정 수량이 양의 정수가 아니면, THEN THE Cancel_Modify_Quantity_Resolver SHALL 오류를 발생시키고 payload를 구성하지 않는다.
9. WHEN 동일한 입력으로 Cancel_Modify_Quantity_Resolver를 여러 번 호출하면, THE Cancel_Modify_Quantity_Resolver SHALL 동일한 payload 또는 동일한 오류를 결정적으로 반환한다.
10. THE MarketConnector SHALL `connector_cancel.py`의 docstring과 CLI help에서 수량 생략은 전량 취소, 수량 지정은 부분 취소임을 반영한다.
11. THE MarketConnector SHALL `connector_cancel.py` 문서에서 전량 취소가 현재 TR ID와 취소 코드로 Paper 환경에서 동작 확인되었음을 반영하고, 실 환경 검증 주장으로 확장하지 않는다.

### Requirement 5: 로컬 주문 요청 상태 단조성

**User Story:** 운영자로서, 로컬 주문 요청 상태가 Terminal 상태에서 이전 상태로 역행하지 않기를 원한다. 그래야 체결/취소 완료 주문이 잘못된 상태로 되돌아가는 것을 방지할 수 있다.

#### Acceptance Criteria

1. WHEN `submit_rvsecncl_order()` 경로에서 대상 주문 요청의 상태 갱신이 요청되고 현재 `request_status`가 Terminal 상태(`FILLED`, `CANCELED`, `REJECTED`, `FAILED`) 중 하나이면, THE Order_Request_Status_Updater SHALL 요청된 상태가 현재 Terminal 상태와 다를 경우 갱신을 수행하지 않고 기존 `request_status`를 변경 없이 유지한다.
2. IF `submit_rvsecncl_order()` 경로에서 현재 상태가 `FILLED`이고 요청된 상태가 `FILLED`이 아니면, THEN THE Order_Request_Status_Updater SHALL 해당 갱신을 차단하고 `FILLED` 상태를 그대로 유지한다.
3. IF `submit_rvsecncl_order()` 경로에서 현재 상태가 `CANCELED`이고 요청된 상태가 `CANCELED`이 아니면, THEN THE Order_Request_Status_Updater SHALL 해당 갱신을 차단하고 `CANCELED` 상태를 그대로 유지한다.
4. WHERE `submit_rvsecncl_order()` 경로에서 현재 Terminal 상태와 동일한 상태값이 다시 적용되면, THE Order_Request_Status_Updater SHALL 이를 오류 없이 허용하고 결과 상태를 동일한 Terminal 상태로 유지한다.
5. IF `submit_rvsecncl_order()` 경로에서 현재 상태가 `REJECTED` 또는 `FAILED`이고 요청된 상태가 `ACCEPTED`이면, THEN THE Order_Request_Status_Updater SHALL 해당 갱신을 차단하고 기존 `REJECTED` 또는 `FAILED` 상태를 그대로 유지한다.
6. IF `submit_rvsecncl_order()` 경로에서 상태 갱신이 단조성 보호로 차단되면, THEN THE Order_Request_Status_Updater SHALL 이를 성공한 갱신으로 반환하지 않고 차단되었음을 나타내는 실패 지시를 호출자에게 전달한다.
7. WHEN `submit_rvsecncl_order()` 취소·정정 성공 후처리가 실행되면, THE Order_Request_Status_Updater SHALL 취소 요청 행을 비-Terminal 상태에서 `CANCEL_ACCEPTED`로 전이하고 활성 주문을 비-Terminal 상태에서 `CANCELED`로 전이하는 기존 동작을 유지한다.
8. THE MarketConnector SHALL 이 단조성 보호를 `submit_rvsecncl_order()` 경로의 로컬 상태 보호로만 정의하고, 대상 파일(`connector_order_common.py`) 밖의 전체 주문 동기화가 단조성을 보장한다고 주장하지 않는다.

### Requirement 6: 검증 안전 경계

**User Story:** 운영자로서, 이 기능의 검증이 외부 API나 운영 DB 없이 수행되기를 원한다. 그래야 검증 중 실제 주문이나 데이터 변경이 발생하지 않는다.

#### Acceptance Criteria

1. WHILE 검증을 수행하는 동안, THE MarketConnector SHALL 브로커 BUY, SELL, 취소, 정정 호출 횟수를 각각 정확히 0으로 유지한다.
2. IF 검증 중 브로커 BUY, SELL, 취소, 정정 호출 횟수가 1회 이상 발생하면, THEN THE MarketConnector SHALL 해당 검증을 실패로 판정하고 위반한 호출 종류를 나타내는 오류를 보고하며 실제 주문 상태를 변경하지 않는다.
3. WHILE 검증을 수행하는 동안, THE MarketConnector SHALL `requests.post`, token 함수, DB 함수를 mock으로 대체하여 외부 KIS API 호출, 실제 주문/취소, 운영 DB 실행 횟수를 각각 0으로 유지한다.
4. WHEN 검증이 시작되면, THE MarketConnector SHALL `requests.post`, token 함수, DB 함수가 모두 mock으로 대체되었는지 사전에(proactively) 확인하고, 하나라도 mock으로 대체되지 않았으면 해당 함수가 실제로 호출되기 전에 검증을 즉시 실패로 판정하며 mock 미적용 대상을 나타내는 오류를 보고한다.
5. WHEN 검증이 실행되면, THE MarketConnector SHALL Python Compile 오류 0건, 기존 Unit Test 실패 0건, 신규 Contract Test 실패 0건, Ruff 위반 0건을 모두 만족하는 경우에만 검증을 성공으로 판정한다.
6. IF Python Compile 오류, 기존 Unit Test 실패, 신규 Contract Test 실패, Ruff 위반 중 1건 이상이 발생하면, THEN THE MarketConnector SHALL 해당 검증을 실패로 판정하고 실패한 검증 단계를 나타내는 오류를 보고한다.

## Remaining Item

`connector_order_check.py` 검토는 전체 상태 동기화 단조성 보장을 위해 여전히 필요하다. Requirement 5의 단조성은 `submit_rvsecncl_order()` 경로에 대한 로컬 보호로 한정한다.
