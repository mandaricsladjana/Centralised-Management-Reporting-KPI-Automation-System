/* =============================================================================
   002_audit_tables.sql: lineage, file manifest, rejected rows, DQ results
   Data dictionary §5. Created before dw because every dw row references
   audit.etl_run (lineage columns, data dictionary §1).
   audit.schema_version is created by 000_schema_version.sql (deploy bootstrap).
   ============================================================================= */
SET NOCOUNT ON;
GO

/* --- One row per pipeline run ---------------------------------------------- */
IF OBJECT_ID(N'audit.etl_run', N'U') IS NULL
CREATE TABLE [audit].etl_run (
    etl_run_id      INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_etl_run PRIMARY KEY,
    started_at_utc  DATETIME2(0)  NOT NULL CONSTRAINT df_etl_run_started DEFAULT SYSUTCDATETIME(),
    ended_at_utc    DATETIME2(0)  NULL,
    environment     VARCHAR(4)    NOT NULL CONSTRAINT ck_etl_run_env CHECK (environment IN ('dev', 'prod')),
    status          VARCHAR(10)   NOT NULL CONSTRAINT ck_etl_run_status
                                           CHECK (status IN ('Running', 'Succeeded', 'Failed', 'Partial')),
    files_processed INT           NOT NULL CONSTRAINT df_etl_run_files    DEFAULT 0,
    rows_read       INT           NOT NULL CONSTRAINT df_etl_run_read     DEFAULT 0,
    rows_loaded     INT           NOT NULL CONSTRAINT df_etl_run_loaded   DEFAULT 0,
    rows_rejected   INT           NOT NULL CONSTRAINT df_etl_run_rejected DEFAULT 0,
    git_commit      VARCHAR(40)   NULL,
    -- 'deploy' = run 0, which owns rows seeded by DDL scripts (special members, reference data)
    triggered_by    VARCHAR(10)   NOT NULL CONSTRAINT ck_etl_run_trigger
                                           CHECK (triggered_by IN ('manual', 'scheduler', 'deploy')),
    error_message   NVARCHAR(MAX) NULL,
    CONSTRAINT ck_etl_run_counts CHECK (files_processed >= 0 AND rows_read >= 0
                                        AND rows_loaded >= 0 AND rows_rejected >= 0),
    CONSTRAINT ck_etl_run_ended  CHECK (ended_at_utc IS NULL OR ended_at_utc >= started_at_utc)
);

GO
/* Run 0: lineage owner for seeded rows. EngineEdition 5 = Azure SQL Database. */
IF NOT EXISTS (SELECT 1 FROM [audit].etl_run WHERE etl_run_id = 0)
BEGIN
    SET IDENTITY_INSERT [audit].etl_run ON;
    INSERT INTO [audit].etl_run (etl_run_id, started_at_utc, ended_at_utc, environment, status, triggered_by)
    VALUES (0, SYSUTCDATETIME(), SYSUTCDATETIME(),
            CASE WHEN CAST(SERVERPROPERTY('EngineEdition') AS INT) = 5 THEN 'prod' ELSE 'dev' END,
            'Succeeded', 'deploy');
    SET IDENTITY_INSERT [audit].etl_run OFF;
END;

GO
/* --- One source file per run ----------------------------------------------- */
IF OBJECT_ID(N'audit.file_manifest', N'U') IS NULL
CREATE TABLE [audit].file_manifest (
    file_id          INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_file_manifest PRIMARY KEY,
    etl_run_id       INT            NOT NULL CONSTRAINT fk_file_manifest_run REFERENCES [audit].etl_run (etl_run_id),
    source_name      VARCHAR(40)    NOT NULL,           -- e.g. 'scrap_log'
    file_name        NVARCHAR(260)  NOT NULL,
    blob_path        NVARCHAR(400)  NULL,
    file_sha256      CHAR(64)       NOT NULL,
    file_size_bytes  BIGINT         NOT NULL CONSTRAINT ck_file_manifest_size CHECK (file_size_bytes >= 0),
    row_count        INT            NULL,
    status           VARCHAR(20)    NOT NULL CONSTRAINT ck_file_manifest_status
                                             CHECK (status IN ('Loaded', 'Skipped-duplicate', 'Failed')),
    processed_at_utc DATETIME2(0)   NOT NULL CONSTRAINT df_file_manifest_processed DEFAULT SYSUTCDATETIME()
);

GO
/* Incremental loading: a hash may be *loaded* only once per source. Skipped and
   failed attempts can repeat the same hash, so the unique index is filtered. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'uq_file_manifest_loaded_hash'
                                           AND object_id = OBJECT_ID(N'audit.file_manifest'))
CREATE UNIQUE INDEX uq_file_manifest_loaded_hash
    ON [audit].file_manifest (source_name, file_sha256) WHERE status = 'Loaded';

GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_file_manifest_run'
                                           AND object_id = OBJECT_ID(N'audit.file_manifest'))
CREATE INDEX ix_file_manifest_run ON [audit].file_manifest (etl_run_id);

GO
/* --- Validation rule catalogue (seeded in 050) ----------------------------- */
IF OBJECT_ID(N'audit.validation_rule', N'U') IS NULL
CREATE TABLE [audit].validation_rule (
    rule_id      VARCHAR(12)   NOT NULL CONSTRAINT pk_validation_rule PRIMARY KEY,   -- e.g. VR-SCR-03
    source_name  VARCHAR(40)   NOT NULL,          -- 'all' or a source / group name
    severity     VARCHAR(6)    NOT NULL CONSTRAINT ck_validation_rule_severity CHECK (severity IN ('Reject', 'Warn')),
    description  NVARCHAR(400) NOT NULL,
    action       NVARCHAR(200) NOT NULL           -- what the pipeline does when the rule fires
);

GO
/* --- One rejected source row ----------------------------------------------- */
IF OBJECT_ID(N'audit.rejected_row', N'U') IS NULL
CREATE TABLE [audit].rejected_row (
    rejected_row_id   BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_rejected_row PRIMARY KEY,
    etl_run_id        INT           NOT NULL CONSTRAINT fk_rejected_row_run  REFERENCES [audit].etl_run (etl_run_id),
    file_id           INT           NOT NULL CONSTRAINT fk_rejected_row_file REFERENCES [audit].file_manifest (file_id),
    source_row_number INT           NULL,          -- NULL when the whole file is rejected (VR-GEN-01)
    rule_id           VARCHAR(12)   NOT NULL CONSTRAINT fk_rejected_row_rule REFERENCES [audit].validation_rule (rule_id),
    reason            NVARCHAR(400) NOT NULL,
    raw_record        NVARCHAR(MAX) NULL CONSTRAINT ck_rejected_row_json CHECK (raw_record IS NULL OR ISJSON(raw_record) = 1)
);

GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_rejected_row_run'
                                           AND object_id = OBJECT_ID(N'audit.rejected_row'))
CREATE INDEX ix_rejected_row_run ON [audit].rejected_row (etl_run_id, rule_id);

GO
/* --- One data-quality check result per run --------------------------------- */
IF OBJECT_ID(N'audit.dq_result', N'U') IS NULL
CREATE TABLE [audit].dq_result (
    dq_result_id    BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_dq_result PRIMARY KEY,
    etl_run_id      INT            NOT NULL CONSTRAINT fk_dq_result_run REFERENCES [audit].etl_run (etl_run_id),
    check_id        VARCHAR(12)    NOT NULL,       -- DQ-nn (cross-source) or VR-xxx-nn (Warn rules)
    passed          BIT            NOT NULL,
    measured_value  DECIMAL(18,6)  NULL,
    threshold       DECIMAL(18,6)  NULL,
    details         NVARCHAR(MAX)  NULL,
    checked_at_utc  DATETIME2(0)   NOT NULL CONSTRAINT df_dq_result_checked DEFAULT SYSUTCDATETIME()
);

GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_dq_result_run'
                                           AND object_id = OBJECT_ID(N'audit.dq_result'))
CREATE INDEX ix_dq_result_run ON [audit].dq_result (etl_run_id, check_id);
