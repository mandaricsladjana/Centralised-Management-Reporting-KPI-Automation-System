/* =============================================================================
   050_seed_validation_rules.sql: validation rule catalogue
   Data dictionary §7. audit.rejected_row.rule_id references these rows.
   New rules go into a new versioned script (e.g. 051_...), not into this one.
   ============================================================================= */
SET NOCOUNT ON;
GO

MERGE [audit].validation_rule AS t
USING (VALUES
    ('VR-GEN-01', 'all',            'Reject', N'Required columns present (header check)',                                         N'Reject the whole file'),
    ('VR-GEN-02', 'all',            'Reject', N'Mandatory fields not empty',                                                       N'Reject row'),
    ('VR-GEN-03', 'all',            'Reject', N'Date parseable (YYYY-MM-DD, DD.MM.YYYY, DD/MM/YYYY, Excel serial)',                N'Reject row'),
    ('VR-GEN-04', 'all',            'Warn',   N'Exact duplicate row within the file',                                              N'Drop duplicate, warn'),
    ('VR-GEN-05', 'all',            'Reject', N'Business key duplicated with different values',                                    N'Reject all rows with that key, warn'),
    ('VR-GEN-06', 'all',            'Warn',   N'Code not found in master data (after the spelling map)',                           N'Load with key -1 (Unknown), warn'),
    ('VR-GEN-07', 'all',            'Reject', N'Event date in the future or before 2024-01-01',                                    N'Reject row'),
    ('VR-QTY-01', 'quantities',     'Reject', N'Negative quantity',                                                                N'Reject row'),
    ('VR-INS-01', 'qa_inspections', 'Reject', N'qty_passed_first_time + qty_failed <> qty_inspected',                              N'Reject row'),
    ('VR-RWK-01', 'rework_log',     'Reject', N'qty_rework_ok > qty_reworked',                                                     N'Reject row'),
    ('VR-RWK-02', 'rework_log',     'Warn',   N'Missing rework cost',                                                              N'Impute labour_hours x labour_rate_eur, cost_source = Imputed, warn'),
    ('VR-8D-01',  '8d_tracker',     'Warn',   N'8D milestones out of order: d(n) < d(n-1)',                                        N'Load, warn'),
    ('VR-8D-02',  '8d_tracker',     'Warn',   N'Status text and D8 date disagree (Closed without D8, or D8 without Closed)',       N'Load, warn; D8 is the truth'),
    ('VR-8D-03',  '8d_actions',     'Reject', N'8D ID unknown',                                                                    N'Reject row'),
    ('VR-AF-01',  'audit_findings', 'Reject', N'Classification variant cannot be mapped to Major NC / Minor NC / Observation / OFI', N'Reject row')
) AS s (rule_id, source_name, severity, description, action)
ON t.rule_id = s.rule_id
WHEN MATCHED AND (t.source_name <> s.source_name OR t.severity <> s.severity
                  OR t.description <> s.description OR t.action <> s.action)
    THEN UPDATE SET source_name = s.source_name, severity = s.severity, description = s.description, action = s.action
WHEN NOT MATCHED BY TARGET
    THEN INSERT (rule_id, source_name, severity, description, action)
         VALUES (s.rule_id, s.source_name, s.severity, s.description, s.action);
