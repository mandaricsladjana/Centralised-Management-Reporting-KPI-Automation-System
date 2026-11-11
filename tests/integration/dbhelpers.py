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
