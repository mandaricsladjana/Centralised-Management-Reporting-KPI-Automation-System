/* =============================================================================
   060_seed_kpi_targets.sql: illustrative KPI targets and RAG limits
   Blueprint §6.2 (targets) and §6.3 (amber = within 10 % relative of target).
   ALL TARGETS ARE ILLUSTRATIVE project targets, not industry benchmarks.
   - Ratios are stored as fractions (0.97 = 97 %).
   - higher_better: amber_limit = target x 0.9;  lower_better: target x 1.1.
   - D21: a target of 0 has no relative band, so these get a small absolute one
     (1 overdue 8D / audit finding is amber; Major NCs have no amber: any is red).
   - kpi_code = the DAX measure's code, the key used in rpt.v_kpi_target.
   Target changes over time: add a row with a new valid_from in a new script
   and close the old row (valid_to). Never overwrite history.
   ============================================================================= */
SET NOCOUNT ON;
GO

MERGE dw.kpi_target AS t
USING (VALUES
    -- Production and quality
    ('FPY_PCT',                    0.970000,      0.873000,    'higher_better', 'pct'),
    ('FTY_PCT',                    0.940000,      0.846000,    'higher_better', 'pct'),
    ('DEFECT_RATE_PCT',            0.030000,      0.033000,    'lower_better',  'pct'),
    ('SCRAP_RATE_PCT',             0.015000,      0.016500,    'lower_better',  'pct'),
    ('SCRAP_COST_EUR',         15000.000000,  16500.000000,    'lower_better',  'eur'),
    ('REWORK_RATE_PCT',            0.020000,      0.022000,    'lower_better',  'pct'),
    ('REWORK_SUCCESS_PCT',         0.900000,      0.810000,    'higher_better', 'pct'),
    ('REWORK_COST_EUR',         8000.000000,   8800.000000,    'lower_better',  'eur'),
    ('COPQ_PCT',                   0.015000,      0.016500,    'lower_better',  'pct'),
    -- Corrective actions (8D)
    ('OVERDUE_8D_COUNT',           0.000000,      1.000000,    'lower_better',  'count'),
    ('AVG_8D_CLOSURE_DAYS',       60.000000,     66.000000,    'lower_better',  'days'),
    ('EIGHT_D_ONTIME_PCT',         0.900000,      0.810000,    'higher_better', 'pct'),
    ('CA_COMPLETION_PCT',          0.950000,      0.855000,    'higher_better', 'pct'),
    ('REPEAT_DEFECT_PCT',          0.050000,      0.055000,    'lower_better',  'pct'),
    -- Audits and compliance
    ('OVERDUE_FINDINGS_COUNT',     0.000000,      1.000000,    'lower_better',  'count'),
    ('MAJOR_NC_COUNT',             0.000000,      0.000000,    'lower_better',  'count'),
    ('AUDIT_ONTIME_PCT',           0.900000,      0.810000,    'higher_better', 'pct'),
    ('AVG_FINDING_CLOSURE_DAYS',  30.000000,     33.000000,    'lower_better',  'days')
) AS s (kpi_code, target_value, amber_limit, direction, unit)
ON t.kpi_code = s.kpi_code AND t.valid_from = '2024-01-01'
WHEN NOT MATCHED BY TARGET
    THEN INSERT (kpi_code, valid_from, valid_to, target_value, amber_limit, direction, unit, target_type, etl_run_id, source_file)
         VALUES (s.kpi_code, '2024-01-01', NULL, s.target_value, s.amber_limit, s.direction, s.unit, 'Illustrative',
                 0, N'sql/ddl/060_seed_kpi_targets.sql');
