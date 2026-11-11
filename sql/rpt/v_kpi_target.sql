/* =============================================================================
   rpt.v_kpi_target: RAG thresholds for Power BI and for the SQL scorecard.
   rpt.fn_kpi_rag:   the single RAG rule (blueprint §6.3), so SQL and DAX agree:
     higher_better: Green >= target, Amber >= amber_limit, else Red
     lower_better:  Green <= target, Amber <= amber_limit, else Red
   Repeatable script.
   ============================================================================= */
CREATE OR ALTER VIEW rpt.v_kpi_target AS
SELECT kpi_target_key, kpi_code, valid_from, valid_to, target_value, amber_limit, direction, unit, target_type,
       CAST(CASE WHEN valid_to IS NULL THEN 1 ELSE 0 END AS BIT) AS is_current
FROM dw.kpi_target;
GO

CREATE OR ALTER FUNCTION rpt.fn_kpi_rag (@value DECIMAL(18,6), @target DECIMAL(18,6),
                                         @amber_limit DECIMAL(18,6), @direction VARCHAR(16))
RETURNS VARCHAR(5)
WITH SCHEMABINDING
AS
BEGIN
    RETURN CASE
        WHEN @value IS NULL OR @target IS NULL THEN NULL
        WHEN @direction = 'higher_better' THEN
             CASE WHEN @value >= @target THEN 'Green' WHEN @value >= @amber_limit THEN 'Amber' ELSE 'Red' END
        WHEN @direction = 'lower_better' THEN
             CASE WHEN @value <= @target THEN 'Green' WHEN @value <= @amber_limit THEN 'Amber' ELSE 'Red' END
    END;
END;
