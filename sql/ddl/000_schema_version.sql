/* =============================================================================
   000_schema_version.sql: bootstrap for python -m etl.deploy
   Creates the table in which every applied script (this one included) is
   recorded. Must stay the first script, so it only needs the audit schema.
   ============================================================================= */
SET NOCOUNT ON;

IF SCHEMA_ID(N'audit') IS NULL EXEC (N'CREATE SCHEMA [audit] AUTHORIZATION dbo');
GO

IF OBJECT_ID(N'audit.schema_version', N'U') IS NULL
CREATE TABLE [audit].schema_version (
    script_name     NVARCHAR(200) NOT NULL CONSTRAINT pk_schema_version PRIMARY KEY,   -- repo-relative path
    script_kind     VARCHAR(10)   NOT NULL CONSTRAINT ck_schema_version_kind
                                           CHECK (script_kind IN ('versioned', 'repeatable')),
    checksum        CHAR(64)      NOT NULL,                 -- SHA-256 (hex), LF line endings
    applied_at_utc  DATETIME2(0)  NOT NULL CONSTRAINT df_schema_version_applied DEFAULT SYSUTCDATETIME(),
    applied_by      NVARCHAR(128) NOT NULL CONSTRAINT df_schema_version_by DEFAULT SUSER_SNAME(),
    git_commit      VARCHAR(40)   NULL
);
