# CHANGELOG

Records the primary change history of port-marketconnector code and documentation.

## Authoring Principles

| Item | Value |
| --- | --- |
| Recording scope | Connector code · Flask API · scripts · configuration · tests · documentation |
| Excluded scope | Internal implementation of other MSes · full orchestration definitions · one-time operational logs |
| Ordering | Add the latest date at the top |
| Classification | Added · Changed · Fixed · Removed · Security |
| Execution records | Record only validation actually performed |
| Sensitive information | No verbatim token · account · order number · secret · ARN · endpoint |

## 2026-08-11 — main Push-based MarketConnector automatic Release completion

### Added

| Item | Value |
| --- | --- |
| Automatic Release | main Push GitHub Actions Release Trigger |
| Manual path | `workflow_dispatch` run maintained |
| EC2 preparation | Verify EC2 state before deployment · temporarily start if needed |
| EC2 restoration | Restore only EC2 that the Workflow started to stopped |
| Automatic deployment link | Automatic creation·wait of S3 Versioned Revision-based CodeDeploy |

### Changed

| Item | Value |
| --- | --- |
| CI Contract | Updated to match the automatic Release structure |
| Release flow | main Push → CodeBuild → Versioned Artifact → EC2/SSM preparation → CodeDeploy |

### Security

| Item | Result |
| --- | --- |
| Full Pytest | 56 succeeded |
| CodeBuild | Succeeded |
| Versioned Artifact | Creation·lookup succeeded |
| EC2 state·SSM Online | Verification succeeded |
| CodeDeploy In-place | Succeeded |
| No-Order Release Boundary | Maintained |
| Connector automatic execution | 0 |
| Broker order API calls | 0 |

## 2026-08-04 — Order-boundary safety hardening and Versioned production deployment

### Added

| Item | Value |
| --- | --- |
| Claim | Execution Order atomic Claim |
| Quantity normalization | Common order-quantity normalization |
| Fatal Max | Environment-variable-based emergency maximum-quantity check |
| Cancel/Modify | Cancel·modify quantity resolver |
| State Guard | Terminal state transition Guard |
| Test | Property Test and regression Test |
| Sync failure | Handling of state-sync failure after Broker success |
| Deployment evidence | MarketConnector-dedicated Versioned Artifact deployment |

### Changed

| Item | Value |
| --- | --- |
| Submission order | Quantity validation → Fatal Max → Claim → Broker call |
| Broker call | Performed only for successfully claimed orders |
| Full-cancel Payload | Fixed to `ORD_QTY=0` · `QTY_ALL_ORD_YN=Y` |
| Partial-cancel Payload | Fixed to requested quantity · `QTY_ALL_ORD_YN=N` |
| State UPDATE | Conditional transition from an allowed previous state |
| Artifact destination | Unified to MarketConnector-dedicated Versioned S3 |
| Artifact Prefix | Aligned with the existing successful-deployment baseline |

### Fixed

| Problem | Resolution |
| --- | --- |
| Possibility of resubmitting the same Execution Order | Block duplicates with an atomic Claim |
| 0 · negative · fractional · Boolean quantity | Explicit rejection before the Broker call |
| Emergency outlier quantity | Block on Fatal Max excess without automatic reduction |
| Full-cancel quantity error | Apply the full 0/Y contract |
| Possibility of Terminal state regression | Apply a conditional state-transition Guard |
| Misjudgment of DB sync failure after Broker success | Block success output and subsequent state recording |
| Additional exception during Claim-failure state recording | Handle as a separate error without stopping the Batch |
| Migration Artifact path remnant in CodeBuild | Switch to the dedicated Versioned Artifact path |

### Security

| Item | Result |
| --- | --- |
| Full Pytest | 55 succeeded |
| Ruff | Succeeded |
| Python Compile | Succeeded |
| CodeBuild | Succeeded |
| Versioned S3 Artifact | Created successfully |
| CodeDeploy In-place | Succeeded |
| Lifecycle Hook | All succeeded |
| Manifest Source SHA | Matched |
| Core operating-file SHA-256 | 3 matched |
| `config.py` permissions · Runtime token | Preserved |
| Connector processes | 0 |
| Broker order API calls | 0 |
| BUY·SELL·cancel·modify execution | 0 |
| Secret value output | 0 |

## 2026-08-01 — MarketConnector DevOps and CodeDeploy In-place deployment

### Added

| Item | Value |
| --- | --- |
| CI | GitHub Actions and CodeBuild |
| Artifact | Git SHA-based Versioned ZIP Bundle |
| Bundle creation | Deterministic creation based on committed Git blob |
| Include Manifest | `.devops/bundle/include.txt` |
| Bundle validation | Bundle Contract Test |
| Artifact storage | Private Versioned S3 |
| Deployment definition | `appspec.yml` |
| Lifecycle Hook | 5 CodeDeploy Hooks |
| Single-execution protection | Deployment Lock |
| Pre-deployment Backup | Operating Source Backup |
| Rollback | Backup Source restore |
| Redeployment | Reuse of the identical S3 Revision |
| Intraday Wrapper | Reflected in the Repository and Bundle |
| Agent | CodeDeploy Agent installation and automatic start |

### Changed

| Item | Value |
| --- | --- |
| `config.py` | Switched to an environment-variable-based structure |
| Sensitive information | Removed KIS App Key·Secret·Paper account hard-coding |
| Source consistency | Aligned EC2 operating Source with the Git Repository |
| Build Source | Detached Head handling |
| Bundle basis | Changed from Working Tree to committed Git blob |
| Intraday | Clarified execution order in the Wrapper |
| Deployment | Applied file permissions and owner |
| Application execution | Maintained existing SSM·Scheduler instead of automatic start |

### Fixed

| Problem | Resolution |
| --- | --- |
| GitHub Repository access | Resolved access-configuration issue |
| Detached Head | Resolved Bundle-creation issue that assumed a Branch name |
| Bundle Hash | Resolved Hash variation caused by line-ending conversion |
| Missing Manifest | Resolved missing Intraday Wrapper |
| Runtime token | Blocked possible token-file loss during deployment |
| Process safety | Blocked duplicate Connector execution and automatic-execution risk |

### Security

| Item | Result |
| --- | --- |
| App Key·Secret hard-coding | 0 |
| Paper account-number hard-coding | 0 |
| token·secret files in Bundle | 0 |
| Runtime token file | Preserved |
| Secret output | 0 |
| Order API calls | 0 |
| BUY·SELL·cancel·modify execution | 0 |
| Connector automatic start | 0 |
| Final Connector processes | 0 |
| S3 Public Access | Blocked |
| S3 encryption | AES256 |
| S3 Versioning | Enabled |
| Python Compile | 25 succeeded |
| Pytest | 21 succeeded |
| Ruff | Succeeded |
| Bundle files | 28 confirmed |
| First deployment·Rollback·redeployment | Validation succeeded |

## 2026-07-22 — MarketConnector documentation baseline overhaul

### Changed

| Item | Value |
| --- | --- |
| `AGENTS.md` | Fully rewritten as MarketConnector-dedicated working rules |
| Highest-priority rules | New standalone tables two columns · local edits · no long cells |
| Execution risk | Hardened token · KIS API · order · DB write standards |
| Order safety | Clarified Daily Step 12 and Intraday sell approval gates |
| Flask standards | Separated execution API and View API responsibilities |
| DB standards | Organized `connector` · `execution` schema and transaction cautions |
| Operational standards | Organized EC2 · SSM RunCommand · Scheduler responsibilities |
| Test standards | Prefer external API · token · DB mocks |
| `README.md` | Fully restructured around current state · responsibility boundary · risk paths |
| `docs/source-file-catalog.md` | Restructured the 5-column long-form structure to be two-column-centric |
| Documentation system | Simplified around README · CHANGELOG · source catalog |

### Security

| Item | Result |
| --- | --- |
| Python code change | None |
| Flask · KIS · broker execution | 0 |
| Token issuance · renewal · file access | 0 |
| DB · AWS · Slack execution | 0 |
| Git write commands | 0 |
| New verbatim sensitive information | 0 |

## 2026-07-01 — Strategy order and Intraday operations documentation

### Added

| Item | Value |
| --- | --- |
| Strategy order entrypoint | `connector_strategy_order_execute.py` |
| Intraday Snapshot | `connector_intraday_snapshot_refresh.py` |
| Intraday evaluation | `connector_intraday_position_evaluate.py` |
| Operational structure | EC2 · SSM RunCommand · Scheduler |
| Daily approval | `portfolio-paper-daily-step12-17-approval` |
| Intraday approval | `portfolio-paper-intraday-stop-sell-approval` |
| Slack delegation | `portfolio-event-notifier` Lambda |
| Worklog | `docs/worklog/2026-07-01.md` |

### Changed

| Item | Value |
| --- | --- |
| README file structure | Reflected the 3 new entrypoints and the Daily balance wrapper |
| Intraday flow | Documented the Snapshot Refresh → Position Evaluate order |
| hard stop | Separated READY sell-candidate creation from actual broker submission |
| Order synchronization | Explained the direct · summary fallback-first semantics |
| Execution risk | Added the 3 new entrypoints |

### Security

| Item | Result |
| --- | --- |
| Functional change | None |
| Connector · KIS · order execution | 0 |
| SSM · Lambda · Slack execution | 0 |
| New verbatim sensitive information | 0 |

## 2026-05-28 — Source catalog and explanatory comments

### Added

| Item | Value |
| --- | --- |
| `docs/source-file-catalog.md` | Organized the roles and operational risks of primary Python files |
| Module docstring | Descriptions of primary Connector files |
| Function docstring | Descriptions of balance · quote · order · fill · View assembly functions |
| Worklog | `docs/worklog/2026-05-28.md` |

### Changed

| Item | Value |
| --- | --- |
| README | Reflected the source catalog and comment cleanup results |
| `connector_order_check.py` | Documented the direct fallback-first semantics |

### Fixed

| Problem | Resolution |
| --- | --- |
| Risk of broad-summary misattribution | Handle the direct `output2` summary before a broad search |

### Security

| Item | Result |
| --- | --- |
| Feature execution | 0 |
| DB · KIS · broker calls | 0 |
| New verbatim sensitive information | 0 |

## 2026-05-27 — DB configuration externalization and schema-per-domain

### Changed

| Item | Value |
| --- | --- |
| DB configuration | Based on `INTEREST_DB_*` environment variables |
| Password | Removed hard-coding in `connector_db.py` |
| Common loader | Use `get_db_config()` in `db_config.py` |
| Database | `portfolio` |
| Schema structure | Single DB · per-domain schema |
| search path | `connector, execution, legacy, reference, public` |
| Existing SQL | Maintained based on connection `search_path` |

### Security

| Item | Result |
| --- | --- |
| Actual DB connection | 0 |
| Flask · KIS · broker execution | 0 |
| DB password verbatim record | 0 |

## 2026-05-26 — Initial documentation and legacy file cleanup

### Added

| Item | Value |
| --- | --- |
| README | Python MarketConnector project draft |
| AGENTS | token · order · DB execution-risk standards |
| Worklog | 2026-05-26 documentation work log |

### Changed

| Item | Value |
| --- | --- |
| Documentation language | Organized in Korean |
| Entrypoint list | Reflected the legacy file cleanup results |

### Removed

| Item | Value |
| --- | --- |
| Legacy script | `app.py` · `buy.py` · `balance.py` |
| Legacy script | `order_check.py` · `get_price_realtime.py` · `get_price_closed.py` |
| Cache | `__pycache__` |

### Security

| Item | Result |
| --- | --- |
| Flask · KIS · broker execution | 0 |
| DB DDL · DML | 0 |
| New verbatim sensitive information | 0 |
