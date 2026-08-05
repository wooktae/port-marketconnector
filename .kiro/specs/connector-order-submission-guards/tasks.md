# Implementation Plan: connector-order-submission-guards

## Overview

이 계획은 브로커 주문 제출 경계의 방어 로직 4종을 순수 함수부터 상향식으로 구현하고, 각 순수 함수·가드 전이를 property 기반 테스트로 검증한 뒤, Claim 기반 `run()` 루프와 취소·정정 후처리에 통합한다.

구현 언어는 Python이며(대상 파일은 `connector_order_common.py`, `connector_strategy_order_execute.py`, `connector_cancel.py`), property 테스트는 Hypothesis를 사용한다. 모든 검증은 `requests.post`, token 함수, DB 함수를 mock으로 격리한 상태에서만 실행하고, 실제 broker/DB 호출은 수행하지 않는다.

property 테스트 태그 형식: **Feature: connector-order-submission-guards, Property {번호}: {property_text}**

실행 순서: 아래 Task Dependency Graph는 참고용(병렬 스케줄링 정보)이며, 실제 구현은 Task 번호 순서로 진행한다. Task 1~2 완료 후 Checkpoint 3, Task 4~5 완료 후 Checkpoint 6, 이어서 Task 7~10, 마지막 Final Checkpoint 11 순이다.

## Tasks

- [x] 1. 수량 정규화·검증 순수 함수 구현 (C1)
  - [x] 1.1 `connector_order_common.py`에 `normalize_order_qty(value) -> int`와 `QuantityValidationError(ValueError)` 구현
    - 1 이상 양의 정수는 값 변경 없이 반환, 정수와 정확히 동일한 `Decimal`·문자열은 정수로 변환
    - `type(value) is bool` 검사를 정수 판정 이전에 먼저 수행해 `bool` 거부
    - 0·음수·소수부 있는 값·비숫자는 절사·반올림 없이 예외 발생
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9_

  - [x] 1.2 유효 수량 값 보존 property 테스트 작성
    - **Property 1: 유효 수량은 값 보존 정규화된다**
    - **Validates: Requirements 1.1, 3.1, 3.7, 3.8**
    - int, Decimal, str 생성기로 최소 100회 반복, 정규화 값이 전달 인자까지 보존됨을 확인

  - [x] 1.3 무효 수량 거부 property 테스트 작성
    - **Property 2: 무효 수량은 항상 거부된다**
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**
    - 0·음수·소수·bool·비숫자 생성기로 `QuantityValidationError` 발생과 미반환 확인

- [x] 2. Fatal Max 검사 함수 구현 (C2)
  - [x] 2.1 `resolve_fatal_max_order_qty() -> Optional[int]`, `check_fatal_max_order_qty(qty, limit)`, 구성 오류 예외 구현
    - `STEP12_FATAL_MAX_ORDER_QTY` 미설정·`0`이면 `None`(비활성), 양의 정수면 상한 반환
    - 음수·소수·비숫자는 `ConfigurationError`로 명시적 처리(조용히 무시·비활성 금지)
    - 상한 초과 수량은 조정하지 않고 Fatal_Max 초과 오류 반환
    - Fatal Max 순수 함수는 `connector_order_common.py`에 정의만 한다. `STEP12_FATAL_MAX_ORDER_QTY` 적용(enforcement)은 여기서 수행하지 않으며, 일반 `submit_cash_order()` 경로에 자동 연결하지 않는다
    - _Requirements: 3.10, 3.11, 3.12_

  - [x] 2.2 Fatal_Max 구성값 해석 property 테스트 작성
    - **Property 3: Fatal_Max 구성값 해석은 결정적으로 분기된다**
    - **Validates: Requirements 3.10, 3.12**

  - [x] 2.3 Fatal_Max 무조정 property 테스트 작성
    - **Property 4: Fatal_Max 검사는 수량을 조정하지 않는다**
    - **Validates: Requirements 3.11**

  - [x] 2.4 검증 순서 property 테스트 작성
    - **Property 5: 수량 검증은 Fatal_Max 검사보다 먼저 수행된다**
    - **Validates: Requirements 3.13, 3.14**
    - 수량 검증(C1)을 Fatal Max보다 먼저 수행하는 조합 함수를 대상으로, 무효 수량 입력 시 수량 검증 오류가 우선 반환됨을 확인

- [x] 3. Checkpoint - 수량 방어 로직 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. CANCEL/MODIFY 수량 Resolver 구현과 통합 (C6)
  - [x] 4.1 `resolve_cancel_modify_quantity(action_type, qty, active_order_qty)`와 `CancelModifyPayloadError` 구현
    - 부작용 없음(broker·DB 접근 없음), `(ord_qty, qty_all_ord_yn)` 반환
    - CANCEL qty None → `("0","Y")`, CANCEL 1≤qty≤active → `(str(qty),"N")`, MODIFY 양의 정수 → `(str(qty),"N")`, MODIFY 생략 → `(str(active),"N")`
    - CANCEL 0·음수·소수·초과, MODIFY 비양정수는 오류 발생, payload 미구성
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9_

  - [x] 4.2 Resolver payload 구성 property 테스트 작성
    - **Property 7: Cancel/Modify resolver는 계약대로 payload 수량을 구성한다**
    - **Validates: Requirements 4.2, 4.3, 4.6, 4.7**

  - [x] 4.3 Resolver 계약 위반 거부 property 테스트 작성
    - **Property 8: Cancel/Modify resolver는 계약 위반을 거부한다**
    - **Validates: Requirements 4.4, 4.5, 4.8**

  - [x] 4.4 Resolver 결정성·무부작용 property 테스트 작성
    - **Property 9: Cancel/Modify resolver는 결정적이고 부작용이 없다**
    - **Validates: Requirements 4.1, 4.9**
    - 반복 호출 동일 결과, broker/DB mock `call_count == 0` 확인

  - [x] 4.5 `submit_rvsecncl_order()`의 인라인 수량 분기를 `resolve_cancel_modify_quantity` 호출로 대체
    - resolver 결과로 `request_qty`와 `QTY_ALL_ORD_YN`을 채우고, payload 구성·브로커 호출·상태 기록 순서는 기존과 동일 유지
    - _Requirements: 4.1_

  - [x] 4.6 전량 취소 payload 예시 테스트 확장
    - 기존 `tests/test_full_cancel_payload.py`를 mock 격리 방식으로 재사용·확장
    - _Requirements: 4.2_

- [x] 5. 로컬 주문 요청 상태 단조성 보호 구현 (C7)
  - [x] 5.1 `resolve_request_status_transition`과 `update_order_request_status_if_not_terminal(order_request_id, request_status, message)` 구현
    - Terminal 집합(`FILLED`,`CANCELED`,`REJECTED`,`FAILED`) 역행 차단 조건부 UPDATE
    - 동일 Terminal 재적용은 오류 없이 허용(no-op), 차단 시 성공 아닌 실패 지시 반환
    - 기존 `update_order_request_status_only()`는 다른 경로 호환을 위해 유지
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.8_

  - [x] 5.2 상태 단조성 property 테스트 작성
    - **Property 10: 로컬 주문 요청 상태는 Terminal에서 역행하지 않는다**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

  - [x] 5.3 `apply_rvsecncl_parent_status_after_success()`와 CANCEL_ACCEPTED 전이에 가드 함수 적용
    - 비-Terminal → `CANCEL_ACCEPTED`, 비-Terminal → `CANCELED` 기존 정상 전이 유지
    - _Requirements: 5.7_

  - [x] 5.4 후처리 상태 전이 예시 테스트 작성
    - 비-Terminal → CANCEL_ACCEPTED/CANCELED 전이 확인
    - _Requirements: 5.7_

- [x] 6. Checkpoint - 취소·정정 경로 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Execution Order Claim과 상태 전이 가드 구현 (C3, C4)
  - [x] 7.1 `connector_strategy_order_execute.py`에 `SUBMITTING_STATUS = "SUBMITTING"` 상수와 `claim_strategy_execution_order(execution_order_id) -> Optional[Dict]` 구현
    - 조건: `id = %s AND execution_status = 'REQUESTED' AND connector_order_request_id IS NULL`, `SUBMITTING`으로 전이 후 `RETURNING`
    - 정확히 1행 갱신 시 그 주문만 반환, 0행이면 상태 불변
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.7_

  - [x] 7.2 `mark_strategy_execution_order_submitted`와 `mark_strategy_execution_order_failed` 가드 조정
    - submitted 가드를 `execution_status = 'SUBMITTING'`으로 변경(그 외 0행)
    - failed에 `NOT IN ('SUBMITTED','FILLED','CANCELED','REJECTED','FAILED')` 가드 추가(Terminal 덮어쓰기 차단)
    - _Requirements: 2.8, 2.9, 2.10, 2.11_

  - [x] 7.3 상태 가드 전이 property 테스트 작성
    - **Property 6: execution_status 가드 전이는 Terminal 상태를 역행시키지 않는다**
    - **Validates: Requirements 2.3, 2.5, 2.6, 2.8, 2.9, 2.10, 2.11**
    - in-memory fake DB store로 임의 초기 `execution_status` 생성, Claim·mark 전이 확인

  - [x] 7.4 Claim·mark 예시 테스트 작성
    - `SUBMITTING_STATUS` 값·참조 확인, Claim 선수행과 브로커 `call_count == 1` 확인
    - _Requirements: 2.1, 2.2, 2.4_

- [x] 8. Claim 기반 run() 처리 루프 통합 (C5)
  - [x] 8.1 `--execute` 루프를 정규화·Fatal Max → Claim → 브로커 제출 → submitted/failed 순서로 재구성
    - 검증 실패·payload 실패 시 브로커 미호출 단위 실패 기록, Claim 0행이면 중복 차단 skip 기록 후 계속
    - Claim DB 오류면 브로커 미호출·실패 단위 결과 반환, Dry_Run에서는 Claim과 DB 변경 미수행
    - C1·C2 함수를 import해 동일 의미로 사용
    - `STEP12_FATAL_MAX_ORDER_QTY` 적용(enforcement)은 이 전략 주문 경로(`connector_strategy_order_execute.py`의 `--execute` 루프)에만 연결한다. 일반 `submit_cash_order()` cash-order 제출 경로에는 적용하지 않는다
    - _Requirements: 2.5, 2.6, 2.12, 2.13, 3.9_

  - [x] 8.2 배치 지속·Dry_Run·Claim DB 오류 예시 테스트 작성
    - 혼합 배치 전체 순회(2.6), Dry_Run claim/mark `call_count == 0`(2.12), Claim DB 오류 시 브로커 미호출(2.13)
    - _Requirements: 2.6, 2.12, 2.13_

- [x] 9. connector_cancel.py 문서 정합 (C8)
  - [x] 9.1 docstring과 CLI help에 수량 생략=전량 취소, 수량 지정=부분 취소 반영
    - 전량 취소 TR ID·취소 코드를 Paper 환경 동작 확인 수준으로 기술, 실 환경 검증 주장으로 확장하지 않음
    - 기존 `--yes` 차단과 `cancel_order()` 시그니처는 변경하지 않음
    - _Requirements: 4.10, 4.11_

  - [x] 9.2 문서 계약 스모크 테스트 작성
    - docstring·CLI help 텍스트 계약 정적 확인
    - _Requirements: 4.10, 4.11_

- [x] 10. 검증 안전 경계 하네스 구성 (Requirement 6)
  - [x] 10.1 conftest fixture로 `requests.post`·token 함수·DB 함수 사전 mock 확인과 실제 외부 side-effect 진입점 call_count 가드 구현
    - 검증 시작 시 mock 미대체 대상을 실제 호출 이전에 즉시 실패로 보고
    - 실제 `requests.post`·token 함수·운영 DB 호출은 모든 시나리오에서 `call_count == 0`을 항상 assert(conftest fixture가 이 진입점들을 가드)
    - mock broker 함수 호출은 시나리오별 기대값을 따른다: 차단·skip 시나리오는 `call_count == 0`, 정상 Claim 성공 시나리오는 정확히 `call_count == 1`
    - mock broker 함수 호출을 무조건 0으로 강제하지 않고, 실제 외부 side-effect 진입점만 0으로 가드하며 시나리오별 mock broker 호출 assert를 허용
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 10.2 검증 성공 기준 실행 확인
    - `python -m py_compile <대상 파일>`, `pytest`(mock 격리), `ruff check`로 compile·test·lint 위반 0 확인
    - _Requirements: 6.5, 6.6_

- [x] 11. Final checkpoint - 전체 검증
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 이 기능은 브로커 주문 경계의 안전 로직이므로 모든 테스트(property·unit·regression) sub-task는 필수이며 건너뛸 수 없다. 선택(`*`) 표시 sub-task는 두지 않는다.
- 실제 구현은 아래 Task Dependency Graph의 병렬 wave가 아니라 Task 번호 순서로 진행한다: Task 1~2 완료 후 Checkpoint 3, Task 4~5 완료 후 Checkpoint 6, 이어서 Task 7~10, 마지막 Final Checkpoint 11.
- 각 task는 추적성을 위해 특정 requirement를 참조한다.
- property 테스트는 순수 함수 또는 in-memory fake DB store 기반 model 테스트로 각 최소 100회 반복한다.
- 모든 테스트는 `requests.post`·token·DB 함수를 mock으로 격리한 상태에서만 실행하며 실제 broker/DB 호출을 하지 않는다.
- 각 correctness property는 단일 property 기반 테스트로 구현한다.

## Task Dependency Graph

아래 그래프는 참고용(informational)이다. 실제 실행은 병렬 wave가 아니라 Task 번호 순서(1→2→Checkpoint 3→4→5→Checkpoint 6→7→8→9→10→Final Checkpoint 11)를 따른다.

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "7.1", "9.1", "10.1"] },
    { "id": 1, "tasks": ["2.1", "7.2", "1.2", "1.3", "9.2"] },
    { "id": 2, "tasks": ["4.1", "8.1", "2.2", "2.3", "2.4", "7.3", "7.4"] },
    { "id": 3, "tasks": ["4.5", "4.2", "4.3", "4.4", "8.2"] },
    { "id": 4, "tasks": ["5.1", "4.6"] },
    { "id": 5, "tasks": ["5.3", "5.2"] },
    { "id": 6, "tasks": ["5.4", "10.2"] }
  ]
}
```
