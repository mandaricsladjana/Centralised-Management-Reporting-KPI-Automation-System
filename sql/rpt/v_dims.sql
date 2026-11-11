/* =============================================================================
   Reporting dimension views (schema rpt). Power BI loads rpt.v_* only, never dw
   (data dictionary §8). Lineage columns are not exposed. Repeatable script.
   ============================================================================= */
CREATE OR ALTER VIEW rpt.v_dim_calendar AS
SELECT date_key, [date], [year], [quarter], quarter_label, [month], month_name, month_short, year_month,
       month_start_date, iso_year, iso_week, day_of_week, day_name, is_weekend, is_plant_holiday, is_working_day
FROM dw.dim_calendar;
GO

CREATE OR ALTER VIEW rpt.v_dim_shift AS
SELECT shift_key, shift_code, shift_name, start_time, end_time, crosses_midnight, sort_order
FROM dw.dim_shift;
GO

CREATE OR ALTER VIEW rpt.v_dim_production_line AS
SELECT line_key, line_code, line_name, process_type, area, is_active
FROM dw.dim_production_line;
GO

CREATE OR ALTER VIEW rpt.v_dim_defect_type AS
SELECT defect_type_key, defect_code, defect_name, defect_category, severity,
       CASE severity WHEN 'A' THEN 'A - Critical' WHEN 'B' THEN 'B - Major' WHEN 'C' THEN 'C - Minor'
                     ELSE defect_category END AS severity_label
FROM dw.dim_defect_type;
GO

CREATE OR ALTER VIEW rpt.v_dim_inspection_stage AS
SELECT stage_key, stage_code, stage_name, stage_sequence
FROM dw.dim_inspection_stage;
GO

/* D10: program flattened into the part (star, not snowflake, in Power BI).
   lifecycle_phase is derived as of today (data dictionary §3.4):
   Launch = first 90 days after SOP, Run-out = last 180 days before EOP. */
CREATE OR ALTER VIEW rpt.v_dim_part AS
SELECT p.part_key, p.part_number, p.part_name, p.component_type,
       p.standard_unit_cost_eur, p.opportunities_per_unit, p.is_active,
       pl.line_code AS primary_line_code, pl.line_name AS primary_line_name,
       vp.program_code, vp.program_name, vp.oem_customer, vp.vehicle_segment,
       CASE WHEN vp.program_key > 0 THEN vp.sop_date END AS sop_date,
       vp.eop_date,
       CASE WHEN p.part_key = -2 OR vp.program_key = -2 THEN 'Not applicable'
            WHEN p.part_key < 0 OR vp.program_key < 0   THEN 'Unknown'
            WHEN ref.today < vp.sop_date                THEN 'Pre-launch'
            WHEN ref.today < DATEADD(DAY, 90, vp.sop_date) THEN 'Launch'
            WHEN vp.eop_date IS NOT NULL AND ref.today > vp.eop_date THEN 'Ended'
            WHEN vp.eop_date IS NOT NULL AND ref.today >= DATEADD(DAY, -180, vp.eop_date) THEN 'Run-out'
            ELSE 'Serial' END AS lifecycle_phase
FROM dw.dim_part p
JOIN dw.dim_vehicle_program vp ON vp.program_key = p.program_key
JOIN dw.dim_production_line pl ON pl.line_key    = p.primary_line_key
CROSS JOIN (SELECT CAST(SYSUTCDATETIME() AS DATE) AS today) ref;
