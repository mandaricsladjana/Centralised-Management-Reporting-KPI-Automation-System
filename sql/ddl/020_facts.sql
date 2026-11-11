/* =============================================================================
   020_facts.sql: fact tables (schema dw)
   Data dictionary §4. Facts never hold NULL dimension keys (unknown = -1,
   not applicable = -2), except optional date keys and eight_d_key (D11).
   fact_8d is created first because fact_nonconformity, fact_8d_action and
   fact_audit_finding reference it.
   ============================================================================= */
SET NOCOUNT ON;
GO

/* --- fact_8d (§4.6): one 8D report, accumulating snapshot ------------------ */
IF OBJECT_ID(N'dw.fact_8d', N'U') IS NULL
CREATE TABLE dw.fact_8d (
    eight_d_key              INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_8d PRIMARY KEY,
    eight_d_id               VARCHAR(15)   NOT NULL CONSTRAINT uq_fact_8d_id UNIQUE,      -- 8D-YYYY-###
    opened_date_key          INT           NOT NULL CONSTRAINT fk_fact_8d_opened   REFERENCES dw.dim_calendar (date_key),
    part_key                 INT           NOT NULL CONSTRAINT fk_fact_8d_part     REFERENCES dw.dim_part (part_key),
    line_key                 INT           NOT NULL CONSTRAINT fk_fact_8d_line     REFERENCES dw.dim_production_line (line_key),
    defect_type_key          INT           NOT NULL CONSTRAINT fk_fact_8d_defect   REFERENCES dw.dim_defect_type (defect_type_key),
    problem_source           VARCHAR(20)   NOT NULL CONSTRAINT ck_fact_8d_source
                                                    CHECK (problem_source IN ('Internal', 'Customer complaint', 'Audit')),
    owner_name               NVARCHAR(80)  NOT NULL,
    owner_department         NVARCHAR(40)  NOT NULL,
    d1_date DATE NULL, d2_date DATE NULL, d3_date DATE NULL, d4_date DATE NULL,
    d5_date DATE NULL, d6_date DATE NULL, d7_date DATE NULL, d8_date DATE NULL,
    target_closure_date_key  INT           NOT NULL CONSTRAINT fk_fact_8d_target   REFERENCES dw.dim_calendar (date_key),
    closed_date_key          INT           NULL     CONSTRAINT fk_fact_8d_closed   REFERENCES dw.dim_calendar (date_key),
    status_reported          NVARCHAR(30)  NULL,    -- free text from Excel, DQ comparison only (VR-8D-02)
    etl_run_id               INT           NOT NULL CONSTRAINT fk_fact_8d_run REFERENCES [audit].etl_run (etl_run_id),
    source_file              NVARCHAR(260) NOT NULL,
    loaded_at_utc            DATETIME2(0)  NOT NULL CONSTRAINT df_fact_8d_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc           DATETIME2(0)  NULL,
    -- opened = D1 and closed = D8 by definition; milestone *order* is a Warn rule (VR-8D-01), not a constraint
    CONSTRAINT ck_fact_8d_opened_is_d1 CHECK (d1_date IS NULL
                                              OR opened_date_key = YEAR(d1_date) * 10000 + MONTH(d1_date) * 100 + DAY(d1_date)),
    CONSTRAINT ck_fact_8d_closed_is_d8 CHECK ((d8_date IS NULL AND closed_date_key IS NULL)
                                              OR closed_date_key = YEAR(d8_date) * 10000 + MONTH(d8_date) * 100 + DAY(d8_date))
);
GO

/* --- fact_production (§4.1): date x shift x line x part -------------------- */
IF OBJECT_ID(N'dw.fact_production', N'U') IS NULL
CREATE TABLE dw.fact_production (
    production_key  BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_production PRIMARY KEY,
    date_key        INT  NOT NULL CONSTRAINT fk_fact_production_date  REFERENCES dw.dim_calendar (date_key),
    shift_key       INT  NOT NULL CONSTRAINT fk_fact_production_shift REFERENCES dw.dim_shift (shift_key),
    line_key        INT  NOT NULL CONSTRAINT fk_fact_production_line  REFERENCES dw.dim_production_line (line_key),
    part_key        INT  NOT NULL CONSTRAINT fk_fact_production_part  REFERENCES dw.dim_part (part_key),
    qty_planned     INT  NOT NULL CONSTRAINT ck_fact_production_planned CHECK (qty_planned >= 0),
    qty_started     INT  NOT NULL CONSTRAINT ck_fact_production_started CHECK (qty_started >= 0),
    etl_run_id      INT           NOT NULL CONSTRAINT fk_fact_production_run REFERENCES [audit].etl_run (etl_run_id),
    source_file     NVARCHAR(260) NOT NULL,
    loaded_at_utc   DATETIME2(0)  NOT NULL CONSTRAINT df_fact_production_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc  DATETIME2(0)  NULL,
    CONSTRAINT uq_fact_production_grain UNIQUE (date_key, shift_key, line_key, part_key)
);
GO

/* --- fact_inspection (§4.2): date x shift x line x part x stage ------------ */
IF OBJECT_ID(N'dw.fact_inspection', N'U') IS NULL
CREATE TABLE dw.fact_inspection (
    inspection_key         BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_inspection PRIMARY KEY,
    date_key               INT  NOT NULL CONSTRAINT fk_fact_inspection_date  REFERENCES dw.dim_calendar (date_key),
    shift_key              INT  NOT NULL CONSTRAINT fk_fact_inspection_shift REFERENCES dw.dim_shift (shift_key),
    line_key               INT  NOT NULL CONSTRAINT fk_fact_inspection_line  REFERENCES dw.dim_production_line (line_key),
    part_key               INT  NOT NULL CONSTRAINT fk_fact_inspection_part  REFERENCES dw.dim_part (part_key),
    stage_key              INT  NOT NULL CONSTRAINT fk_fact_inspection_stage REFERENCES dw.dim_inspection_stage (stage_key),
    qty_inspected          INT  NOT NULL CONSTRAINT ck_fact_inspection_inspected CHECK (qty_inspected >= 0),
    qty_passed_first_time  INT  NOT NULL CONSTRAINT ck_fact_inspection_passed    CHECK (qty_passed_first_time >= 0),
    qty_failed             INT  NOT NULL CONSTRAINT ck_fact_inspection_failed    CHECK (qty_failed >= 0),
    etl_run_id             INT           NOT NULL CONSTRAINT fk_fact_inspection_run REFERENCES [audit].etl_run (etl_run_id),
    source_file            NVARCHAR(260) NOT NULL,
    loaded_at_utc          DATETIME2(0)  NOT NULL CONSTRAINT df_fact_inspection_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc         DATETIME2(0)  NULL,
    CONSTRAINT uq_fact_inspection_grain   UNIQUE (date_key, shift_key, line_key, part_key, stage_key),
    CONSTRAINT ck_fact_inspection_balance CHECK (qty_passed_first_time + qty_failed = qty_inspected)   -- VR-INS-01
);
GO

/* --- fact_nonconformity (§4.3): one NC record ------------------------------ */
IF OBJECT_ID(N'dw.fact_nonconformity', N'U') IS NULL
CREATE TABLE dw.fact_nonconformity (
    nc_key           BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_nonconformity PRIMARY KEY,
    nc_id            VARCHAR(20)  NOT NULL CONSTRAINT uq_fact_nonconformity_id UNIQUE,      -- NC-YYYY-######
    date_key         INT  NOT NULL CONSTRAINT fk_fact_nonconformity_date   REFERENCES dw.dim_calendar (date_key),
    shift_key        INT  NOT NULL CONSTRAINT fk_fact_nonconformity_shift  REFERENCES dw.dim_shift (shift_key),
    line_key         INT  NOT NULL CONSTRAINT fk_fact_nonconformity_line   REFERENCES dw.dim_production_line (line_key),
    part_key         INT  NOT NULL CONSTRAINT fk_fact_nonconformity_part   REFERENCES dw.dim_part (part_key),
    stage_key        INT  NOT NULL CONSTRAINT fk_fact_nonconformity_stage  REFERENCES dw.dim_inspection_stage (stage_key),
    defect_type_key  INT  NOT NULL CONSTRAINT fk_fact_nonconformity_defect REFERENCES dw.dim_defect_type (defect_type_key),
    qty_affected     INT  NOT NULL CONSTRAINT ck_fact_nonconformity_qty CHECK (qty_affected > 0),   -- number of defects
    disposition      VARCHAR(20)  NOT NULL CONSTRAINT ck_fact_nonconformity_disposition
                                           CHECK (disposition IN ('Scrap', 'Rework', 'Use-as-is', 'Return-to-supplier')),
    eight_d_key      INT  NULL     CONSTRAINT fk_fact_nonconformity_8d     REFERENCES dw.fact_8d (eight_d_key),
    etl_run_id       INT           NOT NULL CONSTRAINT fk_fact_nonconformity_run REFERENCES [audit].etl_run (etl_run_id),
    source_file      NVARCHAR(260) NOT NULL,
    loaded_at_utc    DATETIME2(0)  NOT NULL CONSTRAINT df_fact_nonconformity_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc   DATETIME2(0)  NULL
);
GO

/* --- fact_scrap (§4.4): one scrap booking ---------------------------------- */
IF OBJECT_ID(N'dw.fact_scrap', N'U') IS NULL
CREATE TABLE dw.fact_scrap (
    scrap_key        BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_scrap PRIMARY KEY,
    scrap_id         VARCHAR(20)  NOT NULL CONSTRAINT uq_fact_scrap_id UNIQUE,              -- SC-YYYY-######
    date_key         INT  NOT NULL CONSTRAINT fk_fact_scrap_date   REFERENCES dw.dim_calendar (date_key),
    shift_key        INT  NOT NULL CONSTRAINT fk_fact_scrap_shift  REFERENCES dw.dim_shift (shift_key),
    line_key         INT  NOT NULL CONSTRAINT fk_fact_scrap_line   REFERENCES dw.dim_production_line (line_key),
    part_key         INT  NOT NULL CONSTRAINT fk_fact_scrap_part   REFERENCES dw.dim_part (part_key),
    defect_type_key  INT  NOT NULL CONSTRAINT fk_fact_scrap_defect REFERENCES dw.dim_defect_type (defect_type_key),
    qty_scrapped     INT  NOT NULL CONSTRAINT ck_fact_scrap_qty CHECK (qty_scrapped > 0),
    unit_cost_eur    DECIMAL(18,4) NOT NULL CONSTRAINT ck_fact_scrap_unit_cost CHECK (unit_cost_eur >= 0),  -- snapshot (D7)
    scrap_cost_eur   AS CAST(ROUND(qty_scrapped * unit_cost_eur, 2) AS DECIMAL(18,2)) PERSISTED NOT NULL,
    etl_run_id       INT           NOT NULL CONSTRAINT fk_fact_scrap_run REFERENCES [audit].etl_run (etl_run_id),
    source_file      NVARCHAR(260) NOT NULL,
    loaded_at_utc    DATETIME2(0)  NOT NULL CONSTRAINT df_fact_scrap_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc   DATETIME2(0)  NULL
);
GO

/* --- fact_rework (§4.5): one rework job ------------------------------------ */
IF OBJECT_ID(N'dw.fact_rework', N'U') IS NULL
CREATE TABLE dw.fact_rework (
    rework_key       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_rework PRIMARY KEY,
    rework_id        VARCHAR(20)  NOT NULL CONSTRAINT uq_fact_rework_id UNIQUE,             -- RW-YYYY-######
    date_key         INT  NOT NULL CONSTRAINT fk_fact_rework_date   REFERENCES dw.dim_calendar (date_key),
    shift_key        INT  NOT NULL CONSTRAINT fk_fact_rework_shift  REFERENCES dw.dim_shift (shift_key),
    line_key         INT  NOT NULL CONSTRAINT fk_fact_rework_line   REFERENCES dw.dim_production_line (line_key),
    part_key         INT  NOT NULL CONSTRAINT fk_fact_rework_part   REFERENCES dw.dim_part (part_key),
    defect_type_key  INT  NOT NULL CONSTRAINT fk_fact_rework_defect REFERENCES dw.dim_defect_type (defect_type_key),
    qty_reworked     INT           NOT NULL CONSTRAINT ck_fact_rework_qty CHECK (qty_reworked > 0),
    qty_rework_ok    INT           NOT NULL,
    labour_hours     DECIMAL(9,2)  NOT NULL CONSTRAINT ck_fact_rework_hours CHECK (labour_hours > 0),
    labour_rate_eur  DECIMAL(18,4) NOT NULL CONSTRAINT ck_fact_rework_rate  CHECK (labour_rate_eur > 0),
    rework_cost_eur  DECIMAL(18,2) NOT NULL CONSTRAINT ck_fact_rework_cost  CHECK (rework_cost_eur >= 0),
    cost_source      VARCHAR(10)   NOT NULL CONSTRAINT ck_fact_rework_cost_source CHECK (cost_source IN ('Reported', 'Imputed')), -- D12
    etl_run_id       INT           NOT NULL CONSTRAINT fk_fact_rework_run REFERENCES [audit].etl_run (etl_run_id),
    source_file      NVARCHAR(260) NOT NULL,
    loaded_at_utc    DATETIME2(0)  NOT NULL CONSTRAINT df_fact_rework_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc   DATETIME2(0)  NULL,
    CONSTRAINT ck_fact_rework_ok CHECK (qty_rework_ok BETWEEN 0 AND qty_reworked)   -- VR-RWK-01
);
GO

/* --- fact_8d_action (§4.7): one action item -------------------------------- */
IF OBJECT_ID(N'dw.fact_8d_action', N'U') IS NULL
CREATE TABLE dw.fact_8d_action (
    action_key          INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_8d_action PRIMARY KEY,
    action_id           VARCHAR(20)    NOT NULL CONSTRAINT uq_fact_8d_action_id UNIQUE,     -- 8D-YYYY-###-A##
    eight_d_key         INT            NOT NULL CONSTRAINT fk_fact_8d_action_8d REFERENCES dw.fact_8d (eight_d_key),
    action_type         VARCHAR(12)    NOT NULL CONSTRAINT ck_fact_8d_action_type
                                                CHECK (action_type IN ('Containment', 'Corrective', 'Preventive')),
    action_description  NVARCHAR(500)  NOT NULL,
    owner_name          NVARCHAR(80)   NOT NULL,
    due_date_key        INT            NOT NULL CONSTRAINT fk_fact_8d_action_due       REFERENCES dw.dim_calendar (date_key),
    completed_date_key  INT            NULL     CONSTRAINT fk_fact_8d_action_completed REFERENCES dw.dim_calendar (date_key),
    status_reported     NVARCHAR(30)   NULL,
    etl_run_id          INT            NOT NULL CONSTRAINT fk_fact_8d_action_run REFERENCES [audit].etl_run (etl_run_id),
    source_file         NVARCHAR(260)  NOT NULL,
    loaded_at_utc       DATETIME2(0)   NOT NULL CONSTRAINT df_fact_8d_action_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc      DATETIME2(0)   NULL
);
GO

/* --- fact_audit_finding (§4.8): one audit finding -------------------------- */
IF OBJECT_ID(N'dw.fact_audit_finding', N'U') IS NULL
CREATE TABLE dw.fact_audit_finding (
    finding_key          INT IDENTITY(1,1) NOT NULL CONSTRAINT pk_fact_audit_finding PRIMARY KEY,
    finding_id           VARCHAR(20)    NOT NULL CONSTRAINT uq_fact_audit_finding_id UNIQUE,  -- AF-YYYY-####
    audit_id             VARCHAR(20)    NOT NULL,                                             -- degenerate dimension
    audit_date_key       INT            NOT NULL CONSTRAINT fk_fact_audit_finding_audit_date REFERENCES dw.dim_calendar (date_key),
    audit_type           VARCHAR(20)    NOT NULL CONSTRAINT ck_fact_audit_finding_type
                                                 CHECK (audit_type IN ('System', 'Process', 'Product', 'Layered Process', 'Customer')),
    standard_reference   VARCHAR(30)    NULL,
    line_key             INT            NOT NULL CONSTRAINT fk_fact_audit_finding_line REFERENCES dw.dim_production_line (line_key),
    classification       VARCHAR(15)    NOT NULL CONSTRAINT ck_fact_audit_finding_class
                                                 CHECK (classification IN ('Major NC', 'Minor NC', 'Observation', 'OFI')),
    finding_description  NVARCHAR(500)  NOT NULL,
    owner_name           NVARCHAR(80)   NOT NULL,
    due_date_key         INT            NOT NULL CONSTRAINT fk_fact_audit_finding_due    REFERENCES dw.dim_calendar (date_key),
    closed_date_key      INT            NULL     CONSTRAINT fk_fact_audit_finding_closed REFERENCES dw.dim_calendar (date_key),
    eight_d_key          INT            NULL     CONSTRAINT fk_fact_audit_finding_8d     REFERENCES dw.fact_8d (eight_d_key),
    status_reported      NVARCHAR(30)   NULL,
    etl_run_id           INT            NOT NULL CONSTRAINT fk_fact_audit_finding_run REFERENCES [audit].etl_run (etl_run_id),
    source_file          NVARCHAR(260)  NOT NULL,
    loaded_at_utc        DATETIME2(0)   NOT NULL CONSTRAINT df_fact_audit_finding_loaded DEFAULT SYSUTCDATETIME(),
    updated_at_utc       DATETIME2(0)   NULL
);
GO

/* --- Indexes on main date keys and the 8D link (FKs are not auto-indexed) -- */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_fact_inspection_date' AND object_id = OBJECT_ID(N'dw.fact_inspection'))
    CREATE INDEX ix_fact_inspection_date ON dw.fact_inspection (date_key, line_key) INCLUDE (stage_key, qty_inspected, qty_passed_first_time);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_fact_nonconformity_date' AND object_id = OBJECT_ID(N'dw.fact_nonconformity'))
    CREATE INDEX ix_fact_nonconformity_date ON dw.fact_nonconformity (date_key, line_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_fact_nonconformity_8d' AND object_id = OBJECT_ID(N'dw.fact_nonconformity'))
    CREATE INDEX ix_fact_nonconformity_8d ON dw.fact_nonconformity (eight_d_key) WHERE eight_d_key IS NOT NULL;
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_fact_scrap_date' AND object_id = OBJECT_ID(N'dw.fact_scrap'))
    CREATE INDEX ix_fact_scrap_date ON dw.fact_scrap (date_key, line_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_fact_rework_date' AND object_id = OBJECT_ID(N'dw.fact_rework'))
    CREATE INDEX ix_fact_rework_date ON dw.fact_rework (date_key, line_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_fact_8d_action_8d' AND object_id = OBJECT_ID(N'dw.fact_8d_action'))
    CREATE INDEX ix_fact_8d_action_8d ON dw.fact_8d_action (eight_d_key);
