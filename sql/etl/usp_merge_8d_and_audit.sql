/* =============================================================================
   8D and audit loads (data dictionary §4.6-4.8, blueprint §5.5). Same contract
   as usp_merge_masters.sql. Load order (data dictionary §6):
     usp_merge_8d -> usp_merge_nonconformity -> usp_apply_8d_nc_links
     -> usp_merge_8d_action -> usp_merge_audit_finding
   ============================================================================= */

/* One row per 8D report (accumulating snapshot). opened = D1 when D1 is given,
   otherwise the tracker's opened date; closed = D8 (status text is DQ only). */
CREATE OR ALTER PROCEDURE etl.usp_merge_8d
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.eight_d GROUP BY eight_d_id HAVING COUNT(*) > 1)
        THROW 50010, 'stg.eight_d: duplicate eight_d_id (VR-GEN-05).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    WITH src AS (
        SELECT s.eight_d_id,
               etl.fn_date_key(COALESCE(s.d1_date, s.opened_date)) AS opened_date_key,
               ISNULL(p.part_key, -1) AS part_key, ISNULL(pl.line_key, -1) AS line_key,
               ISNULL(d.defect_type_key, -1) AS defect_type_key,
               s.problem_source, s.owner_name, s.owner_department,
               s.d1_date, s.d2_date, s.d3_date, s.d4_date, s.d5_date, s.d6_date, s.d7_date, s.d8_date,
               etl.fn_date_key(s.target_closure_date) AS target_closure_date_key,
               etl.fn_date_key(s.d8_date)             AS closed_date_key,
               s.status_reported, s.source_file
        FROM stg.eight_d s
        LEFT JOIN dw.dim_part p             ON p.part_number = s.part_number AND p.part_key  > 0
        LEFT JOIN dw.dim_production_line pl ON pl.line_code  = s.line_code   AND pl.line_key > 0
        LEFT JOIN dw.dim_defect_type d      ON d.defect_code = s.defect_code AND d.defect_type_key > 0
    )
    MERGE dw.fact_8d AS t
    USING src AS s ON t.eight_d_id = s.eight_d_id
    WHEN MATCHED AND EXISTS (
            SELECT s.opened_date_key, s.part_key, s.line_key, s.defect_type_key, s.problem_source, s.owner_name,
                   s.owner_department, s.d1_date, s.d2_date, s.d3_date, s.d4_date, s.d5_date, s.d6_date, s.d7_date,
                   s.d8_date, s.target_closure_date_key, s.closed_date_key, s.status_reported
            EXCEPT SELECT t.opened_date_key, t.part_key, t.line_key, t.defect_type_key, t.problem_source, t.owner_name,
                   t.owner_department, t.d1_date, t.d2_date, t.d3_date, t.d4_date, t.d5_date, t.d6_date, t.d7_date,
                   t.d8_date, t.target_closure_date_key, t.closed_date_key, t.status_reported)
        THEN UPDATE SET opened_date_key = s.opened_date_key, part_key = s.part_key, line_key = s.line_key,
                        defect_type_key = s.defect_type_key, problem_source = s.problem_source,
                        owner_name = s.owner_name, owner_department = s.owner_department,
                        d1_date = s.d1_date, d2_date = s.d2_date, d3_date = s.d3_date, d4_date = s.d4_date,
                        d5_date = s.d5_date, d6_date = s.d6_date, d7_date = s.d7_date, d8_date = s.d8_date,
                        target_closure_date_key = s.target_closure_date_key, closed_date_key = s.closed_date_key,
                        status_reported = s.status_reported,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (eight_d_id, opened_date_key, part_key, line_key, defect_type_key, problem_source, owner_name,
                     owner_department, d1_date, d2_date, d3_date, d4_date, d5_date, d6_date, d7_date, d8_date,
                     target_closure_date_key, closed_date_key, status_reported, etl_run_id, source_file)
             VALUES (s.eight_d_id, s.opened_date_key, s.part_key, s.line_key, s.defect_type_key, s.problem_source,
                     s.owner_name, s.owner_department, s.d1_date, s.d2_date, s.d3_date, s.d4_date, s.d5_date,
                     s.d6_date, s.d7_date, s.d8_date, s.target_closure_date_key, s.closed_date_key,
                     s.status_reported, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

/* Writes fact_nonconformity.eight_d_key from stg.eight_d_nc_link.
   Snapshot semantics per staged 8D: the tracker row's "linked NC IDs" list is the
   truth for that 8D, so NCs no longer listed are unlinked. 8Ds that are not in
   staging keep their links. One NC belongs to at most one 8D (blueprint §5.5).
   @links_unresolved = listed NC IDs not (yet) in dw: they are linked when a
   later tracker load lists them again; the pipeline records them as a warning. */
CREATE OR ALTER PROCEDURE etl.usp_apply_8d_nc_links
    @etl_run_id INT, @rows_linked INT = NULL OUTPUT, @rows_unlinked INT = NULL OUTPUT,
    @links_unresolved INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.eight_d_nc_link GROUP BY nc_id HAVING COUNT(DISTINCT eight_d_id) > 1)
        THROW 50012, 'stg.eight_d_nc_link: an NC is listed under more than one 8D (one NC -> at most one 8D).', 1;

    -- 1. Unlink NCs that a staged 8D no longer lists
    UPDATE nc
       SET eight_d_key = NULL, etl_run_id = @etl_run_id, updated_at_utc = SYSUTCDATETIME()
      FROM dw.fact_nonconformity nc
      JOIN dw.fact_8d e   ON e.eight_d_key = nc.eight_d_key
      JOIN stg.eight_d se ON se.eight_d_id = e.eight_d_id
     WHERE NOT EXISTS (SELECT 1 FROM stg.eight_d_nc_link k WHERE k.eight_d_id = e.eight_d_id AND k.nc_id = nc.nc_id);
    SET @rows_unlinked = @@ROWCOUNT;

    -- 2. Link (or move) listed NCs
    UPDATE nc
       SET eight_d_key = e.eight_d_key, etl_run_id = @etl_run_id, updated_at_utc = SYSUTCDATETIME()
      FROM (SELECT DISTINCT eight_d_id, nc_id FROM stg.eight_d_nc_link) k
      JOIN dw.fact_8d e             ON e.eight_d_id = k.eight_d_id
      JOIN dw.fact_nonconformity nc ON nc.nc_id     = k.nc_id
     WHERE nc.eight_d_key IS NULL OR nc.eight_d_key <> e.eight_d_key;
    SET @rows_linked = @@ROWCOUNT;

    SELECT @links_unresolved = COUNT(*)
      FROM (SELECT DISTINCT eight_d_id, nc_id FROM stg.eight_d_nc_link) k
     WHERE NOT EXISTS (SELECT 1 FROM dw.fact_nonconformity nc WHERE nc.nc_id = k.nc_id)
        OR NOT EXISTS (SELECT 1 FROM dw.fact_8d e WHERE e.eight_d_id = k.eight_d_id);
END;
GO

/* An action without its 8D is meaningless: the validator rejects it (VR-8D-03).
   If one still reaches staging, the load fails instead of dropping it silently. */
CREATE OR ALTER PROCEDURE etl.usp_merge_8d_action
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.eight_d_action GROUP BY action_id HAVING COUNT(*) > 1)
        THROW 50010, 'stg.eight_d_action: duplicate action_id (VR-GEN-05).', 1;
    IF EXISTS (SELECT 1 FROM stg.eight_d_action s WHERE NOT EXISTS (SELECT 1 FROM dw.fact_8d e WHERE e.eight_d_id = s.eight_d_id))
        THROW 50013, 'stg.eight_d_action: 8D ID not found in dw.fact_8d (VR-8D-03 should have rejected the row).', 1;

    DECLARE @a TABLE (act NVARCHAR(10));
    WITH src AS (
        SELECT s.action_id, e.eight_d_key, s.action_type, s.action_description, s.owner_name,
               etl.fn_date_key(s.due_date) AS due_date_key, etl.fn_date_key(s.completed_date) AS completed_date_key,
               s.status_reported, s.source_file
        FROM stg.eight_d_action s
        JOIN dw.fact_8d e ON e.eight_d_id = s.eight_d_id
    )
    MERGE dw.fact_8d_action AS t
    USING src AS s ON t.action_id = s.action_id
    WHEN MATCHED AND EXISTS (
            SELECT s.eight_d_key, s.action_type, s.action_description, s.owner_name, s.due_date_key,
                   s.completed_date_key, s.status_reported
            EXCEPT SELECT t.eight_d_key, t.action_type, t.action_description, t.owner_name, t.due_date_key,
                   t.completed_date_key, t.status_reported)
        THEN UPDATE SET eight_d_key = s.eight_d_key, action_type = s.action_type,
                        action_description = s.action_description, owner_name = s.owner_name,
                        due_date_key = s.due_date_key, completed_date_key = s.completed_date_key,
                        status_reported = s.status_reported,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (action_id, eight_d_key, action_type, action_description, owner_name, due_date_key,
                     completed_date_key, status_reported, etl_run_id, source_file)
             VALUES (s.action_id, s.eight_d_key, s.action_type, s.action_description, s.owner_name, s.due_date_key,
                     s.completed_date_key, s.status_reported, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
GO

/* Blank line = plant-wide finding -> -2 Not applicable; unknown line code -> -1.
   The 8D link is optional: an eight_d_id that is not in dw is stored as NULL and
   counted in @links_unresolved (a warning, not a rejection). */
CREATE OR ALTER PROCEDURE etl.usp_merge_audit_finding
    @etl_run_id INT, @rows_inserted INT = NULL OUTPUT, @rows_updated INT = NULL OUTPUT,
    @links_unresolved INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    EXEC etl.usp_assert_run @etl_run_id;
    IF EXISTS (SELECT 1 FROM stg.audit_finding GROUP BY finding_id HAVING COUNT(*) > 1)
        THROW 50010, 'stg.audit_finding: duplicate finding_id (VR-GEN-05).', 1;

    SELECT @links_unresolved = COUNT(*)
      FROM stg.audit_finding s
     WHERE s.eight_d_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM dw.fact_8d e WHERE e.eight_d_id = s.eight_d_id);

    DECLARE @a TABLE (act NVARCHAR(10));
    WITH src AS (
        SELECT s.finding_id, s.audit_id, etl.fn_date_key(s.audit_date) AS audit_date_key, s.audit_type,
               s.standard_reference,
               CASE WHEN NULLIF(LTRIM(RTRIM(s.line_code)), '') IS NULL THEN -2 ELSE ISNULL(pl.line_key, -1) END AS line_key,
               s.classification, s.finding_description, s.owner_name,
               etl.fn_date_key(s.due_date) AS due_date_key, etl.fn_date_key(s.closed_date) AS closed_date_key,
               e.eight_d_key, s.status_reported, s.source_file
        FROM stg.audit_finding s
        LEFT JOIN dw.dim_production_line pl ON pl.line_code  = s.line_code  AND pl.line_key > 0
        LEFT JOIN dw.fact_8d e              ON e.eight_d_id  = s.eight_d_id
    )
    MERGE dw.fact_audit_finding AS t
    USING src AS s ON t.finding_id = s.finding_id
    WHEN MATCHED AND EXISTS (
            SELECT s.audit_id, s.audit_date_key, s.audit_type, s.standard_reference, s.line_key, s.classification,
                   s.finding_description, s.owner_name, s.due_date_key, s.closed_date_key, s.eight_d_key, s.status_reported
            EXCEPT SELECT t.audit_id, t.audit_date_key, t.audit_type, t.standard_reference, t.line_key, t.classification,
                   t.finding_description, t.owner_name, t.due_date_key, t.closed_date_key, t.eight_d_key, t.status_reported)
        THEN UPDATE SET audit_id = s.audit_id, audit_date_key = s.audit_date_key, audit_type = s.audit_type,
                        standard_reference = s.standard_reference, line_key = s.line_key,
                        classification = s.classification, finding_description = s.finding_description,
                        owner_name = s.owner_name, due_date_key = s.due_date_key, closed_date_key = s.closed_date_key,
                        eight_d_key = s.eight_d_key, status_reported = s.status_reported,
                        etl_run_id = @etl_run_id, source_file = s.source_file, updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (finding_id, audit_id, audit_date_key, audit_type, standard_reference, line_key, classification,
                     finding_description, owner_name, due_date_key, closed_date_key, eight_d_key, status_reported,
                     etl_run_id, source_file)
             VALUES (s.finding_id, s.audit_id, s.audit_date_key, s.audit_type, s.standard_reference, s.line_key,
                     s.classification, s.finding_description, s.owner_name, s.due_date_key, s.closed_date_key,
                     s.eight_d_key, s.status_reported, @etl_run_id, s.source_file)
    OUTPUT $action INTO @a;
    SELECT @rows_inserted = COUNT(CASE WHEN act = 'INSERT' THEN 1 END),
           @rows_updated  = COUNT(CASE WHEN act = 'UPDATE' THEN 1 END) FROM @a;
END;
