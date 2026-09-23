# port-marketconnector Working Rules

This document defines the standards for modifying the Python code, Flask API, configuration, tests, scripts and documentation of the `port-marketconnector` microservice.

A contributor should be able to understand the work scope, execution risk, broker order gates, DB responsibilities, operating environment and validation principles for `port-marketconnector` by reading this document alone.

## 0. Highest-Priority Documentation Readability Rules

Apply this section before all other rules for documentation work.

### 0.0 Scope Limitation

The readability rules apply only to content newly written or directly modified in the current task.

Unless the user explicitly requests a full-document cleanup or comprehensive review, do not perform the following work.

| Item | Default Handling |
| --- | --- |
| Full scan of existing documentation | Do not perform |
| Complete README restructuring | Do not perform |
| Large-scale cleanup of historical CHANGELOG entries | Do not perform |
| Creation of a new scanner · audit tool | Do not perform |
| Creation of a sub-agent · orchestrator | Do not perform |

For adjacent content, review only the same table row, bullet group, or short paragraph.

Preserve existing out-of-scope violations and, when necessary, list them only as follow-up candidates.

### 0.1 Table Rules

New standalone summary tables use two columns by default.

The default headers are `Item / Value`.

More specific two-column headers may be used in the following situations.

| Situation | Preferred Headers |
| --- | --- |
| Validation results | `Item / Result` |
| Changes by file | `File / Change` |
| endpoint description | `Path / Role` |
| entrypoint description | `File / Role` |
| Configuration summary | `Setting / Value` |
| Risk summary | `Risk / Handling` |
| Test results | `Test / Result` |

When adding a row to an existing table, preserve its existing column structure.

Tables with three or more columns are allowed only in the following cases.

| Condition | Handling |
| --- | --- |
| Explicitly requested by the user | Use the requested structure |
| Preserving the existing table is safer | Keep the existing structure |
| Converting a comparison to two columns would lose meaning | Allow an exception |

### 0.2 Table Cell and Sentence Length

- Keep table cells to no more than two sentences.
- Separate multiple values in one cell with `<br>`.
- Do not place three or more facts in a long sentence within one cell.
- Move lengthy evidence outside the table or link to the relevant document.
- Do not create cells longer than 300 characters or lines longer than 500 characters.
- Do not paste raw logs, complete API responses, complete SQL output and AWS responses into documentation.

### 0.3 Documentation Density

Use the following priority when writing documentation.

1. Short summary
2. Short two-column table
3. Short bullets
4. Link to detailed documentation
5. Long-form text

Do not repeat the same facts at length across the README, CHANGELOG, worklog and detailed documentation.

### 0.4 Status Indicators

Use only the following five status indicators.

| Indicator | Meaning |
| --- | --- |
| 🔴 | Prohibited · live · high risk |
| 🟠 | Pending · observing · unconfirmed |
| 🟢 | Complete · successful · ENABLED |
| 🔵 | Reference · information · evidence |
| ⚫ | Not applicable |

Do not add HTML colors when a status indicator is sufficient.

### 0.5 Work Method Restrictions

Perform documentation work in the following order.

1. Confirm the requested scope
2. Read the target file directly
3. Modify only the necessary sections
4. Save as UTF-8 without BOM
5. Perform a short after-check
6. Report a change summary

Unless explicitly requested by the user, do not use the following methods.

- Full workspace scan
- Sub-agent
- Orchestrator
- New scanner
- Content hash matrix
- Temporary files outside the workspace
- Excessive automation scripts

## 1. Scope

### 1.1 Default Working Directory

`C:\Workspaces\port-marketconnector`

### 1.2 Project Role

port-marketconnector is a Python-based MarketConnector microservice that connects the Korea Investment & Securities (KIS) domestic stock API to PostgreSQL.

Its primary responsibilities are as follows.

| Area | Role |
| --- | --- |
| Token | KIS access token issuance · renewal · file management |
| Quote | Current-price · real-time · period quote queries |
| Balance | Account balance and holdings Snapshot |
| Order | Buy · sell · cancel · modify |
| Order Sync | Order event and fill synchronization |
| Strategy Order | Approved strategy order submission |
| Intraday | Snapshot Refresh and hard stop evaluation |
| View API | Query API for port-view |
| Persistence | connector · execution · legacy DB integration |

### 1.3 Default Modification Targets

| Category | Target |
| --- | --- |
| Python | Root `*.py` and package Python files |
| Flask | `connector_app.py` and route-related code |
| Script | `scripts` |
| Tests | `tests` or `test_*.py` |
| Configuration | Environment-variable loader · config-related code |
| Documentation | `README.md` · `CHANGELOG.md` · `docs` |
| Dependencies | requirements · lock · build-related files |
| Operations | EC2 · SSM execution wrappers and related documentation |

Do not modify files that are not included in the current request.

### 1.4 Other Microservices

The following projects are external dependencies.

- `port-view`
- `port-interest-crawler`
- `port-interest-preprocessor`
- `port_strategy_common`
- `port_strategy_decision`
- `port_strategy_research`
- `port_strategy_execution`

Do not modify code or documentation in another microservice unless the current task explicitly requires it.

Cross-service specs under `.kiro` are not automatically included in the port-marketconnector work scope.

## 2. Execution Risk Levels

In this repository, static verification alone can lead to real tokens, broker APIs, orders and DB writes.

Do not run every Python entrypoint like an ordinary local tool.

### 2.1 Highest-risk entrypoints

| File | Risk |
| --- | --- |
| `connector_buy.py` | Can submit a real buy order |
| `connector_sell.py` | Can submit a real sell order |
| `connector_cancel.py` | Can cancel an order |
| `connector_modify.py` | Can modify an order |
| `connector_strategy_order_execute.py` | Can submit a strategy order when `--execute` is used |
| `connector_app.py` | Can perform order · query · DB writes depending on the route call |

Do not run the files above without the user's explicit execution request and confirmation of the target environment.

### 2.2 External API and DB write risk

| File | Risk |
| --- | --- |
| `token_manager.py` | token issuance · renewal · file creation · deletion |
| `connector_balance.py` | Balance API call and Snapshot save |
| `connector_order_check.py` | Order · fill query and DB save |
| `connector_quote_realtime.py` | Quote API call and optional save |
| `connector_quote_closed.py` | Period quote API call and upsert |
| `connector_intraday_snapshot_refresh.py` | Intraday balance API call and Snapshot save |
| `connector_intraday_position_evaluate.py` | Can create check records and READY sell order rows |
| `scripts/run_connector_balance_daily.sh` | Runs the balance refresh entrypoint |

Even when `--no-save`, a dry run, or a query-oriented option is present, the possibility of token issuance and external API calls may remain.

Do not judge safety based on the option name alone.

### 2.3 Static work defaults

When the user does not specify execution, perform only the following work.

- Read files directly
- Safe text search
- Static code analysis
- Documentation edits
- Test code authoring
- Non-executing syntax and import review
- Read-only Git status inspection

## 3. Broker Order Safety Standards

### 3.1 Paper vs live distinction

- Do not confuse paper and live base URLs, accounts and configuration.
- Do not perform order-related execution when the environment is unclear.
- aws-live automatic BUY/SELL is a separate approval and cutover scope.
- Do not treat order execution as a general validation just because it is paper.

### 3.2 Daily Step 12 order

`connector_strategy_order_execute.py --execute` presupposes execution only within the approved Paper order range.

| Item | Standard |
| --- | --- |
| Target | Execution-target orders in `strategy_execution_order` |
| Default behavior | dry run |
| Real submission | Explicit `--execute` |
| Approval gate | `portfolio-paper-daily-step12-17-approval` |
| Follow-up synchronization | `connector_order_check.py` |

Prohibit default changes that bypass the approval gate, hidden fallbacks and automatic `--execute` additions.

### 3.3 Intraday hard stop

| Stage | Responsibility |
| --- | --- |
| Snapshot | `connector_intraday_snapshot_refresh.py` |
| Evaluation | `connector_intraday_position_evaluate.py` |
| Order candidate | Create READY sell execution order |
| Notification | Can invoke the event notifier Lambda |
| Actual order | Handled after a separate approval workflow |

`connector_intraday_position_evaluate.py` is responsible for evaluation and order-candidate creation and does not carry the responsibility for broker sell submission.

Do not add broker order submission logic inside the evaluation entrypoint.

Actual sell orders must proceed only after the `portfolio-paper-intraday-stop-sell-approval` approval gate.

## 4. Flask API Standards

`connector_app.py` provides both an execution API and a View query API.

### 4.1 Execution API

| Path | Risk |
| --- | --- |
| `/api/v1/quotes/realtime` | External quote API · can save to DB |
| `/api/v1/quotes/eod` | External period quote API · can save to DB |
| `/api/v1/accounts/balance` | Balance API · Snapshot save |
| `/api/v1/orders/buy` | Buy order |
| `/api/v1/orders/sell` | Sell order |
| `/api/v1/orders/cancel` | Order cancellation |
| `/api/v1/orders/modify` | Order modification |
| `/api/v1/orders/history` | Order · fill query and DB save |
| `/api/v1/price` | legacy quote alias |

Do not change route names or HTTP methods without an explicit request.

Do not call the execution API as a health check or smoke test.

### 4.2 View API

The View API is query-centric, but its DB connections and the scope of data exposure must be verified.

| Path | Role |
| --- | --- |
| `/api/v1/view/account-summary` | Account summary |
| `/api/v1/view/dashboard` | Dashboard |
| `/api/v1/view/balance/latest` | Latest balance |
| `/api/v1/view/positions/latest` | Latest positions |
| `/api/v1/view/orders` | Order list |
| `/api/v1/view/orders/<order_request_id>` | Order detail |
| `/api/v1/view/order-events` | Order events |
| `/api/v1/view/quotes/realtime/latest` | Latest quote |
| `/api/v1/view/quotes/eod` | Period quote |
| `/api/v1/view/strategy/trades/recent` | Recent strategy trades |

When changing the View API, verify port-view's DTO, endpoint contracts and backward compatibility.

## 5. Python Code Standards

### 5.1 Common principles

- Prefer preserving existing function names, CLI options, API paths and DB semantics.
- Do not add a wrapper that hides the meaning of a broker request.
- Distinguish functions with side effects from pure assembly functions.
- Ensure that module import alone does not trigger token issuance, API calls, orders or DB writes.
- Preserve the `if __name__ == "__main__":` boundary for entrypoint execution.
- Do not convert a failure into a success or a normal no-order result.

### 5.2 Token

- Do not print or document token values.
- Do not read token files as test fixtures.
- Perform token issuance and renewal only in explicit function calls.
- Ensure that import does not trigger token creation or file deletion.
- Do not treat expiry-detection and reissue failures as success.

### 5.3 KIS API requests

- Specify a timeout.
- Verify both the HTTP status and the KIS response code.
- Do not interpret an API failure response as an empty normal result.
- Fail explicitly when a response field is missing or its format differs.
- Do not apply retry identically to order submission and query APIs.
- Do not resubmit an order through automatic retry, which causes duplicates.

### 5.4 Order processing

- First verify idempotency and the existing order request state.
- Record the pre-order DB state and the broker response separately.
- Do not expose the full broker order number in logs and documentation.
- Do not merge partial-fill, unfilled, rejected and canceled states into a single success state.
- Perform cancel and modify after verifying the original order context.

To block duplicate order submission, follow the Claim principles below.

- Acquire an atomic Claim on the same `strategy_execution_order` before Broker submission.
- The Claim target state is `REQUESTED`, and on success it transitions to `SUBMITTING`.
- If the Claim result is 0 rows, treat the order as duplicate or already claimed and do not call the Broker.
- If a Claim DB error occurs, do not call the Broker.
- Do not convert a Claim failure into an order submission success.
- Isolate a single order's Claim or state-record failure as a unit failure so that it does not abnormally terminate the entire follow-up order Batch.
- The actual number of Broker calls must be at most one per successful Claim.
- Do not resubmit the same Execution Order through automatic retry.

### 5.4.1 Quantity validation

- BUY and SELL quantities pass through a common normalization function before the Broker call.
- The allowed quantity is an integer of 1 or greater.
- Reject 0 and negative numbers.
- An integer or a numeric string or Decimal exactly equal to an integer may be converted to an integer.
- Reject fractional quantities without truncating or rounding.
- Reject Boolean without treating it like an integer.
- Reject values that cannot be interpreted as numbers.
- Pass the normalized quantity to the Broker call without changing the value.
- On quantity validation failure, do not perform the Broker call, token call and operating-DB follow-up processing.

The emergency maximum-quantity block standard is as follows.

- `STEP12_FATAL_MAX_ORDER_QTY` is an emergency maximum-quantity block setting applied just before the order.
- When unset or 0, the check is disabled.
- When a positive integer, block orders exceeding that quantity.
- Do not automatically reduce an excess quantity to the maximum value.
- Treat negative, fractional and non-numeric settings as explicit configuration errors.
- Perform general quantity validation before the Fatal Max check.
- Distinguish the pure validation function definition in `connector_order_common.py` from the application responsibility at the actual order submission boundary.
- Do not assume that all order paths automatically apply the common function just because it exists.

### 5.4.2 Cancel/modify Payload contract

| Item | Standard |
| --- | --- |
| Full cancel/modify | `ORD_QTY=0` · `QTY_ALL_ORD_YN=Y` |
| Partial cancel/modify | Requested quantity · `QTY_ALL_ORD_YN=N` |
| Quantity validation | Validated in the pure resolver before the Broker call |
| Error handling | Do not build the Payload and do not call the Broker |

- Do not pass the existing order quantity for a full cancellation.
- The partial cancellation quantity must be a valid integer of 1 or greater.
- Do not interpret the full/partial branch contract differently in the Wrapper and the common function.

### 5.4.3 Terminal state monotonicity

The Terminal states are `SUBMITTED`, `FAILED` and `CANCELED`.

- Do not arbitrarily regress from a Terminal state to a non-Terminal or a different Terminal state.
- Perform a state UPDATE conditioned on an allowed previous state.
- Do not treat a 0-row UPDATE result as a success.
- If the `SUBMITTED` state reflection fails after a successful Broker submission, do not log an order success.
- In that case, do not record a subsequent state such as `SELL_ORDERED` either.
- Do not overwrite a Broker result with `FAILED` when the DB state synchronization fails after Broker success.
- Leave it as an explicit operational error such as `SUBMITTED_STATE_SYNC_FAILED` and handle it Fail-closed.
- Even if an additional error occurs during state recording, isolate the error so that the rest of the Batch order processing can continue.

### 5.5 Order · fill synchronization

Preserve the direct-query-first semantics of `connector_order_check.py`.

Even when the detailed items of the direct response are empty, if a summary is present, handle the direct fallback before a broad search.

Do not incorrectly attribute a broad summary to a specific order's event or fill.

### 5.6 Balance and Position Snapshot

- Preserve the reference timestamps of both the balance snapshot and the position snapshot.
- Verify the cleanup scope for stale positions of the same account and reference date.
- Distinguish whether an empty holdings list is a normal liquidation or an API anomaly.
- Treat it as a mismatch when there is an OPEN strategy position but the KIS holdings result is empty.
- Do not perform Connector writes with a query-only DB user such as `view_app`.

### 5.7 Quote

- `--no-save` presupposes that it may block only the DB save.
- Do not document that it also blocks external API calls.
- Validate the trading day, time zone and stock code.
- Do not change the EOD upsert key or the realtime save semantics.

## 6. Database Standards

### 6.1 Connection

| Item | Value |
| --- | --- |
| Database | `portfolio` |
| Default config loader | `db_config.py` · `get_db_config()` |
| Environment variables | `INTEREST_DB_*` |
| Password | No default |
| search path | `connector, execution, legacy, reference, public` |

In the real environment, a dedicated Connector DB user is used.

Even if a `postgres` default remains in the code or documentation, do not interpret it as an operational-privilege baseline.

### 6.2 Primary schema responsibilities

| Schema | Role |
| --- | --- |
| `connector` | Account · Snapshot · quote · order · fill |
| `execution` | Strategy order · position state · intraday check |
| `legacy` | Backward-compatibility tables |
| `reference` | Stock and common reference data |
| `public` | fallback search path |

### 6.3 Primary write targets

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
- legacy compatibility tables

Verify the actual schema names against the code and DB contracts before using them.

Do not write SQL using assumed column names or assumed table names.

### 6.4 SQL and transaction

- New SQL uses schema-qualified names whenever possible.
- Verify that existing unqualified SQL is consistent with the connection `search_path`.
- Before modifying SQL, verify columns through the actual code, migrations or `information_schema.columns`.
- Verify transaction boundaries for order- and fill-related writes.
- Preserve exception handling and rollback semantics around commit.
- Do not print a success marker after a failed DB operation.

## 7. Configuration and Sensitive Information

### 7.1 Sensitive information

Do not record the following values verbatim in code, documentation, examples or logs.

- access token
- refresh or token-related file content
- KIS app key · app secret
- account number and product code
- DB password and full connection string
- Slack webhook URL
- AWS account-id
- actual ARN
- public IP and RDS hostname
- full broker order number
- command id
- image digest full SHA256

Use the following placeholders when needed.

| Placeholder | Purpose |
| --- | --- |
| `[REDACTED]` | General sensitive information |
| `[REDACTED_ACCOUNT_NO]` | Account number |
| `[REDACTED_BROKER_ORDER_NO]` | broker order number |
| `[REDACTED_ARN]` | ARN |
| `[REDACTED_SECRET_ARN]` | secret ARN |
| `[REDACTED_PUBLIC_IP]` | public IP |
| `[REDACTED_RDS_HOST]` | RDS hostname |
| `[REDACTED_COMMAND_ID]` | SSM command id |

### 7.2 Configuration changes

When changing a configuration key or loader, also verify the following.

1. `config.py`
2. `db_config.py`
3. The relevant entrypoint
4. Flask route
5. README
6. EC2 · SSM environment-variable injection
7. token and secret storage location

Do not read the actual values of `config.py`, token files and local secret files and move them into the documentation.

## 8. EC2 · SSM Operational Standards

The MarketConnector operational path is based on EC2 and SSM RunCommand.

### 8.1 Current operational structure

| Item | Value |
| --- | --- |
| Compute | MarketConnector EC2 |
| Remote execution | SSM RunCommand |
| Daily order | Executed after Step Functions approval |
| Intraday | Scheduler → SSM |
| Notification | event notifier Lambda |
| EC2 lifecycle | Scheduler start · stop |

IAM Role, policy and Scheduler names are operational identity information; when changing them, verify consistency with cross-service documentation.

### 8.2 Writing operational commands

- Use the `list/describe → extract variable → subsequent verification` flow for dynamic identifiers.
- Do not record actual instance ids, ARNs, command ids and account-id in the documentation.
- Use a UTF-8 No BOM file and the `--parameters file://...` method for multiline SSM commands.
- Do not mix Windows and Linux shell syntax.
- Ensure that stdout does not include tokens, account numbers and broker numbers.
- Verify both the SSM status and the process exit code.

Do not issue an actual SSM RunCommand without the user's explicit request.

### 8.3 DevOps Artifact standards

- The deployment Artifact is a Git SHA-based Versioned ZIP.
- The Bundle Source is a committed Git blob.
- Do not zip Working Tree files directly.
- Base it on the resolved Source SHA even on a Detached Head.
- The Bundle include list is managed in `.devops/bundle/include.txt`.
- The Bundle generator is `.devops/scripts/build_bundle.py`.
- Update the include manifest and contract test together for new operating files.
- Do not include tokens, secrets, cache, runtime files and build output in the Bundle.
- Verify the SHA-256 consistency between the Local Bundle and the S3 Artifact.

### 8.4 CodeDeploy safety standards

- Perform EC2 In-place deployment only through CodeDeploy Lifecycle Hooks.
- Do not overwrite the operating Application path by manual copy.
- Create an existing Source Backup before deployment.
- Maintain the Deployment Lock during deployment.
- If a Connector execution process exists, block deployment according to the safety standard.
- Do not allow duplicate Connector processes.
- Do not automatically start the Connector in ApplicationStart.
- Do not release the Deployment Lock before ValidateService succeeds.
- On failure, do not start a new Application process.
- Do not call the Flask execution API or order entrypoints for deployment validation.

### 8.5 Runtime and Secret protection standards

- Do not hard-code the KIS App Key, Secret and account number in `config.py`.
- The Runtime environment uses environment variables and an AWS Secret injection structure.
- Do not include the Runtime token file in the Bundle.
- Preserve the existing token file during deployment, Rollback and redeployment.
- `config.py` retains permission 600 after deployment.
- Do not record Secret values in Build, Hook, SSM and validation output.
- Secret Rotation is a separate explicit work scope and is not included in this completion criteria.

### 8.6 Wrapper standards

- The Daily Wrapper is `scripts/run_connector_balance_daily.sh`.
- The Intraday Wrapper is `scripts/run_intraday_snapshot_and_evaluate.sh`.
- The Intraday order is Snapshot Refresh → Position Evaluate.
- The Wrapper maintains the Strict Shell settings.
- Shell files are managed with LF.
- Prefer `bash -n` and structural validation for deployment validation.
- Do not actually run the Wrapper without an explicit operational execution request.

### 8.7 Rollback standards

- Identify the last successful state with the pre-deployment Backup and the S3 Versioned Artifact.
- Perform the Backup Rollback with the Deployment Lock held and 0 processes.
- After restoration, verify the key Source SHA-256, Python Compile and Wrapper Syntax.
- The validated identical S3 Revision must be redeployable.
- Preserve token, secret and runtime files during Rollback and redeployment.
- Do not call the order API even during Rollback validation.

### 8.8 Safe validation scope

Allowed:

- Python Compile
- Ruff
- Mock-based Pytest
- Bundle Contract Test
- ZIP structure inspection
- Shell Syntax inspection
- File existence and permission checks
- SHA-256 consistency check
- CodeDeploy Hook status check
- Connector process count check
- Deployment Lock status check

Prohibited without explicit approval:

- Flask order API calls
- Executing BUY, SELL, cancel and modify
- `connector_strategy_order_execute.py --execute`
- Actual operational execution of the Daily Balance Wrapper
- Actual operational execution of the Intraday Wrapper
- New token issuance and forced Rotation
- Smoke Test that involves operating-DB writes

### 8.9 main Push automatic Release standards

main Push is a GitHub Actions Release Trigger, and the `workflow_dispatch` manual run path is also maintained.

The full automatic flow is as follows.

| Stage | Content |
| --- | --- |
| Trigger | main Push or manual run |
| Build | CodeBuild Quality Gate and Git SHA Versioned ZIP |
| Artifact | Dedicated private Versioned S3 · fixed S3 Version ID |
| Compute preparation | Verify EC2 state · verify SSM Online |
| Deployment | S3 Versioned Revision-based CodeDeploy In-place |

The EC2 state-preservation principle is as follows.

- Verify the EC2 state before deployment.
- If stopped, it may be temporarily started for the Release.
- If already running, keep the existing state.
- Only EC2 that the Workflow started directly is restored to the original stopped state after the Release ends.
- This lifecycle is Compute preparation for deployment and does not mean Connector Application execution or order execution.

Maintain the No-Order Release Boundary.

- Maintain the existing principle of not automatically starting the Connector in ApplicationStart.
- Do not execute orders via SSM SendCommand in the Release Workflow.
- Do not run the BUY · SELL · CANCEL · MODIFY entrypoints and `--execute` as a Release smoke test.
- Validate the deployment without an actual Broker order API call.
- Maintain the existing SSM · Scheduler-based Runtime execution responsibility.

The OIDC · IAM Action list and AWS policy implementation details are within the port-devops scope and are not recorded in this document.

## 9. Execution Restrictions

Do not perform the following work unless explicitly requested by the user.

| Category | Prohibited Work |
| --- | --- |
| Flask | Running the connector server |
| Token | Issuance · renewal · deletion · printing |
| KIS | Quote · balance · order · fill API calls |
| Broker | Buy · sell · cancel · modify |
| DB | DDL · DML · migration · psql |
| AWS | EC2 · SSM · Lambda · Scheduler execution or change |
| Slack | Actual webhook or notifier calls |
| Operations | Running Daily · Intraday entrypoints |
| Git | add · commit · push · reset · restore |

Run read-only validation only within the user-requested scope.

## 10. Tests and Validation

### 10.1 Basic principles

- Prefer unit tests that are possible without real KIS and DB.
- Isolate `requests`, the token loader, DB connections and Lambda calls with mocks or stubs.
- Do not use the real base URL and account in order tests.
- Do not create or delete token files during tests.
- Do not inject actual operational environment variables into tests.

### 10.2 Minimum validation by change

| Change Target | Minimum Validation |
| --- | --- |
| Python syntax | `py_compile` or safe compile check |
| Pure functions | Relevant unit test |
| Flask route | test client + external-dependency mock |
| KIS client | request · response parsing mock |
| Order logic | dry-run · idempotency · atomic Claim · duplicate-submission-block test |
| Order quantity | Quantity Property Test · Fatal Max boundary Test |
| Cancel/modify | Full 0/Y · partial quantity/N Payload Contract Test |
| Order status | Terminal state monotonicity Test |
| Order regression | Regression Test for state-sync failure after Broker success · Claim and state-record double failure |
| Call count | Verify 0 Broker · token · operating-DB calls and 1 Broker Mock call per successful Claim |
| Repository | SQL · parameter · transaction test |
| Token | File and HTTP mock |
| Documentation | Links · facts · readability |
| Shell | Syntax and argument-passing static check |

In order-boundary safety features, Property Tests are a required validation, not optional.

For files with execution-risk imports, first check for side effects even during compile.

Do not record validation that could not be run as complete; record the reason it was not run.

## 11. Documentation Management Rules

### 11.1 README.md

The README describes the current structure and operational AS-IS of port-marketconnector.

Content to include in the README:

- Project role
- Execution risk
- Flask API
- Primary entrypoints
- Token · Quote · Balance · Order flow
- Daily and Intraday operational boundaries
- DB and schema
- EC2 · SSM structure
- Configuration and security
- Links to detailed documentation

Do not copy the internal implementation of other microservices and the full Step Functions definition at length.

### 11.2 CHANGELOG.md

The CHANGELOG records only the primary changes to port-marketconnector code and documentation.

- Add the latest date at the top.
- Use `Added`, `Changed`, `Fixed`, `Removed`, `Security` as needed.
- Record only actual Connector changes.
- Do not record one-time command ids, execution ids and raw logs.
- Use primarily two-column tables in new sections.
- Preserve historical entries unless separately requested otherwise.

### 11.3 docs

Detailed explanations are separated into the currently maintained `docs` documents.

Before creating a new document, first determine whether it can be incorporated into the existing README, CHANGELOG and docs.

Do not create date-specific `docs/worklog/*.md` files.

Record code and documentation change history in `CHANGELOG.md`.

### 11.4 Automatic source-file-catalog.md updates

When `docs/source-file-catalog.md` exists and one of the following changes occurs, determine within the same task whether it must be updated.

| Change | Handling |
| --- | --- |
| Create · delete · rename a primary Python file | Update the catalog |
| Change Flask route and entrypoint responsibility | Update roles and risks |
| Change the package · scripts directory | Update paths and structure |
| Change config · token · DB loader roles | Update the configuration section |
| Change EC2 · SSM wrapper roles | Update the operations section |
| Create · delete a document or change its role | Update the Documents section |
| Change only internal implementation · same responsibility | May be omitted |

The catalog is not an inventory of every file.

Record only files, grouped paths and responsibilities meaningful to operations and maintenance.

## 12. Git Rules

Only read-only status inspection is allowed by default.

| Allowed | Prohibited |
| --- | --- |
| `git status --short`<br>`git diff --stat`<br>`git diff --check` | `git add`<br>`git commit`<br>`git push`<br>`git reset`<br>`git restore`<br>`git checkout`<br>`git stash` |

Do not create a commit unless explicitly requested by the user.

## 13. Completion Report

At completion, report only the following and keep it concise.

| Item | Content |
| --- | --- |
| Changed files | Files actually modified |
| Key changes | Summary of functional or documentation changes |
| Risk paths | API · order · DB paths not executed |
| Validation | Static checks and tests performed |
| Not performed | Validation that could not be run |
| Security | Whether sensitive values were recorded verbatim |
| Follow-up | Only items that actually remain |

Distinguish work performed by the operator from work performed by Kiro.

## 14. Completion Checklist

- [ ] Were only the requested port-marketconnector files modified?
- [ ] Were other microservices and `.kiro` files left unchanged unless needed?
- [ ] Were the highest-priority documentation readability rules applied?
- [ ] Do new standalone tables use two columns by default?
- [ ] Were long cells and lines avoided?
- [ ] Were token · KIS · broker · DB side effects distinguished?
- [ ] Were paper and live environments not confused?
- [ ] Was the Daily Step 12 approval gate not bypassed?
- [ ] Were intraday evaluation and actual sell submission responsibilities separated?
- [ ] Were the Flask execution API and View API distinguished?
- [ ] Was the possibility of duplicate orders through order retry avoided?
- [ ] Was DB schema and transaction consistency verified?
- [ ] Were sensitive values kept out of the documentation verbatim?
- [ ] Were failures not recorded as normal or no-order successes?
- [ ] Was validation appropriate to the change scope performed?
- [ ] If file structure or responsibility changed, was `docs/source-file-catalog.md` checked?
- [ ] Were no date-specific `docs/worklog/*.md` files created?
- [ ] Were files saved as UTF-8 without BOM?
- [ ] Were only actual changes reflected in the README and CHANGELOG?
