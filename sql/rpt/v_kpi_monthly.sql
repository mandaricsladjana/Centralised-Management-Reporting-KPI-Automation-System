/* =============================================================================
   rpt.v_kpi_monthly: SQL reference implementation of the monthly KPIs
   (blueprint §6.2). Used to prove that the DAX measures return the same numbers
   (Phase 9 acceptance: DAX = SQL for 3 sample months). Repeatable script.

   Grain: one row per month x production line (row_level = 'Line') plus one
   plant row per month (row_level = 'Plant', line_code = 'ALL').
   - Every ratio is SUM(numerator) / SUM(denominator), never an average of rates.
     Numerators and denominators are exposed too, so any figure can be re-derived.
   - Plant rows sum the line numerators, except FTY: plant FTY = line FTYs
     weighted by qty_started (blueprint §6.2).
   - FTY (D1) = product of the FPYs of the line's stages with inspections in the month.
   - Defect rate, DPU, DPMO and passed quantity use the line's final stage.
   - Snapshot KPIs (open / overdue 8Ds and findings) are as of the month end,
     for months up to the current one.
   - 8D and action KPIs use the 8D's line; audit findings use the finding's line
     (-2 = plant-wide, shown as its own line row and included in the plant row).
   ============================================================================= */
CREATE OR ALTER VIEW rpt.v_kpi_monthly AS
WITH
cal AS (SELECT date_key, month_start_date FROM dw.dim_calendar),
months AS (
    SELECT DISTINCT month_start_date, EOMONTH(month_start_date) AS month_end
    FROM dw.dim_calendar
    WHERE month_start_date <= CAST(SYSUTCDATETIME() AS DATE)
),
prod AS (
    SELECT c.month_start_date, f.line_key,
           SUM(CAST(f.qty_started AS BIGINT))         AS qty_started,
           SUM(f.qty_started * p.standard_unit_cost_eur) AS production_value_eur
    FROM rpt.v_fact_production f
    JOIN cal c         ON c.date_key = f.date_key
    JOIN dw.dim_part p ON p.part_key = f.part_key
    GROUP BY c.month_start_date, f.line_key
),
insp_stage AS (
    SELECT c.month_start_date, f.line_key, f.stage_key,
           SUM(CAST(f.qty_inspected AS BIGINT))         AS inspected,
           SUM(CAST(f.qty_passed_first_time AS BIGINT)) AS passed,
           SUM(CAST(f.qty_failed AS BIGINT))            AS failed,
           SUM(CASE WHEN f.is_final_stage = 1 THEN CAST(f.qty_inspected AS BIGINT) ELSE 0 END)         AS inspected_final,
           SUM(CASE WHEN f.is_final_stage = 1 THEN CAST(f.qty_passed_first_time AS BIGINT) ELSE 0 END) AS passed_final,
           SUM(CASE WHEN f.is_final_stage = 1
                    THEN CAST(f.qty_inspected AS BIGINT) * p.opportunities_per_unit ELSE 0 END)        AS opportunities_final
    FROM rpt.v_fact_inspection f
    JOIN cal c         ON c.date_key = f.date_key
    JOIN dw.dim_part p ON p.part_key = f.part_key
    GROUP BY c.month_start_date, f.line_key, f.stage_key
),
insp AS (
    SELECT month_start_date, line_key,
           SUM(failed)              AS qty_rejected,
           SUM(inspected_final)     AS qty_inspected_final,
           SUM(passed_final)        AS qty_passed_final,
           SUM(opportunities_final) AS opportunities_final,
           -- FTY = product of stage FPYs; EXP(SUM(LOG)) with an explicit zero guard
           CASE WHEN MAX(inspected) = 0 THEN NULL
                WHEN MIN(CASE WHEN inspected > 0 THEN passed END) = 0 THEN 0.0
                ELSE EXP(SUM(CASE WHEN inspected > 0 AND passed > 0
                                  THEN LOG(CAST(passed AS FLOAT) / inspected) END)) END AS fty
    FROM insp_stage
    GROUP BY month_start_date, line_key
),
nc AS (
    SELECT c.month_start_date, f.line_key, SUM(CAST(f.qty_affected AS BIGINT)) AS qty_defects
    FROM rpt.v_fact_nonconformity f JOIN cal c ON c.date_key = f.date_key
    GROUP BY c.month_start_date, f.line_key
),
scrap AS (
    SELECT c.month_start_date, f.line_key,
           SUM(CAST(f.qty_scrapped AS BIGINT)) AS qty_scrapped, SUM(f.scrap_cost_eur) AS scrap_cost_eur
    FROM rpt.v_fact_scrap f JOIN cal c ON c.date_key = f.date_key
    GROUP BY c.month_start_date, f.line_key
),
rework AS (
    SELECT c.month_start_date, f.line_key,
           SUM(CAST(f.qty_reworked AS BIGINT))  AS qty_reworked,
           SUM(CAST(f.qty_rework_ok AS BIGINT)) AS qty_rework_ok,
           SUM(f.rework_cost_eur)               AS rework_cost_eur,
           SUM(CASE WHEN f.cost_source = 'Imputed' THEN f.rework_cost_eur ELSE 0 END) AS rework_cost_imputed_eur
    FROM rpt.v_fact_rework f JOIN cal c ON c.date_key = f.date_key
    GROUP BY c.month_start_date, f.line_key
),
ed_opened AS (
    SELECT c.month_start_date, e.line_key, COUNT(*) AS eight_d_opened, SUM(CAST(e.is_repeat AS INT)) AS eight_d_repeat
    FROM rpt.v_fact_8d e JOIN cal c ON c.date_key = e.opened_date_key
    GROUP BY c.month_start_date, e.line_key
),
ed_closed AS (
    SELECT c.month_start_date, e.line_key, COUNT(*) AS eight_d_closed,
           SUM(e.days_to_close) AS eight_d_closure_days, SUM(CAST(e.is_closed_on_time AS INT)) AS eight_d_closed_on_time
    FROM rpt.v_fact_8d e JOIN cal c ON c.date_key = e.closed_date_key
    GROUP BY c.month_start_date, e.line_key
),
ed_snapshot AS (
    SELECT m.month_start_date, e.line_key,
           COUNT(*) AS eight_d_open_at_month_end,
           SUM(CASE WHEN e.target_closure_date < m.month_end THEN 1 ELSE 0 END) AS eight_d_overdue_at_month_end
    FROM months m
    JOIN rpt.v_fact_8d e ON e.opened_date <= m.month_end AND (e.closed_date IS NULL OR e.closed_date > m.month_end)
    GROUP BY m.month_start_date, e.line_key
),
actions AS (   -- due in the month; completed = completed by that month's end
    SELECT c.month_start_date, e.line_key, COUNT(*) AS actions_due,
           SUM(CASE WHEN a.completed_date <= EOMONTH(c.month_start_date) THEN 1 ELSE 0 END) AS actions_completed
    FROM rpt.v_fact_8d_action a
    JOIN dw.fact_8d e ON e.eight_d_key = a.eight_d_key
    JOIN cal c        ON c.date_key    = a.due_date_key
    GROUP BY c.month_start_date, e.line_key
),
af_raised AS (
    SELECT c.month_start_date, f.line_key, COUNT(*) AS findings_raised,
           SUM(CASE WHEN f.classification = 'Major NC' THEN 1 ELSE 0 END) AS major_nc
    FROM rpt.v_fact_audit_finding f JOIN cal c ON c.date_key = f.audit_date_key
    GROUP BY c.month_start_date, f.line_key
),
af_closed AS (
    SELECT c.month_start_date, f.line_key, COUNT(*) AS findings_closed,
           SUM(f.days_to_close) AS finding_closure_days, SUM(CAST(f.is_closed_on_time AS INT)) AS findings_closed_on_time
    FROM rpt.v_fact_audit_finding f JOIN cal c ON c.date_key = f.closed_date_key
    GROUP BY c.month_start_date, f.line_key
),
af_snapshot AS (
    SELECT m.month_start_date, f.line_key,
           COUNT(*) AS findings_open_at_month_end,
           SUM(CASE WHEN f.due_date < m.month_end THEN 1 ELSE 0 END) AS findings_overdue_at_month_end
    FROM months m
    JOIN rpt.v_fact_audit_finding f ON f.audit_date <= m.month_end AND (f.closed_date IS NULL OR f.closed_date > m.month_end)
    GROUP BY m.month_start_date, f.line_key
),
keys AS (
    SELECT month_start_date, line_key FROM prod        UNION SELECT month_start_date, line_key FROM insp
    UNION SELECT month_start_date, line_key FROM nc    UNION SELECT month_start_date, line_key FROM scrap
    UNION SELECT month_start_date, line_key FROM rework
    UNION SELECT month_start_date, line_key FROM ed_opened UNION SELECT month_start_date, line_key FROM ed_closed
    UNION SELECT month_start_date, line_key FROM ed_snapshot UNION SELECT month_start_date, line_key FROM actions
    UNION SELECT month_start_date, line_key FROM af_raised UNION SELECT month_start_date, line_key FROM af_closed
    UNION SELECT month_start_date, line_key FROM af_snapshot
),
line_rows AS (
    SELECT k.month_start_date, k.line_key,
           ISNULL(pr.qty_started, 0)                    AS qty_started,
           ISNULL(pr.production_value_eur, 0)           AS production_value_eur,
           ISNULL(i.qty_rejected, 0)                    AS qty_rejected,
           ISNULL(i.qty_inspected_final, 0)             AS qty_inspected_final,
           ISNULL(i.qty_passed_final, 0)                AS qty_passed_final,
           ISNULL(i.opportunities_final, 0)             AS opportunities_final,
           ISNULL(n.qty_defects, 0)                     AS qty_defects,
           ISNULL(s.qty_scrapped, 0)                    AS qty_scrapped,
           ISNULL(s.scrap_cost_eur, 0)                  AS scrap_cost_eur,
           ISNULL(r.qty_reworked, 0)                    AS qty_reworked,
           ISNULL(r.qty_rework_ok, 0)                   AS qty_rework_ok,
           ISNULL(r.rework_cost_eur, 0)                 AS rework_cost_eur,
           ISNULL(r.rework_cost_imputed_eur, 0)         AS rework_cost_imputed_eur,
           ISNULL(eo.eight_d_opened, 0)                 AS eight_d_opened,
           ISNULL(eo.eight_d_repeat, 0)                 AS eight_d_repeat,
           ISNULL(ec.eight_d_closed, 0)                 AS eight_d_closed,
           ISNULL(ec.eight_d_closure_days, 0)           AS eight_d_closure_days,
           ISNULL(ec.eight_d_closed_on_time, 0)         AS eight_d_closed_on_time,
           ISNULL(es.eight_d_open_at_month_end, 0)      AS eight_d_open_at_month_end,
           ISNULL(es.eight_d_overdue_at_month_end, 0)   AS eight_d_overdue_at_month_end,
           ISNULL(ac.actions_due, 0)                    AS actions_due,
           ISNULL(ac.actions_completed, 0)              AS actions_completed,
           ISNULL(ar.findings_raised, 0)                AS findings_raised,
           ISNULL(ar.major_nc, 0)                       AS major_nc,
           ISNULL(fc.findings_closed, 0)                AS findings_closed,
           ISNULL(fc.finding_closure_days, 0)           AS finding_closure_days,
           ISNULL(fc.findings_closed_on_time, 0)        AS findings_closed_on_time,
           ISNULL(fs.findings_open_at_month_end, 0)     AS findings_open_at_month_end,
           ISNULL(fs.findings_overdue_at_month_end, 0)  AS findings_overdue_at_month_end,
           i.fty
    FROM keys k
    LEFT JOIN prod pr        ON pr.month_start_date = k.month_start_date AND pr.line_key = k.line_key
    LEFT JOIN insp i         ON i.month_start_date  = k.month_start_date AND i.line_key  = k.line_key
    LEFT JOIN nc n           ON n.month_start_date  = k.month_start_date AND n.line_key  = k.line_key
    LEFT JOIN scrap s        ON s.month_start_date  = k.month_start_date AND s.line_key  = k.line_key
    LEFT JOIN rework r       ON r.month_start_date  = k.month_start_date AND r.line_key  = k.line_key
    LEFT JOIN ed_opened eo   ON eo.month_start_date = k.month_start_date AND eo.line_key = k.line_key
    LEFT JOIN ed_closed ec   ON ec.month_start_date = k.month_start_date AND ec.line_key = k.line_key
    LEFT JOIN ed_snapshot es ON es.month_start_date = k.month_start_date AND es.line_key = k.line_key
    LEFT JOIN actions ac     ON ac.month_start_date = k.month_start_date AND ac.line_key = k.line_key
    LEFT JOIN af_raised ar   ON ar.month_start_date = k.month_start_date AND ar.line_key = k.line_key
    LEFT JOIN af_closed fc   ON fc.month_start_date = k.month_start_date AND fc.line_key = k.line_key
    LEFT JOIN af_snapshot fs ON fs.month_start_date = k.month_start_date AND fs.line_key = k.line_key
),
all_rows AS (
    SELECT 'Line' AS row_level, * FROM line_rows
    UNION ALL
    SELECT 'Plant', month_start_date, NULL,
           SUM(qty_started), SUM(production_value_eur), SUM(qty_rejected), SUM(qty_inspected_final),
           SUM(qty_passed_final), SUM(opportunities_final), SUM(qty_defects), SUM(qty_scrapped), SUM(scrap_cost_eur),
           SUM(qty_reworked), SUM(qty_rework_ok), SUM(rework_cost_eur), SUM(rework_cost_imputed_eur),
           SUM(eight_d_opened), SUM(eight_d_repeat), SUM(eight_d_closed), SUM(eight_d_closure_days),
           SUM(eight_d_closed_on_time), SUM(eight_d_open_at_month_end), SUM(eight_d_overdue_at_month_end),
           SUM(actions_due), SUM(actions_completed), SUM(findings_raised), SUM(major_nc), SUM(findings_closed),
           SUM(finding_closure_days), SUM(findings_closed_on_time), SUM(findings_open_at_month_end),
           SUM(findings_overdue_at_month_end),
           -- plant FTY: volume-weighted average of line FTYs (lines without inspections excluded)
           SUM(fty * qty_started) / NULLIF(SUM(CASE WHEN fty IS NOT NULL THEN qty_started END), 0)
    FROM line_rows
    GROUP BY month_start_date
)
SELECT
    ym.year_month, a.month_start_date, a.row_level, a.line_key,
    CASE WHEN a.row_level = 'Plant' THEN 'ALL' ELSE l.line_code END AS line_code,
    -- additive measures and KPI inputs
    a.qty_started AS production_volume, a.production_value_eur,
    a.qty_inspected_final, a.qty_passed_final AS passed_quantity, a.qty_rejected AS rejected_quantity,
    a.opportunities_final, a.qty_defects,
    a.qty_scrapped, a.scrap_cost_eur, a.qty_reworked, a.qty_rework_ok, a.rework_cost_eur, a.rework_cost_imputed_eur,
    a.scrap_cost_eur + a.rework_cost_eur AS copq_eur,
    a.eight_d_opened, a.eight_d_repeat, a.eight_d_closed, a.eight_d_closure_days, a.eight_d_closed_on_time,
    a.eight_d_open_at_month_end AS open_8d_count, a.eight_d_overdue_at_month_end AS overdue_8d_count,
    a.actions_due, a.actions_completed,
    a.findings_raised, a.major_nc AS major_nc_count, a.findings_closed, a.finding_closure_days,
    a.findings_closed_on_time,
    a.findings_open_at_month_end AS open_findings_count, a.findings_overdue_at_month_end AS overdue_findings_count,
    -- ratios: SUM / SUM, fractions (0.97 = 97 %), NULL when the denominator is 0
    CAST(CAST(a.qty_passed_final AS DECIMAL(38,10)) / NULLIF(a.qty_inspected_final, 0) AS DECIMAL(18,6)) AS fpy_final_stage_pct,
    CAST(a.fty AS DECIMAL(18,6))                                                                          AS fty_pct,
    CAST(CAST(a.qty_defects AS DECIMAL(38,10)) / NULLIF(a.qty_inspected_final, 0) AS DECIMAL(18,6))      AS defect_rate_pct,
    CAST(CAST(a.qty_defects AS DECIMAL(38,10)) / NULLIF(a.qty_inspected_final, 0) AS DECIMAL(18,6))      AS dpu,
    CAST(CAST(a.qty_defects AS DECIMAL(38,10)) * 1000000 / NULLIF(a.opportunities_final, 0) AS DECIMAL(18,2)) AS dpmo,
    CAST(CAST(a.qty_scrapped AS DECIMAL(38,10)) / NULLIF(a.qty_started, 0) AS DECIMAL(18,6))             AS scrap_rate_pct,
    CAST(CAST(a.qty_reworked AS DECIMAL(38,10)) / NULLIF(a.qty_started, 0) AS DECIMAL(18,6))             AS rework_rate_pct,
    CAST(CAST(a.qty_rework_ok AS DECIMAL(38,10)) / NULLIF(a.qty_reworked, 0) AS DECIMAL(18,6))           AS rework_success_pct,
    CAST((a.scrap_cost_eur + a.rework_cost_eur) / NULLIF(a.production_value_eur, 0) AS DECIMAL(18,6))    AS copq_pct,
    CAST(CAST(a.eight_d_closure_days AS DECIMAL(38,10)) / NULLIF(a.eight_d_closed, 0) AS DECIMAL(18,2))  AS avg_8d_closure_days,
    CAST(CAST(a.eight_d_closed_on_time AS DECIMAL(38,10)) / NULLIF(a.eight_d_closed, 0) AS DECIMAL(18,6)) AS eight_d_ontime_pct,
    CAST(CAST(a.actions_completed AS DECIMAL(38,10)) / NULLIF(a.actions_due, 0) AS DECIMAL(18,6))        AS ca_completion_pct,
    CAST(CAST(a.eight_d_repeat AS DECIMAL(38,10)) / NULLIF(a.eight_d_opened, 0) AS DECIMAL(18,6))        AS repeat_defect_pct,
    CAST(CAST(a.findings_closed_on_time AS DECIMAL(38,10)) / NULLIF(a.findings_closed, 0) AS DECIMAL(18,6)) AS audit_ontime_pct,
    CAST(CAST(a.finding_closure_days AS DECIMAL(38,10)) / NULLIF(a.findings_closed, 0) AS DECIMAL(18,2)) AS avg_finding_closure_days
FROM all_rows a
JOIN (SELECT DISTINCT month_start_date, year_month FROM dw.dim_calendar) ym ON ym.month_start_date = a.month_start_date
LEFT JOIN dw.dim_production_line l ON l.line_key = a.line_key;
GO

/* FPY per inspection stage (blueprint §6.2: FPY is per stage, never summed across stages). */
CREATE OR ALTER VIEW rpt.v_kpi_monthly_stage AS
SELECT c.year_month, c.month_start_date, l.line_code, st.stage_code, st.stage_sequence,
       MAX(CAST(f.is_final_stage AS INT)) AS is_final_stage,
       SUM(CAST(f.qty_inspected AS BIGINT))         AS qty_inspected,
       SUM(CAST(f.qty_passed_first_time AS BIGINT)) AS qty_passed_first_time,
       CAST(CAST(SUM(CAST(f.qty_passed_first_time AS BIGINT)) AS DECIMAL(38,10))
            / NULLIF(SUM(CAST(f.qty_inspected AS BIGINT)), 0) AS DECIMAL(18,6)) AS fpy_pct
FROM rpt.v_fact_inspection f
JOIN dw.dim_calendar c         ON c.date_key  = f.date_key
JOIN dw.dim_production_line l  ON l.line_key  = f.line_key
JOIN dw.dim_inspection_stage st ON st.stage_key = f.stage_key
GROUP BY c.year_month, c.month_start_date, l.line_code, st.stage_code, st.stage_sequence;
