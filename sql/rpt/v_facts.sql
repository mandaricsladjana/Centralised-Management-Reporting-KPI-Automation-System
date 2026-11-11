/* =============================================================================
   Reporting fact views (schema rpt). Repeatable script.
   Row-level derivations live here so DAX does not duplicate them (blueprint §5.8):
   final inspection stage, closed / overdue / on-time flags, closure days, repeat 8Ds.
   Ratios are NOT pre-aggregated: they are computed as sum / sum at query time.
   "Today" = CAST(SYSUTCDATETIME() AS DATE); month-end snapshots are in v_kpi_monthly.
   ============================================================================= */
CREATE OR ALTER VIEW rpt.v_fact_production AS
SELECT production_key, date_key, shift_key, line_key, part_key, qty_planned, qty_started
FROM dw.fact_production;
GO

/* is_final_stage: the highest stage sequence with data for the line (data dictionary §3.7).
   Defect Rate, DPMO and Passed Quantity use the final stage only (blueprint §6.2). */
CREATE OR ALTER VIEW rpt.v_fact_inspection AS
WITH final_stage AS (
    SELECT f.line_key, MAX(st.stage_sequence) AS final_sequence
    FROM dw.fact_inspection f
    JOIN dw.dim_inspection_stage st ON st.stage_key = f.stage_key
    WHERE st.stage_key > 0
    GROUP BY f.line_key
)
SELECT f.inspection_key, f.date_key, f.shift_key, f.line_key, f.part_key, f.stage_key,
       f.qty_inspected, f.qty_passed_first_time, f.qty_failed,
       CAST(CASE WHEN st.stage_key > 0 AND st.stage_sequence = fs.final_sequence THEN 1 ELSE 0 END AS BIT) AS is_final_stage
FROM dw.fact_inspection f
JOIN dw.dim_inspection_stage st ON st.stage_key = f.stage_key
LEFT JOIN final_stage fs        ON fs.line_key  = f.line_key;
GO

CREATE OR ALTER VIEW rpt.v_fact_nonconformity AS
SELECT nc_key, nc_id, date_key, shift_key, line_key, part_key, stage_key, defect_type_key,
       qty_affected, disposition, eight_d_key
FROM dw.fact_nonconformity;
GO

CREATE OR ALTER VIEW rpt.v_fact_scrap AS
SELECT scrap_key, scrap_id, date_key, shift_key, line_key, part_key, defect_type_key,
       qty_scrapped, unit_cost_eur, scrap_cost_eur
FROM dw.fact_scrap;
GO

CREATE OR ALTER VIEW rpt.v_fact_rework AS
SELECT rework_key, rework_id, date_key, shift_key, line_key, part_key, defect_type_key,
       qty_reworked, qty_rework_ok, labour_hours, labour_rate_eur, rework_cost_eur, cost_source
FROM dw.fact_rework;
GO

/* Status is derived, not trusted (blueprint §5.5): Closed = D8 set.
   is_repeat: an earlier 8D for the same part AND defect type was closed within the
   180 days before this one was opened (settings.toml [kpi] repeat_defect_window_days;
   tests/unit/test_rpt_views.py keeps the two in sync). Unknown part/defect never repeats. */
CREATE OR ALTER VIEW rpt.v_fact_8d AS
SELECT e.eight_d_key, e.eight_d_id, e.opened_date_key, e.part_key, e.line_key, e.defect_type_key,
       e.problem_source, e.owner_name, e.owner_department,
       e.d1_date, e.d2_date, e.d3_date, e.d4_date, e.d5_date, e.d6_date, e.d7_date, e.d8_date,
       e.target_closure_date_key, e.closed_date_key, e.status_reported,
       od.[date] AS opened_date, td.[date] AS target_closure_date, e.d8_date AS closed_date,
       CAST(CASE WHEN e.d8_date IS NOT NULL THEN 1 ELSE 0 END AS BIT) AS is_closed,
       CASE WHEN e.d8_date IS NOT NULL THEN 'Closed'
            WHEN ref.today > td.[date] THEN 'Overdue' ELSE 'Open' END AS status_derived,
       CAST(CASE WHEN e.d8_date IS NULL AND ref.today > td.[date] THEN 1 ELSE 0 END AS BIT) AS is_overdue,
       CASE WHEN e.d8_date IS NOT NULL
            THEN CAST(CASE WHEN e.d8_date <= td.[date] THEN 1 ELSE 0 END AS BIT) END AS is_closed_on_time,
       DATEDIFF(DAY, od.[date], e.d8_date) AS days_to_close,
       DATEDIFF(DAY, od.[date], COALESCE(e.d8_date, ref.today)) AS days_open,
       CAST(CASE WHEN e.part_key > 0 AND e.defect_type_key > 0 AND EXISTS (
                SELECT 1 FROM dw.fact_8d p
                WHERE p.part_key = e.part_key AND p.defect_type_key = e.defect_type_key
                  AND p.eight_d_key <> e.eight_d_key
                  AND p.d8_date <  od.[date]
                  AND p.d8_date >= DATEADD(DAY, -180, od.[date]))
            THEN 1 ELSE 0 END AS BIT) AS is_repeat,
       CAST(CASE WHEN EXISTS (SELECT 1 FROM dw.fact_nonconformity nc WHERE nc.eight_d_key = e.eight_d_key)
            THEN 1 ELSE 0 END AS BIT) AS has_linked_nc,
       CASE WHEN e.d8_date IS NULL AND e.status_reported LIKE N'%closed%' THEN 'Status Closed, no D8'
            WHEN e.d8_date IS NOT NULL AND (e.status_reported IS NULL OR e.status_reported NOT LIKE N'%closed%')
                 THEN 'D8 set, status not Closed'
       END AS status_mismatch                                           -- VR-8D-02, for the DQ page
FROM dw.fact_8d e
JOIN dw.dim_calendar od ON od.date_key = e.opened_date_key
JOIN dw.dim_calendar td ON td.date_key = e.target_closure_date_key
CROSS JOIN (SELECT CAST(SYSUTCDATETIME() AS DATE) AS today) ref;
GO

CREATE OR ALTER VIEW rpt.v_fact_8d_action AS
SELECT a.action_key, a.action_id, a.eight_d_key, a.action_type, a.action_description, a.owner_name,
       a.due_date_key, a.completed_date_key, a.status_reported,
       dd.[date] AS due_date, cd.[date] AS completed_date,
       CAST(CASE WHEN a.completed_date_key IS NOT NULL THEN 1 ELSE 0 END AS BIT) AS is_completed,
       CASE WHEN a.completed_date_key IS NOT NULL
            THEN CAST(CASE WHEN cd.[date] <= dd.[date] THEN 1 ELSE 0 END AS BIT) END AS is_completed_on_time,
       CAST(CASE WHEN a.completed_date_key IS NULL AND ref.today > dd.[date] THEN 1 ELSE 0 END AS BIT) AS is_overdue
FROM dw.fact_8d_action a
JOIN dw.dim_calendar dd      ON dd.date_key = a.due_date_key
LEFT JOIN dw.dim_calendar cd ON cd.date_key = a.completed_date_key
CROSS JOIN (SELECT CAST(SYSUTCDATETIME() AS DATE) AS today) ref;
GO

CREATE OR ALTER VIEW rpt.v_fact_audit_finding AS
SELECT f.finding_key, f.finding_id, f.audit_id, f.audit_date_key, f.audit_type, f.standard_reference,
       f.line_key, f.classification, f.finding_description, f.owner_name,
       f.due_date_key, f.closed_date_key, f.eight_d_key, f.status_reported,
       ad.[date] AS audit_date, dd.[date] AS due_date, cd.[date] AS closed_date,
       CASE f.classification WHEN 'Major NC' THEN 1 WHEN 'Minor NC' THEN 2 WHEN 'Observation' THEN 3 ELSE 4 END
           AS classification_sort,
       CAST(CASE WHEN f.closed_date_key IS NOT NULL THEN 1 ELSE 0 END AS BIT) AS is_closed,
       CAST(CASE WHEN f.closed_date_key IS NULL AND ref.today > dd.[date] THEN 1 ELSE 0 END AS BIT) AS is_overdue,
       CASE WHEN f.closed_date_key IS NOT NULL
            THEN CAST(CASE WHEN cd.[date] <= dd.[date] THEN 1 ELSE 0 END AS BIT) END AS is_closed_on_time,
       DATEDIFF(DAY, ad.[date], cd.[date]) AS days_to_close
FROM dw.fact_audit_finding f
JOIN dw.dim_calendar ad      ON ad.date_key = f.audit_date_key
JOIN dw.dim_calendar dd      ON dd.date_key = f.due_date_key
LEFT JOIN dw.dim_calendar cd ON cd.date_key = f.closed_date_key
CROSS JOIN (SELECT CAST(SYSUTCDATETIME() AS DATE) AS today) ref;
