/* =============================================================================
   etl.fn_date_key: DATE -> yyyymmdd INT (dw.dim_calendar.date_key). NULL -> NULL.
   Repeatable script. Same expression as the CHECK constraints in sql/ddl.
   Named fn_* so it sorts (and deploys) before the usp_* procedures that use it.
   ============================================================================= */
CREATE OR ALTER FUNCTION etl.fn_date_key (@d DATE)
RETURNS INT
WITH SCHEMABINDING
AS
BEGIN
    RETURN YEAR(@d) * 10000 + MONTH(@d) * 100 + DAY(@d);
END;
