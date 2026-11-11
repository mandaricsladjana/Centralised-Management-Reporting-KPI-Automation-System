/* =============================================================================
   030_staging.sql: one staging table per source (schema stg)
   Data dictionary §5 and §6. Columns are the *standardised* source columns,
   already typed and validated by Python, with business codes instead of keys.
   Staging is truncated at the start of each load; etl.usp_merge_* procedures
   resolve codes to keys and MERGE into dw. No constraints here on purpose:
   rules are enforced by the validator (rows) and by dw (last line of defence).
   ============================================================================= */
SET NOCOUNT ON;
GO

IF OBJECT_ID(N'stg.line', N'U') IS NULL
CREATE TABLE stg.line (
    line_code VARCHAR(5), line_name NVARCHAR(60), process_type VARCHAR(30), area NVARCHAR(30), is_active BIT,
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.program', N'U') IS NULL
CREATE TABLE stg.program (
    program_code VARCHAR(15), program_name NVARCHAR(60), oem_customer NVARCHAR(60), vehicle_segment VARCHAR(20),
    sop_date DATE, eop_date DATE,
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.part', N'U') IS NULL
CREATE TABLE stg.part (
    part_number VARCHAR(20), part_name NVARCHAR(100), component_type VARCHAR(30),
    program_code VARCHAR(15), primary_line_code VARCHAR(5),
    standard_unit_cost_eur DECIMAL(18,4), opportunities_per_unit SMALLINT, is_active BIT,
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.defect_type', N'U') IS NULL
CREATE TABLE stg.defect_type (
    defect_code VARCHAR(10), defect_name NVARCHAR(80), defect_category VARCHAR(20), severity CHAR(1),
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.production', N'U') IS NULL
CREATE TABLE stg.production (
    event_date DATE, shift_code VARCHAR(5), line_code VARCHAR(5), part_number VARCHAR(20),
    qty_planned INT, qty_started INT,
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.inspection', N'U') IS NULL
CREATE TABLE stg.inspection (
    event_date DATE, shift_code VARCHAR(5), line_code VARCHAR(5), part_number VARCHAR(20), stage_code VARCHAR(10),
    qty_inspected INT, qty_passed_first_time INT, qty_failed INT,
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.nonconformity', N'U') IS NULL
CREATE TABLE stg.nonconformity (
    nc_id VARCHAR(20), event_date DATE, shift_code VARCHAR(5), line_code VARCHAR(5), part_number VARCHAR(20),
    stage_code VARCHAR(10), defect_code VARCHAR(10), qty_affected INT, disposition VARCHAR(20),
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.scrap', N'U') IS NULL
CREATE TABLE stg.scrap (
    scrap_id VARCHAR(20), event_date DATE, shift_code VARCHAR(5), line_code VARCHAR(5), part_number VARCHAR(20),
    defect_code VARCHAR(10), qty_scrapped INT,
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.rework', N'U') IS NULL
CREATE TABLE stg.rework (
    rework_id VARCHAR(20), event_date DATE, shift_code VARCHAR(5), line_code VARCHAR(5), part_number VARCHAR(20),
    defect_code VARCHAR(10), qty_reworked INT, qty_rework_ok INT, labour_hours DECIMAL(9,2),
    labour_rate_eur DECIMAL(18,4), rework_cost_eur DECIMAL(18,2), cost_source VARCHAR(10),
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.eight_d', N'U') IS NULL
CREATE TABLE stg.eight_d (
    eight_d_id VARCHAR(15), opened_date DATE, part_number VARCHAR(20), line_code VARCHAR(5), defect_code VARCHAR(10),
    problem_source VARCHAR(20), owner_name NVARCHAR(80), owner_department NVARCHAR(40),
    d1_date DATE, d2_date DATE, d3_date DATE, d4_date DATE, d5_date DATE, d6_date DATE, d7_date DATE, d8_date DATE,
    target_closure_date DATE, status_reported NVARCHAR(30),
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
/* The tracker's comma-separated "linked NC IDs" field, split by Python: one row per (8D, NC). */
IF OBJECT_ID(N'stg.eight_d_nc_link', N'U') IS NULL
CREATE TABLE stg.eight_d_nc_link (
    eight_d_id VARCHAR(15), nc_id VARCHAR(20),
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.eight_d_action', N'U') IS NULL
CREATE TABLE stg.eight_d_action (
    action_id VARCHAR(20), eight_d_id VARCHAR(15), action_type VARCHAR(12), action_description NVARCHAR(500),
    owner_name NVARCHAR(80), due_date DATE, completed_date DATE, status_reported NVARCHAR(30),
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
GO
IF OBJECT_ID(N'stg.audit_finding', N'U') IS NULL
CREATE TABLE stg.audit_finding (
    finding_id VARCHAR(20), audit_id VARCHAR(20), audit_date DATE, audit_type VARCHAR(20), standard_reference VARCHAR(30),
    line_code VARCHAR(5),          -- NULL = plant-wide finding -> line_key -2 (Not applicable)
    classification VARCHAR(15), finding_description NVARCHAR(500), owner_name NVARCHAR(80),
    due_date DATE, closed_date DATE, eight_d_id VARCHAR(15), status_reported NVARCHAR(30),
    source_file NVARCHAR(260) NOT NULL, source_row_number INT NOT NULL
);
