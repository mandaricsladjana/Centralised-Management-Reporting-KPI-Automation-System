"""Database side of the pipeline: audit tables, staging, MERGE procedures."""
from __future__ import annotations

import json
from pathlib import Path

# source -> (staging table, load procedure, OUTPUT parameters)
TARGETS = {
    "master_lines":     ("stg.line",          "usp_merge_line"),
    "master_programs":  ("stg.program",       "usp_merge_program"),
    "master_parts":     ("stg.part",          "usp_merge_part"),
    "master_defects":   ("stg.defect_type",   "usp_merge_defect_type"),
    "production_daily": ("stg.production",    "usp_merge_production"),
    "qa_inspections":   ("stg.inspection",    "usp_merge_inspection"),
    "eight_d_tracker":  ("stg.eight_d",       "usp_merge_8d"),
    "qa_defects":       ("stg.nonconformity", "usp_merge_nonconformity"),
    "scrap_log":        ("stg.scrap",         "usp_merge_scrap"),
    "rework_log":       ("stg.rework",        "usp_merge_rework"),
    "eight_d_actions":  ("stg.eight_d_action", "usp_merge_8d_action"),
    "audit_findings":   ("stg.audit_finding", "usp_merge_audit_finding"),
}
LOAD_ORDER = list(TARGETS)          # data dictionary §6: masters, production, inspection, 8D, NC, ...
STAGING_TABLES = [t for t, _ in TARGETS.values()] + ["stg.eight_d_nc_link"]
MERGE_OUTPUTS = ("rows_inserted", "rows_updated")
LINK_OUTPUTS = ("rows_linked", "rows_unlinked", "links_unresolved")


# --- runs ----------------------------------------------------------------------------------
def start_run(conn, environment: str, triggered_by: str, git_commit: str | None) -> int:
    run_id = conn.cursor().execute(
        "SET NOCOUNT ON; INSERT INTO [audit].etl_run (environment, status, triggered_by, git_commit) "
        "OUTPUT inserted.etl_run_id VALUES (?, 'Running', ?, ?)", environment, triggered_by, git_commit).fetchval()
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, files: int, read: int, loaded: int, rejected: int,
               error: str | None = None) -> None:
    conn.cursor().execute(
        "UPDATE [audit].etl_run SET status = ?, ended_at_utc = SYSUTCDATETIME(), files_processed = ?, "
        "rows_read = ?, rows_loaded = ?, rows_rejected = ?, error_message = ? WHERE etl_run_id = ?",
        status, files, read, loaded, rejected, error, run_id)
    conn.commit()


# --- files -----------------------------------------------------------------------------------
def already_loaded(cur, source_name: str, sha256: str) -> bool:
    return cur.execute("SELECT 1 FROM [audit].file_manifest WHERE source_name = ? AND file_sha256 = ? "
                       "AND status = 'Loaded'", source_name, sha256).fetchval() is not None


def insert_manifest(cur, run_id: int, source_name: str, path: Path, sha256: str, size: int,
                    status: str, row_count: int | None, blob_path: str | None = None) -> int:
    return cur.execute(
        "SET NOCOUNT ON; INSERT INTO [audit].file_manifest (etl_run_id, source_name, file_name, blob_path, "
        "file_sha256, file_size_bytes, row_count, status) OUTPUT inserted.file_id VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        run_id, source_name, path.name, blob_path, sha256, size, row_count, status).fetchval()


def insert_rejections(cur, run_id: int, file_id: int, rejections) -> None:
    if not rejections:
        return
    cur.fast_executemany = True
    cur.executemany(
        "INSERT INTO [audit].rejected_row (etl_run_id, file_id, source_row_number, rule_id, reason, raw_record) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(run_id, file_id, r.source_row_number, r.rule_id, r.reason[:400],
          json.dumps(r.raw, ensure_ascii=False, default=str)) for r in rejections])
    cur.fast_executemany = False


def insert_dq(cur, run_id: int, check_id: str, passed: bool, measured=None, threshold=None,
              details: str | None = None) -> None:
    cur.execute("INSERT INTO [audit].dq_result (etl_run_id, check_id, passed, measured_value, threshold, details) "
                "VALUES (?, ?, ?, ?, ?, ?)", run_id, check_id, passed, measured, threshold, details)


def insert_warnings(cur, run_id: int, file_name: str, warnings: dict[str, list[str]]) -> None:
    """One dq_result row per (file, Warn rule): count + the first examples."""
    for rule, items in sorted(warnings.items()):
        sample = "; ".join(items[:10]) + (f"; ... (+{len(items) - 10} more)" if len(items) > 10 else "")
        insert_dq(cur, run_id, rule, False, len(items), 0, f"{file_name}: {sample}")


# --- staging and MERGE -------------------------------------------------------------------------
def truncate_staging(cur) -> None:
    for t in STAGING_TABLES:
        cur.execute(f"TRUNCATE TABLE {t}")


def stage(cur, table: str, rows: list[dict]) -> None:
    cur.execute(f"TRUNCATE TABLE {table}")
    if not rows:
        return
    cols = list(rows[0])
    cur.fast_executemany = True
    cur.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [tuple(r[c] for c in cols) for r in rows])
    cur.fast_executemany = False


def call_proc(cur, proc: str, run_id: int, outputs=MERGE_OUTPUTS) -> dict:
    declare = ", ".join(f"@{o} INT" for o in outputs)
    args = ", ".join(f"@{o} = @{o} OUTPUT" for o in outputs)
    row = cur.execute(f"SET NOCOUNT ON; DECLARE {declare}; EXEC etl.{proc} @etl_run_id = ?, {args}; "
                      f"SELECT {', '.join('@' + o for o in outputs)};", run_id).fetchone()
    return dict(zip(outputs, row))


def known_codes(cur) -> dict[str, set[str]]:
    """Master codes currently in dw (special members excluded), for VR-GEN-06 / VR-8D-03."""
    queries = {
        "line":    "SELECT line_code FROM dw.dim_production_line WHERE line_key > 0",
        "shift":   "SELECT shift_code FROM dw.dim_shift WHERE shift_key > 0",
        "part":    "SELECT part_number FROM dw.dim_part WHERE part_key > 0",
        "stage":   "SELECT stage_code FROM dw.dim_inspection_stage WHERE stage_key > 0",
        "defect":  "SELECT defect_code FROM dw.dim_defect_type WHERE defect_type_key > 0",
        "program": "SELECT program_code FROM dw.dim_vehicle_program WHERE program_key > 0",
        "eight_d": "SELECT eight_d_id FROM dw.fact_8d",
    }
    return {k: {r[0] for r in cur.execute(q).fetchall()} for k, q in queries.items()}
