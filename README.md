# Centralised Manufacturing Quality KPI Reporting System

A portfolio project that simulates an automotive Tier‑1 supplier (*Danube Automotive Components*, fictional). Quality data from five departments is scattered across Excel and CSV files. The project consolidates it into **one governed SQL star schema**, with **one definition per KPI**, and reports it in **Power BI**.

> **Status:** Phases 1–8 complete: 24 months of synthetic data loaded into DEV by the ETL. Next: Phase 9, the Power BI report. See the [roadmap](documentation/01_project_blueprint.md#11-implementation-roadmap).

## Architecture

```
Department files (Excel/CSV) ─► Azure Blob (raw, archive) ─► Python ETL (validate · standardise · conform)
     ─► SQL Server star schema (stg · dw · audit · rpt) ─► Power BI semantic model ─► 8-page report
```

| Layer | DEV (local) | PROD (cloud) |
|---|---|---|
| Raw files | `data/raw/` | Azure Blob `rawdata` / `archive` |
| Database | SQL Server Express `QualityKPI_Dev` | Azure SQL `QualityKPI` (Basic, Austria East) |
| Reporting | Power BI Desktop (PBIP in `powerbi/`) | same, pointed at Azure SQL |

The full design is in [documentation/01_project_blueprint.md](documentation/01_project_blueprint.md).

## Documentation

| Document | Content |
|---|---|
| [01 Project blueprint](documentation/01_project_blueprint.md) | Business case, architecture, KPI framework, report design, roadmap, risks, **decision log** |
| [02 Data dictionary](documentation/02_data_dictionary.md) | Every table and column, ERD, source-to-target mapping, validation rules |
| [03 Azure infrastructure](documentation/03_azure_infrastructure.md) | Cloud resources, authentication, rebuild commands, issues log |
| [04 Synthetic data](documentation/04_synthetic_data.md) | Generator, source files, the built-in quality story, injected data-quality issues |
| [05 ETL pipeline](documentation/05_etl_pipeline.md) | How to run the load, flow, modules, validation rules, data-quality checks |

## Repository structure

```
config/          non-secret settings (settings.toml)
data/            raw/ and processed/ are git-ignored; sample/ is committed
documentation/   design documents, decisions, images
logs/            run logs (git-ignored)
powerbi/         Power BI project (PBIP)
python/          generate/ (synthetic data), etl/ (pipeline), extraction/ (smoke tests)
sql/             ddl/, etl/ (MERGE procedures), rpt/ (views), tests/, archive/
tests/           unit/, integration/ (DEV database) and data_quality/ (pytest)
```

## Getting started (Windows)

Prerequisites: Python 3.15, ODBC Driver 18 for SQL Server, SQL Server Express (DEV), Azure CLI, Power BI Desktop.

```powershell
# 1. Clone and create the virtual environment
git clone https://github.com/mandaricsladjana/Centralised-Management-Reporting-KPI-Automation-System.git
cd Centralised-Management-Reporting-KPI-Automation-System
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 2. Configure secrets
copy .env.example .env      # then fill in the values
az login                    # for Entra ID access to Azure SQL

# 3. Create the schema on the local DEV database (QualityKPI_Dev must exist)
cd python
..\.venv\Scripts\python.exe -m etl.deploy --env dev --dry-run   # list pending scripts
..\.venv\Scripts\python.exe -m etl.deploy --env dev             # apply them
cd ..

# 4. Generate the synthetic department files (24 months -> data\raw, ~45 s)
cd python
..\.venv\Scripts\python.exe -m generate

# 5. Load them into DEV (~35 s; a rerun skips unchanged files)
..\.venv\Scripts\python.exe -m etl.pipeline --env dev
cd ..

# 6. Smoke test
.\.venv\Scripts\python.exe python\extraction\test_blob_connection.py
.\.venv\Scripts\python.exe -m pytest        # integration tests skip if DEV is unreachable
```

### Database changes

`python -m etl.deploy` applies `sql/ddl/NNN_*.sql` once each, in order, and records each one with a SHA-256 checksum in `audit.schema_version`. **Never edit a script that has already been applied**: the deploy refuses it. Add the next number instead (for example `021_fix_8d_closed_check.sql`). Procedures and views in `sql/etl/` and `sql/rpt/` use `CREATE OR ALTER` and are re-applied whenever they change. PROD requires an explicit `--env prod`.

> **VS Code users:** `.env` is also injected into new terminals. After editing `.env`, open a **new** terminal. The project code always loads `.env` with `override=True` ([issue I-04](documentation/03_azure_infrastructure.md#7-issues-log)).

## Git workflow

- `main` is always runnable. Work happens on `feature/p<phase>-<topic>` branches and is merged by pull request.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.
- Never commit `.env`, generated data, logs or `.bacpac` files (enforced by `.gitignore`).

## Tech stack

Python (pandas, SQLAlchemy, pyodbc, openpyxl) · T-SQL (SQL Server 2025 Express / Azure SQL) · Azure Blob Storage · Power BI (PBIP, DAX) · GitHub · Notion · pytest
