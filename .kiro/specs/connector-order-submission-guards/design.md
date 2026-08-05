# Design Document

## Overview

이 설계는 MarketConnector의 브로커 주문 제출 경계에 최소 방어 로직 4종을 추가한다.

핵심 목표는 Execution의 주문 결정을 재판단하지 않으면서, 브로커 제출 직전에 발생 가능한 치명적 사고만 차단하는 것이다.

| 항목 | 값 |
| --- | --- |
| 대상 파일 | `connector_strategy_order_execute.py` |
| 대상 파일 | `connector_order_common.py` |
| 대상 파일 | `connector_cancel.py` |
| 방어 영역 1 | 동일 Execution Order 중복 제출 차단 (Claim) |
| 방어 영역 2 | 최소 수량 안전 검증과 Fatal Max 검사 |
| 방어 영역 3 | CANCEL/MODIFY 수량 분기 순수 함수 |
| 방어 영역 4 | `submit_rvsecncl_order()` 로컬 상태 단조성 |

설계는 기존 Retry Normalizer, Rate Limit 재시도, 주문 간 Sleep, 조회·정렬 순서를 변경하지 않는다.

## 책임 경계 (Requirement 1)

MarketConnector는 종목, side, 수량, 주문 순서, 주문 방식을 Execution 결정 그대로 사용한다.

| 항목 | 처리 |
| --- | --- |
| 종목·side·수량·방식 | Execution 값 그대로 브로커 요청에 사용 |
| 주문 순서 | 기존 `fetch_requested_strategy_orders` 정렬 유지 |
| 현금 재배분 | 수행하지 않음 |
| 자동 수량 축소 | 수행하지 않음 |
| 시장가 버퍼 재계산 | 수행하지 않음 |
| 일반 금액 상한 | 도입하지 않음 |

가용 현금 초과 주문도 재배분 없이 그대로 처리하고 브로커 결과(거부 포함)를 기록한다.

방어 로직은 위 원칙을 위반하지 않는 순수 검증과 상태 전이로만 구성한다.

## Architecture

### 제출 경로 흐름

Step 12 실제 제출(`--execute`) 경로에 Claim 단계를 추가한다.

| 단계 | 내용 |
| --- | --- |
| 1 | `fetch_requested_strategy_orders`로 대상 조회 |
| 2 | 주문별 수량 정규화·검증(신규) |
| 3 | Fatal Max 검사(신규, 조건부) |
| 4 | Claim: `REQUESTED → SUBMITTING` 조건부 UPDATE(신규) |
| 5 | Claim 성공 행만 브로커 주문 1회 호출 |
| 6 | 성공 시 `SUBMITTING → SUBMITTED`, 실패 시 `→ FAILED` |
| 7 | Claim 0행이면 중복 차단(skip)으로 기록 후 다음 주문 |

Dry_Run 경로는 Claim과 모든 `execution_status` DB 변경을 수행하지 않는다.

### 취소·정정 경로 흐름

`submit_rvsecncl_order()`의 수량 분기를 순수 함수로 분리하고, 후처리 상태 갱신에 단조성 보호를 적용한다.

| 단계 | 내용 |
| --- | --- |
| 1 | 원주문·활성 주문 조회(기존) |
| 2 | 활성 주문 Terminal 여부 확인(기존) |
| 3 | 수량 분기를 Resolver 순수 함수로 계산(신규 분리) |
| 4 | payload 구성과 브로커 호출(기존) |
| 5 | 성공 후처리 상태 갱신에 단조성 보호 적용(신규) |

## Components and Interfaces

### C1. 수량 정규화·검증 (Requirement 3)

`connector_order_common.py`에 순수 함수를 정의하고, execute 모듈이 import하여 동일 의미로 사용한다.

| 항목 | 값 |
| --- | --- |
| 위치 | `connector_order_common.py` |
| 함수 | `normalize_order_qty(value) -> int` |
| 예외 | `QuantityValidationError(ValueError)` |
| 소비자 | `connector_strategy_order_execute.py` |

동작 계약:

- 1 이상 양의 정수는 값 변경 없이 반환한다.
- 정수와 정확히 동일한 `Decimal`·문자열은 정수로 변환해 허용한다.
- 0, 음수, 소수부가 있는 값, `bool`, 숫자 해석 불가 값은 예외를 발생시킨다.
- 소수부 절사·반올림·수량 축소를 수행하지 않는다.

`bool`은 `int` 하위 타입이므로 정수 판정 이전에 `type(value) is bool` 검사로 먼저 거부한다.

기존 `submit_cash_order`의 `qty <= 0` 검사와 `_validate_order_for_execute`의 검사는 이 함수 의미와 일관되게 유지한다.

### C2. Fatal Max 검사 (Requirement 3)

| 항목 | 값 |
| --- | --- |
| 위치 | `connector_order_common.py` |
| 함수 | `resolve_fatal_max_order_qty() -> Optional[int]` |
| 함수 | `check_fatal_max_order_qty(qty, limit)` |
| 환경변수 | `STEP12_FATAL_MAX_ORDER_QTY` |
| 예외 | `QuantityValidationError`, `ConfigurationError` |

동작 계약:

- 미설정 또는 값 `0`이면 검사를 비활성화한다(`None` 반환).
- 양의 정수이면 그 값을 상한으로 사용한다.
- 미설정·`0`을 제외하고 양의 정수로 해석 불가하면(음수·소수·비숫자) 명시적 구성 오류로 처리한다.
- 상한 초과 수량은 상한으로 조정하지 않고 Fatal_Max 초과 오류를 반환한다.

검사 순서는 수량 정규화·검증(C1)을 먼저 수행하고 그다음 Fatal Max 검사를 수행한다.

수량 검증 실패 시 Fatal Max 검사를 수행하지 않고 항상 수량 검증 오류를 먼저 반환한다.

### C3. Execution Order Claim (Requirement 2)

동일 주문의 중복 제출을 원자적 조건부 UPDATE로 차단한다.

| 항목 | 값 |
| --- | --- |
| 위치 | `connector_strategy_order_execute.py` |
| 상수 | `SUBMITTING_STATUS = "SUBMITTING"` |
| 함수 | `claim_strategy_execution_order(execution_order_id) -> Optional[Dict]` |

조건부 UPDATE 계약:

- 조건: `id = %s AND execution_status = 'REQUESTED' AND connector_order_request_id IS NULL`
- 동작: `execution_status`를 `'SUBMITTING'`으로 전이하고 갱신 행을 `RETURNING`한다.
- 정확히 1행 갱신 시 그 주문만 브로커를 1회 호출한다.
- 갱신 0행이면 브로커를 호출하지 않고 상태를 그대로 유지한다.

동시 실행에서는 조건부 UPDATE의 원자성으로 정확히 한 실행만 성공하고 나머지는 0행이 된다.

`fetch_requested_strategy_orders`의 조회 조건(`execution_status = 'REQUESTED'`, `connector_order_request_id IS NULL`)과 Claim 조건이 일치하므로 정상 대상은 그대로 Claim된다.

### C4. 제출·실패 상태 전이 가드 (Requirement 2)

Claim 도입에 맞춰 후속 전이 함수의 상태 가드를 조정한다.

| 함수 | 변경 |
| --- | --- |
| `mark_strategy_execution_order_submitted` | 가드를 `execution_status = 'SUBMITTING'`으로 변경 |
| `mark_strategy_execution_order_failed` | Terminal 상태를 덮어쓰지 않도록 가드 추가 |

`mark_strategy_execution_order_submitted`:

- 기존 `WHERE ... execution_status = 'REQUESTED' AND connector_order_request_id IS NULL`을 `execution_status = 'SUBMITTING'`으로 대체한다.
- 대상이 `SUBMITTING`이 아니면 `SUBMITTED`로 전이하지 않고 기존 상태를 유지한다(0행 반환).

`mark_strategy_execution_order_failed`:

- `WHERE id = %s AND execution_status NOT IN ('SUBMITTED','FILLED','CANCELED','REJECTED','FAILED')` 가드를 추가한다.
- 이미 Terminal이면 `FAILED`로 덮어쓰지 않고 기존 상태를 유지한다.
- `SUBMITTING`은 Terminal이 아니므로 `FAILED` 전이가 허용된다.

### C5. Claim 기반 run() 처리 루프 (Requirement 2)

`--execute` 루프의 주문 처리 순서를 다음으로 재구성한다.

| 순서 | 처리 |
| --- | --- |
| 1 | `normalize_order_qty` + Fatal Max 검사 |
| 2 | 검증 실패 시 브로커 미호출, 단위 실패 결과 기록 |
| 3 | `claim_strategy_execution_order` 호출 |
| 4 | Claim 0행이면 중복 차단(skip) 단위 결과 기록 후 계속 |
| 5 | Claim DB 오류면 브로커 미호출, 실패 단위 결과 반환 |
| 6 | Claim 1행이면 브로커 제출 후 submitted/failed 전이 |

한 주문의 skip·실패가 전체 배치를 실패로 종료시키지 않고, 나머지 대상 처리를 계속한다.

Dry_Run에서는 1~2단계의 순수 검증만 수행하고 Claim과 DB 변경은 수행하지 않는다.

### C6. CANCEL/MODIFY 수량 Resolver (Requirement 4)

`submit_rvsecncl_order()`의 수량 분기를 부작용 없는 순수 함수로 분리한다.

| 항목 | 값 |
| --- | --- |
| 위치 | `connector_order_common.py` |
| 함수 | `resolve_cancel_modify_quantity(action_type, qty, active_order_qty)` |
| 반환 | `(ord_qty: int, qty_all_ord_yn: str)` |
| 부작용 | 없음(브로커·DB 접근 없음) |

동작 계약:

| 입력 | 결과 |
| --- | --- |
| CANCEL, qty None | `ord_qty=0`, `QTY_ALL_ORD_YN="Y"` |
| CANCEL, 1≤qty≤active_qty 정수 | `ord_qty=qty`, `QTY_ALL_ORD_YN="N"` |
| CANCEL, qty 0·음수·소수 | 오류 발생, payload 미구성 |
| CANCEL, qty>active_qty | 오류 발생, payload 미구성 |
| MODIFY, 양의 정수 qty | `ord_qty=qty`, `QTY_ALL_ORD_YN="N"` |
| MODIFY, qty 생략 | `ord_qty=active_qty`, `QTY_ALL_ORD_YN="N"` |
| MODIFY, 양의 정수 아님 | 오류 발생, payload 미구성 |

동일 입력에 대해 동일 payload 또는 동일 오류를 결정적으로 반환한다.

`submit_rvsecncl_order()`는 이 함수 결과로 `request_qty`와 `QTY_ALL_ORD_YN`을 채우고, 나머지 payload 구성·브로커 호출·상태 기록 순서는 기존과 동일하게 유지한다.

기존 인라인 분기(전량·부분 취소, MODIFY 수량)는 이 함수 호출로 대체한다.

### C7. 로컬 상태 단조성 보호 (Requirement 5)

`submit_rvsecncl_order()` 경로의 상태 갱신에만 Terminal 역행을 차단한다.

| 항목 | 값 |
| --- | --- |
| 위치 | `connector_order_common.py` |
| 함수 | `update_order_request_status_if_not_terminal(order_request_id, request_status, message)` |
| Terminal 집합 | `FILLED`, `CANCELED`, `REJECTED`, `FAILED` |
| 반환 | 갱신 성공 여부(차단 시 실패 지시) |

조건부 UPDATE 계약:

```
UPDATE connector_order_request
   SET request_status = %(status)s, ...
 WHERE id = %(id)s
   AND (
        request_status = %(status)s
        OR request_status NOT IN ('FILLED','CANCELED','REJECTED','FAILED')
   )
```

- 현재 Terminal이고 요청 상태가 다르면 갱신하지 않고 기존 상태를 유지한다(0행).
- 동일 Terminal 상태를 다시 적용하면 오류 없이 허용하고 동일 상태를 유지한다.
- 갱신이 차단되면 성공으로 반환하지 않고 차단 실패 지시를 호출자에게 전달한다.

적용 범위:

- `apply_rvsecncl_parent_status_after_success()`와 CANCEL_ACCEPTED 전이가 이 가드 함수를 사용한다.
- 기존 정상 전이(비-Terminal → `CANCEL_ACCEPTED`, 비-Terminal → `CANCELED`)는 그대로 동작한다.
- 이 보호는 `submit_rvsecncl_order()` 경로의 로컬 보호로만 정의하며, 대상 파일 밖 전체 동기화 단조성은 주장하지 않는다.

기존 `update_order_request_status_only()`는 다른 경로 호환을 위해 유지하되, `submit_rvsecncl_order()` 후처리는 가드 함수로 전환한다.

### C8. connector_cancel.py 문서 정합 (Requirement 4)

| 항목 | 처리 |
| --- | --- |
| docstring·CLI help | 수량 생략=전량 취소, 수량 지정=부분 취소 반영 |
| TR ID·취소 코드 | Paper 환경 동작 확인 수준으로 기술 |
| 검증 주장 | 실 환경 검증 주장으로 확장하지 않음 |

기존 `--yes` 차단과 `cancel_order()` 시그니처는 변경하지 않는다.

## Data Models

새 테이블·컬럼은 도입하지 않는다. 기존 상태값만 사용한다.

### Execution Order 상태 전이

| 상태 | 의미 |
| --- | --- |
| `REQUESTED` | 제출 대기, Claim 이전 |
| `SUBMITTING` | Claim 성공, 브로커 호출 진행 중(신규 사용) |
| `SUBMITTED` | 브로커 제출 성공 |
| `FAILED` | 처리 실패 |

전이 규칙:

- `REQUESTED → SUBMITTING`: Claim 조건부 UPDATE.
- `SUBMITTING → SUBMITTED`: `mark_strategy_execution_order_submitted`.
- `SUBMITTING → FAILED`: `mark_strategy_execution_order_failed`(Terminal 아님).
- Terminal(`SUBMITTED`,`FILLED`,`CANCELED`,`REJECTED`,`FAILED`) → `FAILED`: 차단.

### Order Request 상태(취소·정정 경로)

| 상태 | 분류 |
| --- | --- |
| `PENDING`, `ACCEPTED`, `CANCEL_ACCEPTED`, `MODIFIED` | 비-Terminal |
| `FILLED`, `CANCELED`, `REJECTED`, `FAILED` | Terminal |

## Correctness Properties

*속성(property)은 시스템의 모든 유효한 실행에서 참이어야 하는 특성 또는 동작이다. 사람이 읽는 명세와 기계로 검증 가능한 정확성 보증을 잇는 형식적 진술이다.*

이 기능의 방어 로직 핵심은 순수 판정 함수(수량 정규화, Fatal_Max 해석, Cancel/Modify resolver, 상태 단조성 판정)와 가드 상태 전이다. 이들은 입력 공간이 넓고 "모든 입력에 대해 P가 성립한다"로 표현되므로 property 기반 테스트에 적합하다. 조건부 UPDATE의 동시성 원자성(2.7)과 검증 하네스 규칙(Requirement 6)은 property가 아니라 통합/스모크로 다룬다(Testing Strategy 참조).

아래 property는 순수 함수 또는 in-memory fake DB store를 사용하는 model 기반 검증으로 실행하며, 실제 broker API·token·운영 DB를 호출하지 않는다.

### Property 1: 유효 수량은 값 보존 정규화된다

*For any* 1 이상의 정수 n에 대해, `normalize_order_quantity(n)`, `normalize_order_quantity(Decimal(n))`, `normalize_order_quantity(str(n))`는 모두 값 변경 없이 정확히 정수 n을 반환하며, 정규화된 n은 `_submit_order` 전달 인자까지 동일하게 보존된다(종목·side·주문 방식도 변형 없이 전달).

**Validates: Requirements 1.1, 3.1, 3.7, 3.8**

### Property 2: 무효 수량은 항상 거부된다

*For any* 0, 음수, 정수가 아닌 소수, bool, 또는 숫자로 해석할 수 없는 값에 대해, `normalize_order_quantity`는 절사·반올림 없이 `QuantityValidationError`를 발생시키고 정규화 수량을 반환하지 않는다.

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**

### Property 3: Fatal_Max 구성값 해석은 결정적으로 분기된다

*For any* `STEP12_FATAL_MAX_ORDER_QTY` 문자열에 대해, 미설정 또는 `"0"`이면 `resolve_fatal_max_order_qty`는 검사 비활성(`None`)을 반환하고, 양의 정수 문자열이면 그 정수 상한을 반환하며, 음수·소수·비숫자 문자열이면 `FatalMaxConfigError`를 발생시킨다(조용히 무시하거나 비활성화하지 않는다).

**Validates: Requirements 3.10, 3.12**

### Property 4: Fatal_Max 검사는 수량을 조정하지 않는다

*For any* 양의 상한 c와 정규화 수량 q에 대해, `check_fatal_max_order_qty`는 q <= c일 때만 통과하고 q > c이면 `FatalMaxExceededError`를 발생시키며, 어떤 경우에도 q를 상한으로 축소·조정하지 않는다.

**Validates: Requirements 3.11**

### Property 5: 수량 검증은 Fatal_Max 검사보다 먼저 수행된다

*For any* 무효 수량 입력(상한을 초과하는 규모를 포함)에 대해, 수량 검증 오류(`QuantityValidationError`)가 Fatal_Max 검사보다 먼저 반환되며, 수량 검증이 실패하면 Fatal_Max 검사는 수행되지 않는다.

**Validates: Requirements 3.13, 3.14**

### Property 6: execution_status 가드 전이는 Terminal 상태를 역행시키지 않는다

*For any* 초기 `execution_status`를 가진 Execution_Order에 대해(in-memory fake DB store 사용), Claim은 상태가 `REQUESTED`이고 `connector_order_request_id IS NULL`일 때만 정확히 한 행을 `SUBMITTING`으로 전이하며 그 외에는 0행을 반환해 상태를 변경하지 않고 skip으로 표시한다. `mark_strategy_execution_order_submitted`는 현재 `SUBMITTING`일 때만 `SUBMITTED`로 전이하고, `mark_strategy_execution_order_failed`는 현재 상태가 Terminal(`SUBMITTED, FILLED, CANCELED, REJECTED, FAILED`)이면 `FAILED`로 덮어쓰지 않고 기존 상태를 유지한다.

**Validates: Requirements 2.3, 2.5, 2.6, 2.8, 2.9, 2.10, 2.11**

### Property 7: Cancel/Modify resolver는 계약대로 payload 수량을 구성한다

*For any* Active_Order 수량 a에 대해, `resolve_cancel_modify_quantity`는 다음을 반환한다: CANCEL & qty 생략이면 `("0", "Y")`; CANCEL & 1 <= qty <= a인 정수면 `(str(qty), "N")`; MODIFY & 1 이상 양의 정수 qty면 `(str(qty), "N")`; MODIFY & qty 생략이면 `(str(a), "N")`.

**Validates: Requirements 4.2, 4.3, 4.6, 4.7**

### Property 8: Cancel/Modify resolver는 계약 위반을 거부한다

*For any* 무효 수량에 대해(CANCEL의 0·음수·소수 또는 Active_Order 수량 초과, MODIFY의 양의 정수가 아닌 값), `resolve_cancel_modify_quantity`는 `CancelModifyPayloadError`를 발생시키고 payload를 구성하지 않는다.

**Validates: Requirements 4.4, 4.5, 4.8**

### Property 9: Cancel/Modify resolver는 결정적이고 부작용이 없다

*For any* 동일한 (action_type, qty, active_order_qty) 입력에 대해, `resolve_cancel_modify_quantity`를 여러 번 호출해도 동일한 payload 또는 동일한 오류를 반환하며, 호출 과정에서 broker API 호출과 DB 쓰기가 발생하지 않는다.

**Validates: Requirements 4.1, 4.9**

### Property 10: 로컬 주문 요청 상태는 Terminal에서 역행하지 않는다

*For any* 현재 `request_status`와 요청 상태 쌍에 대해, `resolve_request_status_transition`은 현재 상태가 비-Terminal이면 갱신을 허용하고 결과를 요청 상태로 하며, 현재 상태가 Terminal(`FILLED, CANCELED, REJECTED, FAILED`)이고 요청 상태가 현재와 같으면 오류 없이 허용(no-op)하여 결과를 그 Terminal 상태로 유지하고, 현재 상태가 Terminal이고 요청 상태가 다르면 갱신을 차단(성공 아님, 차단 실패 지시)하며 결과를 현재 Terminal 상태로 유지한다.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

## Error Handling

### 오류 분류와 처리

| 오류 | 발생 지점 | 처리 |
| --- | --- | --- |
| `QuantityValidationError` | 수량 정규화·검증 | 브로커 미호출, 단위 주문을 수량 검증 오류로 실패 처리, 배치 계속 |
| `FatalMaxConfigError` | `STEP12_FATAL_MAX_ORDER_QTY` 해석 | 명시적 구성 오류로 처리, 조용히 무시·비활성 금지 |
| `FatalMaxExceededError` | Fatal_Max 검사 | 브로커 미호출, 수량 미조정, Fatal_Max 초과 오류 반환 |
| `CancelModifyPayloadError` | Cancel/Modify resolver | payload 미구성, 브로커 미호출 |
| Claim 0행 | 조건부 UPDATE | 중복 제출 차단 skip 단위 결과, 상태 불변, 배치 계속 |
| Claim DB 오류 | 조건부 UPDATE 실행 | 브로커 미호출, 실패 단위 결과 반환 |
| 단조성 차단 | `resolve_request_status_transition` | 성공으로 반환하지 않고 차단 실패 지시를 호출자에게 전달 |

### 처리 원칙

- 단위 주문 실패(수량·Fatal_Max·payload·Claim 오류)는 전체 배치를 중단시키지 않고 해당 주문만 실패/skip 단위 결과로 기록한 뒤 나머지 대상을 계속 처리한다(Requirements 2.6).
- 브로커 호출 이전 단계(수량 검증 → Fatal_Max → Claim)에서 실패하면 broker BUY/SELL/취소/정정 호출은 발생하지 않는다.
- 구성 오류(`FatalMaxConfigError`)는 실패를 정상 또는 무주문 성공으로 변환하지 않고 명시적으로 신호한다.
- 기존 `mark_strategy_execution_order_failed`의 예외 경로 기록 동작은 유지하되, Terminal 상태 역행 금지 가드를 추가한다.

## Testing Strategy

### 이중 테스트 접근

- **Property 테스트**: 위 Property 1-10을 순수 함수 또는 in-memory fake DB store 기반 model 테스트로 검증. 각 property는 무작위 입력으로 최소 100회 반복 실행한다.
- **Unit/Example 테스트**: 특정 예시, 경계, 회귀, 호출 순서, 호출 횟수를 검증(prework에서 EXAMPLE/EDGE_CASE로 분류한 항목).
- **Integration/Smoke 테스트**: 검증 하네스 안전 규칙(Requirement 6)과 문서 계약을 확인.

### Property 기반 테스트 대상 (PBT 적용)

이 기능은 순수 판정 로직이 핵심이므로 PBT가 적합하다. Python property 테스트 라이브러리(Hypothesis)를 사용하며 직접 구현하지 않는다.

| Property | 대상 함수 | 생성기 요점 |
| --- | --- | --- |
| P1, P2 | `normalize_order_quantity` | int, Decimal, str, float, bool, 비숫자 혼합 |
| P3 | `resolve_fatal_max_order_qty` | 미설정, "0", 양의 정수 문자열, 음수·소수·비숫자 |
| P4, P5 | `check_fatal_max_order_qty` + 검증 순서 | (상한, 수량) 쌍, 무효 수량 |
| P6 | `claim_*`, `mark_*` + fake DB store | 임의 초기 `execution_status` |
| P7, P8, P9 | `resolve_cancel_modify_quantity` | action, qty(None/유효/무효), active_qty |
| P10 | `resolve_request_status_transition` | 현재·요청 상태 조합 |

- 각 property 테스트는 최소 100회 반복하도록 구성한다.
- 각 테스트에 대응 property를 주석으로 태깅한다.
- 태그 형식: **Feature: connector-order-submission-guards, Property {번호}: {property_text}**
- 각 correctness property는 단일 property 기반 테스트로 구현한다.

### Example/Integration 테스트 대상

| 항목 | 유형 | 검증 |
| --- | --- | --- |
| SUBMITTING_STATUS 상수·사용 (2.1) | Example | 값 == "SUBMITTING", 참조 확인 |
| Claim 선수행·1회 호출 (2.2, 2.4) | Example | mock 호출 순서·`call_count == 1` |
| 혼합 배치 지속 (2.6) | Example | 일부 skip이어도 전체 순회 |
| Dry_Run 미변경 (2.12) | Example | claim/mark `call_count == 0` |
| Claim DB 오류 (2.13) | Example | 예외 시 브로커 미호출·실패 결과 |
| 두 파일 동일 정규화 (3.9) | Example | 동일 `normalize` 함수 재사용 확인 |
| 전량 취소 payload (4.2) | Example | `tests/test_full_cancel_payload.py` 기존 자산 활용·확장 |
| 후처리 상태 전이 (5.7) | Example | 비-Terminal → CANCEL_ACCEPTED/CANCELED |
| 동시 Claim 원자성 (2.7) | Integration | 별도 승인 시 실제 DB 동시성. 기본은 model 단일-승자 근사 |
| docstring·CLI help (4.10, 4.11) | Smoke | 텍스트 계약 정적 확인 |

기존 `tests/test_full_cancel_payload.py`는 `config.py`의 환경변수 키를 import-only 더미로 주입하고 `_request_api`·DB helper를 mock하여 broker/DB 호출 없이 `submit_rvsecncl_order` payload를 검증한다. 신규 테스트도 동일한 mock 격리 방식을 재사용한다.

### 검증 안전 경계 (Requirement 6)

- 모든 테스트는 `requests.post`, token 함수, DB 함수를 mock으로 대체한 상태에서만 실행한다.
- conftest fixture는 검증 시작 시 위 대상이 mock으로 대체되었는지 사전(proactively) 확인하고, 하나라도 미대체이면 실제 호출 이전에 즉시 실패시키며 미적용 대상을 보고한다.
- broker BUY/SELL/취소/정정 진입점의 `call_count`가 각각 정확히 0인지 assert한다. 1회 이상이면 검증 실패로 판정한다.
- 검증 성공은 Python compile 오류 0, 기존 unit test 실패 0, 신규 contract/property test 실패 0, Ruff 위반 0을 모두 만족할 때만 인정한다. 하나라도 실패하면 실패한 검증 단계를 보고한다.
- 검증 명령: `python -m py_compile <대상 파일>`, `pytest`(mock 격리), `ruff check`. 실제 주문 entrypoint(`--execute`), token 발급, 운영 DB 쓰기는 수행하지 않는다.
