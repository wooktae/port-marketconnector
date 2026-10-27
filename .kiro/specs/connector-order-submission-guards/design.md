# Design Document

## Overview

This design adds four minimum defensive controls at the MarketConnector Broker order-submission boundary.

The core goal is to block only critical incidents immediately before Broker submission without reassessing Execution's order decision.

| Item | Value |
| --- | --- |
| Target file | `connector_strategy_order_execute.py` |
| Target file | `connector_order_common.py` |
| Target file | `connector_cancel.py` |
| Defense area 1 | Block duplicate submission of the same Execution Order through a Claim |
| Defense area 2 | Minimum quantity validation and Fatal Max check |
| Defense area 3 | Pure CANCEL/MODIFY quantity-branch function |
| Defense area 4 | Local status monotonicity in `submit_rvsecncl_order()` |

The design does not change the existing Retry Normalizer, Rate Limit retry, inter-order Sleep, or query/sort order.

## Responsibility Boundary (Requirement 1)

MarketConnector uses the ticker, side, quantity, order sequence, and order method exactly as decided by Execution.

| Item | Handling |
| --- | --- |
| Ticker, side, quantity, method | Use Execution values unchanged in the Broker request |
| Order sequence | Preserve existing `fetch_requested_strategy_orders` sorting |
| Cash redistribution | Not performed |
| Automatic quantity reduction | Not performed |
| Market-order buffer recalculation | Not performed |
| General amount limit | Not introduced |

Orders that exceed available cash are processed without redistribution, and the Broker result, including rejection, is recorded.

The defenses consist only of pure validation and guarded transitions that do not violate this boundary.

## Architecture

### Submission Path Flow

Add a Claim stage to the Step 12 actual-submission (`--execute`) path.

| Stage | Details |
| --- | --- |
| 1 | Query targets with `fetch_requested_strategy_orders` |
| 2 | Normalize and validate each order quantity (new) |
| 3 | Run the Fatal Max check (new, conditional) |
| 4 | Claim through conditional `REQUESTED → SUBMITTING` UPDATE (new) |
| 5 | Call the Broker exactly once only for a successfully claimed row |
| 6 | On success use `SUBMITTING → SUBMITTED`; on failure use `→ FAILED` |
| 7 | For a zero-row Claim, record duplicate-blocked skip and continue |

Dry_Run performs neither Claim nor any `execution_status` DB change.

### Cancellation/Modification Path Flow

Separate `submit_rvsecncl_order()` quantity branching into a pure function and apply monotonicity protection to post-processing status updates.

| Stage | Details |
| --- | --- |
| 1 | Query original and active orders (existing) |
| 2 | Confirm whether the active order is Terminal (existing) |
| 3 | Resolve the quantity branch with a pure Resolver (new separation) |
| 4 | Build the payload and call the Broker (existing) |
| 5 | Apply monotonicity protection to successful post-processing updates (new) |

## Components and Interfaces

### C1. Quantity Normalization and Validation (Requirement 3)

Define a pure function in `connector_order_common.py` and import it from the execute module so both paths use identical semantics.

| Item | Value |
| --- | --- |
| Location | `connector_order_common.py` |
| Function | `normalize_order_qty(value) -> int` |
| Exception | `QuantityValidationError(ValueError)` |
| Consumer | `connector_strategy_order_execute.py` |

Behavior contract:

- Return a positive integer of at least 1 without changing its value.
- Convert a `Decimal` or string exactly equal to an integer and allow it.
- Raise an exception for 0, negative, fractional, `bool`, or non-numeric values.
- Do not truncate, round, or reduce quantity.

Because `bool` is a subtype of `int`, reject it with `type(value) is bool` before the integer check.

Keep the existing `submit_cash_order` `qty <= 0` check and `_validate_order_for_execute` validation consistent with this function.

### C2. Fatal Max Check (Requirement 3)

| Item | Value |
| --- | --- |
| Location | `connector_order_common.py` |
| Function | `resolve_fatal_max_order_qty() -> Optional[int]` |
| Function | `check_fatal_max_order_qty(qty, limit)` |
| Environment variable | `STEP12_FATAL_MAX_ORDER_QTY` |
| Exceptions | `QuantityValidationError`, `ConfigurationError` |

Behavior contract:

- Disable the check and return `None` when unset or `0`.
- Use a positive integer as the limit.
- Treat negative, fractional, or non-numeric values other than unset/`0` as explicit configuration errors.
- Return a Fatal_Max exceeded error without clamping a quantity above the limit.

Run C1 quantity normalization and validation before the Fatal Max check.

When quantity validation fails, do not run the Fatal Max check; return the quantity-validation error first.

### C3. Execution Order Claim (Requirement 2)

Block duplicate submission atomically through a conditional UPDATE.

| Item | Value |
| --- | --- |
| Location | `connector_strategy_order_execute.py` |
| Constant | `SUBMITTING_STATUS = "SUBMITTING"` |
| Function | `claim_strategy_execution_order(execution_order_id) -> Optional[Dict]` |

Conditional UPDATE contract:

- Condition components remain `execution_status = 'REQUESTED'` and `connector_order_request_id IS NULL`.
- Combined condition: `id = %s AND execution_status = 'REQUESTED' AND connector_order_request_id IS NULL`
- Action: transition `execution_status` to `'SUBMITTING'` and return the updated row with `RETURNING`.
- When exactly one row is updated, only that order calls the Broker once.
- When zero rows are updated, do not call the Broker and preserve status.

Under concurrent execution, conditional UPDATE atomicity allows exactly one execution to succeed; the others receive zero rows.

The `fetch_requested_strategy_orders` query condition matches the Claim condition, so valid targets remain claimable without changing target selection.

### C4. Submitted/Failed Transition Guards (Requirement 2)

Adjust the downstream transition guards for the Claim.

| Function | Change |
| --- | --- |
| `mark_strategy_execution_order_submitted` | Change guard to `execution_status = 'SUBMITTING'` |
| `mark_strategy_execution_order_failed` | Add a guard that prevents overwriting Terminal status |

`mark_strategy_execution_order_submitted`:

- Replace `WHERE ... execution_status = 'REQUESTED' AND connector_order_request_id IS NULL` with `execution_status = 'SUBMITTING'`.
- If the target is not `SUBMITTING`, return zero rows and preserve its status.

`mark_strategy_execution_order_failed`:

- Add `WHERE id = %s AND execution_status NOT IN ('SUBMITTED','FILLED','CANCELED','REJECTED','FAILED')`.
- The protected Terminal sequence remains `SUBMITTED, FILLED, CANCELED, REJECTED, FAILED`.
- Preserve an existing Terminal status rather than overwriting it with `FAILED`.
- Allow `SUBMITTING → FAILED` because `SUBMITTING` is non-Terminal.

### C5. Claim-Based `run()` Processing Loop (Requirement 2)

Reorder each `--execute` iteration as follows.

| Order | Handling |
| --- | --- |
| 1 | `normalize_order_qty` followed by the Fatal Max check |
| 2 | On validation failure, do not call the Broker; record a failed unit |
| 3 | Call `claim_strategy_execution_order` |
| 4 | On zero-row Claim, record duplicate-blocked skip and continue |
| 5 | On Claim DB error, do not call the Broker; return a failed unit |
| 6 | On one-row Claim, submit to the Broker and transition to submitted/failed |

A skipped or failed order does not terminate the whole batch; remaining targets continue.

Dry_Run performs only pure validation from steps 1-2 and performs neither Claim nor DB change.

### C6. CANCEL/MODIFY Quantity Resolver (Requirement 4)

Separate `submit_rvsecncl_order()` quantity branching into a side-effect-free pure function.

| Item | Value |
| --- | --- |
| Location | `connector_order_common.py` |
| Function | `resolve_cancel_modify_quantity(action_type, qty, active_order_qty)` |
| Return | `(ord_qty: int, qty_all_ord_yn: str)` |
| Side effects | None; no Broker or DB access |

Behavior contract:

| Input | Result |
| --- | --- |
| CANCEL, qty None | `ord_qty=0`, `QTY_ALL_ORD_YN="Y"` |
| CANCEL, integer 1≤qty≤active_qty | `ord_qty=qty`, `QTY_ALL_ORD_YN="N"` |
| CANCEL, qty 0/negative/fractional | Error; no payload |
| CANCEL, qty>active_qty | Error; no payload |
| MODIFY, positive integer qty | `ord_qty=qty`, `QTY_ALL_ORD_YN="N"` |
| MODIFY, qty omitted | `ord_qty=active_qty`, `QTY_ALL_ORD_YN="N"` |
| MODIFY, not a positive integer | Error; no payload |

The same input deterministically returns the same payload or error.

`submit_rvsecncl_order()` populates `request_qty` and `QTY_ALL_ORD_YN` from this result and preserves the existing order of remaining payload construction, Broker call, and state recording.

Replace the existing inline full/partial cancellation and MODIFY quantity branches with this function call.

### C7. Local Status Monotonicity Protection (Requirement 5)

Block Terminal regression only for status updates on the `submit_rvsecncl_order()` path.

| Item | Value |
| --- | --- |
| Location | `connector_order_common.py` |
| Function | `update_order_request_status_if_not_terminal(order_request_id, request_status, message)` |
| Terminal set | `FILLED`, `CANCELED`, `REJECTED`, `FAILED` |
| Contract sequence | `FILLED, CANCELED, REJECTED, FAILED` |
| Return | Whether the update succeeded; return failure when blocked |

Conditional UPDATE contract:

```sql
UPDATE connector_order_request
   SET request_status = %(status)s, ...
 WHERE id = %(id)s
   AND (
        request_status = %(status)s
        OR request_status NOT IN ('FILLED','CANCELED','REJECTED','FAILED')
   )
```

- When the current status is Terminal and the requested status differs, update zero rows and preserve the current status.
- Allow reapplication of the same Terminal status without error and keep the same status.
- When blocked, return a failure indication rather than reporting success.

Application scope:

- `apply_rvsecncl_parent_status_after_success()` and the CANCEL_ACCEPTED transition use this guarded function.
- Preserve normal non-Terminal → `CANCEL_ACCEPTED` and non-Terminal → `CANCELED` behavior.
- Define this only as local protection on the `submit_rvsecncl_order()` path; do not claim monotonicity across all synchronization outside the target file.

Preserve `update_order_request_status_only()` for other-path compatibility, but use the guarded function for `submit_rvsecncl_order()` post-processing.

### C8. `connector_cancel.py` Documentation Alignment (Requirement 4)

| Item | Handling |
| --- | --- |
| Docstring and CLI help | State that omitted quantity means full cancellation and specified quantity means partial cancellation |
| TR ID and cancellation code | Describe only as confirmed Paper-environment behavior |
| Validation claim | Do not extend into a live-environment validation claim |

Preserve the existing `--yes` block and `cancel_order()` signature.

## Data Models

No new table or column is introduced. Only existing status values are used.

### Execution Order Status Transition

| Status | Meaning |
| --- | --- |
| `REQUESTED` | Awaiting submission; before Claim |
| `SUBMITTING` | Claim succeeded; Broker call in progress (new usage) |
| `SUBMITTED` | Broker submission succeeded |
| `FAILED` | Processing failed |

Transition rules:

- `REQUESTED → SUBMITTING`: Claim conditional UPDATE.
- `SUBMITTING → SUBMITTED`: `mark_strategy_execution_order_submitted`.
- `SUBMITTING → FAILED`: `mark_strategy_execution_order_failed` because it is non-Terminal.
- Terminal (`SUBMITTED`,`FILLED`,`CANCELED`,`REJECTED`,`FAILED`) → `FAILED`: blocked.

### Order Request Status on the Cancellation/Modification Path

| Status | Classification |
| --- | --- |
| `PENDING`, `ACCEPTED`, `CANCEL_ACCEPTED`, `MODIFIED` | Non-Terminal |
| `FILLED`, `CANCELED`, `REJECTED`, `FAILED` | Terminal |

## Correctness Properties

*A property is a characteristic or behavior that must hold across all valid system executions. It is a formal statement that connects a human-readable specification to a machine-verifiable correctness guarantee.*

The core defenses are pure decision functions for quantity normalization, Fatal_Max interpretation, Cancel/Modify resolution, and status monotonicity, plus guarded status transitions. Their broad input spaces make them suitable for property-based tests. Conditional UPDATE concurrency atomicity (2.7) and validation-harness rules (Requirement 6) are handled by integration/smoke tests rather than properties; see Testing Strategy.

The properties below use pure functions or a model-based in-memory fake DB store and never call an actual Broker API, token function, or operating DB.

### Property 1: Valid Quantities Are Normalized Without Value Changes

*For any* integer n of at least 1, `normalize_order_quantity(n)`, `normalize_order_quantity(Decimal(n))`, and `normalize_order_quantity(str(n))` all return exactly integer n without changing its value. The normalized n remains unchanged through the `_submit_order` argument, together with ticker, side, and order method.

**Validates: Requirements 1.1, 3.1, 3.7, 3.8**

### Property 2: Invalid Quantities Are Always Rejected

*For any* 0, negative, non-integer fractional, bool, or non-numeric value, `normalize_order_quantity` raises `QuantityValidationError` without truncation or rounding and returns no normalized quantity.

**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**

### Property 3: Fatal_Max Configuration Resolution Branches Deterministically

*For any* `STEP12_FATAL_MAX_ORDER_QTY` string, unset or `"0"` makes `resolve_fatal_max_order_qty` return disabled (`None`), a positive-integer string returns that integer limit, and a negative/fractional/non-numeric string raises `FatalMaxConfigError` rather than being silently ignored or disabled.

**Validates: Requirements 3.10, 3.12**

### Property 4: Fatal_Max Does Not Adjust Quantity

*For any* positive limit c and normalized quantity q, `check_fatal_max_order_qty` passes only when q <= c and raises `FatalMaxExceededError` when q > c. It never reduces or adjusts q.

**Validates: Requirements 3.11**

### Property 5: Quantity Validation Runs Before Fatal_Max

*For any* invalid quantity, including a magnitude above the limit, `QuantityValidationError` is returned before the Fatal_Max check, and Fatal_Max is not run after quantity validation fails.

**Validates: Requirements 3.13, 3.14**

### Property 6: `execution_status` Guards Never Regress Terminal Status

*For any* initial `execution_status` in an in-memory fake DB store:

- Claim transitions exactly one row to `SUBMITTING` only when status is `REQUESTED` and `connector_order_request_id IS NULL`; otherwise it returns zero rows, preserves status, and marks skip.
- `mark_strategy_execution_order_submitted` transitions only current `SUBMITTING` to `SUBMITTED`.
- `mark_strategy_execution_order_failed` preserves current Terminal status (`SUBMITTED`, `FILLED`, `CANCELED`, `REJECTED`, `FAILED`) rather than overwriting it with `FAILED`.

**Validates: Requirements 2.3, 2.5, 2.6, 2.8, 2.9, 2.10, 2.11**

### Property 7: Cancel/Modify Resolver Builds Payload Quantity According to Contract

*For any* Active_Order quantity a, `resolve_cancel_modify_quantity` returns `("0", "Y")` for CANCEL with omitted qty; `(str(qty), "N")` for CANCEL with integer 1 <= qty <= a; `(str(qty), "N")` for MODIFY with a positive integer qty; and `(str(a), "N")` for MODIFY with omitted qty.

**Validates: Requirements 4.2, 4.3, 4.6, 4.7**

### Property 8: Cancel/Modify Resolver Rejects Contract Violations

*For any* invalid quantity, including CANCEL 0/negative/fractional/exceeding Active_Order quantity and MODIFY values that are not positive integers, `resolve_cancel_modify_quantity` raises `CancelModifyPayloadError` and builds no payload.

**Validates: Requirements 4.4, 4.5, 4.8**

### Property 9: Cancel/Modify Resolver Is Deterministic and Side-Effect Free

*For any* identical `(action_type, qty, active_order_qty)` input, repeated `resolve_cancel_modify_quantity` calls return the same payload or error and perform no Broker API call or DB write.

**Validates: Requirements 4.1, 4.9**

### Property 10: Local Order-Request Status Never Regresses from Terminal

*For any* pair of current `request_status` and requested status, `resolve_request_status_transition` allows updates from non-Terminal status. When current status is Terminal (`FILLED`, `CANCELED`, `REJECTED`, `FAILED`) and requested status matches it, it allows a no-op and preserves that status. When current status is Terminal and requested status differs, it blocks the update, returns a failure indication, and preserves current status.

**Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6**

## Error Handling

### Error Classification and Handling

| Error | Origin | Handling |
| --- | --- | --- |
| `QuantityValidationError` | Quantity normalization/validation | No Broker call; fail the unit order with a quantity error; continue batch |
| `FatalMaxConfigError` | `STEP12_FATAL_MAX_ORDER_QTY` resolution | Explicit configuration error; never silently ignore or disable |
| `FatalMaxExceededError` | Fatal_Max check | No Broker call; do not adjust quantity; return exceeded error |
| `CancelModifyPayloadError` | Cancel/Modify resolver | Build no payload; no Broker call |
| Zero-row Claim | Conditional UPDATE | Duplicate-blocked skip result; preserve status; continue batch |
| Claim DB error | Conditional UPDATE | No Broker call; return failed unit |
| Monotonicity block | `resolve_request_status_transition` | Return a blocked failure indication rather than success |

### Handling Principles

- A unit-order quantity/Fatal_Max/payload/Claim failure does not stop the whole batch. Record the failed/skipped unit and continue remaining targets (Requirement 2.6).
- If a pre-Broker stage fails in quantity validation → Fatal_Max → Claim, no Broker BUY/SELL/cancellation/modification call occurs.
- `FatalMaxConfigError` remains an explicit failure and is never converted to normal or no-order success.
- Preserve existing exception-path recording in `mark_strategy_execution_order_failed`, while adding the Terminal-regression guard.

## Testing Strategy

### Dual Test Approach

- **Property tests**: Validate Properties 1-10 using pure functions or model tests backed by an in-memory fake DB store. Run each property for at least 100 generated examples.
- **Unit/example tests**: Validate specific examples, boundaries, regressions, call order, and call count identified as EXAMPLE/EDGE_CASE in prework.
- **Integration/smoke tests**: Validate the Requirement 6 harness safety rules and documentation contracts.

### Property-Based Test Targets

The feature centers on pure decision logic, so it uses Hypothesis rather than a custom property-test implementation.

| Property | Target function | Generator focus |
| --- | --- | --- |
| P1, P2 | `normalize_order_quantity` | Mixed int, Decimal, str, float, bool, non-numeric |
| P3 | `resolve_fatal_max_order_qty` | Unset, `"0"`, positive-integer, negative/fractional/non-numeric strings |
| P4, P5 | `check_fatal_max_order_qty` plus validation order | Limit/quantity pairs and invalid quantities |
| P6 | `claim_*`, `mark_*` plus fake DB store | Arbitrary initial `execution_status` |
| P7, P8, P9 | `resolve_cancel_modify_quantity` | action, qty None/valid/invalid, active_qty |
| P10 | `resolve_request_status_transition` | Current/requested status pairs |

- Run every property test at least 100 times.
- Tag each test comment with its corresponding property.
- Tag format: **Feature: connector-order-submission-guards, Property {number}: {property_text}**
- Implement each correctness property as one property-based test.

### Example/Integration Test Targets

| Item | Type | Validation |
| --- | --- | --- |
| `SUBMITTING_STATUS` constant/use (2.1) | Example | Value equals `"SUBMITTING"`; references confirmed |
| Claim before call; one call (2.2, 2.4) | Example | Mock call order and `call_count == 1` |
| Mixed-batch continuation (2.6) | Example | Full traversal despite skipped units |
| Dry_Run unchanged (2.12) | Example | Claim/mark `call_count == 0` |
| Claim DB error (2.13) | Example | No Broker call after exception; failed result |
| Identical normalization in both files (3.9) | Example | Reuse the same `normalize` function |
| Full-cancellation payload (4.2) | Example | Reuse and extend `tests/test_full_cancel_payload.py` |
| Post-processing transitions (5.7) | Example | Non-Terminal → CANCEL_ACCEPTED/CANCELED |
| Concurrent Claim atomicity (2.7) | Integration | Actual DB concurrency only with separate approval; default model approximates one winner |
| Docstring/CLI help (4.10, 4.11) | Smoke | Static text-contract confirmation |

The existing `tests/test_full_cancel_payload.py` injects import-only dummy values for `config.py` environment keys and mocks `_request_api` and DB helpers to validate `submit_rvsecncl_order` payloads without Broker/DB calls. New tests reuse this isolation pattern.

### Validation Safety Boundary (Requirement 6)

- Run tests only after replacing `requests.post`, token functions, and DB functions with mocks.
- At validation start, a conftest fixture proactively confirms those targets are mocked. If any target is not mocked, fail before the actual call and report the target.
- Assert exactly zero calls to actual Broker BUY/SELL/cancellation/modification entry points; each protected entry-point `call_count` remains zero. Any call causes validation failure.
- Validation succeeds only with zero Python compile errors, zero existing unit-test failures, zero new contract/property-test failures, and zero Ruff violations. Report the failed stage if any gate fails.
- Commands: `python -m py_compile <대상 파일>`, `pytest` with mock isolation, and `ruff check`. Do not execute actual order entry points with `--execute`, issue tokens, or write to the operating DB.
