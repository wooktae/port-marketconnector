# Requirements Document

## Introduction

This feature adds the minimum defensive logic only at the MarketConnector broker (KIS) order-submission boundary.

The core responsibility principles are as follows.

- MarketConnector does not reassess the order plan decided by Execution. It trusts Execution's decisions for ticker, side, quantity, order sequence, and order method.
- MarketConnector does not perform cash redistribution, automatic quantity reduction, market-order buffer recalculation, or strategy-priority changes.
- MarketConnector does not introduce a general maximum amount for new orders.
- At the broker-submission boundary, MarketConnector blocks only four critical accident classes: duplicate submission of the same Execution Order, clearly invalid quantity, Broker payload contract violations, and regression from a Terminal state.

While preserving these principles, this document defines requirements for four areas: duplicate-submission blocking, minimum quantity safety validation, the full/partial cancellation payload contract, and local state monotonicity.

The target files are limited to `connector_strategy_order_execute.py`, `connector_order_common.py`, `connector_cancel.py`, and their directly related existing test files.

## Glossary

- **MarketConnector**: The system that submits strategy orders to the Broker API and records their status. It is the subject of SHALL statements in this document.
- **Execution**: The external layer that decides the order plan, including ticker, side, quantity, sequence, and method. MarketConnector trusts Execution's decision.
- **Broker**: The Korea Investment & Securities (KIS) domestic-stock order API, which receives actual BUY, SELL, cancellation, and modification calls.
- **Execution_Order**: One order row in the `strategy_execution_order` table. It has an `execution_status` value.
- **Claim**: The operation that acquires processing ownership immediately before the Broker call by conditionally and atomically updating `strategy_execution_order`.
- **SUBMITTING_STATUS**: A constant whose value is `"SUBMITTING"`. It is the `execution_status` after a successful Claim.
- **REQUESTED_STATUS**: A constant whose value is `"REQUESTED"`. It is the target status before a Claim.
- **SUBMITTED_STATUS**: A constant whose value is `"SUBMITTED"`. It is the status after successful Broker submission.
- **FAILED_STATUS**: A constant whose value is `"FAILED"`. It is the status after processing fails.
- **Terminal_Status**: A final status that must not regress. It includes `SUBMITTED`, `FILLED`, `CANCELED`, `REJECTED`, and `FAILED`.
- **Normalized_Quantity**: An order quantity normalized to a positive integer of at least 1.
- **Fatal_Max_Order_Qty**: An emergency data-corruption guard configured by `STEP12_FATAL_MAX_ORDER_QTY`. It is not a strategy limit.
- **Dry_Run**: Execution without `--execute`. It prints the plan and performs neither Claim nor DB status changes.
- **Cancel_Modify_Quantity_Resolver**: A pure function that determines the quantity branch for CANCEL/MODIFY requests.
- **Active_Order**: The currently active order that `submit_rvsecncl_order()` interprets as the modification/cancellation target.
- **Order_Request_Status_Updater**: The `update_order_request_status_only()` function, which updates local order-request status.

## Requirements

### Requirement 1: Preserve the Responsibility Boundary

**User Story:** As an operator, I want MarketConnector not to reassess Execution's order decision, so the responsibility boundary between the strategy and execution layers remains intact.

#### Acceptance Criteria

1. WHEN a Broker order is called for an Execution_Order, THE MarketConnector SHALL use the exact ticker, side, quantity, and order method decided by Execution in the Broker request.
2. WHEN multiple Execution_Orders are processed, THE MarketConnector SHALL call Broker orders in the same order sequence decided by Execution.
3. THE MarketConnector SHALL NOT perform cash redistribution, automatic quantity reduction, market-order buffer recalculation, or strategy-priority changes.
3a. WHERE an order decided by Execution exceeds the available cash of an account, THE MarketConnector SHALL NOT make the order executable through cash redistribution. It SHALL process the order unchanged and record the result, including Broker rejection.
4. THE MarketConnector SHALL NOT introduce a general maximum amount for new orders.
5. THE MarketConnector SHALL preserve the existing Retry Normalizer, Rate Limit retry, and inter-order Sleep behavior without change.

### Requirement 2: Block Duplicate Submission of the Same Execution Order

**User Story:** As an operator, I want the same Execution Order not to be submitted to the Broker more than once, so duplicate-fill incidents are prevented.

#### Acceptance Criteria

1. THE MarketConnector SHALL define `SUBMITTING_STATUS = "SUBMITTING"` and use it in Claim target queries and `execution_status` transitions.
2. WHEN a Broker order is about to be submitted for an Execution_Order, THE MarketConnector SHALL perform the Claim for that Execution_Order before calling the Broker.
3. WHERE a row exists whose Execution_Order id matches, `execution_status = 'REQUESTED'`, and `connector_order_request_id IS NULL`, THE MarketConnector SHALL transition `execution_status` to `'SUBMITTING'` with one conditional UPDATE and return the updated row.
4. WHEN the Claim updates exactly one row to `'SUBMITTING'`, THE MarketConnector SHALL call the Broker exactly once for that Execution_Order.
5. IF the Claim updates zero rows, THEN THE MarketConnector SHALL NOT call the Broker API for that Execution_Order, SHALL NOT perform a status-changing operation so that `execution_status` remains exactly equal to its pre-update value (`execution_status = execution_status_next`), and SHALL mark the order as skipped because duplicate submission was blocked.
6. IF the Claim updates zero rows, THEN THE MarketConnector SHALL NOT fail the entire batch. It SHALL record a duplicate-submission-blocked unit result and continue processing the remaining target orders.
7. WHEN two or more executions concurrently attempt to Claim the same Execution_Order, THE MarketConnector SHALL allow exactly one execution to succeed in the conditional UPDATE and call the Broker. All other executions SHALL be handled as zero-row updates.
8. WHILE the target Execution_Order is `SUBMITTING`, WHEN `mark_strategy_execution_order_submitted()` is called, THE MarketConnector SHALL transition its `execution_status` to `SUBMITTED`.
9. IF the target Execution_Order is not `SUBMITTING` when `mark_strategy_execution_order_submitted()` is called, THEN THE MarketConnector SHALL NOT transition it to `SUBMITTED` and SHALL preserve the existing status.
10. WHEN `mark_strategy_execution_order_failed()` is called, THE MarketConnector SHALL transition the target Execution_Order's `execution_status` to `FAILED`.
11. IF the target Execution_Order is already `SUBMITTED` or `FAILED` (Terminal_Status) when `mark_strategy_execution_order_failed()` is called, THEN THE MarketConnector SHALL NOT overwrite it with `FAILED` and SHALL preserve the existing status.
12. WHILE the execution mode is Dry_Run, THE MarketConnector SHALL NOT perform the Claim conditional UPDATE or any `execution_status` DB change.
13. IF the Claim conditional UPDATE fails with a DB error, THEN THE MarketConnector SHALL NOT call the Broker for that Execution_Order. It SHALL process the order as a failed unit and return a result that represents the error.

### Requirement 3: Minimum Quantity Safety Validation

**User Story:** As an operator, I want clearly invalid quantities to be blocked before they reach the Broker, so orders caused by corrupted data are prevented.

#### Acceptance Criteria

1. WHERE a quantity is a positive integer of at least 1, THE MarketConnector SHALL allow it as Normalized_Quantity without changing its value.
2. IF a quantity is 0, THEN THE MarketConnector SHALL reject the request without calling the Broker and return a quantity-validation error to the caller.
3. IF a quantity is negative, THEN THE MarketConnector SHALL reject the request without calling the Broker and return a quantity-validation error.
4. IF a quantity is a non-integer fractional value, THEN THE MarketConnector SHALL NOT truncate or round it, SHALL NOT call the Broker, and SHALL return a quantity-validation error.
5. IF a quantity is a bool value, THEN THE MarketConnector SHALL reject the request without calling the Broker and return a quantity-validation error.
6. IF a quantity cannot be interpreted as numeric, THEN THE MarketConnector SHALL reject the request without calling the Broker and return a quantity-validation error.
7. WHERE a quantity is a Decimal or string exactly equal to an integer, THE MarketConnector SHALL convert it to an integer of at least 1 and allow it without changing the value.
8. THE MarketConnector SHALL pass Normalized_Quantity unchanged to the Broker call and SHALL NOT truncate, recalculate, or reduce it.
9. THE MarketConnector SHALL apply the quantity normalization and validation semantics of criteria 1-8 consistently in `connector_strategy_order_execute.py` and `connector_order_common.py`.
10. WHERE `STEP12_FATAL_MAX_ORDER_QTY` is unset or is 0, THE MarketConnector SHALL disable the Fatal_Max_Order_Qty check.
11. IF `STEP12_FATAL_MAX_ORDER_QTY` is positive and a BUY/SELL quantity exceeds it, THEN THE MarketConnector SHALL NOT clamp the quantity, SHALL NOT call the Broker, and SHALL return a Fatal_Max exceeded error.
12. IF `STEP12_FATAL_MAX_ORDER_QTY` is neither unset nor 0 and cannot be interpreted as a positive integer, including negative, fractional, or non-numeric values, THEN THE MarketConnector SHALL treat it as an explicit configuration error and SHALL NOT silently ignore it or disable the check.
13. THE MarketConnector SHALL perform the quantity validation in criteria 1-8 before the Fatal_Max_Order_Qty check.
14. IF quantity validation under criteria 2-6 fails, THEN THE MarketConnector SHALL NOT perform the Fatal_Max_Order_Qty check and SHALL always return the quantity-validation error first.

### Requirement 4: Full/Partial Cancellation and Modification Payload Contract

**User Story:** As a developer, I want CANCEL and MODIFY quantity branching separated into a verifiable pure function, so Broker payload contract violations are blocked before a Broker call.

#### Acceptance Criteria

1. THE MarketConnector SHALL separate CANCEL/MODIFY quantity branching into the pure Cancel_Modify_Quantity_Resolver function. This function SHALL NOT perform side effects such as Broker calls or DB writes.
2. WHEN action is CANCEL and quantity is None, THE Cancel_Modify_Quantity_Resolver SHALL build a full-cancellation payload with `ORD_QTY="0"` and `QTY_ALL_ORD_YN="Y"`.
3. WHEN action is CANCEL and quantity is an integer of at least 1 and no greater than the Active_Order's `order_qty`, THE Cancel_Modify_Quantity_Resolver SHALL build a partial-cancellation payload with that integer as `ORD_QTY` and `QTY_ALL_ORD_YN="N"`.
4. IF action is CANCEL and the specified quantity is 0, negative, or fractional, THEN THE Cancel_Modify_Quantity_Resolver SHALL raise an error, SHALL NOT build a payload, and SHALL NOT proceed to the Broker call.
5. IF action is CANCEL and the specified partial-cancellation quantity exceeds the Active_Order's `order_qty`, THEN THE Cancel_Modify_Quantity_Resolver SHALL raise an error and SHALL NOT build a payload.
6. WHEN action is MODIFY and a positive integer quantity of at least 1 is specified, THE Cancel_Modify_Quantity_Resolver SHALL build a modification payload with that quantity and `QTY_ALL_ORD_YN="N"`.
7. WHEN action is MODIFY and quantity is omitted, THE Cancel_Modify_Quantity_Resolver SHALL use the Active_Order's existing quantity and keep `QTY_ALL_ORD_YN="N"`.
8. IF action is MODIFY and the modification quantity is not a positive integer, THEN THE Cancel_Modify_Quantity_Resolver SHALL raise an error and SHALL NOT build a payload.
9. WHEN Cancel_Modify_Quantity_Resolver is called repeatedly with the same input, THE Cancel_Modify_Quantity_Resolver SHALL deterministically return the same payload or the same error.
10. THE MarketConnector SHALL state in the `connector_cancel.py` docstring and CLI help that omitting quantity means full cancellation and specifying quantity means partial cancellation.
11. THE MarketConnector SHALL state in `connector_cancel.py` that full cancellation was confirmed to work in the Paper environment with the current TR ID and cancellation code, and SHALL NOT extend this into a live-environment validation claim.

### Requirement 5: Local Order-Request Status Monotonicity

**User Story:** As an operator, I want local order-request status not to regress from a Terminal state, so filled or canceled orders cannot return to an invalid earlier state.

#### Acceptance Criteria

1. WHEN a target order-request status update is requested on the `submit_rvsecncl_order()` path and the current `request_status` is Terminal (`FILLED`, `CANCELED`, `REJECTED`, or `FAILED`), THE Order_Request_Status_Updater SHALL NOT update it when the requested status differs from the current Terminal status and SHALL preserve the existing `request_status` unchanged.
2. IF the current status is `FILLED` on the `submit_rvsecncl_order()` path and the requested status is not `FILLED`, THEN THE Order_Request_Status_Updater SHALL block the update and preserve `FILLED`.
3. IF the current status is `CANCELED` on the `submit_rvsecncl_order()` path and the requested status is not `CANCELED`, THEN THE Order_Request_Status_Updater SHALL block the update and preserve `CANCELED`.
4. WHERE the same status value as the current Terminal status is applied again on the `submit_rvsecncl_order()` path, THE Order_Request_Status_Updater SHALL allow it without error and keep the resulting status equal to the same Terminal status.
5. IF the current status is `REJECTED` or `FAILED` on the `submit_rvsecncl_order()` path and the requested status is `ACCEPTED`, THEN THE Order_Request_Status_Updater SHALL block the update and preserve `REJECTED` or `FAILED`.
6. IF a status update is blocked by the monotonicity guard on the `submit_rvsecncl_order()` path, THEN THE Order_Request_Status_Updater SHALL NOT return it as a successful update and SHALL return a failure indication that represents the block.
7. WHEN successful cancellation/modification post-processing runs after `submit_rvsecncl_order()`, THE Order_Request_Status_Updater SHALL preserve the existing behavior that transitions the cancellation-request row from non-Terminal to `CANCEL_ACCEPTED` and the active order from non-Terminal to `CANCELED`.
8. THE MarketConnector SHALL define this monotonicity guard only as local state protection on the `submit_rvsecncl_order()` path and SHALL NOT claim that all order synchronization outside the target file (`connector_order_common.py`) guarantees monotonicity.

### Requirement 6: Validation Safety Boundary

**User Story:** As an operator, I want this feature to be validated without external APIs or the operating DB, so validation cannot submit actual orders or change data.

#### Acceptance Criteria

1. WHILE validation is running, THE MarketConnector SHALL keep the count of actual Broker BUY, SELL, cancellation, and modification calls exactly 0 for each call type.
2. IF the count of an actual Broker BUY, SELL, cancellation, or modification call reaches 1 or more during validation, THEN THE MarketConnector SHALL fail validation, report the violating call type, and SHALL NOT change actual order state.
3. WHILE validation is running, THE MarketConnector SHALL replace `requests.post`, token functions, and DB functions with mocks so the counts of external KIS API calls, actual order/cancellation calls, and operating DB executions each remain 0.
4. WHEN validation starts, THE MarketConnector SHALL proactively confirm that `requests.post`, token functions, and DB functions have all been replaced by mocks. If any target is not mocked, validation SHALL fail immediately before that function is actually called and report the unmocked target.
5. WHEN validation runs, THE MarketConnector SHALL consider validation successful only when there are 0 Python Compile errors, 0 existing Unit Test failures, 0 new Contract Test failures, and 0 Ruff violations.
6. IF one or more Python Compile errors, existing Unit Test failures, new Contract Test failures, or Ruff violations occur, THEN THE MarketConnector SHALL fail validation and report the failed validation stage.

## Remaining Item

Review of `connector_order_check.py` is still required to guarantee full synchronization-state monotonicity. Requirement 5 monotonicity is limited to local protection on the `submit_rvsecncl_order()` path.
