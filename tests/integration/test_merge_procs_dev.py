"""Integration tests for the etl.usp_merge_* load procedures on DEV.

Each test stages rows, calls the procedures and checks dw. Everything runs in
one transaction per test and is rolled back, so DEV data is never changed.
"""
import datetime as dt

import pyodbc
import pytest

from etl.config import ConfigError, db_target, load_env
from etl.db import connect

pytestmark = pytest.mark.db

STAGING = ["line", "program", "part", "defect_type", "production", "inspection", "nonconformity", "scrap",
           "rework", "eight_d", "eight_d_nc_link", "eight_d_action", "audit_finding"]
SRC = "test_file.xlsx"


@pytest.fixture(scope="module")
def conn():
    load_env()
    try:
        c = connect(db_target("dev"))
    except (ConfigError, pyodbc.Error) as exc:
        pytest.skip(f"DEV database not reachable: {exc}")
    if c.cursor().execute("SELECT OBJECT_ID(N'etl.usp_merge_scrap')").fetchval() is None:
        pytest.skip("Load procedures not deployed: run python -m etl.deploy --env dev")
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def cur(conn):
    c = conn.cursor()
    for t in STAGING:
        c.execute(f"DELETE FROM stg.{t}")
    yield c
    conn.rollback()


@pytest.fixture
def run(cur):
    return cur.execute("SET NOCOUNT ON; INSERT INTO [audit].etl_run (environment, status, triggered_by) "
                       "OUTPUT inserted.etl_run_id VALUES ('dev', 'Running', 'manual')").fetchval()


# --- helpers -----------------------------------------------------------------------------
def stage(cur, table, rows):
    """Insert dict rows into stg.<table>; adds source_file and source_row_number."""
    cur.execute(f"DELETE FROM stg.{table}")
    for i, row in enumerate(rows, start=2):
        row = {**row, "source_file": SRC, "source_row_number": i}
        cols = ", ".join(row)
        cur.execute(f"INSERT INTO stg.{table} ({cols}) VALUES ({', '.join('?' * len(row))})", *row.values())


def call(cur, proc, run_id, outputs=("rows_inserted", "rows_updated")):
    """EXEC a load procedure and return its OUTPUT parameters as a dict."""
    declare = ", ".join(f"@{o} INT" for o in outputs)
    args = ", ".join(f"@{o} = @{o} OUTPUT" for o in outputs)
    row = cur.execute(f"SET NOCOUNT ON; DECLARE {declare}; EXEC etl.{proc} @etl_run_id = ?, {args}; "
                      f"SELECT {', '.join('@' + o for o in outputs)};", run_id).fetchone()
    return dict(zip(outputs, row))


def scalar(cur, sql, *params):
    return cur.execute(sql, *params).fetchval()


def load_masters(cur, run):
    stage(cur, "line", [
        {"line_code": "T01", "line_name": "Test Assembly", "process_type": "Assembly", "area": "Hall T", "is_active": 1},
        {"line_code": "T02", "line_name": "Test Moulding", "process_type": "Injection Moulding", "area": "Hall T", "is_active": 1}])
    stage(cur, "program", [{"program_code": "VP-TEST", "program_name": "Test program", "oem_customer": "Fictional OEM",
                            "vehicle_segment": "C-SUV", "sop_date": dt.date(2024, 3, 1), "eop_date": None}])
    stage(cur, "part", [
        {"part_number": "DAC-90001", "part_name": "Test bracket", "component_type": "Bracket", "program_code": "VP-TEST",
         "primary_line_code": "T01", "standard_unit_cost_eur": 42.5, "opportunities_per_unit": 12, "is_active": 1},
        {"part_number": "DAC-90002", "part_name": "Orphan trim", "component_type": "Trim", "program_code": "VP-NOPE",
         "primary_line_code": "T99", "standard_unit_cost_eur": 10, "opportunities_per_unit": 4, "is_active": 1}])
    stage(cur, "defect_type", [{"defect_code": "TST-01", "defect_name": "Test scratch",
                                "defect_category": "Surface", "severity": "C"}])
    return {p: call(cur, f"usp_merge_{p}", run) for p in ("line", "program", "part", "defect_type")}


def key(cur, table, key_col, code_col, code):
    return scalar(cur, f"SELECT {key_col} FROM dw.{table} WHERE {code_col} = ?", code)


# --- guards ---------------------------------------------------------------------------------
def test_run_must_exist_and_be_running(cur, run):
    with pytest.raises(pyodbc.Error, match="not Running"):
        call(cur, "usp_merge_line", 999999)
    cur.execute("UPDATE [audit].etl_run SET status = 'Succeeded', ended_at_utc = SYSUTCDATETIME() WHERE etl_run_id = ?", run)
    with pytest.raises(pyodbc.Error, match="not Running"):
        call(cur, "usp_merge_line", run)


def test_duplicate_business_key_in_staging_fails_loudly(cur, run):
    row = {"line_code": "T01", "line_name": "A", "process_type": "Assembly", "area": "Hall T", "is_active": 1}
    stage(cur, "line", [row, {**row, "line_name": "B"}])
    with pytest.raises(pyodbc.Error, match="VR-GEN-05"):
        call(cur, "usp_merge_line", run)


# --- master data ---------------------------------------------------------------------------
def test_masters_insert_then_rerun_is_a_no_op_then_change_updates_one(cur, run):
    first = load_masters(cur, run)
    assert first["line"] == {"rows_inserted": 2, "rows_updated": 0}
    assert first["part"] == {"rows_inserted": 2, "rows_updated": 0}
    again = {p: call(cur, f"usp_merge_{p}", run) for p in ("line", "program", "part", "defect_type")}
    assert all(v == {"rows_inserted": 0, "rows_updated": 0} for v in again.values()), again

    cur.execute("UPDATE stg.part SET standard_unit_cost_eur = 45 WHERE part_number = 'DAC-90001'")
    assert call(cur, "usp_merge_part", run) == {"rows_inserted": 0, "rows_updated": 1}
    assert scalar(cur, "SELECT updated_at_utc FROM dw.dim_part WHERE part_number = 'DAC-90001'") is not None


def test_unknown_codes_in_masters_map_to_minus_one(cur, run):
    load_masters(cur, run)
    row = cur.execute("SELECT program_key, primary_line_key FROM dw.dim_part WHERE part_number = 'DAC-90002'").fetchone()
    assert tuple(row) == (-1, -1)


# --- event facts ----------------------------------------------------------------------------
def test_production_resolves_keys_and_upserts(cur, run):
    load_masters(cur, run)
    rows = [{"event_date": dt.date(2025, 3, 12), "shift_code": "N", "line_code": "T01", "part_number": "DAC-90001",
             "qty_planned": 500, "qty_started": 480},
            {"event_date": dt.date(2025, 3, 12), "shift_code": "E", "line_code": "T01", "part_number": "DAC-UNKNOWN",
             "qty_planned": 100, "qty_started": 90}]
    stage(cur, "production", rows)
    assert call(cur, "usp_merge_production", run) == {"rows_inserted": 2, "rows_updated": 0}
    assert scalar(cur, "SELECT COUNT(*) FROM dw.fact_production WHERE date_key = 20250312 AND part_key = -1") == 1

    rows[0]["qty_started"] = 485            # corrected file
    stage(cur, "production", rows)
    assert call(cur, "usp_merge_production", run) == {"rows_inserted": 0, "rows_updated": 1}


def test_two_unknown_parts_on_same_grain_are_refused(cur, run):
    load_masters(cur, run)
    base = {"event_date": dt.date(2025, 3, 12), "shift_code": "E", "line_code": "T01", "qty_planned": 1, "qty_started": 1}
    stage(cur, "production", [{**base, "part_number": "DAC-X1"}, {**base, "part_number": "DAC-X2"}])
    with pytest.raises(pyodbc.Error, match="after key resolution"):
        call(cur, "usp_merge_production", run)


def test_inspection_loads_per_stage(cur, run):
    load_masters(cur, run)
    base = {"event_date": dt.date(2025, 3, 12), "shift_code": "E", "line_code": "T01", "part_number": "DAC-90001"}
    stage(cur, "inspection", [{**base, "stage_code": "IPC", "qty_inspected": 100, "qty_passed_first_time": 98, "qty_failed": 2},
                              {**base, "stage_code": "EOL", "qty_inspected": 98, "qty_passed_first_time": 97, "qty_failed": 1}])
    assert call(cur, "usp_merge_inspection", run) == {"rows_inserted": 2, "rows_updated": 0}


def test_scrap_keeps_cost_snapshot_when_master_cost_changes(cur, run):
    """D7: historical scrap cost never changes because of a later standard-cost change."""
    load_masters(cur, run)
    row = {"scrap_id": "SC-2025-900001", "event_date": dt.date(2025, 3, 12), "shift_code": "L", "line_code": "T01",
           "part_number": "DAC-90001", "defect_code": "TST-01", "qty_scrapped": 180}
    stage(cur, "scrap", [row])
    call(cur, "usp_merge_scrap", run)
    assert scalar(cur, "SELECT scrap_cost_eur FROM dw.fact_scrap WHERE scrap_id = 'SC-2025-900001'") == 7650

    cur.execute("UPDATE dw.dim_part SET standard_unit_cost_eur = 50 WHERE part_number = 'DAC-90001'")
    stage(cur, "scrap", [{**row, "qty_scrapped": 200}])                 # corrected quantity
    assert call(cur, "usp_merge_scrap", run) == {"rows_inserted": 0, "rows_updated": 1}
    unit, cost = cur.execute("SELECT unit_cost_eur, scrap_cost_eur FROM dw.fact_scrap "
                             "WHERE scrap_id = 'SC-2025-900001'").fetchone()
    assert (unit, cost) == (42.5, 8500)                                 # old snapshot, new quantity


def test_rework_keeps_cost_source(cur, run):
    load_masters(cur, run)
    stage(cur, "rework", [{"rework_id": "RW-2025-900001", "event_date": dt.date(2025, 3, 12), "shift_code": "E",
                           "line_code": "T01", "part_number": "DAC-90001", "defect_code": "TST-01",
                           "qty_reworked": 10, "qty_rework_ok": 9, "labour_hours": 2.5, "labour_rate_eur": 38,
                           "rework_cost_eur": 95, "cost_source": "Imputed"}])
    call(cur, "usp_merge_rework", run)
    assert scalar(cur, "SELECT cost_source FROM dw.fact_rework WHERE rework_id = 'RW-2025-900001'") == "Imputed"


# --- 8D, NC links, actions, audit findings ------------------------------------------------------
def eight_d_row(eight_d_id, d8=None):
    return {"eight_d_id": eight_d_id, "opened_date": dt.date(2025, 3, 1), "part_number": "DAC-90001",
            "line_code": "T01", "defect_code": "TST-01", "problem_source": "Internal", "owner_name": "A. Test",
            "owner_department": "Quality", "d1_date": dt.date(2025, 3, 1), "d8_date": d8,
            "target_closure_date": dt.date(2025, 4, 30), "status_reported": "Closed" if d8 else "Open"}


def nc_row(nc_id):
    return {"nc_id": nc_id, "event_date": dt.date(2025, 3, 2), "shift_code": "E", "line_code": "T01",
            "part_number": "DAC-90001", "stage_code": "EOL", "defect_code": "TST-01", "qty_affected": 3,
            "disposition": "Rework"}


def test_8d_closed_date_comes_from_d8(cur, run):
    load_masters(cur, run)
    stage(cur, "eight_d", [eight_d_row("8D-2025-901", d8=dt.date(2025, 4, 20)), eight_d_row("8D-2025-902")])
    assert call(cur, "usp_merge_8d", run) == {"rows_inserted": 2, "rows_updated": 0}
    closed = dict(cur.execute("SELECT eight_d_id, closed_date_key FROM dw.fact_8d "
                              "WHERE eight_d_id IN ('8D-2025-901', '8D-2025-902')").fetchall())
    assert closed == {"8D-2025-901": 20250420, "8D-2025-902": None}


def test_nc_links_follow_the_tracker_snapshot(cur, run):
    load_masters(cur, run)
    links = ("usp_apply_8d_nc_links", ("rows_linked", "rows_unlinked", "links_unresolved"))
    stage(cur, "eight_d", [eight_d_row("8D-2025-901")])
    call(cur, "usp_merge_8d", run)
    stage(cur, "nonconformity", [nc_row("NC-2025-900001"), nc_row("NC-2025-900002")])
    call(cur, "usp_merge_nonconformity", run)

    # Tracker lists NC 1 and an NC that has not arrived yet
    stage(cur, "eight_d_nc_link", [{"eight_d_id": "8D-2025-901", "nc_id": "NC-2025-900001"},
                                   {"eight_d_id": "8D-2025-901", "nc_id": "NC-2025-999999"}])
    assert call(cur, links[0], run, links[1]) == {"rows_linked": 1, "rows_unlinked": 0, "links_unresolved": 1}

    # A corrected NC file must not wipe the link
    stage(cur, "nonconformity", [{**nc_row("NC-2025-900001"), "qty_affected": 4}])
    call(cur, "usp_merge_nonconformity", run)
    assert scalar(cur, "SELECT eight_d_key FROM dw.fact_nonconformity WHERE nc_id = 'NC-2025-900001'") is not None

    # Next tracker snapshot lists NC 2 instead of NC 1
    stage(cur, "eight_d_nc_link", [{"eight_d_id": "8D-2025-901", "nc_id": "NC-2025-900002"}])
    assert call(cur, links[0], run, links[1]) == {"rows_linked": 1, "rows_unlinked": 1, "links_unresolved": 0}
    linked = dict(cur.execute("SELECT nc_id, eight_d_key FROM dw.fact_nonconformity "
                              "WHERE nc_id IN ('NC-2025-900001', 'NC-2025-900002')").fetchall())
    assert linked["NC-2025-900001"] is None and linked["NC-2025-900002"] is not None


def test_nc_listed_under_two_8ds_is_refused(cur, run):
    stage(cur, "eight_d_nc_link", [{"eight_d_id": "8D-2025-901", "nc_id": "NC-2025-900001"},
                                   {"eight_d_id": "8D-2025-902", "nc_id": "NC-2025-900001"}])
    with pytest.raises(pyodbc.Error, match="more than one 8D"):
        call(cur, "usp_apply_8d_nc_links", run, ("rows_linked", "rows_unlinked", "links_unresolved"))


def test_action_for_unknown_8d_fails_loudly(cur, run):
    stage(cur, "eight_d_action", [{"action_id": "8D-2025-999-A01", "eight_d_id": "8D-2025-999",
                                   "action_type": "Containment", "action_description": "Sort stock",
                                   "owner_name": "A. Test", "due_date": dt.date(2025, 3, 5)}])
    with pytest.raises(pyodbc.Error, match="VR-8D-03"):
        call(cur, "usp_merge_8d_action", run)


def test_action_loads_with_completion(cur, run):
    load_masters(cur, run)
    stage(cur, "eight_d", [eight_d_row("8D-2025-901")])
    call(cur, "usp_merge_8d", run)
    stage(cur, "eight_d_action", [{"action_id": "8D-2025-901-A01", "eight_d_id": "8D-2025-901",
                                   "action_type": "Corrective", "action_description": "Adjust mould temperature",
                                   "owner_name": "A. Test", "due_date": dt.date(2025, 3, 20),
                                   "completed_date": dt.date(2025, 3, 18), "status_reported": "Done"}])
    assert call(cur, "usp_merge_8d_action", run) == {"rows_inserted": 1, "rows_updated": 0}
    assert scalar(cur, "SELECT completed_date_key FROM dw.fact_8d_action WHERE action_id = '8D-2025-901-A01'") == 20250318


def test_audit_finding_line_and_8d_resolution(cur, run):
    load_masters(cur, run)
    stage(cur, "eight_d", [eight_d_row("8D-2025-901")])
    call(cur, "usp_merge_8d", run)
    base = {"audit_id": "AU-2025-901", "audit_date": dt.date(2025, 3, 10), "audit_type": "Process",
            "standard_reference": "IATF 16949 8.5.1", "classification": "Minor NC",
            "finding_description": "Test finding", "owner_name": "A. Test", "due_date": dt.date(2025, 4, 10)}
    stage(cur, "audit_finding", [
        {**base, "finding_id": "AF-2025-9001", "line_code": None, "eight_d_id": None},           # plant-wide
        {**base, "finding_id": "AF-2025-9002", "line_code": "T01", "eight_d_id": "8D-2025-901"},
        {**base, "finding_id": "AF-2025-9003", "line_code": "T77", "eight_d_id": "8D-2025-777"}])  # unknown both
    out = call(cur, "usp_merge_audit_finding", run, ("rows_inserted", "rows_updated", "links_unresolved"))
    assert out == {"rows_inserted": 3, "rows_updated": 0, "links_unresolved": 1}
    rows = {r.finding_id: (r.line_key, r.eight_d_key) for r in cur.execute(
        "SELECT finding_id, line_key, eight_d_key FROM dw.fact_audit_finding WHERE finding_id LIKE 'AF-2025-900_'")}
    t01 = key(cur, "dim_production_line", "line_key", "line_code", "T01")
    assert rows["AF-2025-9001"] == (-2, None)
    assert rows["AF-2025-9002"][0] == t01 and rows["AF-2025-9002"][1] is not None
    assert rows["AF-2025-9003"] == (-1, None)
