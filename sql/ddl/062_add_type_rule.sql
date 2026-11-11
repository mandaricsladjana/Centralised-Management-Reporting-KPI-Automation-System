/* =============================================================================
   062_add_type_rule.sql: validation rule VR-GEN-08 (Phase 6)
   A value of the wrong type ("abc" in a quantity) or outside an allowed list
   (disposition, audit type, ...) is rejected per row by the validator, instead
   of failing the whole file on a dw CHECK constraint during MERGE.
   ============================================================================= */
SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM [audit].validation_rule WHERE rule_id = 'VR-GEN-08')
    INSERT INTO [audit].validation_rule (rule_id, source_name, severity, description, action)
    VALUES ('VR-GEN-08', 'all', 'Reject',
            N'Value has the wrong type or is not in the allowed list (disposition, audit type, ...)',
            N'Reject row');
