/* =============================================================================
   061_rescale_cost_targets.sql (decision D24)
   The blueprint's absolute cost targets (scrap <= EUR 15k, rework <= EUR 8k per
   month) were written before the plant size was fixed. The simulated plant
   (Phase 5) produces about EUR 6.5M of parts per month, so those targets would
   be red in every month and say nothing. They are rescaled to the plant's size,
   still illustrative. Percentage targets (incl. COPQ % = 1.5 %) are unchanged.
   This corrects the seed value of rows nobody has reported on yet, so the
   original rows are updated rather than closed with valid_to.
   ============================================================================= */
SET NOCOUNT ON;
GO

UPDATE dw.kpi_target
   SET target_value = 75000, amber_limit = 82500,
       source_file = N'sql/ddl/061_rescale_cost_targets.sql', updated_at_utc = SYSUTCDATETIME()
 WHERE kpi_code = 'SCRAP_COST_EUR' AND valid_from = '2024-01-01' AND target_value = 15000;

UPDATE dw.kpi_target
   SET target_value = 30000, amber_limit = 33000,
       source_file = N'sql/ddl/061_rescale_cost_targets.sql', updated_at_utc = SYSUTCDATETIME()
 WHERE kpi_code = 'REWORK_COST_EUR' AND valid_from = '2024-01-01' AND target_value = 8000;
