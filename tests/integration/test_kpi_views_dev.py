"""Phase 8 acceptance: rpt.v_kpi_monthly reproduces the hand-calculated examples
of blueprint §6.2. Data is loaded through the real etl.usp_merge_* procedures and
rolled back after each test.

Scenario, March 2025:
  T01 Assembly (IPC -> EOL -> FA), part DAC-90001: EUR 42.50, 12 defect opportunities
    started 12 400 | IPC 4 900/5 000, EOL 4 850/5 000, FA 4 950/5 000
    150 defects (NC) | scrap 180 | rework 230, 207 OK, EUR 3 000 reported + EUR 2 420 imputed
  T02 Moulding (EOL only), part DAC-90003: EUR 10.00, 4 opportunities
    started 1 000 | EOL 900/1 000
  8D A: opened 1 Feb, target 15 Mar, D8 20 Mar (47 days, late), defect TST-01
  8D B: opened 5 Mar, target 25 Mar, open        -> overdue at 31 Mar, defect TST-02
  8D C: opened 25 Mar, target 31 May, open, TST-01 -> repeat of A (closed 5 days before)
  Actions: A-01 due 12 Mar, done 10 Mar | B-01 due 20 Mar, open
  Findings: F1 Major NC plant-wide 3 Mar, due 31 Mar, closed 28 Mar (25 days)
            F2 Minor NC on T01 10 Mar, due 20 Mar, open -> overdue at 31 Mar
"""
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from pytest import approx

from dbhelpers import call, scalar, stage

pytestmark = pytest.mark.db
D = dt.date


@pytest.fixture
def kpi(dbcur, run_id):
    cur, run = dbcur, run_id
    stage(cur, "line", [
        {"line_code": "T01", "line_name": "Test Assembly", "process_type": "Assembly", "area": "Hall T", "is_active": 1},
        {"line_code": "T02", "line_name": "Test Moulding", "process_type": "Injection Moulding", "area": "Hall T", "is_active": 1}])
    stage(cur, "program", [{"program_code": "VP-TEST", "program_name": "Test", "oem_customer": "Fictional OEM",
                            "vehicle_segment": "C-SUV", "sop_date": D(2024, 3, 1), "eop_date": None}])
    stage(cur, "part", [
        {"part_number": "DAC-90001", "part_name": "Test bracket", "component_type": "Bracket", "program_code": "VP-TEST",
         "primary_line_code": "T01", "standard_unit_cost_eur": 42.5, "opportunities_per_unit": 12, "is_active": 1},
        {"part_number": "DAC-90003", "part_name": "Test cap", "component_type": "Trim", "program_code": "VP-TEST",
         "primary_line_code": "T02", "standard_unit_cost_eur": 10, "opportunities_per_unit": 4, "is_active": 1}])
    stage(cur, "defect_type", [
        {"defect_code": "TST-01", "defect_name": "Scratch", "defect_category": "Surface", "severity": "C"},
        {"defect_code": "TST-02", "defect_name": "Gap", "defect_category": "Dimensional", "severity": "B"}])
    for p in ("line", "program", "part", "defect_type"):
        call(cur, f"usp_merge_{p}", run)

    day = {"event_date": D(2025, 3, 12), "shift_code": "E"}
    t01 = {**day, "line_code": "T01", "part_number": "DAC-90001"}
    t02 = {**day, "line_code": "T02", "part_number": "DAC-90003"}
    stage(cur, "production", [{**t01, "qty_planned": 12500, "qty_started": 12400},
                              {**t02, "qty_planned": 1000, "qty_started": 1000}])
    stage(cur, "inspection", [
        {**t01, "stage_code": "IPC", "qty_inspected": 5000, "qty_passed_first_time": 4900, "qty_failed": 100},
        {**t01, "stage_code": "EOL", "qty_inspected": 5000, "qty_passed_first_time": 4850, "qty_failed": 150},
        {**t01, "stage_code": "FA",  "qty_inspected": 5000, "qty_passed_first_time": 4950, "qty_failed": 50},
        {**t02, "stage_code": "EOL", "qty_inspected": 1000, "qty_passed_first_time": 900,  "qty_failed": 100}])
    stage(cur, "nonconformity", [
        {**t01, "nc_id": "NC-2025-900001", "stage_code": "FA", "defect_code": "TST-01", "qty_affected": 100, "disposition": "Rework"},
        {**t01, "nc_id": "NC-2025-900002", "stage_code": "EOL", "defect_code": "TST-02", "qty_affected": 50, "disposition": "Scrap"}])
    stage(cur, "scrap", [{**t01, "scrap_id": "SC-2025-900001", "defect_code": "TST-02", "qty_scrapped": 180}])
    rw = {**t01, "defect_code": "TST-01", "labour_rate_eur": 38}
    stage(cur, "rework", [
        {**rw, "rework_id": "RW-2025-900001", "qty_reworked": 130, "qty_rework_ok": 117, "labour_hours": 70,
         "rework_cost_eur": 3000, "cost_source": "Reported"},
        {**rw, "rework_id": "RW-2025-900002", "qty_reworked": 100, "qty_rework_ok": 90, "labour_hours": 63.68,
         "rework_cost_eur": 2420, "cost_source": "Imputed"}])
    for p in ("production", "inspection", "nonconformity", "scrap", "rework"):
        call(cur, f"usp_merge_{p}", run)

    ed = {"part_number": "DAC-90001", "line_code": "T01", "problem_source": "Internal", "owner_name": "A. Test",
          "owner_department": "Quality", "status_reported": None}
    stage(cur, "eight_d", [
        {**ed, "eight_d_id": "8D-2025-901", "defect_code": "TST-01", "opened_date": D(2025, 2, 1), "d1_date": D(2025, 2, 1),
         "d8_date": D(2025, 3, 20), "target_closure_date": D(2025, 3, 15)},
        {**ed, "eight_d_id": "8D-2025-902", "defect_code": "TST-02", "opened_date": D(2025, 3, 5), "d1_date": D(2025, 3, 5),
         "d8_date": None, "target_closure_date": D(2025, 3, 25)},
        {**ed, "eight_d_id": "8D-2025-903", "defect_code": "TST-01", "opened_date": D(2025, 3, 25), "d1_date": D(2025, 3, 25),
         "d8_date": None, "target_closure_date": D(2025, 5, 31)}])
    call(cur, "usp_merge_8d", run)
    act = {"action_type": "Corrective", "action_description": "Fix", "owner_name": "A. Test", "status_reported": None}
    stage(cur, "eight_d_action", [
        {**act, "action_id": "8D-2025-901-A01", "eight_d_id": "8D-2025-901", "due_date": D(2025, 3, 12), "completed_date": D(2025, 3, 10)},
        {**act, "action_id": "8D-2025-902-A01", "eight_d_id": "8D-2025-902", "due_date": D(2025, 3, 20), "completed_date": None}])
    call(cur, "usp_merge_8d_action", run)
    af = {"audit_id": "AU-2025-901", "audit_type": "Process", "standard_reference": "IATF 16949 8.5.1",
          "finding_description": "Test", "owner_name": "A. Test", "eight_d_id": None, "status_reported": None}
    stage(cur, "audit_finding", [
        {**af, "finding_id": "AF-2025-9001", "audit_date": D(2025, 3, 3), "line_code": None, "classification": "Major NC",
         "due_date": D(2025, 3, 31), "closed_date": D(2025, 3, 28)},
        {**af, "finding_id": "AF-2025-9002", "audit_date": D(2025, 3, 10), "line_code": "T01", "classification": "Minor NC",
         "due_date": D(2025, 3, 20), "closed_date": None}])
    call(cur, "usp_merge_audit_finding", run, ("rows_inserted", "rows_updated", "links_unresolved"))

    def row(line_code, year_month="2025-03"):
        r = cur.execute("SELECT * FROM rpt.v_kpi_monthly WHERE year_month = ? AND line_code = ?",
                        year_month, line_code).fetchone()
        assert r is not None, f"no row for {year_month} / {line_code}"
        return {col[0]: (float(v) if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) else v)
                for col, v in zip(r.cursor_description, r)}
    return row


# --- production and quality (blueprint §6.2 examples) --------------------------------------
def test_line_t01_quality_kpis_match_hand_calculation(kpi):
    r = kpi("T01")
    assert r["production_volume"] == 12400
    assert r["passed_quantity"] == 4950                       # final stage (FA)
    assert r["rejected_quantity"] == 300                      # all stages
    assert r["fpy_final_stage_pct"] == approx(0.99)
    assert r["fty_pct"] == approx(0.98 * 0.97 * 0.99, abs=1e-6)   # 94.1 %
    assert r["defect_rate_pct"] == approx(150 / 5000)             # 3.0 %
    assert r["dpmo"] == approx(2500)                              # 150 / (5 000 x 12) x 10^6
    assert r["scrap_rate_pct"] == approx(180 / 12400, abs=1e-6)   # 1.45 %
    assert r["scrap_cost_eur"] == approx(7650)                    # 180 x 42.50
    assert r["rework_rate_pct"] == approx(230 / 12400, abs=1e-6)  # 1.85 %
    assert r["rework_success_pct"] == approx(0.90)                # 207 / 230
    assert r["rework_cost_eur"] == approx(5420)
    assert r["rework_cost_imputed_eur"] == approx(2420)           # D12: visible, not hidden
    assert r["copq_eur"] == approx(13070)                         # 7 650 + 5 420
    assert r["copq_pct"] == approx(13070 / 527000, abs=1e-6)      # production value 12 400 x 42.50


def test_plant_row_sums_lines_and_weights_fty_by_volume(kpi):
    p = kpi("ALL")
    assert p["row_level"] == "Plant" and p["line_key"] is None
    assert p["production_volume"] == 13400
    assert p["fty_pct"] == approx((0.98 * 0.97 * 0.99 * 12400 + 0.90 * 1000) / 13400, abs=1e-6)
    assert p["defect_rate_pct"] == approx(150 / 6000)             # ratio of sums, not average of rates
    assert p["dpmo"] == approx(150 / (5000 * 12 + 1000 * 4) * 1e6)
    assert p["copq_pct"] == approx(13070 / 537000, abs=1e-6)


def test_fpy_is_reported_per_stage(dbcur, kpi):
    rows = {r.stage_code: (float(r.fpy_pct), r.is_final_stage) for r in dbcur.execute(
        "SELECT stage_code, fpy_pct, is_final_stage FROM rpt.v_kpi_monthly_stage "
        "WHERE year_month = '2025-03' AND line_code = 'T01'")}
    assert rows == {"IPC": (0.98, 0), "EOL": (0.97, 0), "FA": (0.99, 1)}


# --- 8D and corrective actions ----------------------------------------------------------------
def test_8d_kpis_for_march(kpi):
    r = kpi("T01")
    assert (r["eight_d_opened"], r["eight_d_repeat"]) == (2, 1)
    assert r["repeat_defect_pct"] == approx(0.5)                  # C repeats A (same part + defect, < 180 days)
    assert (r["eight_d_closed"], r["avg_8d_closure_days"]) == (1, 47)
    assert r["eight_d_ontime_pct"] == 0                           # A closed 20 Mar, target 15 Mar
    assert (r["open_8d_count"], r["overdue_8d_count"]) == (2, 1)  # as of 31 Mar: B and C open, B overdue
    assert (r["actions_due"], r["actions_completed"], r["ca_completion_pct"]) == (2, 1, 0.5)


def test_8d_snapshot_is_semi_additive_over_months(kpi):
    feb = kpi("T01", "2025-02")
    assert (feb["open_8d_count"], feb["overdue_8d_count"]) == (1, 0)   # only A, not yet due


# --- audits ---------------------------------------------------------------------------------------
def test_audit_kpis_plant_wide_finding_counts_in_plant_row(kpi):
    p = kpi("ALL")
    assert (p["findings_raised"], p["major_nc_count"]) == (2, 1)
    assert (p["findings_closed"], p["avg_finding_closure_days"], p["audit_ontime_pct"]) == (1, 25, 1.0)
    assert (p["open_findings_count"], p["overdue_findings_count"]) == (1, 1)
    assert kpi("N/A")["major_nc_count"] == 1                      # plant-wide finding: line -2


# --- reporting views -------------------------------------------------------------------------
def test_fact_8d_view_derivations(dbcur, kpi):
    rows = {r.eight_d_id: r for r in dbcur.execute(
        "SELECT eight_d_id, is_closed, days_to_close, is_closed_on_time, is_repeat, status_derived "
        "FROM rpt.v_fact_8d WHERE eight_d_id LIKE '8D-2025-90_'")}
    a, b, c = rows["8D-2025-901"], rows["8D-2025-902"], rows["8D-2025-903"]
    assert (a.is_closed, a.days_to_close, a.is_closed_on_time, a.status_derived) == (True, 47, False, "Closed")
    assert (b.is_closed, b.days_to_close, b.is_closed_on_time, b.is_repeat) == (False, None, None, False)
    assert c.is_repeat is True and a.is_repeat is False


def test_dim_part_is_flattened_with_program(dbcur, kpi):
    r = dbcur.execute("SELECT program_code, oem_customer, primary_line_code, lifecycle_phase "
                      "FROM rpt.v_dim_part WHERE part_number = 'DAC-90001'").fetchone()
    assert tuple(r) == ("VP-TEST", "Fictional OEM", "T01", "Serial")


def test_rpt_views_do_not_expose_lineage_columns(dbcur):
    leaked = dbcur.execute(
        "SELECT OBJECT_NAME(c.object_id) + '.' + c.name FROM sys.columns c JOIN sys.views v ON v.object_id = c.object_id "
        "WHERE SCHEMA_NAME(v.schema_id) = 'rpt' "
        "AND c.name IN ('etl_run_id', 'source_file', 'loaded_at_utc', 'updated_at_utc')").fetchall()
    assert leaked == []


# --- targets and RAG (blueprint §6.3) -----------------------------------------------------------
def test_kpi_targets_are_seeded_and_illustrative(dbcur):
    n, illustrative = dbcur.execute("SELECT COUNT(*), SUM(CASE WHEN target_type = 'Illustrative' THEN 1 ELSE 0 END) "
                                    "FROM rpt.v_kpi_target WHERE is_current = 1").fetchone()
    assert n == illustrative == 18


@pytest.mark.parametrize("kpi_code, value, expected", [
    ("FPY_PCT", 0.975, "Green"), ("FPY_PCT", 0.90, "Amber"), ("FPY_PCT", 0.80, "Red"),
    ("SCRAP_RATE_PCT", 0.0145, "Green"), ("SCRAP_RATE_PCT", 0.016, "Amber"), ("SCRAP_RATE_PCT", 0.02, "Red"),
    ("OVERDUE_8D_COUNT", 0, "Green"), ("OVERDUE_8D_COUNT", 1, "Amber"), ("OVERDUE_8D_COUNT", 3, "Red"),
    ("MAJOR_NC_COUNT", 1, "Red"),
])
def test_rag_rule(dbcur, kpi_code, value, expected):
    assert scalar(dbcur, "SELECT rpt.fn_kpi_rag(?, target_value, amber_limit, direction) FROM rpt.v_kpi_target "
                         "WHERE kpi_code = ? AND is_current = 1", value, kpi_code) == expected


def test_repeat_window_in_view_matches_settings():
    """The 180-day window is in rpt.v_fact_8d and in settings.toml; keep them equal."""
    from etl.config import load_settings
    days = load_settings()["kpi"]["repeat_defect_window_days"]
    sql = (Path(__file__).resolve().parents[2] / "sql" / "rpt" / "v_facts.sql").read_text(encoding="utf-8")
    assert f"DATEADD(DAY, -{days}, od.[date])" in sql
