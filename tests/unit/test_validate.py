"""Unit tests for value parsing (etl.transform) and the rule engine (etl.validate). No database."""
import datetime as dt
from pathlib import Path

import pytest

from etl import transform as tf
from etl.extract import RawFile
from etl.sources import sources
from etl.validate import Context, validate

D = dt.date


# --- transform ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    (D(2025, 3, 14), D(2025, 3, 14)), (dt.datetime(2025, 3, 14, 7, 30), D(2025, 3, 14)),
    ("2025-03-14", D(2025, 3, 14)), ("14.03.2025", D(2025, 3, 14)), ("14/03/2025", D(2025, 3, 14)),
    (45730, D(2025, 3, 14)), ("45730", D(2025, 3, 14)), (None, None), ("  ", None),
])
def test_parse_date_accepted_formats(raw, expected):
    assert tf.parse_date(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["31.02.2025", "2025-13-05", "next week", "03/14/2025"])
def test_parse_date_rejects_impossible_or_unknown(raw):
    value, err = tf.parse_date(raw)
    assert value is None and err


@pytest.mark.parametrize("raw, expected", [("42", 42), (42.0, 42), ("-3", -3), (7, 7)])
def test_parse_int(raw, expected):
    assert tf.parse_int(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["4.5", "abc", True])
def test_parse_int_rejects(raw):
    assert tf.parse_int(raw)[1] is not None


@pytest.mark.parametrize("target, raw, expected", [
    ("line_code", "Line 1", "L01"), ("line_code", "line-04", "L04"), ("line_code", "ASSY-2", "L06"),
    ("shift_code", "Night", "N"), ("shift_code", "1", "E"),
    ("classification", "MAJ", "Major NC"), ("classification", "obs", "Observation"),
    ("disposition", "UAI", "Use-as-is"),
])
def test_spelling_variants_standardise(target, raw, expected):
    assert tf.standardise(target, raw) == (expected, True)


def test_unmappable_variant_is_reported():
    assert tf.standardise("classification", "Critical?") == ("Critical?", False)
    assert tf.standardise("part_number", "dac-1") == ("dac-1", True)       # no spelling list: unchanged


def test_nc_link_split_and_imputation():
    assert tf.split_nc_links("NC-1, NC-2;NC-3  NC-1") == ["NC-1", "NC-2", "NC-3"]
    assert tf.split_nc_links(None) == []
    assert tf.impute_rework_cost(2.5, 38.0) == 95.0


# --- validate ------------------------------------------------------------------------------------
@pytest.fixture
def ctx():
    return Context(known={"line": {"L01"}, "shift": {"E", "L", "N"}, "part": {"DAC-10421"},
                          "stage": {"IPC", "EOL", "FA"}, "defect": {"SUR-01"}, "program": {"VP-SUV-C"},
                          "eight_d": {"8D-2025-001"}},
                   labour_rate_eur=38.0, earliest_event_date=D(2024, 1, 1), today=D(2026, 11, 11))


def raw_file(source_name, rows, headers=None):
    src = sources()[source_name]
    headers = headers or src.headers
    return RawFile(Path(f"{source_name}.csv"), src, headers,
                   [(n, dict(zip(headers, r))) for n, r in enumerate(rows, start=2)], "0" * 64, 0)


INSP = ["2025-03-12", "E", "Line 1", "DAC-10421", "EOL", 100, 97, 3]


def test_clean_row_is_loaded_with_standard_codes(ctx):
    res = validate(raw_file("qa_inspections", [INSP]), ctx)
    assert not res.rejections and len(res.rows) == 1
    row = res.rows[0]
    assert (row["line_code"], row["event_date"], row["source_row_number"]) == ("L01", D(2025, 3, 12), 2)


@pytest.mark.parametrize("change, rule", [
    ({1: None}, "VR-GEN-02"),                          # shift empty
    ({0: "31.02.2025"}, "VR-GEN-03"),
    ({0: "2030-01-15"}, "VR-GEN-07"), ({0: "2023-12-31"}, "VR-GEN-07"),
    ({5: "a lot"}, "VR-GEN-08"),
    ({7: -3, 6: 103}, "VR-QTY-01"),
    ({6: 98}, "VR-INS-01"),
])
def test_each_rule_rejects_its_row(ctx, change, rule):
    row = list(INSP)
    for i, v in change.items():
        row[i] = v
    res = validate(raw_file("qa_inspections", [INSP[:2] + ["L01", "DAC-10421", "IPC", 50, 50, 0], row]), ctx)
    assert [(r.source_row_number, r.rule_id) for r in res.rejections] == [(3, rule)]
    assert len(res.rows) == 1


def test_exact_duplicate_dropped_and_key_conflict_rejected(ctx):
    conflict = list(INSP[:5]) + [110, 107, 3]
    res = validate(raw_file("qa_inspections", [INSP, INSP, conflict]), ctx)
    assert res.duplicates_dropped == 1 and len(res.warnings["VR-GEN-04"]) == 1
    assert sorted((r.source_row_number, r.rule_id) for r in res.rejections) == [(2, "VR-GEN-05"), (4, "VR-GEN-05")]
    assert res.rows == []


def test_unknown_code_is_a_warning_not_a_rejection(ctx):
    row = list(INSP)
    row[3] = "DAC-99999"
    res = validate(raw_file("qa_inspections", [row]), ctx)
    assert not res.rejections and len(res.rows) == 1
    assert "DAC-99999" in res.warnings["VR-GEN-06"][0]


def test_missing_header_rejects_the_file(ctx):
    src = sources()["qa_inspections"]
    headers = [h for h in src.headers if h != "Failed"]
    res = validate(raw_file("qa_inspections", [INSP[:-1]], headers), ctx)
    assert res.file_rejected and res.rejections[0].rule_id == "VR-GEN-01" and "Failed" in res.rejections[0].reason


def test_rework_cost_imputed_and_ok_gt_reworked_rejected(ctx):
    base = ["RW-2025-000001", "2025-03-12", "N", "L01", "DAC-10421", "SUR-01", 10, 9, 2.5]
    res = validate(raw_file("rework_log", [base + [None], base[:6] + [10, 11, 2.5, 120.0]]), ctx)
    assert [(r.rule_id) for r in res.rejections] == ["VR-RWK-01"]
    row = res.rows[0]
    assert (row["rework_cost_eur"], row["cost_source"], row["labour_rate_eur"]) == (95.0, "Imputed", 38.0)


def test_8d_tracker_warnings_and_links(ctx):
    src = sources()["eight_d_tracker"]
    vals = {"8D ID": "8D-2025-002", "Opened": "2025-03-01", "Part": "DAC-10421", "Line": "L01",
            "Defect Code": "SUR-01", "Source": "Internal", "Owner": "A. Test", "Department": "Quality",
            "D1 Team": "2025-03-01", "D2 Problem": "2025-03-03", "D3 Containment": "2025-03-02",
            "Target Closure": "2025-04-30", "Status": "Closed", "Linked NCs": "NC-2025-000001, NC-2025-000002"}
    res = validate(raw_file("eight_d_tracker", [[vals.get(h) for h in src.headers]]), ctx)
    assert set(res.warnings) == {"VR-8D-01", "VR-8D-02"}       # D3 before D2; 'Closed' without D8
    assert [lk["nc_id"] for lk in res.links] == ["NC-2025-000001", "NC-2025-000002"]


def test_action_for_unknown_8d_and_bad_classification_rejected(ctx):
    act = validate(raw_file("eight_d_actions", [
        ["8D-2025-001-A01", "8D-2025-001", "Corrective", "Fix", "A. Test", "2025-03-20", None, "Open"],
        ["8D-2023-999-A01", "8D-2023-999", "Corrective", "Fix", "A. Test", "2025-03-20", None, "Open"]]), ctx)
    assert [r.rule_id for r in act.rejections] == ["VR-8D-03"]
    af = validate(raw_file("audit_findings", [
        ["AF-2025-0001", "AU-2025-001", "2025-03-03", "System", None, None, "MAJ", "x", "A", "2025-04-02", None, None, None],
        ["AF-2025-0002", "AU-2025-001", "2025-03-03", "System", None, None, "Critical?", "x", "A", "2025-04-02", None, None, None]]), ctx)
    assert [r.rule_id for r in af.rejections] == ["VR-AF-01"]
    assert af.rows[0]["classification"] == "Major NC" and af.rows[0]["line_code"] is None
