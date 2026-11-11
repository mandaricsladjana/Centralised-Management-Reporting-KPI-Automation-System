/* =============================================================================
   040_security.sql: database roles (least privilege, blueprint §8.2, D5)
   Roles only. Users are NOT created here because a SQL-auth user needs a
   password, which must never be in Git. Membership is granted separately:
     PROD: CREATE USER etl_loader WITH PASSWORD = '<from .env>';
           ALTER ROLE etl_writer ADD MEMBER etl_loader;
           CREATE USER [<entra user>] FROM EXTERNAL PROVIDER;
           ALTER ROLE rpt_reader ADD MEMBER [<entra user>];
     DEV:  the Windows login is dbo; no extra users are needed.
   GRANT is idempotent, so this script is safe to re-run.
   ============================================================================= */
SET NOCOUNT ON;
GO

IF DATABASE_PRINCIPAL_ID(N'etl_writer') IS NULL CREATE ROLE etl_writer AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID(N'rpt_reader') IS NULL CREATE ROLE rpt_reader AUTHORIZATION dbo;
GO

/* Scheduled ETL: writes staging and audit, changes dw only through etl.* procedures
   (ownership chaining: dbo owns both), reads dw for key lookups and DQ checks.
   ALTER on stg is needed for TRUNCATE TABLE. */
GRANT SELECT, INSERT, UPDATE, DELETE, ALTER ON SCHEMA::stg     TO etl_writer;
GRANT SELECT, INSERT, UPDATE                ON SCHEMA::[audit] TO etl_writer;
GRANT SELECT                                ON SCHEMA::dw      TO etl_writer;
GRANT EXECUTE                               ON SCHEMA::etl     TO etl_writer;

/* Power BI: the rpt views only (data dictionary §8: never dw directly). */
GRANT SELECT ON SCHEMA::rpt TO rpt_reader;
