/* =============================================================================
   Event fact loads: stg -> dw.fact_production / inspection / nonconformity /
   scrap / rework. Same contract as usp_merge_masters.sql.
   - Codes resolve to keys; an unknown code becomes -1 (VR-GEN-06, already
     warned by the validator).
   - Periodic facts (production, inspection) are checked for duplicates on the
     *resolved* grain, which also catches two different unknown codes that both
     map to -1 (data dictionary §11, open point).
   - Facts are never deleted: history is rebuilt from raw files, not truncated.
   ============================================================================= */
CREATE OR ALTER PROCEDURE etl.usp_merge_production
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;

    SELECT etl.fn_date_key(s.event_date) AS date_key,
           ISNULL(sh.shift_key, -1) AS shift_key, ISNULL(pl.line_key, -1) AS line_key, ISNULL(p.part_key, -1) AS part_key,
           s.qty_planned, s.qty_started, s.source_file
    INTO #src
    FROM stg.production s
    LEFT JOIN dw.dim_shift sh           ON sh.shift_code  = s.shift_code  AND sh.shift_key > 0
    LEFT JOIN dw.dim_production_line pl ON pl.line_code   = s.line_code   AND pl.line_key  > 0
    LEFT JOIN dw.dim_part p             ON p.part_number  = s.part_number AND p.part_key   > 0;

    IF EXISTS (SELECT 1 FROM #src GROUP BY date_key, shift_key, line_key, part_key HAVING COUNT(*) > 1)
        THROW 50011, 'stg.production: duplicate date/shift/line/part after key resolution (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    MERGE dw.fact_production AS t
    USING #src AS s
       ON t.date_key = s.date_key AND t.shift_key = s.shift_key AND t.line_key = s.line_key AND t.part_key = s.part_key
    WHEN MATCHED AND EXISTS (SELECT s.qty_planned, s.qty_started EXCEPT SELECT t.qty_planned, t.qty_started)
        THEN UPDATE SET qty_planned = s.qty_planned, qty_started = s.qty_started,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (date_key, shift_key, line_key, part_key, qty_planned, qty_started, etl_run_id, source_file)
             VALUES (s.date_key, s.shift_key, s.line_key, s.part_key, s.qty_planned, s.qty_started, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

CREATE OR ALTER PROCEDURE etl.usp_merge_inspection
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;

    SELECT etl.fn_date_key(s.event_date) AS date_key,
           ISNULL(sh.shift_key, -1) AS shift_key, ISNULL(pl.line_key, -1) AS line_key,
           ISNULL(p.part_key, -1) AS part_key, ISNULL(st.stage_key, -1) AS stage_key,
           s.qty_inspected, s.qty_passed_first_time, s.qty_failed, s.source_file
    INTO #src
    FROM stg.inspection s
    LEFT JOIN dw.dim_shift sh            ON sh.shift_code = s.shift_code  AND sh.shift_key > 0
    LEFT JOIN dw.dim_production_line pl  ON pl.line_code  = s.line_code   AND pl.line_key  > 0
    LEFT JOIN dw.dim_part p              ON p.part_number = s.part_number AND p.part_key   > 0
    LEFT JOIN dw.dim_inspection_stage st ON st.stage_code = s.stage_code  AND st.stage_key > 0;

    IF EXISTS (SELECT 1 FROM #src GROUP BY date_key, shift_key, line_key, part_key, stage_key HAVING COUNT(*) > 1)
        THROW 50011, 'stg.inspection: duplicate date/shift/line/part/stage after key resolution (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    MERGE dw.fact_inspection AS t
    USING #src AS s
       ON t.date_key = s.date_key AND t.shift_key = s.shift_key AND t.line_key = s.line_key
      AND t.part_key = s.part_key AND t.stage_key = s.stage_key
    WHEN MATCHED AND EXISTS (SELECT s.qty_inspected, s.qty_passed_first_time, s.qty_failed
                             EXCEPT SELECT t.qty_inspected, t.qty_passed_first_time, t.qty_failed)
        THEN UPDATE SET qty_inspected = s.qty_inspected, qty_passed_first_time = s.qty_passed_first_time,
                        qty_failed = s.qty_failed,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (date_key, shift_key, line_key, part_key, stage_key, qty_inspected, qty_passed_first_time,
                     qty_failed, etl_run_id, source_file)
             VALUES (s.date_key, s.shift_key, s.line_key, s.part_key, s.stage_key, s.qty_inspected,
                     s.qty_passed_first_time, s.qty_failed, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

/* eight_d_key is NOT set here: it is owned by etl.usp_apply_8d_nc_links, so a
   corrected NC file can never wipe an existing 8D link. */
CREATE OR ALTER PROCEDURE etl.usp_merge_nonconformity
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.nonconformity GROUP BY nc_id HAVING COUNT(*) > 1)
        THROW 50010, 'stg.nonconformity: duplicate nc_id (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    WITH src AS (
        SELECT s.nc_id, etl.fn_date_key(s.event_date) AS date_key,
               ISNULL(sh.shift_key, -1) AS shift_key, ISNULL(pl.line_key, -1) AS line_key,
               ISNULL(p.part_key, -1) AS part_key, ISNULL(st.stage_key, -1) AS stage_key,
               ISNULL(d.defect_type_key, -1) AS defect_type_key,
               s.qty_affected, s.disposition, s.source_file
        FROM stg.nonconformity s
        LEFT JOIN dw.dim_shift sh            ON sh.shift_code = s.shift_code  AND sh.shift_key > 0
        LEFT JOIN dw.dim_production_line pl  ON pl.line_code  = s.line_code   AND pl.line_key  > 0
        LEFT JOIN dw.dim_part p              ON p.part_number = s.part_number AND p.part_key   > 0
        LEFT JOIN dw.dim_inspection_stage st ON st.stage_code = s.stage_code  AND st.stage_key > 0
        LEFT JOIN dw.dim_defect_type d       ON d.defect_code = s.defect_code AND d.defect_type_key > 0
    )
    MERGE dw.fact_nonconformity AS t
    USING src AS s ON t.nc_id = s.nc_id
    WHEN MATCHED AND EXISTS (
            SELECT s.date_key, s.shift_key, s.line_key, s.part_key, s.stage_key, s.defect_type_key, s.qty_affected, s.disposition
            EXCEPT SELECT t.date_key, t.shift_key, t.line_key, t.part_key, t.stage_key, t.defect_type_key, t.qty_affected, t.disposition)
        THEN UPDATE SET date_key = s.date_key, shift_key = s.shift_key, line_key = s.line_key, part_key = s.part_key,
                        stage_key = s.stage_key, defect_type_key = s.defect_type_key, qty_affected = s.qty_affected,
                        disposition = s.disposition,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (nc_id, date_key, shift_key, line_key, part_key, stage_key, defect_type_key, qty_affected,
                     disposition, etl_run_id, source_file)
             VALUES (s.nc_id, s.date_key, s.shift_key, s.line_key, s.part_key, s.stage_key, s.defect_type_key,
                     s.qty_affected, s.disposition, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

/* D7: unit_cost_eur is the part's standard cost *when the booking was first
   loaded*. A corrected booking keeps its snapshot unless its part changed. */
CREATE OR ALTER PROCEDURE etl.usp_merge_scrap
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.scrap GROUP BY scrap_id HAVING COUNT(*) > 1)
        THROW 50010, 'stg.scrap: duplicate scrap_id (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    WITH src AS (
        SELECT s.scrap_id, etl.fn_date_key(s.event_date) AS date_key,
               ISNULL(sh.shift_key, -1) AS shift_key, ISNULL(pl.line_key, -1) AS line_key,
               ISNULL(p.part_key, -1) AS part_key, ISNULL(d.defect_type_key, -1) AS defect_type_key,
               s.qty_scrapped,
               ISNULL(p.standard_unit_cost_eur, 0) AS current_unit_cost_eur,   -- unknown part: 0 (D11)
               s.source_file
        FROM stg.scrap s
        LEFT JOIN dw.dim_shift sh           ON sh.shift_code = s.shift_code  AND sh.shift_key > 0
        LEFT JOIN dw.dim_production_line pl ON pl.line_code  = s.line_code   AND pl.line_key  > 0
        LEFT JOIN dw.dim_part p             ON p.part_number = s.part_number AND p.part_key   > 0
        LEFT JOIN dw.dim_defect_type d      ON d.defect_code = s.defect_code AND d.defect_type_key > 0
    )
    MERGE dw.fact_scrap AS t
    USING src AS s ON t.scrap_id = s.scrap_id
    WHEN MATCHED AND EXISTS (
            SELECT s.date_key, s.shift_key, s.line_key, s.part_key, s.defect_type_key, s.qty_scrapped
            EXCEPT SELECT t.date_key, t.shift_key, t.line_key, t.part_key, t.defect_type_key, t.qty_scrapped)
        THEN UPDATE SET date_key = s.date_key, shift_key = s.shift_key, line_key = s.line_key,
                        unit_cost_eur = CASE WHEN t.part_key = s.part_key THEN t.unit_cost_eur ELSE s.current_unit_cost_eur END,
                        part_key = s.part_key, defect_type_key = s.defect_type_key, qty_scrapped = s.qty_scrapped,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (scrap_id, date_key, shift_key, line_key, part_key, defect_type_key, qty_scrapped, unit_cost_eur,
                     etl_run_id, source_file)
             VALUES (s.scrap_id, s.date_key, s.shift_key, s.line_key, s.part_key, s.defect_type_key, s.qty_scrapped,
                     s.current_unit_cost_eur, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

/* Costs arrive complete: the validator imputes missing costs and sets
   cost_source = 'Imputed' (VR-RWK-02, D12). */
CREATE OR ALTER PROCEDURE etl.usp_merge_rework
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.rework GROUP BY rework_id HAVING COUNT(*) > 1)
        THROW 50010, 'stg.rework: duplicate rework_id (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    WITH src AS (
        SELECT s.rework_id, etl.fn_date_key(s.event_date) AS date_key,
               ISNULL(sh.shift_key, -1) AS shift_key, ISNULL(pl.line_key, -1) AS line_key,
               ISNULL(p.part_key, -1) AS part_key, ISNULL(d.defect_type_key, -1) AS defect_type_key,
               s.qty_reworked, s.qty_rework_ok, s.labour_hours, s.labour_rate_eur, s.rework_cost_eur, s.cost_source,
               s.source_file
        FROM stg.rework s
        LEFT JOIN dw.dim_shift sh           ON sh.shift_code = s.shift_code  AND sh.shift_key > 0
        LEFT JOIN dw.dim_production_line pl ON pl.line_code  = s.line_code   AND pl.line_key  > 0
        LEFT JOIN dw.dim_part p             ON p.part_number = s.part_number AND p.part_key   > 0
        LEFT JOIN dw.dim_defect_type d      ON d.defect_code = s.defect_code AND d.defect_type_key > 0
    )
    MERGE dw.fact_rework AS t
    USING src AS s ON t.rework_id = s.rework_id
    WHEN MATCHED AND EXISTS (
            SELECT s.date_key, s.shift_key, s.line_key, s.part_key, s.defect_type_key, s.qty_reworked, s.qty_rework_ok,
                   s.labour_hours, s.labour_rate_eur, s.rework_cost_eur, s.cost_source
            EXCEPT SELECT t.date_key, t.shift_key, t.line_key, t.part_key, t.defect_type_key, t.qty_reworked, t.qty_rework_ok,
                   t.labour_hours, t.labour_rate_eur, t.rework_cost_eur, t.cost_source)
        THEN UPDATE SET date_key = s.date_key, shift_key = s.shift_key, line_key = s.line_key, part_key = s.part_key,
                        defect_type_key = s.defect_type_key, qty_reworked = s.qty_reworked, qty_rework_ok = s.qty_rework_ok,
                        labour_hours = s.labour_hours, labour_rate_eur = s.labour_rate_eur,
                        rework_cost_eur = s.rework_cost_eur, cost_source = s.cost_source,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (rework_id, date_key, shift_key, line_key, part_key, defect_type_key, qty_reworked, qty_rework_ok,
                     labour_hours, labour_rate_eur, rework_cost_eur, cost_source, etl_run_id, source_file)
             VALUES (s.rework_id, s.date_key, s.shift_key, s.line_key, s.part_key, s.defect_type_key, s.qty_reworked,
                     s.qty_rework_ok, s.labour_hours, s.labour_rate_eur, s.rework_cost_eur, s.cost_source,
                     @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
