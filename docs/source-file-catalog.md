# Source File Catalog

A document for quickly checking the roles of the primary files and directories of port-marketconnector.

It is not an inventory that lists every file, but records only the items needed to understand the structure and judge change impact.

It uses paths relative to the repository root and excludes cache, token files and one-time outputs.

## Usage Principles

| Item | Value |
| --- | --- |
| Basis | Current port-marketconnector code and operational structure |
| Included | Primary entrypoints · common helpers · operationally impactful files |
| Excluded | cache · token · debug dump · one-time output |
| Update | When a file's path · responsibility · execution risk changes |
| Omit | When only internal implementation changes and the file responsibility is the same |
| Sensitive information | No verbatim token · account · secret · ARN · endpoint |

## Root

| File | Role |
| --- | --- |
| `AGENTS.md` | MarketConnector code and documentation working rules |
| `README.md` | Current structure · execution risk · operational AS-IS |
| `CHANGELOG.md` | Primary change history |
| `config.py` | KIS App Key·Secret·Base URL·Paper account environment-variable contract |
| `db_config.py` | PostgreSQL environment-variable loader |
| `connector_db.py` | DB repository helper |

### Check when changing

| Target | Check |
| --- | --- |
| `AGENTS.md` | Order gate · execution restriction · documentation update rules |
| `README.md` | Current entrypoints and operational structure |
| `CHANGELOG.md` | Record only actual Connector changes |
| `config.py` | No sensitive-information hard-coding · Runtime injection targets · permission 600 after deployment |
| `db_config.py` | `INTEREST_DB_*` and password default |
| `connector_db.py` | schema · SQL · transaction · mapping |

## Flask

| File | Role |
| --- | --- |
| `connector_app.py` | Execution API and View API routes |
| `connector_view_service.py` | View API response assembly |

### Execution API

| Path | Role |
| --- | --- |
| `/api/v1/quotes/realtime` | Real-time quote query · can save |
| `/api/v1/quotes/eod` | Period quote query · can save |
| `/api/v1/accounts/balance` | Balance query · Snapshot save |
| `/api/v1/orders/buy` | Buy order |
| `/api/v1/orders/sell` | Sell order |
| `/api/v1/orders/cancel` | Order cancellation |
| `/api/v1/orders/modify` | Order modification |
| `/api/v1/orders/history` | Order · fill synchronization |
| `/api/v1/price` | legacy quote alias |

### View API

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

### Check when changing

| Item | Value |
| --- | --- |
| Route | URL · HTTP method · response contract |
| Execution API | token · external API · DB side effect |
| View API | port-view DTO and backward compatibility |
| Security | Whether account · token · order number is exposed |
| Validation | Prefer mock tests over actual route calls |

## Token

| File | Role |
| --- | --- |
| `token_manager.py` | token file · issuance · renewal · deletion |

### Check when changing

| Item | Value |
| --- | --- |
| Import | No issuance or file change on import alone |
| Log | No verbatim token output |
| Failure | Do not treat a renewal failure as success |
| Test | HTTP and file-system mock |
| Storage | Exclude token files from source control |

## Balance and Position

| File | Role |
| --- | --- |
| `connector_balance.py` | Daily balance and position Snapshot |
| `connector_intraday_snapshot_refresh.py` | Intraday Snapshot Refresh |
| `scripts/run_connector_balance_daily.sh` | Daily balance execution wrapper · Bundle·CodeDeploy deployment target |
| `scripts/run_intraday_snapshot_and_evaluate.sh` | Intraday wrapper that runs Position Evaluate sequentially after Snapshot Refresh |

### Primary save targets

| Table | Role |
| --- | --- |
| `connector.connector_balance_snapshot` | Account Snapshot |
| `connector.connector_position_snapshot` | Held-position Snapshot |
| `connector.connector_api_call_log` | KIS call record |
| legacy balance · holdings | Backward compatibility |

### Check when changing

| Item | Value |
| --- | --- |
| Empty holdings | Distinguish normal liquidation from API anomaly |
| OPEN mismatch | Mismatch between strategy OPEN and KIS holdings |
| stale cleanup | Account · reference-date scope |
| Reference time | balance and position consistency |
| DB user | Use Connector write privileges |

## Quote

| File | Role |
| --- | --- |
| `connector_quote_realtime.py` | Current-price · real-time quote |
| `connector_quote_closed.py` | Period quote · EOD upsert |

### Check when changing

| Item | Value |
| --- | --- |
| `--no-save` | May block only the DB save |
| KIS call | Can occur regardless of the option |
| Token | Possibility of issuance · renewal |
| Mapping | Meaning of current-price and EOD fields |
| Upsert | key and trading-day basis |

## Order Common

| File | Role |
| --- | --- |
| `connector_order_common.py` | Order-quantity normalization · Fatal Max · cancel/modify resolver · state-transition Guard · broker common processing |
| `connector_buy.py` | Buy order wrapper |
| `connector_sell.py` | Sell order wrapper |
| `connector_cancel.py` | Cancellation wrapper applying the full 0/Y · partial quantity/N contract |
| `connector_modify.py` | Modification wrapper applying the full·partial quantity contract |

### Check when changing

| Item | Value |
| --- | --- |
| Idempotency | Verify the existing order request state |
| Retry | No automatic order retry |
| TR ID | paper/live and request-type consistency |
| Status | Distinguish request · acceptance · fill · rejection |
| Log | No full broker order number exposure |
| Quantity | Integer of 1 or greater · block Boolean·fractional |
| Fatal Max | No automatic reduction of excess quantity |
| Cancel/Modify | Full 0/Y · partial quantity/N |
| Terminal Status | Conditional UPDATE from an allowed previous state |
| Side Effect | Block Broker·token·DB follow-up calls on validation failure |
| Test | Property · boundary · call-count validation |

## Order and Fill Synchronization

| File | Role |
| --- | --- |
| `connector_order_check.py` | Order event · fill · legacy order synchronization |

### Primary save targets

| Table | Role |
| --- | --- |
| `connector.connector_order_event` | Order status events |
| `connector.connector_fill` | Fills |
| `connector.connector_api_call_log` | API call record |
| legacy trade orders | Backward compatibility |

### Check when changing

| Item | Value |
| --- | --- |
| Direct | Prioritize the specific-order query |
| Summary | direct `output2` fallback |
| Broad | Search after direct handling |
| Mapping | broker number and order request |
| Misattribution | Do not link a broad summary to a specific order |

## Daily Strategy Order

| File | Role |
| --- | --- |
| `connector_strategy_order_execute.py` | Approved strategy order submission · Execution Order atomic Claim · block duplicate Broker submission · quantity and Fatal Max validation · Broker result and state synchronization · unit-failure isolation |

### Operational standards

| Item | Value |
| --- | --- |
| Default | dry run |
| Real order | `--execute` |
| Approval gate | `portfolio-paper-daily-step12-17-approval` |
| Claim | `REQUESTED → SUBMITTING` |
| Broker call | Only for successfully claimed orders |
| Duplicate order | Skip if Claim is 0 rows |
| State failure | Do not report as Broker success |
| Batch | Continue with follow-up targets after a unit failure |
| Save | order request · API call log |
| Follow-up | `connector_order_check.py` |

Do not add a default or automatic-execution fallback that bypasses the approval gate.

## Intraday hard stop

| File | Role |
| --- | --- |
| `connector_intraday_position_evaluate.py` | OPEN position hard stop evaluation |

### Responsibility boundary

| Stage | Responsibility |
| --- | --- |
| Snapshot | `connector_intraday_snapshot_refresh.py` |
| Evaluation | `connector_intraday_position_evaluate.py` |
| Result | `execution.strategy_intraday_position_check` |
| Order candidate | `execution.strategy_execution_order` READY sell |
| Notification | event notifier Lambda |
| Actual sell | After the approval workflow |

Do not add broker order submission logic to the evaluation entrypoint.

## Database Contract

| Item | Value |
| --- | --- |
| Database | `portfolio` |
| Config loader | `db_config.py` · `get_db_config()` |
| Environment variables | `INTEREST_DB_*` |
| Password | No default |
| search path | `connector, execution, legacy, reference, public` |

### Schema responsibilities

| Schema | Role |
| --- | --- |
| `connector` | Account · Snapshot · quote · order · fill |
| `execution` | Strategy order · position · intraday check |
| `legacy` | Backward compatibility |
| `reference` | Stock and common reference data |
| `public` | fallback search path |

### Primary write targets

| Area | Table |
| --- | --- |
| API Log | `connector.connector_api_call_log` |
| Balance | `connector.connector_balance_snapshot` |
| Position | `connector.connector_position_snapshot` |
| Quote | `connector.connector_quote_realtime` · `connector.connector_quote_eod` |
| Order | `connector.connector_order_request` |
| Event | `connector.connector_order_event` |
| Fill | `connector.connector_fill` |
| Strategy Order | `execution.strategy_execution_order` |
| Intraday Check | `execution.strategy_intraday_position_check` |

Do not write SQL using assumed tables and columns.

Verify that existing unqualified SQL is consistent with the connection `search_path`.

## EC2 and SSM

| Item | Role |
| --- | --- |
| MarketConnector EC2 | Runs Python entrypoints |
| SSM RunCommand | Daily · Intraday remote execution |
| EventBridge Scheduler | EC2 lifecycle · intraday interval |
| Step Functions | Daily and sell approval |
| Event Notifier Lambda | Operational notification |

### Operational identifiers

| Item | Name |
| --- | --- |
| EC2 Role | `portfolio-paper-marketconnector-ec2-role` |
| Lambda policy | `portfolio-paper-marketconnector-event-notifier-invoke` |
| Start Scheduler | `portfolio-paper-ec2-start-0750-kst` |
| Stop Scheduler | `portfolio-paper-marketconnector-stop-1550-kst` |
| Intraday Scheduler | `portfolio-paper-intraday-snapshot-evaluate-10min-kst` |

Do not record actual ARNs, instance ids, command ids, account-id and endpoints.

## DevOps and Deployment

### Root deployment files

| File | Role |
| --- | --- |
| `appspec.yml` | CodeDeploy EC2 In-place deployment and Lifecycle Hook wiring |
| `requirements.txt` | EC2 Runtime Dependency baseline |
| `.github/workflows/marketconnector-codebuild.yml` | main Push · manual-run automatic Release · CodeBuild · EC2 state preparation/restoration · CodeDeploy wiring |

### Bundle

| File | Role |
| --- | --- |
| `.devops/bundle/include.txt` | Manifest of deployment files to include in the Versioned ZIP |
| `.devops/scripts/build_bundle.py` | Create the committed Git blob-based Versioned ZIP and Manifest |
| `.devops/codebuild/buildspec.yml` | Compile · Test · Ruff · Bundle · S3 upload Phase |
| `.devops/scripts/compile_check.py` | Safe Python Compile check |

Do not list `.devops/artifacts` outputs and one-time Artifacts in the catalog.

### CodeDeploy Hook

Based on the relative paths referenced by `appspec.yml`.

| File | Role |
| --- | --- |
| `codedeploy/application_stop.sh` | Verify running processes and deployment-safe state |
| `codedeploy/before_install.sh` | Deployment Lock and existing Source Backup |
| `codedeploy/after_install.sh` | Bundle install · apply permissions · preserve Runtime files |
| `codedeploy/application_start.sh` | Keep the safe state without automatic start |
| `codedeploy/validate_service.sh` | Validate Compile · Wrapper Syntax · required files · single execution · Lock release |

### Check when changing

| Item | Value |
| --- | --- |
| Bundle Include change | Update the Contract Test |
| Hook change | Verify `appspec.yml` reference consistency |
| Shell change | Verify LF and `bash -n` |
| Runtime files | Verify exclusion from the Bundle |
| Source·Bundle | Verify Source SHA and Bundle Version consistency |
| Hook execution | Verify no automatic Connector·order execution |
| Rollback | Verify token-file preservation |
| Artifact Store | MarketConnector-dedicated Versioned S3 |
| Revision | Fixed Bucket · Key · Version ID |
| Manifest | Verify Source SHA |
| Operating Source | Compare Manifest SHA-256 with actual files |
| Runtime protection | Preserve `config.py` · token file |
| No-order validation | 0 Connector processes and Broker calls |

## Tests

| Change | Validation |
| --- | --- |
| Python syntax | Safe compile check |
| Pure functions | unit test |
| Flask route | test client · external-dependency mock |
| KIS parsing | HTTP response mock |
| Token | HTTP · file mock |
| Order | dry-run · idempotency · atomic Claim · duplicate block |
| Order quantity | Quantity Property Test · Fatal Max |
| Cancel·modify | Full·partial cancellation Contract |
| Order status | Terminal state monotonicity |
| Call count | Broker·token·DB call count |
| Repository | SQL · parameter · transaction |
| Shell | syntax · argument-passing static check |
| Documentation | Links · facts · readability |

Do not use real KIS, token, account, DB and Lambda in tests.

## Documents

| Document | Role |
| --- | --- |
| `AGENTS.md` | MarketConnector working rules |
| `README.md` | Current structure and operational AS-IS |
| `CHANGELOG.md` | Primary change history |
| `docs/source-file-catalog.md` | Primary files and responsibilities |

Do not create date-specific `docs/worklog/*.md` files.

When keeping past worklog files, treat them only as historical records and do not use them as a baseline for new work.

## External Dependency Modules

| Module | Relationship |
| --- | --- |
| `port-view` | Consumes the Connector query API |
| `port-interest-crawler` | Source data collection |
| `port-interest-preprocessor` | Preprocessing |
| `port_strategy_common` | Common strategy model |
| `port_strategy_research` | Strategy research |
| `port_strategy_decision` | Strategy decision |
| `port_strategy_execution` | Order planning and execution state |

The internal code and documentation of other MSes are not automatically included in the MarketConnector change scope.

## Catalog Update Conditions

| Change | Handling |
| --- | --- |
| Create · delete · rename a primary Python file | Update |
| Change Flask route and entrypoint responsibility | Update |
| Change the package · scripts structure | Update |
| Change token · configuration · DB loader roles | Update |
| Change EC2 · SSM wrapper roles | Update |
| Create · delete a document or change its role | Update |
| Change only internal implementation · same responsibility | May be omitted |

When updating the catalog, do not create a new full repository inventory.

Check only the changed area and adjacent items.
