/* =============================================================================
   001_schemas.sql: database schemas
   Data dictionary §1. Versioned migration: applied once, recorded in
   audit.schema_version (by python -m etl.deploy). Idempotent anyway.
   ============================================================================= */
SET NOCOUNT ON;

IF SCHEMA_ID(N'stg')   IS NULL EXEC (N'CREATE SCHEMA stg AUTHORIZATION dbo');    -- staging, truncated per load
IF SCHEMA_ID(N'dw')    IS NULL EXEC (N'CREATE SCHEMA dw AUTHORIZATION dbo');     -- star schema
IF SCHEMA_ID(N'audit') IS NULL EXEC (N'CREATE SCHEMA [audit] AUTHORIZATION dbo');-- lineage and data quality
IF SCHEMA_ID(N'rpt')   IS NULL EXEC (N'CREATE SCHEMA rpt AUTHORIZATION dbo');    -- views for Power BI
IF SCHEMA_ID(N'etl')   IS NULL EXEC (N'CREATE SCHEMA etl AUTHORIZATION dbo');    -- load procedures (MERGE)
