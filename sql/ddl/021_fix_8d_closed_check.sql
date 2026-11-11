/* =============================================================================
   021_fix_8d_closed_check.sql
   Fixes ck_fact_8d_closed_is_d8 from 020. The original expression
       (d8_date IS NULL AND closed_date_key IS NULL) OR closed_date_key = f(d8_date)
   evaluates to UNKNOWN (not FALSE) when exactly one side is NULL, and a CHECK
   constraint only rejects FALSE. "Closed without a D8 date" and "D8 date but not
   closed" were therefore accepted. Found by
   tests/integration/test_schema_dev.py::test_8d_closed_date_must_equal_d8.
   Every branch below is TRUE or FALSE, never UNKNOWN.
   ============================================================================= */
SET NOCOUNT ON;
GO

IF EXISTS (SELECT 1 FROM sys.check_constraints
           WHERE name = N'ck_fact_8d_closed_is_d8' AND parent_object_id = OBJECT_ID(N'dw.fact_8d')
             AND definition NOT LIKE N'%IS NOT NULL%')
    ALTER TABLE dw.fact_8d DROP CONSTRAINT ck_fact_8d_closed_is_d8;
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = N'ck_fact_8d_closed_is_d8' AND parent_object_id = OBJECT_ID(N'dw.fact_8d'))
    ALTER TABLE dw.fact_8d WITH CHECK ADD CONSTRAINT ck_fact_8d_closed_is_d8 CHECK (
           (d8_date IS NULL AND closed_date_key IS NULL)
        OR (d8_date IS NOT NULL AND closed_date_key IS NOT NULL
            AND closed_date_key = YEAR(d8_date) * 10000 + MONTH(d8_date) * 100 + DAY(d8_date)));
