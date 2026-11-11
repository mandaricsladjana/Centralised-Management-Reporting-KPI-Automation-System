# Azure Infrastructure — Phase 3 Runbook

**Document status:** v1.0. Phase 3 executed on 2026-11-11.
**Related:** [01_project_blueprint.md](01_project_blueprint.md) §8, decisions D5, D13, D14

This runbook records **what exists, why, and the exact commands to rebuild it**. All commands run in PowerShell from any folder, after `az login`.

---

## 1. Inventory

| Resource | Name | Region | Configuration | Est. cost / month |
|---|---|---|---|---|
| Resource group | `Centralised-KPI-Reporting-System` | Switzerland North | — | — |
| Storage account | `azurestoragecentralkpi` | Switzerland North | StorageV2, Standard_LRS, Hot, HTTPS only, TLS 1.2, public blob access **off**, blob and container soft delete **7 days** (already configured, verified) | < €1 |
| Blob containers | `rawdata`, `archive`, `processeddata`, `stagingdata`, `backupdata` | — | Private | — |
| SQL logical server | `sql-qualitykpi-og6p7` (`sql-qualitykpi-og6p7.database.windows.net`) | **Austria East** | TLS 1.2 min; Entra admin = project owner account; SQL admin login `kpiadmin`; Entra-only authentication **off** (D5) | — |
| SQL database | `QualityKPI` | Austria East | **Basic** (5 DTU, 2 GB), local backup redundancy | ≈ €4.5 |
| Firewall rule | `dev-laptop-home` | — | Single IP of the development machine | — |
| Budget | `budget-qualitykpi-monthly` | Subscription, filtered to the resource group | €10/month; alerts at 50 % and 80 % actual and 100 % forecast to the subscription **Owner** role | — |
| Local DEV database | `QualityKPI_Dev` on `.\SQLEXPRESS` | Local | SQL Server 2025 Express, compatibility level 170, collation `SQL_Latin1_General_CP1_CI_AS` | €0 |

## 2. Deviations from the blueprint (and why)

| ID | Planned | Actual | Reason | Impact |
|---|---|---|---|---|
| **D13** | SQL server in Switzerland North (next to storage) | **Austria East** | Switzerland North returned `RegionDoesNotAllowProvisioning` for new SQL servers. Austria East is the closest allowed region (subscription policy: switzerlandnorth, denmarkeast, austriaeast, polandcentral, belgiumcentral). | Storage and SQL are in different regions. Data volumes are tiny (MB), so cross-region traffic cost and latency are negligible. The ETL reads blobs and writes to SQL from the local machine anyway, so there is no direct cloud-to-cloud path. |
| **D14** | Free offer (serverless, auto-pause) | **Basic tier** | `ProvisioningDisabled`: the free offer is not available in Austria East for this subscription. Basic was the documented fallback. | ≈ €4.5/month from student credit. No auto-pause, so the database is always available (simpler for Power BI refresh). 2 GB limit vs. expected < 100 MB of data. |

Both are logged in the blueprint decision log (§14).

## 3. Authentication model (D5)

| Who / what | Method | Identity | Permissions (set up in Phase 7 scripts) |
|---|---|---|---|
| Developer (you), SSMS, Azure Data Studio | Entra ID (MFA) | Your Entra account = server Entra admin | Full (admin) |
| Power BI Desktop | Entra ID ("Microsoft account") | Your Entra account | Read via `rpt` schema |
| Scheduled ETL (Task Scheduler) | SQL authentication | `etl_loader` (contained database user, **created in Phase 7**) | `stg` read/write, EXECUTE on `etl` procedures, read on `dw` |
| Break-glass administration | SQL authentication | `kpiadmin` (server admin) | Full. **Never used by the pipeline.** |

**Where credentials live:** only in the local `.env` (git-ignored): `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`, `AZURE_SQL_ADMIN_USERNAME`, `AZURE_SQL_ADMIN_PASSWORD`. The admin password was generated randomly (24 characters, 4 character classes) and has never been printed or committed. Recommendation: also store it in a password manager, because `.env` is a single local copy.

**SSMS connection (Entra):** Server `sql-qualitykpi-og6p7.database.windows.net`, Authentication *Microsoft Entra MFA*, user = your university account, Options → Connect to database `QualityKPI`, Encrypt = Mandatory.

## 4. Rebuild commands

```powershell
$rg  = 'Centralised-KPI-Reporting-System'
$srv = 'sql-qualitykpi-og6p7'          # must be globally unique if recreated
$upn = az ad signed-in-user show --query userPrincipalName -o tsv
$oid = az ad signed-in-user show --query id -o tsv

# SQL server with Entra admin + SQL admin (password from a secure source, never typed in plain history)
az sql server create -g $rg -n $srv -l austriaeast `
  --admin-user kpiadmin --admin-password $env:AZURE_SQL_ADMIN_PASSWORD `
  --external-admin-principal-type User --external-admin-name $upn --external-admin-sid $oid `
  --minimal-tls-version 1.2
az sql server ad-only-auth disable -g $rg -n $srv          # D5: keep SQL auth available

# Firewall: current public IP only
$ip = Invoke-RestMethod https://api.ipify.org
az sql server firewall-rule create -g $rg -s $srv -n dev-laptop-home --start-ip-address $ip --end-ip-address $ip

# Database (Basic, D14)
az sql db create -g $rg -s $srv -n QualityKPI --edition Basic --capacity 5 --max-size 2GB --backup-storage-redundancy Local

# Local DEV database
sqlcmd -S .\SQLEXPRESS -E -C -Q "IF DB_ID('QualityKPI_Dev') IS NULL CREATE DATABASE QualityKPI_Dev"
```

Budget: `az rest --method put` against `Microsoft.Consumption/budgets/budget-qualitykpi-monthly` (API version 2023-05-01), with the body described in §1.

## 5. Operations

| Task | How |
|---|---|
| **Your IP changed** (home router, other network): "Client with IP address … is not allowed" | `az sql server firewall-rule update -g $rg -s $srv -n dev-laptop-home --start-ip-address <ip> --end-ip-address <ip>` |
| Check cost | Azure portal → Cost Management → Budgets → `budget-qualitykpi-monthly` |
| Backup | Automatic point-in-time restore: 7 days (Basic). **Monthly `.bacpac` export** to the `backupdata` container (procedure added in Phase 7, see blueprint §8.5) |
| Pause cost entirely | Basic cannot pause. To stop all cost: export a bacpac, then delete the database. The full rebuild path is Git (DDL) + `archive` blobs + ETL. |
| Subscription ends | Export the bacpac and raw files, then continue on `QualityKPI_Dev` locally (`ENVIRONMENT=dev`) |

## 6. Acceptance test results (2026-11-11)

| Test | Expected | Result |
|---|---|---|
| T3-01 Storage: list containers with the `.env` connection string | 5 containers listed | ✅ Pass |
| T3-02 TCP 1433 reachable from the dev machine | `TcpTestSucceeded = True` | ✅ Pass |
| T3-03 Python → Azure SQL with an Entra token (az login) | Login succeeds, `DB_NAME() = QualityKPI` | ✅ Pass: login = project owner account, tier = Basic, 2.2 s |
| T3-04 Python → Azure SQL with SQL authentication (`kpiadmin`) | Login succeeds | ✅ Pass: tier = Basic, 0.3 s |
| T3-05 Local DEV database exists | `QualityKPI_Dev` in `sys.databases` | ✅ Pass |
| T3-06 Budget exists with 3 alerts | `budget-qualitykpi-monthly` | ✅ Pass |

## 7. Issues log

| # | Symptom | Cause | Resolution |
|---|---|---|---|
| I-01 | `RegionDoesNotAllowProvisioning` in Switzerland North | Regional capacity restriction for new SQL servers | Server created in Austria East (D13) |
| I-02 | `ProvisioningDisabled` for the free-limit database | Free offer not supported for this region/subscription | Basic tier (D14) |
| I-03 | First login attempts: `TCP Provider: Timeout error [258]` | Invalid ODBC keyword in the test script (`Connection Timeout`; pyodbc's `timeout=` argument is the correct way), and see I-04 | Script fixed |
| I-04 | `Azure Active Directory only authentication is enabled` (SQL login) and `The server is not currently configured to accept this token` (Entra), although the server showed Entra-only = false and the correct admin | **Wrong target server.** `.vscode/settings.json` has `"python.terminal.useEnvFile": true`, so VS Code injected the *old* `.env` values (`AZURE_SQL_SERVER=central-kpi-report…`, a server outside this subscription) into the terminal as process variables. `load_dotenv()` does **not** override existing variables, so every test connected to that old server. | Load with `load_dotenv(override=True)`. **Rule for all project code: `.env` always wins** (implemented in `python/etl/config.py`, Phase 6). After editing `.env`, open a new VS Code terminal. |
| I-05 | While diagnosing I-04, a replacement server `sql-qualitykpi-3indr` was created and the admin password in `.env` was overwritten | Diagnostic step taken before the real cause was found | `og6p7` admin password reset to the value in `.env`; `3indr` verified empty and deleted. Only `sql-qualitykpi-og6p7` remains. |
| I-06 | `etl/db.py` (first version) used `Connection Timeout=30` in the connection string again | Same invalid keyword as I-03; the driver ignores it silently, so nothing failed | Replaced with pyodbc `timeout=30`; unit test `test_no_invalid_timeout_keyword` prevents a regression |

## 8. Phase 7 deployment to PROD (2026-11-11)

| Step | Result |
|---|---|
| `python -m etl.deploy --env prod --dry-run` | 16 of 16 scripts pending (empty database), Entra token via `az login` |
| `python -m etl.deploy --env prod` | 16 scripts applied; `dw.plant_holiday` 56 rows; `dw.dim_calendar` 1461 days. DEV and PROD now have the same `audit.schema_version` checksums |
| `etl_loader` user | Contained database user (SQL authentication), member of `etl_writer`. Password: 24 random characters, generated and written directly to `.env` (`AZURE_SQL_USERNAME` / `AZURE_SQL_PASSWORD`), never printed. The previous `.env` was saved as `.env.bak` (git-ignored). **Store the password in your password manager.** |
| Least-privilege check (logged in as `etl_loader`, all statements rolled back) | Allowed: SELECT `dw`, write and TRUNCATE `stg`, INSERT `audit`, EXEC `etl.usp_merge_line`. Denied: INSERT `dw.dim_shift`, DELETE `dw.fact_scrap`, CREATE TABLE |
| Power BI reader | The Entra admin already has full access. `rpt_reader` gets members when other report users exist (`CREATE USER [upn] FROM EXTERNAL PROVIDER; ALTER ROLE rpt_reader ADD MEMBER [upn];`) |

**Rotate the `etl_loader` password:** `ALTER USER etl_loader WITH PASSWORD = '<new>'` as the Entra admin, then update `.env`.
