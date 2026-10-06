# Implementation Plan: connector-order-submission-guards

## Overview

This plan implements the four defensive controls at the Broker order-submission boundary from the bottom up, beginning with pure functions. It validates each pure function and guarded transition with property-based tests, then integrates them into the Claim-based `run()` loop and cancellation/modification post-processing.

The implementation language is Python. Target files are `connector_order_common.py`, `connector_strategy_order_execute.py`, and `connector_cancel.py`. Property tests use Hypothesis. Every validation runs with `requests.post`, token functions, and DB functions isolated by mocks; no actual Broker or DB call is performed.

Property-test tag format: **Feature: connector-order-submission-guards, Property {number}: {property_text}**

Execution order: the Task Dependency Graph below is informational parallel-scheduling data. Actual implementation follows task-number order: Tasks 1-2, Checkpoint 3, Tasks 4-5, Checkpoint 6, Tasks 7-10, then Final Checkpoint 11.

## Tasks

- [x] 1. Implement the pure quantity normalization and validation function (C1)
  - [x] 1.1 Implement `normalize_order_qty(value) -> int` and `QuantityValidationError(ValueError)` in `connector_order_common.py`
    - Return positive integers of at least 1 without changing their value; convert `Decimal` and string values exactly equal to an integer.
    - Reject `bool` by checking `type(value) is bool` before the integer check.
    - Raise an exception without truncation or rounding for 0, negative, fractional, and non-numeric values.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9_

  - [x] 1.2 Add a value-preservation property test for valid quantities
    - **Property 1: Valid quantities are normalized without changing their value**
    - **Validates: Requirements 1.1, 3.1, 3.7, 3.8**
    - Run at least 100 examples from int, Decimal, and str generators; confirm the normalized value remains unchanged through the call argument.

  - [x] 1.3 Add a rejection property test for invalid quantities
    - **Property 2: Invalid quantities are always rejected**
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**
    - Generate 0, negative, fractional, bool, and non-numeric values; confirm `QuantityValidationError` is raised and no quantity is returned.

- [x] 2. Implement Fatal Max check functions (C2)
  - [x] 2.1 Implement `resolve_fatal_max_order_qty() -> Optional[int]`, `check_fatal_max_order_qty(qty, limit)`, and the configuration-error exception
    - Return `None` when `STEP12_FATAL_MAX_ORDER_QTY` is unset or `0`; return the limit for a positive integer.
    - Treat negative, fractional, and non-numeric values as explicit `ConfigurationError` cases; do not silently ignore or disable them.
    - Return a Fatal_Max exceeded error without adjusting a quantity above the limit.
    - Define the Fatal Max pure functions only in `connector_order_common.py`. Enforcement of `STEP12_FATAL_MAX_ORDER_QTY` does not occur here and is not automatically connected to the general `submit_cash_order()` path.
    - _Requirements: 3.10, 3.11, 3.12_

  - [x] 2.2 Add a property test for Fatal_Max configuration resolution
    - **Property 3: Fatal_Max configuration values branch deterministically**
    - **Validates: Requirements 3.10, 3.12**

  - [x] 2.3 Add a property test that Fatal_Max never adjusts quantity
    - **Property 4: The Fatal_Max check does not adjust quantity**
    - **Validates: Requirements 3.11**

  - [x] 2.4 Add a validation-order property test
    - **Property 5: Quantity validation runs before the Fatal_Max check**
    - **Validates: Requirements 3.13, 3.14**
    - Test the composition that runs C1 before Fatal Max; an invalid quantity must return the quantity-validation error first.

- [x] 3. Checkpoint - validate the quantity defenses
  - Ensure all tests pass; ask the user if questions arise.

- [x] 4. Implement and integrate the CANCEL/MODIFY quantity Resolver (C6)
  - [x] 4.1 Implement `resolve_cancel_modify_quantity(action_type, qty, active_order_qty)` and `CancelModifyPayloadError`
    - No side effects or Broker/DB access; return `(ord_qty, qty_all_ord_yn)`.
    - CANCEL with qty None → `("0","Y")`; CANCEL with integer 1≤qty≤active → `(str(qty),"N")`; MODIFY with positive integer → `(str(qty),"N")`; MODIFY with omitted qty → `(str(active),"N")`.
    - Raise an error and build no payload for CANCEL 0/negative/fractional/excess and MODIFY non-positive-integer values.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9_

  - [x] 4.2 Add a property test for Resolver payload construction
    - **Property 7: The Cancel/Modify resolver builds payload quantity according to the contract**
    - **Validates: Requirements 4.2, 4.3, 4.6, 4.7**

  - [x] 4.3 Add a property test that the Resolver rejects contract violations
    - **Property 8: The Cancel/Modify resolver rejects contract violations**
    - **Validates: Requirements 4.4, 4.5, 4.8**

  - [x] 4.4 Add a determinism and side-effect-free property test
    - **Property 9: The Cancel/Modify resolver is deterministic and side-effect free**
    - **Validates: Requirements 4.1, 4.9**
    - Confirm repeated calls return the same result and Broker/DB mock `call_count == 0`.

  - [x] 4.5 Replace the inline quantity branch in `submit_rvsecncl_order()` with `resolve_cancel_modify_quantity`
    - Populate `request_qty` and `QTY_ALL_ORD_YN` from the resolver; preserve the existing payload, Broker-call, and state-recording order.
    - _Requirements: 4.1_

  - [x] 4.6 Extend the existing full-cancellation payload example test
    - Reuse and extend `tests/test_full_cancel_payload.py` with the existing mock isolation.
    - _Requirements: 4.2_

- [x] 5. Implement local order-request status monotonicity protection (C7)
  - [x] 5.1 Implement `resolve_request_status_transition` and `update_order_request_status_if_not_terminal(order_request_id, request_status, message)`
    - Block regression from the Terminal set (`FILLED`,`CANCELED`,`REJECTED`,`FAILED`) with a conditional UPDATE.
    - Allow reapplication of the same Terminal state as a no-op; return a failure indication when blocked.
    - Preserve `update_order_request_status_only()` for compatibility with other paths.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.8_

  - [x] 5.2 Add a status-monotonicity property test
    - **Property 10: Local order-request status never regresses from Terminal**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

  - [x] 5.3 Apply the guarded function to `apply_rvsecncl_parent_status_after_success()` and the CANCEL_ACCEPTED transition
    - Preserve normal non-Terminal → `CANCEL_ACCEPTED` and non-Terminal → `CANCELED` transitions.
    - _Requirements: 5.7_

  - [x] 5.4 Add post-processing state-transition example tests
    - Confirm non-Terminal → CANCEL_ACCEPTED/CANCELED transitions.
    - _Requirements: 5.7_

- [x] 6. Checkpoint - validate the cancellation/modification path
  - Ensure all tests pass; ask the user if questions arise.

- [x] 7. Implement the Execution Order Claim and status-transition guards (C3, C4)
  - [x] 7.1 Implement `SUBMITTING_STATUS = "SUBMITTING"` and `claim_strategy_execution_order(execution_order_id) -> Optional[Dict]` in `connector_strategy_order_execute.py`
    - Condition: `id = %s AND execution_status = 'REQUESTED' AND connector_order_request_id IS NULL`; transition to `SUBMITTING` and use `RETURNING`.
    - Return the order only for exactly one updated row; preserve state when zero rows are updated.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.7_

  - [x] 7.2 Adjust the guards in `mark_strategy_execution_order_submitted` and `mark_strategy_execution_order_failed`
    - Change the submitted guard to `execution_status = 'SUBMITTING'`; all other states return zero rows.
    - Add `NOT IN ('SUBMITTED','FILLED','CANCELED','REJECTED','FAILED')` to failed so Terminal status is not overwritten.
    - _Requirements: 2.8, 2.9, 2.10, 2.11_

  - [x] 7.3 Add a status-guard transition property test
    - **Property 6: execution_status guard transitions never regress a Terminal state**
    - **Validates: Requirements 2.3, 2.5, 2.6, 2.8, 2.9, 2.10, 2.11**
    - Generate arbitrary initial `execution_status` values in an in-memory fake DB store and validate Claim/mark transitions.

  - [x] 7.4 Add Claim and mark example tests
    - Confirm the `SUBMITTING_STATUS` value/reference, Claim-before-call ordering, and Broker `call_count == 1`.
    - _Requirements: 2.1, 2.2, 2.4_

- [x] 8. Integrate the Claim-based `run()` processing loop (C5)
  - [x] 8.1 Reorder the `--execute` loop as normalize/Fatal Max → Claim → Broker submission → submitted/failed
    - On validation or payload failure, do not call the Broker and record a failed unit. On zero-row Claim, record a skipped duplicate block and continue.
    - On Claim DB error, do not call the Broker and return a failed unit. Dry_Run performs neither Claim nor DB change.
    - Import and reuse the C1/C2 functions to preserve identical semantics.
    - Enforce `STEP12_FATAL_MAX_ORDER_QTY` only on the strategy-order path in the `connector_strategy_order_execute.py` `--execute` loop, not on the general `submit_cash_order()` path.
    - _Requirements: 2.5, 2.6, 2.12, 2.13, 3.9_

  - [x] 8.2 Add batch-continuation, Dry_Run, and Claim DB-error example tests
    - Validate full traversal of a mixed batch (2.6), Dry_Run claim/mark `call_count == 0` (2.12), and no Broker call after a Claim DB error (2.13).
    - _Requirements: 2.6, 2.12, 2.13_

- [x] 9. Align `connector_cancel.py` documentation (C8)
  - [x] 9.1 State in the docstring and CLI help that omitted quantity means full cancellation and specified quantity means partial cancellation
    - Describe the full-cancellation TR ID and cancellation code only as confirmed Paper-environment behavior; do not claim live validation.
    - Preserve the existing `--yes` block and `cancel_order()` signature.
    - _Requirements: 4.10, 4.11_

  - [x] 9.2 Add a documentation-contract smoke test
    - Statically verify the docstring and CLI help text contract.
    - _Requirements: 4.10, 4.11_

- [x] 10. Build the validation safety-boundary harness (Requirement 6)
  - [x] 10.1 Implement proactive mock checks and actual external-side-effect call-count guards for `requests.post`, token functions, and DB functions in a conftest fixture
    - Report an unmocked target and fail before any actual call.
    - Always assert actual `requests.post`, token-function, and operating-DB entry-point `call_count == 0` in every scenario.
    - Mock Broker calls follow scenario-specific expectations: blocked/skipped scenarios use `call_count == 0`; a successful Claim uses exactly `call_count == 1`.
    - Do not force mock Broker calls to zero globally; guard actual side-effect entry points while allowing scenario-specific mock assertions.
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 10.2 Confirm the validation success criteria
    - Use `python -m py_compile <대상 파일>`, `pytest` with mock isolation, and `ruff check`; confirm zero compile, test, and lint violations.
    - _Requirements: 6.5, 6.6_

- [x] 11. Final checkpoint - run the full validation
  - Ensure all tests pass; ask the user if questions arise.

## Notes

- This feature is safety logic at the Broker order boundary, so every property, unit, and regression test subtask is mandatory and cannot be skipped. There are no optional (`*`) subtasks.
- Implementation follows task-number order rather than the parallel waves in the Task Dependency Graph: Tasks 1-2, Checkpoint 3, Tasks 4-5, Checkpoint 6, Tasks 7-10, then Final Checkpoint 11.
- Each task references specific requirements for traceability.
- Every property test runs at least 100 examples against a pure function or in-memory fake DB store.
- Every test isolates `requests.post`, token functions, and DB functions with mocks and performs no actual Broker or DB call.
- Each correctness property is implemented as one property-based test.

## Task Dependency Graph

The graph below is informational. Actual execution follows task-number order rather than parallel waves: 1→2→Checkpoint 3→4→5→Checkpoint 6→7→8→9→10→Final Checkpoint 11.

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
