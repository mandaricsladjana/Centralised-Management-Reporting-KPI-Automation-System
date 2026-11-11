"""Helpers shared by the DEV integration tests (imported by test modules and conftest)."""

STAGING = ["line", "program", "part", "defect_type", "production", "inspection", "nonconformity", "scrap",
           "rework", "eight_d", "eight_d_nc_link", "eight_d_action", "audit_finding"]
SRC = "test_file.xlsx"


def stage(cur, table, rows):
    """Replace stg.<table> with dict rows; adds source_file and source_row_number."""
    cur.execute(f"DELETE FROM stg.{table}")
    for i, row in enumerate(rows, start=2):
        row = {**row, "source_file": SRC, "source_row_number": i}
        cur.execute(f"INSERT INTO stg.{table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                    *row.values())


def call(cur, proc, run_id, outputs=("rows_inserted", "rows_updated")):
    """EXEC an etl.<proc> load procedure; return its OUTPUT parameters as a dict."""
    declare = ", ".join(f"@{o} INT" for o in outputs)
    args = ", ".join(f"@{o} = @{o} OUTPUT" for o in outputs)
    row = cur.execute(f"SET NOCOUNT ON; DECLARE {declare}; EXEC etl.{proc} @etl_run_id = ?, {args}; "
                      f"SELECT {', '.join('@' + o for o in outputs)};", run_id).fetchone()
    return dict(zip(outputs, row))


def scalar(cur, sql, *params):
    return cur.execute(sql, *params).fetchval()


# --- the separate test database (never DEV) ---------------------------------------------
TEST_DB = "QualityKPI_Test"
RESET_SQL = [  # children before parents; keeps run 0, schema_version, rules, calendar, targets
    "DELETE FROM [audit].rejected_row", "DELETE FROM [audit].dq_result", "DELETE FROM [audit].file_manifest",
    "DELETE FROM dw.fact_8d_action", "DELETE FROM dw.fact_nonconformity", "DELETE FROM dw.fact_audit_finding",
    "DELETE FROM dw.fact_8d", "DELETE FROM dw.fact_scrap", "DELETE FROM dw.fact_rework",
    "DELETE FROM dw.fact_inspection", "DELETE FROM dw.fact_production",
    "DELETE FROM dw.dim_part WHERE part_key > 0", "DELETE FROM dw.dim_vehicle_program WHERE program_key > 0",
    "DELETE FROM dw.dim_production_line WHERE line_key > 0", "DELETE FROM dw.dim_defect_type WHERE defect_type_key > 0",
    "DELETE FROM [audit].etl_run WHERE etl_run_id > 0",
]
_prepared = False


def prepare_test_db():
    """Create QualityKPI_Test if needed, deploy the schema once per session, and empty its data.
    Raises pytest.skip when no local SQL Server is reachable."""
    global _prepared
    import pyodbc
    import pytest

    from etl import deploy
    from etl.config import ConfigError, db_target, load_env
    from etl.db import connect

    load_env()
    try:
        with connect(db_target("dev", "master"), autocommit=True) as m:
            m.cursor().execute(f"IF DB_ID('{TEST_DB}') IS NULL CREATE DATABASE {TEST_DB}")
    except (ConfigError, pyodbc.Error) as exc:
        pytest.skip(f"local SQL Server not reachable: {exc}")
    if not _prepared:
        deploy.deploy("dev", database=TEST_DB)
        _prepared = True
    with connect(db_target("dev", TEST_DB)) as c:
        for sql in RESET_SQL:
            c.cursor().execute(sql)
        c.commit()
