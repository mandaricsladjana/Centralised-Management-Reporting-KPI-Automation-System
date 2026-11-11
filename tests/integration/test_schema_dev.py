"""Integration tests against the DEV database (QualityKPI_Dev on SQL Express).

Prerequisite: python -m etl.deploy --env dev. Skipped when DEV is unreachable.
Every test that writes runs inside a transaction that is rolled back.
"""
import pyodbc
import pytest

from etl import deploy
from etl.config import ConfigError, db_target, load_env, load_settings
from etl.db import connect

pytestmark = pytest.mark.db

DIMENSIONS = {  # table: (key column, code column)
    "dw.dim_shift": ("shift_key", "shift_code"),
    "dw.dim_production_line": ("line_key", "line_code"),
    "dw.dim_vehicle_program": ("program_key", "program_code"),
    "dw.dim_part": ("part_key", "part_number"),
    "dw.dim_defect_type": ("defect_type_key", "defect_code"),
    "dw.dim_inspection_stage": ("stage_key", "stage_code"),
}
FACTS = ["dw.fact_production", "dw.fact_inspection", "dw.fact_nonconformity", "dw.fact_scrap",
         "dw.fact_rework", "dw.fact_8d", "dw.fact_8d_action", "dw.fact_audit_finding"]


@pytest.fixture(scope="module")
def conn():
    load_env()
    try:
        c = connect(db_target("dev"))
    except (ConfigError, pyodbc.Error) as exc:
        pytest.skip(f"DEV database not reachable: {exc}")
    if c.cursor().execute("SELECT OBJECT_ID(N'audit.schema_version')").fetchval() is None:
        pytest.skip("DEV schema not deployed: run python -m etl.deploy --env dev")
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def cur(conn):
    """Cursor whose changes are always rolled back."""
    yield conn.cursor()
    conn.rollback()


def scalar(cur, sql, *params):
    return cur.execute(sql, *params).fetchval()


# --- deployment state ------------------------------------------------------------
def test_every_script_is_applied_with_current_checksum(cur):
    applied = dict(cur.execute("SELECT script_name, checksum FROM [audit].schema_version").fetchall())
    assert deploy.plan(deploy.discover_scripts(), applied) == [], "run python -m etl.deploy --env dev"


def test_versioned_scripts_are_idempotent(cur):
    """PR checklist: scripts run twice without error and without changing data."""
    before = {t: scalar(cur, f"SELECT COUNT(*) FROM {t}") for t in DIMENSIONS}
    for script in deploy.discover_scripts():
        if script.kind == "versioned":
            for batch in script.batches():
                cur.execute(batch)
                while cur.nextset():
                    pass
    assert {t: scalar(cur, f"SELECT COUNT(*) FROM {t}") for t in DIMENSIONS} == before


@pytest.mark.parametrize("schema", ["stg", "dw", "audit", "rpt", "etl"])
def test_schemas_exist(cur, schema):
    assert scalar(cur, "SELECT SCHEMA_ID(?)", schema) is not None


@pytest.mark.parametrize("table", list(DIMENSIONS) + FACTS + ["dw.kpi_target", "dw.plant_holiday"])
def test_dw_tables_have_lineage_columns(cur, table):
    cols = {r.name: r.is_nullable for r in cur.execute(
        "SELECT name, is_nullable FROM sys.columns WHERE object_id = OBJECT_ID(?)", table)}
    assert cols, f"{table} missing"
    assert cols["etl_run_id"] is False and cols["source_file"] is False
    assert cols["loaded_at_utc"] is False and cols["updated_at_utc"] is True


# --- special members and reference data (D11) --------------------------------------
@pytest.mark.parametrize("table", DIMENSIONS)
def test_special_members_exist(cur, table):
    key, code = DIMENSIONS[table]
    rows = dict(cur.execute(f"SELECT {key}, {code} FROM {table} WHERE {key} < 0").fetchall())
    assert set(rows) == {-1, -2}


def test_reference_data_shifts_and_stages(cur):
    shifts = cur.execute("SELECT shift_code, start_time, crosses_midnight FROM dw.dim_shift "
                         "WHERE shift_key > 0 ORDER BY sort_order").fetchall()
    assert [(s[0], s[1].strftime("%H:%M"), s[2]) for s in shifts] == [
        ("E", "06:00", False), ("L", "14:00", False), ("N", "22:00", True)]
    stages = cur.execute("SELECT stage_code FROM dw.dim_inspection_stage WHERE stage_key > 0 "
                         "ORDER BY stage_sequence").fetchall()
    assert [s[0] for s in stages] == ["IPC", "EOL", "FA"]


def test_validation_rules_cover_data_dictionary(cur):
    assert scalar(cur, "SELECT COUNT(*) FROM [audit].validation_rule") == 15


# --- calendar (§3.1) ---------------------------------------------------------------------
def test_calendar_covers_configured_range_without_gaps(cur):
    p = load_settings()["periods"]
    lo, hi, n = cur.execute("SELECT MIN([date]), MAX([date]), COUNT(*) FROM dw.dim_calendar").fetchone()
    assert (lo, hi) == (p["calendar_start"], p["calendar_end"])
    assert n == (hi - lo).days + 1


@pytest.mark.parametrize("date_key, dow, iso_year, iso_week, working", [
    (20241230, 1, 2025, 1, True),    # Monday in ISO week 2025-W01
    (20270101, 5, 2026, 53, False),  # 2026 has 53 ISO weeks; New Year is a plant holiday
    (20250809, 6, 2025, 32, False),  # Saturday
    (20250806, 3, 2025, 32, False),  # summer shutdown
    (20250312, 3, 2025, 11, True),   # ordinary Wednesday
])
def test_calendar_attributes(cur, date_key, dow, iso_year, iso_week, working):
    row = cur.execute("SELECT day_of_week, iso_year, iso_week, is_working_day FROM dw.dim_calendar "
                      "WHERE date_key = ?", date_key).fetchone()
    assert tuple(row) == (dow, iso_year, iso_week, working)


def test_calendar_independent_of_datefirst_and_language(cur):
    cur.execute("SET DATEFIRST 7; SET LANGUAGE German;")
    try:
        cur.execute("EXEC etl.usp_build_calendar @start_date = '2025-03-10', @end_date = '2025-03-16'")
        row = cur.execute("SELECT day_of_week, day_name, month_name FROM dw.dim_calendar "
                          "WHERE date_key = 20250310").fetchone()
        assert tuple(row) == (1, "Monday", "March")
    finally:
        cur.execute("SET DATEFIRST 1; SET LANGUAGE us_english;")


# --- constraints reject what the validator should have caught -------------------------------
def _seed_one_of_each(cur):
    """Minimal master data for constraint tests; returns (line_key, part_key)."""
    cur.execute("INSERT INTO dw.dim_production_line (line_code, line_name, process_type, area, is_active, etl_run_id, source_file) "
                "VALUES ('TST', N'Test line', 'Assembly', N'Hall T', 1, 0, N'test')")
    line = scalar(cur, "SELECT line_key FROM dw.dim_production_line WHERE line_code = 'TST'")
    cur.execute("INSERT INTO dw.dim_vehicle_program (program_code, program_name, oem_customer, vehicle_segment, sop_date, etl_run_id, source_file) "
                "VALUES ('VP-TST', N'Test', N'Test OEM', 'C-SUV', '2024-01-01', 0, N'test')")
    prog = scalar(cur, "SELECT program_key FROM dw.dim_vehicle_program WHERE program_code = 'VP-TST'")
    cur.execute("INSERT INTO dw.dim_part (part_number, part_name, component_type, program_key, primary_line_key, "
                "standard_unit_cost_eur, opportunities_per_unit, is_active, etl_run_id, source_file) "
                "VALUES ('DAC-99999', N'Test part', 'Bracket', ?, ?, 42.5, 12, 1, 0, N'test')", prog, line)
    part = scalar(cur, "SELECT part_key FROM dw.dim_part WHERE part_number = 'DAC-99999'")
    return line, part


def test_inspection_balance_is_enforced(cur):
    line, part = _seed_one_of_each(cur)
    sql = ("INSERT INTO dw.fact_inspection (date_key, shift_key, line_key, part_key, stage_key, qty_inspected, "
           "qty_passed_first_time, qty_failed, etl_run_id, source_file) VALUES (20250312, 1, ?, ?, 2, ?, ?, ?, 0, N'test')")
    cur.execute(sql, line, part, 100, 97, 3)
    with pytest.raises(pyodbc.IntegrityError, match="ck_fact_inspection_balance"):
        cur.execute(sql.replace("1, ?, ?, 2", "2, ?, ?, 2"), line, part, 100, 97, 4)


def test_scrap_cost_is_computed_from_snapshot(cur):
    line, part = _seed_one_of_each(cur)
    cur.execute("INSERT INTO dw.fact_scrap (scrap_id, date_key, shift_key, line_key, part_key, defect_type_key, "
                "qty_scrapped, unit_cost_eur, etl_run_id, source_file) "
                "VALUES ('SC-2025-000001', 20250312, 3, ?, ?, -1, 180, 42.5, 0, N'test')", line, part)
    assert scalar(cur, "SELECT scrap_cost_eur FROM dw.fact_scrap WHERE scrap_id = 'SC-2025-000001'") == 7650


def test_8d_closed_date_must_equal_d8(cur):
    line, part = _seed_one_of_each(cur)
    sql = ("INSERT INTO dw.fact_8d (eight_d_id, opened_date_key, part_key, line_key, defect_type_key, problem_source, "
           "owner_name, owner_department, d1_date, d8_date, target_closure_date_key, closed_date_key, etl_run_id, source_file) "
           "VALUES (?, 20250301, ?, ?, -1, 'Internal', N'A. Test', N'Quality', '2025-03-01', ?, 20250430, ?, 0, N'test')")
    cur.execute(sql, "8D-2025-001", part, line, "2025-04-15", 20250415)
    with pytest.raises(pyodbc.IntegrityError, match="ck_fact_8d_closed_is_d8"):
        cur.execute(sql, "8D-2025-002", part, line, None, 20250415)    # closed without D8
    with pytest.raises(pyodbc.IntegrityError, match="ck_fact_8d_closed_is_d8"):
        cur.execute(sql, "8D-2025-003", part, line, "2025-04-15", None)  # D8 but not closed


def test_nonconformity_disposition_list_is_enforced(cur):
    line, part = _seed_one_of_each(cur)
    sql = ("INSERT INTO dw.fact_nonconformity (nc_id, date_key, shift_key, line_key, part_key, stage_key, defect_type_key, "
           "qty_affected, disposition, etl_run_id, source_file) VALUES (?, 20250312, 1, ?, ?, 2, -1, 1, ?, 0, N'test')")
    cur.execute(sql, "NC-2025-000001", line, part, "Use-as-is")
    with pytest.raises(pyodbc.IntegrityError, match="ck_fact_nonconformity_disposition"):
        cur.execute(sql, "NC-2025-000002", line, part, "Scrapped")    # unstandardised variant


def test_audit_finding_classification_list_is_enforced_and_line_can_be_not_applicable(cur):
    sql = ("INSERT INTO dw.fact_audit_finding (finding_id, audit_id, audit_date_key, audit_type, line_key, classification, "
           "finding_description, owner_name, due_date_key, etl_run_id, source_file) "
           "VALUES (?, 'AU-2025-001', 20250312, 'System', -2, ?, N'Plant-wide finding', N'A. Test', 20250412, 0, N'test')")
    cur.execute(sql, "AF-2025-0001", "Major NC")
    with pytest.raises(pyodbc.IntegrityError, match="ck_fact_audit_finding_class"):
        cur.execute(sql, "AF-2025-0002", "major")                     # VR-AF-01 variant


def test_file_hash_can_be_loaded_only_once_per_source(cur):
    run = scalar(cur, "INSERT INTO [audit].etl_run (environment, status, triggered_by) OUTPUT inserted.etl_run_id "
                      "VALUES ('dev', 'Running', 'manual')")
    sql = ("INSERT INTO [audit].file_manifest (etl_run_id, source_name, file_name, file_sha256, file_size_bytes, status) "
           "VALUES (?, 'scrap_log', N'scrap_log.csv', REPLICATE('a', 64), 10, ?)")
    cur.execute(sql, run, "Loaded")
    cur.execute(sql, run, "Skipped-duplicate")          # allowed: a skip is recorded with the same hash
    with pytest.raises(pyodbc.IntegrityError, match="uq_file_manifest_loaded_hash"):
        cur.execute(sql, run, "Loaded")


def test_roles_have_least_privilege(cur):
    perms = {(r.grantee, r.permission_name, r.schema_name) for r in cur.execute(
        "SELECT USER_NAME(p.grantee_principal_id) AS grantee, p.permission_name, SCHEMA_NAME(p.major_id) AS schema_name "
        "FROM sys.database_permissions p WHERE p.class = 3 AND USER_NAME(p.grantee_principal_id) IN ('etl_writer', 'rpt_reader')")}
    assert {g_p_s for g_p_s in perms if g_p_s[0] == "rpt_reader"} == {("rpt_reader", "SELECT", "rpt")}
    assert ("etl_writer", "EXECUTE", "etl") in perms
    assert not any(g == "etl_writer" and p in ("INSERT", "UPDATE", "DELETE") and s == "dw" for g, p, s in perms)
