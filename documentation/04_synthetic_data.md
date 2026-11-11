# Synthetic Source Data — Phase 5

**Document status:** v1.0, 2026-11-11.
**Related:** [01_project_blueprint.md](01_project_blueprint.md) §4, [02_data_dictionary.md](02_data_dictionary.md) §6–7, `config/source_schemas.toml`, `python/generate/`

All data is **fictional**: the company (*Danube Automotive Components*), the OEMs (*Auron Motors*, *Velora Automotive*, *Kestrel EV Works*), the people and the parts. Audit clause references follow the IATF 16949 / VDA 6.3 numbering style, but no standard text is reproduced.

---

## 1. How to run

```powershell
cd python
..\.venv\Scripts\python.exe -m generate             # 24 months -> data\raw     (git-ignored, ~9 MB, ~45 s)
..\.venv\Scripts\python.exe -m generate --sample    # March 2025 -> data\sample (committed, ~0.4 MB)
..\.venv\Scripts\python.exe -m generate --seed 7 --out C:\temp\variant
```

- **Reproducible:** the same seed gives **byte-identical files**. `.xlsx` files are re-zipped with fixed timestamps, because openpyxl would otherwise stamp the current time into them. The SHA-256 of every file is recorded in `_generation/generation.json`. This matters because the ETL skips already-loaded files by hash (D20).
- `data/sample/**` is marked `-text` in `.gitattributes` so git never changes its bytes.
- The seed (`20261111`) and the DQ rate (3 %) come from `config/settings.toml` `[synthetic]`.

## 2. One contract, two users

`config/source_schemas.toml` describes every file: department headers, the target (staging) column, type, mandatory flag, and the accepted spelling variants. The generator **writes** files from it and the ETL (Phase 6) will **read** them with it, so headers cannot drift apart.

| File | Owner | Rows (24 months) | Format notes |
|---|---|---|---|
| `master_lines.xlsx`, `master_programs.xlsx`, `master_parts.xlsx`, `master_defects.xlsx` | Production, Program mgmt, Engineering, Quality | 6 / 3 / 15 / 15 | Clean master data; `Active` = Y/N |
| `production_daily_YYYY_MM.xlsx` (24 files) | Production | ≈ 21 000 | One file per month |
| `qa_inspections.csv` | QA | ≈ 32 000 | System export, ISO dates |
| `qa_defects.csv` | QA | ≈ 59 000 | One row per NC (defect type) |
| `scrap_log.csv` | Production / Finance | ≈ 13 600 | `Cost EUR` is Finance's estimate, often blank, **not loaded** (D7) |
| `rework_log.xlsx` | Rework area | ≈ 35 000 | Cost may be blank (imputed, VR-RWK-02) |
| `8d_tracker.xlsx` | Quality engineering | ≈ 38 | D1–D8 dates; `Linked NCs` comma-separated |
| `8d_actions.xlsx` | Quality engineering | ≈ 120 | |
| `audit_findings.xlsx` | Quality management | ≈ 80 | Blank line = plant-wide finding |

## 3. How the data is produced

For every working day (weekends, plant holidays and the summer shutdown come from `settings.toml`), line, active part and shift:

1. **Production:** planned volume per shift × ramp-up after SOP (40 % → 100 % over 8 weeks) × run-out before EOP (→ 50 %); started ≈ 90–101 % of planned.
2. **Inspection**, stage by stage (stage map D1 / data dictionary §3.7). Failure probability = stage base rate × shift factor × launch factor × active-event factor × lasting improvement after closed 8Ds × plant trend × day-to-day noise. Units that pass, are reworked OK, or are accepted as-is go on to the next stage.
3. **Nonconformities:** failed units are split into defect types (stage-specific mix; an active event's defect dominates). Each (date, shift, line, part, stage, defect) becomes one NC with a disposition by severity.
4. **Scrap and rework bookings** follow the NC dispositions (about 1 % of scrap is never booked, so DQ-07 shows a small, realistic gap). Rework cost = hours × €38/h + material.
5. **8D reports:** 9 scripted events plus 1–2 unscripted 8Ds a month for the worst (line, part, defect) combination. NCs of the 21 days before opening are linked. Actions follow the milestones; about 15 % are late.
6. **Audits:** annual system audits, quarterly process audits (VDA 6.3), monthly product and layered process audits, two customer audits.

### 3.1 The built-in story (what the report should reveal)

| Story | Where | Visible as |
|---|---|---|
| Night shift on the paint line is worse | L04, shift N | FPY ≈ 96.3 % at night vs ≈ 97.8 % in the day |
| Paint runs on night shift, fixed by an 8D | L04, DAC-30210, Jan–Mar 2025 | Spike, then a lasting drop after D8 |
| **Repeat defect** | L01, DAC-10421, sink marks: 8D closed 11 Apr 2025, back on 1 Sep 2025 (143 days < 180) | Repeat Defect Rate > 0 in Sep 2025 |
| Customer complaints | L05 missing clip (May 2025), L03 weld crack (Feb 2026, closed late) | 8D on-time % below target |
| Audit → 8D | Customer audit 6 Oct 2025, Major NC → 8D on wrong torque, L05 | Audit page: finding linked to an 8D |
| **EV launch** (SOP 1 Sep 2025) | VP-EV-D parts, esp. L06 door panels | Failure rate ≈ 45 ‰ in Sep 2025 → ≈ 11 ‰ by Jan 2026 |
| Still open / overdue at the end | L04 dirt inclusion (opened May 2026, overdue), L02 warpage (Sep 2026) | Open and overdue 8Ds on the last snapshot |
| Plant-wide improvement | All lines | Defect rate ≈ 3.3 % (first 6 months) → ≈ 2.7 % (last 6) |
| Hatchback run-out (EOP 30 Jun 2026) | VP-HATCH-B parts | Volume falls to ≈ 50 %, then stops |

KPI levels were tuned so that the traffic lights are **mixed** rather than all green: COPQ % is green in about 13 of 24 months, defect rate in about 14, and the cost targets were rescaled to the plant's size (D24).

## 4. Injected data-quality issues

About **3 %** of transactional rows carry a defect that a validation rule must catch (data dictionary §7). Separately, about 10 000 values are **valid variants** that must be standardised, not rejected. Every one is listed in `_generation/injected_issues.csv` with file, source row number, business key, issue, rule and expected outcome (`Reject` / `Warn` / `Standardise`). Phase 6 tests use this list to prove that each issue is handled.

| Issue | Rule | Expected | Files |
|---|---|---|---|
| Exact duplicate row | VR-GEN-04 | Warn (drop copy) | production, qa_defects, scrap, rework |
| Same business key, different values | VR-GEN-05 | Reject both | qa_inspections |
| Mandatory value empty | VR-GEN-02 | Reject | production, inspections, defects, scrap, actions |
| Impossible date (`31.02.2025`, `2025-13-05`) | VR-GEN-03 | Reject | production, scrap |
| Event date in 2030 | VR-GEN-07 | Reject | production, qa_defects |
| Unknown part / defect / stage code | VR-GEN-06 | Warn, load as −1 | production, inspections, defects, scrap |
| Negative quantity | VR-QTY-01 | Reject | all transactional files |
| Passed first time > inspected | VR-INS-01 | Reject | qa_inspections |
| Qty OK after rework > reworked | VR-RWK-01 | Reject | rework_log |
| Rework cost blank | VR-RWK-02 | Warn, impute | rework_log |
| Milestones out of order | VR-8D-01 | Warn | 8d_tracker |
| Status vs D8 disagree | VR-8D-02 | Warn, D8 wins | 8d_tracker |
| Action for an unknown 8D | VR-8D-03 | Reject | 8d_actions |
| Unmappable classification | VR-AF-01 | Reject | audit_findings |
| Line / shift spelling variants (`Line 1`, `LINE-01`, `Night`, …) | — | Standardise | production, scrap, rework |
| Dates as text `DD.MM.YYYY` / `DD/MM/YYYY` | — | Standardise | production, scrap |
| Classification variants (`MAJ`, `Obs`, …) | — | Standardise | audit_findings |

Unknown part numbers are always distinct (`DAC-9nnnn`), so two unknown parts never collide on the same production grain (data dictionary §11.1).

## 5. Tests

`tests/unit/test_generate.py` (18 tests):
- same seed gives the same bytes;
- headers match the contract;
- every manifest row really shows its defect;
- clean-data invariants: inspection balance, flow ≤ started, scrap/rework reconcile with NCs, ID formats, milestone order, unique NC links;
- every story in §3.1 is present.
