# Power BI Semantic Model — Phase 9 (part 1)

**Document status:** v1.0, 2026-11-12.
**Related:** [01_project_blueprint.md](01_project_blueprint.md) §6–7, [02_data_dictionary.md](02_data_dictionary.md) §8 and §12, `powerbi/Centralised-KPI-Report-System.SemanticModel/definition/` (TMDL)

## 1. How to open and point at DEV or PROD

Open `powerbi/Centralised-KPI-Report-System.pbip` in Power BI Desktop. Two parameters choose the database (blueprint §8.6). Change them in *Transform data → Edit parameters*:

| Parameter | DEV (default) | PROD |
|---|---|---|
| `SqlServer` | `.\SQLEXPRESS` | `sql-qualitykpi-og6p7.database.windows.net` (sign in with Microsoft account / Entra ID) |
| `SqlDatabase` | `QualityKPI_Dev` | `QualityKPI` |

Storage mode is **Import**. Every table reads one `rpt` view, never `dw` (data dictionary §8). Auto date/time is off, and implicit measures are discouraged.

## 2. Tables

| Table | Source | Grain / role |
|---|---|---|
| Calendar | `rpt.v_dim_calendar` | Day, **marked as date table** |
| Shift, Production Line, Part, Defect Type, Inspection Stage | `rpt.v_dim_*` | Dimensions. Part includes the vehicle program (D10) |
| Production, Inspections, Nonconformities, Scrap, Rework | `rpt.v_fact_*` | Event facts |
| 8D Reports, 8D Actions, Audit Findings | `rpt.v_fact_*` | Accumulating snapshots with derived status flags |
| KPI Targets | `rpt.v_kpi_target` (current rows) | Utility table without relationships |
| Time Comparison | calculation group | Current, Previous Month, Change vs Previous Month, Previous Year, Change vs Previous Year, Rolling 3 Months |

Keys, and the raw quantity columns behind each measure, are hidden. Month and day names sort chronologically.

## 3. Relationships (40)

- **31 active**: one-to-many, single direction, dimension → fact.
- **9 inactive**, each switched on in at least one measure with `USERELATIONSHIP`:
  - 8D target and closed date;
  - action due and completed date;
  - finding due and closed date;
  - 8D → nonconformity and 8D → audit finding (D9).
- `8D Actions` reaches the calendar through `8D Reports` (opened date). Measures on the action due date switch that path off with `CROSSFILTER(..., NONE)`, so no filter path is ambiguous.

## 4. Measures (86)

Measures live on their fact tables in display folders. Names follow the KPI dictionary (blueprint §6.2), and every description defines its acronym.

| Table | Measures |
|---|---|
| Production | Production Volume, Planned Volume, Production Value € |
| Inspections | Inspected Quantity, Inspected Quantity Final Stage, Passed Quantity, Rejected Quantity, FPY %, FPY Final Stage %, FTY % |
| Nonconformities | Defect Quantity, # Nonconformities, Defect Rate %, DPU, DPMO |
| Scrap | Scrapped Quantity, Scrap Cost €, Scrap Rate %, COPQ €, COPQ % |
| Rework | Reworked Quantity, Rework OK Quantity, Rework Cost €, Rework Cost Imputed €, Rework Rate %, Rework Success % |
| 8D Reports | # 8Ds Opened, # Repeat 8Ds, Repeat Defect Rate %, # 8Ds Closed, Avg 8D Closure Days, 8D On-time Closure %, # 8Ds Due, Open 8Ds, Overdue 8Ds, # Linked NCs, # Linked Audit Findings |
| 8D Actions | # Actions Due, # Actions Completed, CA Completion %, # Actions Completed In Period |
| Audit Findings | # Findings Raised, Major NCs, # Findings Closed, Avg Finding Closure Days, Audit On-time Closure %, # Findings Due, Open Findings, Overdue Findings |
| KPI Targets | `<KPI> Target` ×18, `<KPI> Status` ×18 (Green / Amber / Red, same rule as `rpt.fn_kpi_rag`), Core KPIs On Target |

Points worth knowing:
- **FTY %**: product of stage FPYs per line; across lines, weighted by Production Volume (D1, blueprint §6.2).
- **Snapshots** (Open/Overdue 8Ds and findings): measured as of the end of the selected period, never later than today, the same as SQL.
- **Core KPIs On Target**: the scorecard counts how many of 6 core KPIs are green. It is not a blended index.

## 5. Reconciliation: DAX = SQL (Phase 9 acceptance)

All KPIs were queried in DAX against the model and in SQL against `rpt.v_kpi_monthly` (plant level) for **March 2025, September 2025 and February 2026**. **Every value matches** to the precision SQL stores (6 decimals):

| KPI | 2025-03 | 2025-09 | 2026-02 |
|---|---|---|---|
| Production Volume | 135 769 | 169 224 | 161 357 |
| FTY % | 96.3037 % | 95.4611 % | 96.5640 % |
| Defect Rate % | 3.6864 % | 3.7928 % | 2.6175 % |
| DPMO | 4 223.21 | 4 329.45 | 2 942.09 |
| Scrap Rate % / Scrap Cost € | 0.7734 % / 64 478.00 | 0.8474 % / 102 249.30 | 0.6346 % / 65 920.20 |
| Rework Rate % / Success % | 2.0962 % / 90.5833 % | 2.2491 % / 89.7793 % | 1.4527 % / 89.6331 % |
| COPQ € / COPQ % | 96 760.71 / 1.7406 % | 144 673.95 / 2.0298 % | 92 302.65 / 1.3267 % |
| 8D opened / repeat / closed | 1 / 0 / 1 | 3 / 1 / 0 | 2 / 0 / 2 |
| Avg 8D closure days / on-time % | 53.0 / 100 % | – | 38.5 / 50 % |
| Open / overdue 8Ds (month end) | 5 / 1 | 7 / 3 | 4 / 2 |
| Actions due / completed | 8 / 7 | 5 / 3 | 6 / 6 |
| Findings raised / closed / avg days | 8 / 4 / 61.75 | 3 / 3 / 38.0 | 1 / 0 / – |
| Open / overdue findings | 10 / 0 | 2 / 0 | 7 / 1 |

DAX returns *blank* where SQL returns 0 for an empty count. The status measures treat blank counts as 0.

**Scorecard** (Core KPIs On Target): Nov 2024: 4 · Mar 2025: 2 · Sep 2025: 2 (EV launch) · Feb 2026: 5 · Oct 2026: 5.

## 6. Tooling notes

- Built with the local `powerbi-modeling-mcp` server against Power BI Desktop, then exported to the PBIP `definition/` folder as TMDL.
- The hosted Fabric connector returns *403 FeatureNotAvailable* for this tenant, which has no Fabric or Power BI service capacity.
- During the session a recalculation failed with *"The base version must not be negative when impact is requested"*. Disconnecting and reconnecting the MCP session fixed it.
