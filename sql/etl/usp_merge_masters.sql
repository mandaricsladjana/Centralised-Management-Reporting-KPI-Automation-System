/* =============================================================================
   Master data loads: stg.line / program / part / defect_type -> dw dimensions
   Data dictionary §6. Type 1 (overwrite). Never deletes: a part missing from a
   new master file keeps its row (facts reference it); is_active comes from the file.
   Special members (key < 0) are never touched.
   Contract for every etl.usp_merge_* procedure:
     - @etl_run_id must be a 'Running' run (etl.usp_assert_run)
     - staging must not contain duplicate business keys (VR-GEN-05), else THROW
     - only rows whose values changed are updated (null-safe EXCEPT comparison)
     - @rows_inserted / @rows_updated are returned for audit.etl_run
   The caller (Python pipeline) owns the transaction.
   ============================================================================= */
CREATE OR ALTER PROCEDURE etl.usp_merge_line
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.line GROUP BY line_code HAVING COUNT(*) > 1)
        THROW 50010, 'stg.line: duplicate line_code (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    MERGE dw.dim_production_line AS t
    USING stg.line AS s ON t.line_code = s.line_code
    WHEN MATCHED AND t.line_key > 0 AND EXISTS (SELECT s.line_name, s.process_type, s.area, s.is_active
                                               EXCEPT SELECT t.line_name, t.process_type, t.area, t.is_active)
        THEN UPDATE SET line_name = s.line_name, process_type = s.process_type, area = s.area, is_active = s.is_active,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (line_code, line_name, process_type, area, is_active, etl_run_id, source_file)
             VALUES (s.line_code, s.line_name, s.process_type, s.area, s.is_active, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

CREATE OR ALTER PROCEDURE etl.usp_merge_program
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.program GROUP BY program_code HAVING COUNT(*) > 1)
        THROW 50010, 'stg.program: duplicate program_code (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    MERGE dw.dim_vehicle_program AS t
    USING stg.program AS s ON t.program_code = s.program_code
    WHEN MATCHED AND t.program_key > 0 AND EXISTS (
            SELECT s.program_name, s.oem_customer, s.vehicle_segment, s.sop_date, s.eop_date
            EXCEPT SELECT t.program_name, t.oem_customer, t.vehicle_segment, t.sop_date, t.eop_date)
        THEN UPDATE SET program_name = s.program_name, oem_customer = s.oem_customer, vehicle_segment = s.vehicle_segment,
                        sop_date = s.sop_date, eop_date = s.eop_date,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (program_code, program_name, oem_customer, vehicle_segment, sop_date, eop_date, etl_run_id, source_file)
             VALUES (s.program_code, s.program_name, s.oem_customer, s.vehicle_segment, s.sop_date, s.eop_date,
                     @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

/* Parts need programs and lines first (load order, data dictionary §6). */
CREATE OR ALTER PROCEDURE etl.usp_merge_part
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.part GROUP BY part_number HAVING COUNT(*) > 1)
        THROW 50010, 'stg.part: duplicate part_number (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    WITH src AS (
        SELECT s.part_number, s.part_name, s.component_type,
               ISNULL(vp.program_key, -1) AS program_key,        -- VR-GEN-06: unknown code -> -1
               ISNULL(pl.line_key, -1)    AS primary_line_key,
               s.standard_unit_cost_eur, s.opportunities_per_unit, s.is_active, s.source_file
        FROM stg.part s
        LEFT JOIN dw.dim_vehicle_program vp ON vp.program_code = s.program_code AND vp.program_key > 0
        LEFT JOIN dw.dim_production_line pl ON pl.line_code = s.primary_line_code AND pl.line_key > 0
    )
    MERGE dw.dim_part AS t
    USING src AS s ON t.part_number = s.part_number
    WHEN MATCHED AND t.part_key > 0 AND EXISTS (
            SELECT s.part_name, s.component_type, s.program_key, s.primary_line_key,
                   s.standard_unit_cost_eur, s.opportunities_per_unit, s.is_active
            EXCEPT SELECT t.part_name, t.component_type, t.program_key, t.primary_line_key,
                   t.standard_unit_cost_eur, t.opportunities_per_unit, t.is_active)
        THEN UPDATE SET part_name = s.part_name, component_type = s.component_type, program_key = s.program_key,
                        primary_line_key = s.primary_line_key, standard_unit_cost_eur = s.standard_unit_cost_eur,
                        opportunities_per_unit = s.opportunities_per_unit, is_active = s.is_active,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (part_number, part_name, component_type, program_key, primary_line_key,
                     standard_unit_cost_eur, opportunities_per_unit, is_active, etl_run_id, source_file)
             VALUES (s.part_number, s.part_name, s.component_type, s.program_key, s.primary_line_key,
                     s.standard_unit_cost_eur, s.opportunities_per_unit, s.is_active, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

CREATE OR ALTER PROCEDURE etl.usp_merge_defect_type
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.defect_type GROUP BY defect_code HAVING COUNT(*) > 1)
        THROW 50010, 'stg.defect_type: duplicate defect_code (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    MERGE dw.dim_defect_type AS t
    USING stg.defect_type AS s ON t.defect_code = s.defect_code
    WHEN MATCHED AND t.defect_type_key > 0 AND EXISTS (SELECT s.defect_name, s.defect_category, s.severity
                                                      EXCEPT SELECT t.defect_name, t.defect_category, t.severity)
        THEN UPDATE SET defect_name = s.defect_name, defect_category = s.defect_category, severity = s.severity,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (defect_code, defect_name, defect_category, severity, etl_run_id, source_file)
             VALUES (s.defect_code, s.defect_name, s.defect_category, s.severity, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
