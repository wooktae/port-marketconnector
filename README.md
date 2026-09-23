# port-marketconnector

A Python-based MarketConnector microservice that connects the Korea Investment & Securities (KIS) domestic stock API to PostgreSQL.

It processes quote, balance, position, order, fill, strategy-order and intraday-check data, and provides query APIs for port-view to consume.

The execution paths in this repository can lead to token issuance, external API calls, order submission and DB writes, so static analysis and documentation work are the default mode.

## Current State

| Item | Value |
| --- | --- |
| Runtime environment | AWS Paper |
| Compute | MarketConnector EC2 |
| Remote execution | SSM RunCommand |
| Daily order | Executed after Step Functions approval |
| Intraday | Scheduler-based Snapshot Refresh · hard stop evaluation |
| broker order | Submitted only through the approved path |
| Duplicate-order protection | Execution Order atomic Claim |
| Order-quantity protection | Integer validation · Fatal Max |
| Cancellation contract | Full 0/Y · partial quantity/N |
| State protection | Terminal state monotonicity |
| View API | port-view query integration |
| Release Trigger | main Push · `workflow_dispatch` manual run |
| CI | GitHub Actions → CodeBuild Quality Gate |
| Deployment Artifact | Git SHA-based Versioned ZIP Bundle |
| Artifact store | MarketConnector-dedicated versioning-enabled private S3 |
| Deployment method | CodeDeploy EC2 In-place |
| Deployment validation | Manifest and operating-file SHA-256 consistency |
| Deployment target | Existing MarketConnector EC2 Application path |
| Deployment safety | Deployment Lock · Connector single-execution Guard |
| Rollback | Restore pre-deployment Backup Source |
| Redeployment | Validated identical S3 Versioned Revision |
| Application start | No automatic start during deployment |
| Runtime token | Preserved across deployment and Rollback |
| Order validation | Static and no-order Smoke Test without real orders |
| Secret | Environment variables and AWS Runtime injection |
| aws-live BUY/SELL | 🔴 Not started |
| Documentation baseline principle | Static verification without token · order · DB execution |

> A Paper environment can still submit real orders. Do not call order-related entrypoints or the Flask execution API as a general smoke test.

## Technical Stack

| Item | Value |
| --- | --- |
| Language | Python |
| API | Flask |
| HTTP | Requests |
| Database Driver | psycopg v3 |
| Database | PostgreSQL |
| Broker | KIS domestic stock API |
| Compute | EC2 |
| Remote Execution | SSM RunCommand |
| CI Trigger | GitHub Actions |
| Build | CodeBuild |
| Artifact | Versioned ZIP Bundle |
| Artifact Store | Amazon S3 |
| Deploy | AWS CodeDeploy |

## Responsibility Boundary

### In-scope responsibilities

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

### Out-of-scope responsibilities

| Item | Actual responsible area |
| --- | --- |
| Strategy generation | Strategy Research |
| Strategy decision | Strategy Decision |
| Order planning | Strategy Execution |
| Daily orchestration | Step Functions |
| Automatic execution timing | EventBridge Scheduler |
| View screens | port-view |
| Data collection · preprocessing | Crawler · Preprocessor |
| aws-live cutover | Separate approval scope |

## Primary Files

| File | Role |
| --- | --- |
| `connector_app.py` | Flask execution API and View API |
| `token_manager.py` | token file · issuance · renewal |
| `config.py` | Configuration module for KIS and Paper account environment-variable contracts |
| `db_config.py` | DB environment-variable loader |
| `connector_db.py` | connector · execution · legacy repository helper |
| `connector_view_service.py` | View API response assembly |
| `connector_order_common.py` | Common order processing |
| `connector_buy.py` | Buy order wrapper |
| `connector_sell.py` | Sell order wrapper |
| `connector_cancel.py` | Order cancellation wrapper |
| `connector_modify.py` | Order modification wrapper |
| `connector_balance.py` | Daily balance and position Snapshot |
| `connector_order_check.py` | Order event and fill synchronization |
| `connector_quote_realtime.py` | Real-time quote query |
| `connector_quote_closed.py` | Period quote query |
| `connector_strategy_order_execute.py` | Daily strategy order submission |
| `connector_intraday_snapshot_refresh.py` | Intraday Snapshot Refresh |
| `connector_intraday_position_evaluate.py` | Intraday hard stop evaluation |
| `scripts/run_connector_balance_daily.sh` | Daily balance Snapshot wrapper |
| `scripts/run_intraday_snapshot_and_evaluate.sh` | Intraday Snapshot Refresh → Position Evaluate wrapper |
| `appspec.yml` | CodeDeploy In-place deployment and Lifecycle Hook wiring |
| `docs/source-file-catalog.md` | Primary files and responsibilities |

For detailed roles, see the [source file catalog](docs/source-file-catalog.md).

## Execution Risk Levels

### Highest risk

| File | Risk |
| --- | --- |
| `connector_buy.py` | Can place a real buy order |
| `connector_sell.py` | Can place a real sell order |
| `connector_cancel.py` | Can cancel an order |
| `connector_modify.py` | Can modify an order |
| `connector_strategy_order_execute.py` | Can place a strategy order when `--execute` is used |
| `connector_app.py` | Can perform order · API · DB writes depending on the route |

### External API and DB write risk

| File | Risk |
| --- | --- |
| `token_manager.py` | token issuance · renewal · file change |
| `connector_balance.py` | Balance API · Snapshot save |
| `connector_order_check.py` | Order · fill query and save |
| `connector_quote_realtime.py` | Quote API · optional save |
| `connector_quote_closed.py` | Period quote API · upsert |
| `connector_intraday_snapshot_refresh.py` | Intraday balance API · Snapshot save |
| `connector_intraday_position_evaluate.py` | Check record · can create READY order row |

`--no-save` or dry run does not mean all side effects are blocked.

The possibility of token issuance and external API calls must be verified separately.

## Flask API

`connector_app.py` provides both the execution API and the View API.

### Execution API

| Path | Role · Risk |
| --- | --- |
| `GET /api/v1/quotes/realtime` | Real-time quote query · can save to DB |
| `GET /api/v1/quotes/eod` | Period quote query · can save to DB |
| `GET /api/v1/accounts/balance` | Balance query · Snapshot save |
| `POST /api/v1/orders/buy` | Buy order |
| `POST /api/v1/orders/sell` | Sell order |
| `POST /api/v1/orders/cancel` | Order cancellation |
| `POST /api/v1/orders/modify` | Order modification |
| `GET /api/v1/orders/history` | Order · fill query and save |
| `GET /api/v1/price` | legacy quote alias |

Do not call the execution API for health checks or documentation verification.

### View API

| Path | Role |
| --- | --- |
| `GET /api/v1/view/account-summary` | Account summary |
| `GET /api/v1/view/dashboard` | Dashboard |
| `GET /api/v1/view/balance/latest` | Latest balance |
| `GET /api/v1/view/positions/latest` | Latest positions |
| `GET /api/v1/view/orders` | Order list |
| `GET /api/v1/view/orders/<order_request_id>` | Order detail |
| `GET /api/v1/view/order-events` | Order events |
| `GET /api/v1/view/quotes/realtime/latest` | Latest quote |
| `GET /api/v1/view/quotes/eod` | Period quote |
| `GET /api/v1/view/strategy/trades/recent` | Recent strategy trades |

The View API is query-centric, but its DB connections and the scope of sensitive-information exposure must be verified.

## Token

`token_manager.py` can perform the following actions.

| Item | Content |
| --- | --- |
| Read | Existing token file |
| Issue | KIS token endpoint |
| Renew | Reissue after expiry detection |
| Delete | Remove token file |
| Save | New token file |

Do not run it during documentation work or static analysis.

Do not print or document token values or token file content.

## Balance and Position Snapshot

### Daily Snapshot

`connector_balance.py` calls the KIS balance API and can save account and holdings data.

| Target | Role |
| --- | --- |
| `connector.connector_balance_snapshot` | Account summary |
| `connector.connector_position_snapshot` | Held positions |
| `connector.connector_api_call_log` | API call record |
| legacy balance · holdings | Backward compatibility |

It can clean up stale position rows for the same account and reference date based on current holdings.

An empty holdings result must be distinguished between a legitimate liquidation and a KIS response anomaly.

### Intraday Snapshot

`connector_intraday_snapshot_refresh.py` refreshes the intraday Snapshot.

| Situation | Handling |
| --- | --- |
| KIS holdings present | Save Snapshot |
| KIS holdings absent · no OPEN position | Can terminate normally |
| KIS holdings absent · OPEN position present | mismatch failure |

This entrypoint is not responsible for strategy decisions or broker order submission.

## Quote

| File | Role |
| --- | --- |
| `connector_quote_realtime.py` | Current-price · real-time quote |
| `connector_quote_closed.py` | Period quote · EOD upsert |

`--no-save` may block only the DB save.

The possibility of external KIS calls and token issuance remains.

## Order Processing

### Buy · Sell

`connector_buy.py` and `connector_sell.py` use the common logic in `connector_order_common.py`.

Order flow:

1. Check the order target and existing state
2. Quantity normalization and Fatal Max check
3. `REQUESTED → SUBMITTING` atomic Claim
4. Submit only successfully claimed orders to the KIS Broker
5. Save the Broker result
6. Reflect the `SUBMITTED` or failure result via an allowed state transition
7. Follow-up order · fill synchronization

Key principles:

- Block duplicate submission of the same Execution Order.
- Skip a 0-row Claim without a Broker call.
- Do not auto-correct quantities on quantity errors or Fatal Max excess; block them instead.
- Do not report a normal order success when the DB state synchronization fails after Broker success.
- Isolate a single order's failure as a unit result and keep processing the rest of the Batch.
- Do not create duplicate orders through automatic retry.

### Cancel · Modify

| File | Role |
| --- | --- |
| `connector_cancel.py` | Cancel an existing order |
| `connector_modify.py` | Modify an existing order |

| Category | Payload |
| --- | --- |
| Full cancel/modify | `ORD_QTY=0` · `QTY_ALL_ORD_YN=Y` |
| Partial cancel/modify | Requested quantity · `QTY_ALL_ORD_YN=N` |

- Do not pass the original order quantity for a full cancellation.
- Validate the Payload quantity before the Broker call.
- Perform the action after verifying the original order context and broker order state.

### Order · Fill Synchronization

`connector_order_check.py` can save the following data.

| Target | Role |
| --- | --- |
| `connector.connector_order_event` | Order status events |
| `connector.connector_fill` | Fills |
| `connector.connector_api_call_log` | API call record |
| legacy trade orders | Backward compatibility |

In a direct query, even when the detailed `output1` is empty, if the `output2` summary is present, handle the direct fallback before a broad search.

Do not misattribute a broad summary to a specific order's event or fill.

## Daily Strategy Order

`connector_strategy_order_execute.py` submits approved strategy orders to the KIS Paper account.

| Item | Value |
| --- | --- |
| Target | Strategy execution orders |
| Default behavior | dry run |
| Real submission | `--execute` |
| Approval gate | `portfolio-paper-daily-step12-17-approval` |
| Submission claim | `REQUESTED → SUBMITTING` atomic Claim |
| Duplicate prevention | Broker call only for successfully claimed orders |
| Quantity validation | Integer quantity · Fatal Max |
| State transition | Block Terminal state regression |
| Failure isolation | Continue the Batch after a unit order failure |
| Save | order request · API call log |
| Follow-up | `connector_order_check.py` |

`SUBMITTING` is a claim state before Broker submission, not a Broker success state.

Do not use `--execute` in a state that has not passed the approval workflow.

## Intraday hard stop

The intraday flow separates evaluation from actual order submission.

| Stage | Responsibility |
| --- | --- |
| Snapshot | `connector_intraday_snapshot_refresh.py` |
| Evaluation | `connector_intraday_position_evaluate.py` |
| Result record | `execution.strategy_intraday_position_check` |
| Order candidate | `execution.strategy_execution_order` READY sell |
| Notification | event notifier Lambda |
| Actual sell | After a separate approval workflow |

Even when a hard stop condition is met, the evaluation entrypoint itself does not submit a broker order.

Actual sell submission is handled after `portfolio-paper-intraday-stop-sell-approval`.

## EC2 and SSM Operations

MarketConnector runs on EC2 via SSM RunCommand.

### Operational flow

| Item | Value |
| --- | --- |
| EC2 start | 07:50 KST Scheduler |
| Daily execution | Step Functions → SSM |
| Intraday execution | 09:10–15:50 KST · 10-minute interval |
| Execution order | Snapshot Refresh → Position Evaluate |
| Notification | event notifier Lambda |
| EC2 stop | 15:50 KST Scheduler |

### Operational identifiers

| Item | Name |
| --- | --- |
| EC2 Role | `portfolio-paper-marketconnector-ec2-role` |
| Lambda invoke policy | `portfolio-paper-marketconnector-event-notifier-invoke` |
| EC2 start Scheduler | `portfolio-paper-ec2-start-0750-kst` |
| EC2 stop Scheduler | `portfolio-paper-marketconnector-stop-1550-kst` |
| Intraday Scheduler | `portfolio-paper-intraday-snapshot-evaluate-10min-kst` |

Do not record actual ARNs, instance ids, command ids, account-id and public IPs in the documentation.

## DevOps Deployment Structure

main Push automatically runs the GitHub Actions Release, and the `workflow_dispatch` manual run path is also maintained.

Deployment builds the Artifact based on the committed Git state and reflects it In-place onto EC2 via CodeDeploy.

```
main Push → GitHub Actions → CodeBuild Quality Gate → Git SHA Versioned ZIP → Private Versioned S3 → EC2 state preparation → CodeDeploy EC2 In-place → No-Order Validate
```

### EC2 state preservation

| Item | Value |
| --- | --- |
| Before deployment | Verify EC2 state · SSM Online |
| stopped | Can start temporarily for the Release |
| running | Keep the existing state |
| Restore target | Only EC2 that the Workflow started directly is restored to stopped |
| Meaning | Compute preparation for deployment · not Connector execution |

| Item | Value |
| --- | --- |
| Bundle basis | committed Git blob |
| Excluded | Working Tree state · line-ending conversion dependency |
| Determinism | Identical Bundle on Main and Detached Head |
| Bundle include list | `.devops/bundle/include.txt` |
| Bundle generator | `.devops/scripts/build_bundle.py` |
| Artifact path | Unified to the MarketConnector-dedicated deployment Bucket and Prefix |
| IAM Policy | Reuse the existing dedicated S3 IAM Policy |
| Object Key | Git SHA-based |
| Revision | CodeDeploy Revision with a fixed S3 Version ID |
| Manifest validation | Verify `.codedeploy/staging/deployment-manifest.json` Source SHA |
| File consistency | Compare Manifest core-file SHA-256 with operating src file SHA-256 |
| Runtime preservation | Preserve `config.py` and Runtime token file |
| Execution during deployment | 0 Connector processes and 0 order API calls |
| Hook responsibilities | Backup · install · permissions · Lock · validation |
| Application execution | CodeDeploy does not start it automatically |
| Actual execution path | Existing SSM · Scheduler operations |

### Lifecycle Hook

| Hook | Role |
| --- | --- |
| ApplicationStop | Verify running processes and deployment-safe state |
| BeforeInstall | Deployment Lock and existing Source Backup |
| AfterInstall | Bundle install · apply permissions · preserve Runtime files |
| ApplicationStart | Keep the safe state without automatic start |
| ValidateService | Validate Compile · Wrapper Syntax · required files · single execution · Lock release |

Hook Script internals are managed in the `codedeploy/` files and are not copied at length into the documentation.

### Rollback

| Item | Value |
| --- | --- |
| CodeDeploy failure | Auto Rollback |
| Operational validation | Restore the pre-deployment Backup Source |
| Redeployment | Validated identical S3 Versioned Revision |
| Runtime token | Preserved across Rollback and redeployment |
| Application · orders | Not executed during Rollback and redeployment |

## Validation Results

DevOps completion validation results as of 2026-08-01.

| Item | Result |
| --- | --- |
| Python Compile | 25 succeeded |
| Pytest | 21 succeeded |
| Ruff | Succeeded |
| Bundle files | 28 confirmed |
| Bundle forbidden files | 0 |
| Lifecycle Hook | All succeeded |
| First In-place deployment | Succeeded |
| Backup Rollback | Succeeded |
| Same-Revision redeployment | Succeeded |
| Runtime token preservation | Succeeded |
| Connector processes | 0 |
| Order API calls | 0 |

### 2026-08-04 order-boundary safety improvement validation

| Item | Result |
| --- | --- |
| Full Pytest | 55 succeeded |
| Property Test | Succeeded |
| Ruff | Succeeded |
| Python Compile | Succeeded |
| CodeBuild | Succeeded |
| Versioned Artifact | Created successfully |
| CodeDeploy Lifecycle | All succeeded |
| Manifest Source SHA | Matched |
| Core-file SHA-256 | 3 matched |
| Runtime files | Preserved |
| Connector processes | 0 |
| Broker order API | 0 |

### 2026-08-11 main Push automatic Release completion validation

| Item | Result |
| --- | --- |
| Full Pytest | 56 succeeded |
| CodeBuild Quality Gate | Succeeded |
| Versioned Artifact creation·lookup | Succeeded |
| EC2 state check · SSM Online | Succeeded |
| CodeDeploy EC2 In-place | Succeeded |
| main Push automatic Release | Succeeded |
| No-Order Release Boundary | Maintained |
| Connector automatic execution | 0 |
| Broker order API | 0 |

Do not record the raw Deployment ID, SSM Command ID, S3 Version ID and full SHA-256 values.

## Database

### Connection basis

| Item | Value |
| --- | --- |
| Database | `portfolio` |
| Config loader | `db_config.py` · `get_db_config()` |
| Environment variables | `INTEREST_DB_*` |
| Password | No default |
| search path | `connector, execution, legacy, reference, public` |

Production environments use a Connector-dedicated DB user.

Do not interpret the example default user in the documentation as an operational-privilege baseline.

### Schema responsibilities

| Schema | Role |
| --- | --- |
| `connector` | Account · Snapshot · quote · order · fill |
| `execution` | Strategy order · position state · intraday check |
| `legacy` | Backward compatibility |
| `reference` | Stock and common reference data |
| `public` | fallback search path |

New SQL uses schema-qualified names whenever possible.

Existing unqualified SQL operates based on the connection `search_path`.

### Primary write tables

| Area | Table |
| --- | --- |
| API Log | `connector.connector_api_call_log` |
| Balance | `connector.connector_balance_snapshot` |
| Position | `connector.connector_position_snapshot` |
| Quote | `connector.connector_quote_realtime` · `connector.connector_quote_eod` |
| Order | `connector.connector_order_request` |
| Order Event | `connector.connector_order_event` |
| Fill | `connector.connector_fill` |
| Strategy Order | `execution.strategy_execution_order` |
| Intraday Check | `execution.strategy_intraday_position_check` |

Verify the actual columns and tables against the code and DB contracts.

Do not write SQL using assumed column names.

## Configuration

No dependency lock file is currently confirmed.

Example package install:

```powershell
pip install flask requests psycopg
```

Sensitive information is managed outside source control.

### DB environment variables

| Environment variable | Default · Role |
| --- | --- |
| `INTEREST_DB_HOST` | `localhost` |
| `INTEREST_DB_PORT` | `5433` |
| `INTEREST_DB_NAME` | `portfolio` |
| `PORTFOLIO_DB_NAME` | `portfolio` |
| `INTEREST_DB_USER` | Per-environment Connector DB user |
| `INTEREST_DB_PASSWORD` | No default |

### KIS configuration

`config.py` reads KIS and Paper account values as an environment-variable contract. It does not hard-code the App Key, Secret and account number.

| Item | Handling |
| --- | --- |
| App key | Environment variable · AWS Runtime injection |
| App secret | Environment variable · AWS Runtime injection |
| Base URL | Per-environment configuration |
| Account number | Environment-variable injection |
| Product code | Environment-variable injection |
| Token | Runtime file · excluded from Bundle · preserved during deployment |
| Post-deployment permissions | `config.py` 600 |

Do not move `config.py`, token file and local secret values into the documentation.

## Execution

Do not run unless the operator explicitly intends an external API call, an order, or a DB write.

The following command is an execution-risk example.

```powershell
python connector_app.py
```

After the Flask server starts, route calls can lead to KIS API and DB writes.

## Do-Not-Run List

Do not run the following entrypoints during documentation work and static analysis.

- `token_manager.py`
- `connector_app.py`
- `connector_buy.py`
- `connector_sell.py`
- `connector_cancel.py`
- `connector_modify.py`
- `connector_balance.py`
- `connector_order_check.py`
- `connector_quote_realtime.py`
- `connector_quote_closed.py`
- `connector_strategy_order_execute.py`
- `connector_intraday_snapshot_refresh.py`
- `connector_intraday_position_evaluate.py`

## Security

Do not record the following values verbatim in code, documentation or logs.

- access token
- KIS app key · app secret
- actual account number
- DB password and connection string
- full broker order number
- Slack webhook URL
- AWS account-id
- actual ARN
- public IP
- RDS hostname
- SSM command id

Placeholder:

| Value | Placeholder |
| --- | --- |
| General sensitive information | `[REDACTED]` |
| Account number | `[REDACTED_ACCOUNT_NO]` |
| broker order number | `[REDACTED_BROKER_ORDER_NO]` |
| ARN | `[REDACTED_ARN]` |
| secret ARN | `[REDACTED_SECRET_ARN]` |
| public IP | `[REDACTED_PUBLIC_IP]` |
| RDS hostname | `[REDACTED_RDS_HOST]` |
| command id | `[REDACTED_COMMAND_ID]` |

## Documentation

| Document | Role |
| --- | --- |
| `AGENTS.md` | port-marketconnector working rules |
| `README.md` | Current structure and operational AS-IS |
| `CHANGELOG.md` | Primary change history |
| `docs/source-file-catalog.md` | Primary files and responsibilities |

Do not create date-specific `docs/worklog/*.md` files.

Record documentation change history in `CHANGELOG.md`.
