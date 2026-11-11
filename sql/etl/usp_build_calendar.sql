/* =============================================================================
   etl.usp_build_calendar: generate / refresh dw.dim_calendar (data dictionary §3.1)
   Repeatable script (CREATE OR ALTER): re-applied by python -m etl.deploy
   whenever the file changes.
   - Independent of SET DATEFIRST and SET LANGUAGE: day_of_week is computed from
     the fact that 1900-01-01 was a Monday; names come from fixed English lists.
   - Holidays come from dw.plant_holiday (synchronised from config/settings.toml).
   - MERGE: re-running refreshes flags (e.g. a new holiday) without touching keys.
   ============================================================================= */
CREATE OR ALTER PROCEDURE etl.usp_build_calendar
    @start_date  DATE,
    @end_date    DATE,
    @etl_run_id  INT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @start_date IS NULL OR @end_date IS NULL OR @end_date < @start_date
        THROW 50001, 'usp_build_calendar: invalid date range.', 1;

    DECLARE @days INT = DATEDIFF(DAY, @start_date, @end_date) + 1;
    DECLARE @src NVARCHAR(260) = N'sql/etl/usp_build_calendar.sql';

    WITH digits AS (SELECT d FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS v (d)),
    numbers AS (   -- 0 .. 99 999 (enough for 270 years)
        SELECT TOP (@days) a.d + 10 * b.d + 100 * c.d + 1000 * e.d + 10000 * f.d AS n
        FROM digits a CROSS JOIN digits b CROSS JOIN digits c CROSS JOIN digits e CROSS JOIN digits f
        ORDER BY n
    ),
    base AS (
        SELECT DATEADD(DAY, n, @start_date) AS dt FROM numbers
    ),
    calc AS (
        SELECT dt,
               (DATEDIFF(DAY, '19000101', dt) % 7) + 1 AS dow          -- 1 = Monday ... 7 = Sunday
        FROM base
    ),
    src AS (
        SELECT
            YEAR(c.dt) * 10000 + MONTH(c.dt) * 100 + DAY(c.dt)               AS date_key,
            c.dt                                                             AS [date],
            CAST(YEAR(c.dt) AS SMALLINT)                                     AS [year],
            CAST(DATEPART(QUARTER, c.dt) AS TINYINT)                         AS [quarter],
            CAST(CONCAT(YEAR(c.dt), '-Q', DATEPART(QUARTER, c.dt)) AS CHAR(7)) AS quarter_label,
            CAST(MONTH(c.dt) AS TINYINT)                                     AS [month],
            CAST(CHOOSE(MONTH(c.dt), 'January', 'February', 'March', 'April', 'May', 'June', 'July',
                        'August', 'September', 'October', 'November', 'December') AS VARCHAR(10)) AS month_name,
            CAST(CHOOSE(MONTH(c.dt), 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul',
                        'Aug', 'Sep', 'Oct', 'Nov', 'Dec') AS CHAR(3))       AS month_short,
            CAST(CONVERT(CHAR(7), c.dt, 126) AS CHAR(7))                     AS year_month,   -- yyyy-mm
            DATEFROMPARTS(YEAR(c.dt), MONTH(c.dt), 1)                        AS month_start_date,
            CAST(YEAR(DATEADD(DAY, 4 - c.dow, c.dt)) AS SMALLINT)            AS iso_year,     -- year of the week's Thursday
            CAST(DATEPART(ISO_WEEK, c.dt) AS TINYINT)                        AS iso_week,
            CAST(c.dow AS TINYINT)                                           AS day_of_week,
            CAST(CHOOSE(c.dow, 'Monday', 'Tuesday', 'Wednesday', 'Thursday',
                        'Friday', 'Saturday', 'Sunday') AS VARCHAR(10))      AS day_name,
            CAST(CASE WHEN c.dow >= 6 THEN 1 ELSE 0 END AS BIT)              AS is_weekend,
            CAST(CASE WHEN h.holiday_date IS NOT NULL THEN 1 ELSE 0 END AS BIT) AS is_plant_holiday,
            CAST(CASE WHEN c.dow < 6 AND h.holiday_date IS NULL THEN 1 ELSE 0 END AS BIT) AS is_working_day
        FROM calc c
        LEFT JOIN dw.plant_holiday h ON h.holiday_date = c.dt
    )
    MERGE dw.dim_calendar AS t
    USING src AS s ON t.date_key = s.date_key
    WHEN MATCHED AND (t.is_plant_holiday <> s.is_plant_holiday OR t.is_working_day <> s.is_working_day
                      OR t.iso_year <> s.iso_year OR t.iso_week <> s.iso_week OR t.day_of_week <> s.day_of_week
                      OR t.month_name <> s.month_name OR t.day_name <> s.day_name)
        THEN UPDATE SET
            t.iso_year = s.iso_year, t.iso_week = s.iso_week, t.day_of_week = s.day_of_week,
            t.month_name = s.month_name, t.day_name = s.day_name, t.is_weekend = s.is_weekend,
            t.is_plant_holiday = s.is_plant_holiday, t.is_working_day = s.is_working_day,
            t.etl_run_id = @etl_run_id, t.updated_at_utc = SYSUTCDATETIME()
    WHEN NOT MATCHED BY TARGET
        THEN INSERT (date_key, [date], [year], [quarter], quarter_label, [month], month_name, month_short,
                     year_month, month_start_date, iso_year, iso_week, day_of_week, day_name,
                     is_weekend, is_plant_holiday, is_working_day, etl_run_id, source_file)
             VALUES (s.date_key, s.[date], s.[year], s.[quarter], s.quarter_label, s.[month], s.month_name, s.month_short,
                     s.year_month, s.month_start_date, s.iso_year, s.iso_week, s.day_of_week, s.day_name,
                     s.is_weekend, s.is_plant_holiday, s.is_working_day, @etl_run_id, @src);
    -- Rows outside the range are deliberately kept: facts may reference them.
END;
