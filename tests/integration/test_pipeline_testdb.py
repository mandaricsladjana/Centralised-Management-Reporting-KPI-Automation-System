"""Phase 6 acceptance: the pipeline catches every injected issue, loses no clean
row, standardises every variant, and a rerun loads nothing.

Runs against a separate database, QualityKPI_Test (created and deployed here),
never DEV. The committed sample (data/sample, March 2025) is loaded twice.
"""
import pandas as pd
import pytest

from dbhelpers import TEST_DB, prepare_test_db
from etl.config import REPO_ROOT, db_target
from etl.db import connect
from etl.pipeline import run_pipeline

pytestmark = pytest.mark.db

SAMPLE = REPO_ROOT / "data" / "sample"


@pytest.fixture(scope="module")
def loaded():
    prepare_test_db()
    first = run_pipeline("dev", SAMPLE, database=TEST_DB)
    second = run_pipeline("dev", SAMPLE, database=TEST_DB)
    conn = connect(db_target("dev", TEST_DB))
    yield conn, first, second
    conn.close()


@pytest.fixture(scope="module")
def manifest():
    return pd.read_csv(SAMPLE / "_generation" / "injected_issues.csv", dtype=str, keep_default_na=False)


def rows(conn, sql, *params):
    return conn.cursor().execute(sql, *params).fetchall()


# --- run bookkeeping ----------------------------------------------------------------------
def test_first_run_succeeds_and_counts_reconcile(loaded):
    _, first, _ = loaded
    assert first.status == "Succeeded" and first.files_loaded == 12 and first.files_failed == 0
    assert first.rows_read == first.rows_loaded + first.rows_rejected + first.duplicates_dropped


def test_rerun_skips_every_file_and_loads_nothing(loaded):
    """Blueprint Phase 6 acceptance: rerun loads 0 new rows (incremental by SHA-256, D20)."""
    conn, first, second = loaded
    assert second.files_skipped == 12 and second.files_loaded == 0 and second.rows_loaded == 0
    status = {r[0] for r in rows(conn, "SELECT status FROM [audit].file_manifest WHERE etl_run_id = ?", second.run_id)}
    assert status == {"Skipped-duplicate"}


def test_run_is_recorded_in_audit(loaded):
    conn, first, _ = loaded
    r = rows(conn, "SELECT status, rows_read, rows_loaded, rows_rejected, ended_at_utc FROM [audit].etl_run "
                   "WHERE etl_run_id = ?", first.run_id)[0]
    assert (r.status, r.rows_read, r.rows_loaded, r.rows_rejected) == (
        "Succeeded", first.rows_read, first.rows_loaded, first.rows_rejected)
    assert r.ended_at_utc is not None


# --- rejections: exactly the injected ones ------------------------------------------------------
def test_every_injected_reject_is_rejected_with_its_rule(loaded, manifest):
    conn, first, _ = loaded
    got = {(r.file_name, r.source_row_number, r.rule_id) for r in rows(conn, """
        SELECT m.file_name, r.source_row_number, r.rule_id FROM [audit].rejected_row r
        JOIN [audit].file_manifest m ON m.file_id = r.file_id WHERE r.etl_run_id = ?""", first.run_id)}
    expected = {(i.file, int(i.source_row_number), i.rule_id) for i in manifest.itertuples() if i.expected == "Reject"}
    missing = expected - got
    assert not missing, f"injected issues not caught: {sorted(missing)[:10]}"


def test_no_clean_row_is_rejected(loaded, manifest):
    conn, first, _ = loaded
    got = {(r.file_name, r.source_row_number) for r in rows(conn, """
        SELECT m.file_name, r.source_row_number FROM [audit].rejected_row r
        JOIN [audit].file_manifest m ON m.file_id = r.file_id WHERE r.etl_run_id = ?""", first.run_id)}
    injected = {(i.file, int(i.source_row_number)) for i in manifest.itertuples() if i.expected == "Reject"}
    unexpected = got - injected
    assert not unexpected, f"clean rows rejected: {sorted(unexpected)[:10]}"


def test_rejected_rows_keep_the_raw_record(loaded):
    conn, first, _ = loaded
    bad = rows(conn, "SELECT COUNT(*) FROM [audit].rejected_row WHERE etl_run_id = ? AND (raw_record IS NULL "
                     "OR ISJSON(raw_record) = 0)", first.run_id)[0][0]
    assert bad == 0


# --- warnings: loaded and counted ----------------------------------------------------------------------
def _warn_counts(conn, run_id, rule):
    out = {}
    for d, n in rows(conn, "SELECT details, measured_value FROM [audit].dq_result WHERE etl_run_id = ? "
                           "AND check_id = ?", run_id, rule):
        out[d.split(":")[0]] = int(n)
    return out


def test_exact_duplicates_dropped_and_counted_per_file(loaded, manifest):
    conn, first, _ = loaded
    expected = manifest[manifest.issue == "duplicate_row"].groupby("file").size().to_dict()
    assert _warn_counts(conn, first.run_id, "VR-GEN-04") == expected


def test_missing_rework_cost_is_imputed_and_flagged(loaded, manifest):
    conn, _, _ = loaded
    ids = list(manifest.loc[manifest.issue == "missing_cost", "business_key"])
    got = {r.rework_id: (r.cost_source, float(r.rework_cost_eur), float(r.labour_hours)) for r in rows(conn,
           f"SELECT rework_id, cost_source, rework_cost_eur, labour_hours FROM dw.fact_rework "
           f"WHERE rework_id IN ({','.join('?' * len(ids))})", *ids)}
    assert set(got) == set(ids)
    for source, cost, hours in got.values():
        assert source == "Imputed" and cost == pytest.approx(round(hours * 38.0, 2))


def test_unknown_codes_are_loaded_as_minus_one(loaded, manifest):
    conn, _, _ = loaded
    for issue, table, key_col, id_col in [("unknown_defect", "dw.fact_nonconformity", "defect_type_key", "nc_id"),
                                          ("unknown_defect", "dw.fact_scrap", "defect_type_key", "scrap_id")]:
        ids = list(manifest.loc[(manifest.issue == issue) & manifest.business_key.str.startswith(id_col[:2].upper()),
                                "business_key"])
        if not ids:
            continue
        keys = {r[0] for r in rows(conn, f"SELECT {key_col} FROM {table} WHERE {id_col} IN "
                                         f"({','.join('?' * len(ids))})", *ids)}
        assert keys == {-1}, (issue, table)
    n_unknown_part = (manifest.issue == "unknown_part").sum()
    assert rows(conn, "SELECT COUNT(*) FROM dw.fact_production WHERE part_key = -1")[0][0] == n_unknown_part
    n_unknown_stage = (manifest.issue == "unknown_stage").sum()
    assert rows(conn, "SELECT COUNT(*) FROM dw.fact_inspection WHERE stage_key = -1")[0][0] == n_unknown_stage


# --- standardisation ------------------------------------------------------------------------------------
def test_spelling_variants_are_standardised_not_rejected(loaded, manifest):
    conn, _, _ = loaded
    for prefix, table, id_col in [("SC-", "dw.fact_scrap", "scrap_id"), ("RW-", "dw.fact_rework", "rework_id")]:
        ids = list(manifest.loc[(manifest.expected == "Standardise") & manifest.business_key.str.startswith(prefix),
                                "business_key"])
        assert ids, prefix
        got = rows(conn, f"SELECT f.{id_col}, l.line_code, s.shift_code FROM {table} f "
                         f"JOIN dw.dim_production_line l ON l.line_key = f.line_key "
                         f"JOIN dw.dim_shift s ON s.shift_key = f.shift_key "
                         f"WHERE f.{id_col} IN ({','.join('?' * len(ids))})", *ids)
        assert len(got) == len(ids), f"variant rows missing in {table}"
        assert all(r.line_code in {f"L0{i}" for i in range(1, 7)} and r.shift_code in "ELN" for r in got)


# --- cross-source checks and KPI layer ------------------------------------------------------------------
def test_cross_source_dq_checks_are_recorded(loaded):
    conn, first, _ = loaded
    checks = {r[0] for r in rows(conn, "SELECT check_id FROM [audit].dq_result WHERE etl_run_id = ?", first.run_id)}
    assert {"DQ-01", "DQ-02", "DQ-07", "DQ-08", "DQ-10"} <= checks
    passed = dict(rows(conn, "SELECT check_id, passed FROM [audit].dq_result WHERE etl_run_id = ? "
                             "AND check_id LIKE 'DQ-%'", first.run_id))
    assert passed["DQ-10"] is True                      # ~3 % injected, limit 5 %
    # The injected defects remove production rows (rejected) while their inspections load,
    # so the cross-source checks are EXPECTED to flag this dirty sample: proof they work.
    assert passed["DQ-01"] is False


def test_kpi_view_reflects_the_loaded_month(loaded):
    conn, _, _ = loaded
    r = rows(conn, "SELECT production_volume, defect_rate_pct, fty_pct FROM rpt.v_kpi_monthly "
                   "WHERE year_month = '2025-03' AND line_code = 'ALL'")[0]
    started = rows(conn, "SELECT SUM(CAST(qty_started AS BIGINT)) FROM dw.fact_production")[0][0]
    assert r.production_volume == started
    assert 0.01 < float(r.defect_rate_pct) < 0.06 and 0.90 < float(r.fty_pct) < 1.0
