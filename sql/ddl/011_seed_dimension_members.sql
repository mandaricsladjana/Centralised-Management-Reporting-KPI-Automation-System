/* =============================================================================
   011_seed_dimension_members.sql
   1. Special members in every dimension except the calendar (D11):
        -1 = Unknown         (code not found in master data, VR-GEN-06)
        -2 = Not applicable  (e.g. a plant-wide audit finding with no line)
   2. Reference data that has no source file: shifts (§3.2), inspection stages (§3.7).
   All rows are owned by etl_run_id 0 (the deploy run, created in 002).
   ============================================================================= */
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

DECLARE @src NVARCHAR(260) = N'sql/ddl/011_seed_dimension_members.sql';

/* --- dim_shift ------------------------------------------------------------- */
SET IDENTITY_INSERT dw.dim_shift ON;
INSERT INTO dw.dim_shift (shift_key, shift_code, shift_name, start_time, end_time, crosses_midnight, sort_order, etl_run_id, source_file)
SELECT v.k, v.c, v.n, '00:00', '00:00', 0, v.s, 0, @src
FROM (VALUES (-1, 'UNK', N'Unknown', 98), (-2, 'N/A', N'Not applicable', 99)) AS v (k, c, n, s)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_shift d WHERE d.shift_key = v.k);
SET IDENTITY_INSERT dw.dim_shift OFF;

/* --- dim_production_line --------------------------------------------------- */
SET IDENTITY_INSERT dw.dim_production_line ON;
INSERT INTO dw.dim_production_line (line_key, line_code, line_name, process_type, area, is_active, etl_run_id, source_file)
SELECT v.k, v.c, v.n, v.n, v.n, 0, 0, @src
FROM (VALUES (-1, 'UNK', N'Unknown'), (-2, 'N/A', N'Not applicable')) AS v (k, c, n)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_production_line d WHERE d.line_key = v.k);
SET IDENTITY_INSERT dw.dim_production_line OFF;

/* --- dim_vehicle_program (sop_date 1900-01-01 = sentinel; views treat key < 0 separately) */
SET IDENTITY_INSERT dw.dim_vehicle_program ON;
INSERT INTO dw.dim_vehicle_program (program_key, program_code, program_name, oem_customer, vehicle_segment, sop_date, eop_date, etl_run_id, source_file)
SELECT v.k, v.c, v.n, v.n, v.n, '1900-01-01', NULL, 0, @src
FROM (VALUES (-1, 'UNKNOWN', N'Unknown'), (-2, 'N/A', N'Not applicable')) AS v (k, c, n)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_vehicle_program d WHERE d.program_key = v.k);
SET IDENTITY_INSERT dw.dim_vehicle_program OFF;

/* --- dim_part (cost 0, 1 opportunity: keeps DPMO denominators defined) ------ */
SET IDENTITY_INSERT dw.dim_part ON;
INSERT INTO dw.dim_part (part_key, part_number, part_name, component_type, program_key, primary_line_key,
                         standard_unit_cost_eur, opportunities_per_unit, is_active, etl_run_id, source_file)
SELECT v.k, v.c, v.n, v.n, v.k, v.k, 0, 1, 0, 0, @src
FROM (VALUES (-1, 'UNKNOWN', N'Unknown'), (-2, 'N/A', N'Not applicable')) AS v (k, c, n)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_part d WHERE d.part_key = v.k);
SET IDENTITY_INSERT dw.dim_part OFF;

/* --- dim_defect_type ------------------------------------------------------- */
SET IDENTITY_INSERT dw.dim_defect_type ON;
INSERT INTO dw.dim_defect_type (defect_type_key, defect_code, defect_name, defect_category, severity, etl_run_id, source_file)
SELECT v.k, v.c, v.n, v.n, '-', 0, @src
FROM (VALUES (-1, 'UNKNOWN', N'Unknown'), (-2, 'N/A', N'Not applicable')) AS v (k, c, n)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_defect_type d WHERE d.defect_type_key = v.k);
SET IDENTITY_INSERT dw.dim_defect_type OFF;

/* --- dim_inspection_stage (sequence 0 = never the final stage) ------------- */
SET IDENTITY_INSERT dw.dim_inspection_stage ON;
INSERT INTO dw.dim_inspection_stage (stage_key, stage_code, stage_name, stage_sequence, etl_run_id, source_file)
SELECT v.k, v.c, v.n, 0, 0, @src
FROM (VALUES (-1, 'UNKNOWN', N'Unknown'), (-2, 'N/A', N'Not applicable')) AS v (k, c, n)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_inspection_stage d WHERE d.stage_key = v.k);
SET IDENTITY_INSERT dw.dim_inspection_stage OFF;

/* --- Reference data: shifts (§3.2). Night shift belongs to its start date. -- */
INSERT INTO dw.dim_shift (shift_code, shift_name, start_time, end_time, crosses_midnight, sort_order, etl_run_id, source_file)
SELECT v.c, v.n, v.st, v.en, v.x, v.s, 0, @src
FROM (VALUES ('E', N'Early', CAST('06:00' AS TIME(0)), CAST('14:00' AS TIME(0)), 0, 1),
             ('L', N'Late',  CAST('14:00' AS TIME(0)), CAST('22:00' AS TIME(0)), 0, 2),
             ('N', N'Night', CAST('22:00' AS TIME(0)), CAST('06:00' AS TIME(0)), 1, 3)) AS v (c, n, st, en, x, s)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_shift d WHERE d.shift_code = v.c);

/* --- Reference data: inspection stages (§3.7) ------------------------------ */
INSERT INTO dw.dim_inspection_stage (stage_code, stage_name, stage_sequence, etl_run_id, source_file)
SELECT v.c, v.n, v.s, 0, @src
FROM (VALUES ('IPC', N'In-process check', 1),
             ('EOL', N'End-of-line test', 2),
             ('FA',  N'Final audit',      3)) AS v (c, n, s)
WHERE NOT EXISTS (SELECT 1 FROM dw.dim_inspection_stage d WHERE d.stage_code = v.c);
