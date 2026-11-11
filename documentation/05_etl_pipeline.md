# ETL Pipeline — Phase 6

**Document status:** v1.0, 2026-11-11.
**Related:** [02_data_dictionary.md](02_data_dictionary.md) §5–7 (audit tables, mapping, rules), [04_synthetic_data.md](04_synthetic_data.md), `config/source_schemas.toml`, `python/etl/`

## 1. How to run

```powershell
cd python
..\.venv\Scripts\python.exe -m etl.pipeline --env dev                         # data\raw -> QualityKPI_Dev
..\.venv\Scripts\python.exe -m etl.pipeline --env dev --source-dir ..\data\sample
..\.venv\Scripts\python.exe -m etl.pipeline --env prod --triggered-by scheduler  # Azure SQL (explicit --env prod)
..\.venv\Scripts\python.exe -m etl.pipeline --env dev --database QualityKPI_Test # any other database
```

Exit code: 0 = Succeeded, 3 = Partial or Failed, 1 = configuration error, 2 = PROD not explicit. Each run writes a log file to `logs/` (git-ignored). `--upload-blob` also uploads every file to the `rawdata` container and copies loaded files to `archive`. It is off by default because it sends files to the cloud.

Measured on DEV: 35 files and 159 324 rows of 24 months in about 35 s; a rerun skips all 35 files in about 2 s.

## 2. Flow

```
audit.etl_run (Running) ──► truncate stg.* ──► for each source in load order, for each file:
   SHA-256 already Loaded? ──yes──► file_manifest 'Skipped-duplicate'
        │ no
   extract (contract headers) ──► validate + standardise ──► ONE transaction:
        file_manifest + rejected_row + stg.<table> + etl.usp_merge_* (+ 8D↔NC links) + dq_result warnings
   (a file that fails is rolled back, logged 'Failed', and the run continues -> status Partial)
──► cross-source checks (DQ-01/02/07/08/10, DQ-LNK-NC) ──► audit.etl_run (Succeeded / Partial / Failed)
```

Load order (data dictionary §6): lines, programs, parts, defects → production (24 monthly files) → inspections → 8D tracker → nonconformities → scrap → rework → 8D actions → audit findings. The 8D↔NC links are applied after the tracker (for NCs already loaded) and again after the NC file (for new NCs).

## 3. Modules

| Module | Responsibility |
|---|---|
| `config.py` / `db.py` | `.env` (override=True) and `settings.toml`; connections (Windows / Entra token / SQL login). `--database` overrides the database name |
| `sources.py` | Loads the source contract (`config/source_schemas.toml`), which is shared with the generator (D25) |
| `extract.py` | Reads xlsx (openpyxl read-only; rows padded because trailing empty cells are dropped) and csv as raw values; SHA-256 |
| `transform.py` | Parsers (dates in 4 formats + Excel serials, ints, decimals, Y/N), spelling standardisation, cost imputation, NC-link splitting |
| `validate.py` | Rule engine (§4). Output: typed rows, rejections, warnings |
| `load.py` | Audit tables, staging (`fast_executemany`), `etl.usp_merge_*` calls |
| `dq_checks.py` | Cross-source checks after the load |
| `storage.py` | Optional Blob upload/archive, layout `<container>/<source>/<yyyy>/<mm>/<file>` |
| `pipeline.py` | Orchestration and CLI |

## 4. Validation rules as implemented

A row is rejected for the **first** rule it breaks, in this order:

| Step | Rules |
|---|---|
| File | **VR-GEN-01** required header missing → whole file `Failed`, one rejected row without a row number |
| Before row checks | **VR-GEN-04** exact duplicate (all contract columns equal) → later copies dropped, warning |
| Per row | **VR-GEN-02** mandatory empty · **VR-GEN-03** unreadable date · **VR-GEN-08** wrong type or not an allowed value (new, migration `062`) · **VR-AF-01** classification not mappable · **VR-GEN-07** event date in the future or before 2024-01-01 · **VR-QTY-01** negative quantity, or hours / cost / opportunities ≤ 0 · **VR-INS-01** · **VR-RWK-01** · **VR-8D-03** |
| Warnings (row loaded) | **VR-GEN-06** unknown code → key −1 · **VR-RWK-02** cost imputed (`hours × €38`, `cost_source = Imputed`) · **VR-8D-01** milestones out of order · **VR-8D-02** status vs D8 (D8 wins) |
| After row checks | **VR-GEN-05** same business key, different values → all of them rejected, warning |

**Standardisation** (not an error): line, shift, classification and disposition variants from `source_schemas.toml`; dates as `DD.MM.YYYY` / `DD/MM/YYYY` text.

**Where results go:**
- Rejections → `audit.rejected_row`, with the raw row as JSON.
- Warnings → `audit.dq_result`, **one row per file and rule**, with the count in `measured_value` and up to 10 examples in `details`.

## 5. Cross-source checks (`audit.dq_result`, whole warehouse)

| Check | Measures | On the 24-month synthetic data |
|---|---|---|
| DQ-01 | Share of inspection keys with a production record | < 100 %: production rows with injected defects were rejected while their inspections loaded. **Expected, and shows the check works** |
| DQ-02 | Line-months where final-stage inspected ≤ started | Fails in some line-months for the same reason |
| DQ-07 / DQ-08 | Worst deviation of Σ NC qty (Scrap / Rework) vs. bookings, per line-month, limit ±2 % | Deviations come from rejected rows and ≈ 1 % unbooked scrap |
| DQ-10 | Rejected / read in this run, limit 5 % | ≈ 1.7 %: passes |
| DQ-LNK-NC | NC IDs listed by this run's 8D tracker that are not in the warehouse | 0 on the full load |

## 6. Tests

- `tests/unit/test_validate.py` (44): parsers, spelling, every rule on small hand-made files.
- `tests/integration/test_pipeline_testdb.py` (12, needs local SQL Server). Creates and deploys **`QualityKPI_Test`**, never DEV, and loads the committed sample twice. It proves:
  - every injected `Reject` issue is rejected with its rule;
  - **no other row** is rejected;
  - duplicates are counted per file, costs are imputed, unknown codes are loaded as −1 and variants are standardised;
  - DQ checks are recorded and the KPI view is populated;
  - the **rerun loads 0 rows**.
