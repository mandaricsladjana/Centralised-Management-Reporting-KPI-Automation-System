"""Phase 5 acceptance: the synthetic generator is reproducible, follows the source
contract, documents every injected defect, and tells the scripted quality story."""
import csv
import datetime as dt
import json
import re

import pandas as pd
import pytest
from openpyxl import load_workbook

from etl.config import load_settings
from etl.deploy import plant_holidays
from etl.sources import sources
from generate import model, scenario
from generate.__main__ import SAMPLE_PERIOD, generate

SEED = 20261111


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample")
    return out, generate(out, SEED, *SAMPLE_PERIOD)


@pytest.fixture(scope="module")
def full_clean():
    s = load_settings()
    p = s["periods"]
    return model.generate_clean(SEED, s, p["history_start"], p["history_end"], set(plant_holidays(s)))


def read_source(path, src):
    if src.is_excel:
        ws = load_workbook(path, read_only=True)[src.sheet]
        rows = list(ws.iter_rows(values_only=True))
        width = len(rows[0])
        # read-only mode drops trailing empty cells: pad so blanks in the last column stay visible
        return list(rows[0]), [tuple(r) + (None,) * (width - len(r)) for r in rows[1:]]
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


# --- reproducibility --------------------------------------------------------------------
def test_same_seed_gives_byte_identical_files(sample, tmp_path):
    _, first = sample
    second = generate(tmp_path, SEED, *SAMPLE_PERIOD)
    assert [(f["file"], f["sha256"]) for f in first["files"]] == [(f["file"], f["sha256"]) for f in second["files"]]


def test_different_seed_gives_different_data(sample, tmp_path):
    _, first = sample
    other = generate(tmp_path, SEED + 1, *SAMPLE_PERIOD)
    changed = {f["file"] for f in first["files"]} - {f["file"] for f in other["files"]} | {
        a["file"] for a, b in zip(first["files"], other["files"]) if a["sha256"] != b["sha256"]}
    assert "qa_inspections.csv" in changed


# --- contract ------------------------------------------------------------------------------
def test_every_source_is_written_with_contract_headers(sample):
    out, summary = sample
    written = {f["file"] for f in summary["files"]}
    contract = sources()
    for src in contract.values():
        files = [f for f in written if re.fullmatch(src.file_pattern.replace(".", r"\.").replace("*", ".+"), f)]
        assert files, f"no file for {src.name}"
        for f in files:
            header, _ = read_source(out / f, src)
            assert header == src.headers, f


def test_monthly_production_files(sample):
    _, summary = sample
    assert [f["file"] for f in summary["files"] if f["source"] == "production_daily"] == ["production_daily_2025_03.xlsx"]


def test_excel_dates_are_real_dates(sample):
    out, _ = sample
    src = sources()["master_programs"]
    _, rows = read_source(out / src.file_pattern, src)
    assert all(isinstance(r[4], dt.datetime) for r in rows)        # SOP Date


# --- injected issues -------------------------------------------------------------------------
def test_rule_issue_rate_is_close_to_setting(sample):
    _, summary = sample
    rate = load_settings()["synthetic"]["dq_issue_rate"]
    assert rate * 0.7 <= summary["rule_issue_rate"] <= rate * 1.4


def test_every_listed_issue_is_really_in_the_file(sample):
    """Each manifest row points at a source row that shows exactly that defect."""
    out, _ = sample
    issues = pd.read_csv(out / "_generation" / "injected_issues.csv", dtype=str, keep_default_na=False)
    contract = {s.file_pattern: s for s in sources().values()}
    contract.update({f.name: contract["production_daily_*.xlsx"] for f in out.glob("production_daily_*.xlsx")})
    cache = {}
    checked = set()
    for issue in issues.itertuples():
        src = contract[issue.file]
        if issue.file not in cache:
            header, rows = read_source(out / issue.file, src)
            cache[issue.file] = (header, rows)
        header, rows = cache[issue.file]
        row = dict(zip([c.target for c in src.columns], rows[int(issue.source_row_number) - 2]))
        blank = lambda v: v in (None, "")                                     # noqa: E731
        num = lambda v: float(v)                                              # noqa: E731
        match issue.issue:
            case "negative_qty":
                assert any(not blank(v) and isinstance(v, (int, float, str)) and str(v).startswith("-")
                           for k, v in row.items() if k.startswith("qty_")), issue
            case "passed_gt_inspected":
                assert num(row["qty_passed_first_time"]) > num(row["qty_inspected"]), issue
            case "ok_gt_reworked":
                assert num(row["qty_rework_ok"]) > num(row["qty_reworked"]), issue
            case "unknown_defect":
                assert row["defect_code"] == "XXX-99", issue
            case "unknown_stage":
                assert row["stage_code"] == "VIS", issue
            case "unknown_part":
                assert str(row["part_number"]).startswith("DAC-9"), issue
            case "missing_value" | "missing_cost":
                assert any(blank(v) for v in row.values()), issue
            case "future_date":
                assert "2030" in str(row["event_date"]), issue
            case "invalid_date":
                assert isinstance(row["event_date"], str) and ("31.02." in row["event_date"] or "-13-" in row["event_date"])
            case "duplicate_row":
                prev = rows[int(issue.source_row_number) - 3]
                assert list(prev) == list(rows[int(issue.source_row_number) - 2]), issue
            case "line_variant":
                assert row["line_code"] not in {c for c, *_ in scenario.LINES}, issue
            case "date_text":
                assert isinstance(row["event_date"], str) and re.fullmatch(r"\d\d[./]\d\d[./]\d{4}", row["event_date"])
            case "unmappable_classification":
                assert row["classification"] in ("Critical?", "Major/Minor"), issue
            case "unknown_8d":
                assert row["eight_d_id"] == "8D-2023-999", issue
        checked.add(issue.issue)
    assert {"negative_qty", "passed_gt_inspected", "duplicate_row", "unknown_defect", "line_variant"} <= checked


def test_manifest_expected_actions_follow_rule_catalogue(sample):
    out, _ = sample
    issues = pd.read_csv(out / "_generation" / "injected_issues.csv", dtype=str, keep_default_na=False)
    assert set(issues["expected"]) <= {"Reject", "Warn", "Standardise"}
    assert (issues.loc[issues["rule_id"] == "", "expected"] == "Standardise").all()
    assert (issues.loc[issues["rule_id"] == "VR-INS-01", "expected"] == "Reject").all()


# --- clean data invariants (24 months) --------------------------------------------------------------
def test_inspection_balances_and_flow(full_clean):
    ins, prod = full_clean["qa_inspections"], full_clean["production"]
    assert (ins.qty_passed_first_time + ins.qty_failed == ins.qty_inspected).all()
    key = ["event_date", "shift_code", "line_code", "part_number"]
    merged = ins.groupby(key).qty_inspected.max().reset_index().merge(prod, on=key)
    assert (merged.qty_inspected <= merged.qty_started).all()                       # DQ-02 holds in clean data


def test_scrap_and_rework_reconcile_with_nc(full_clean):
    """DQ-07 / DQ-08: bookings follow the NC dispositions (scrap within the 2 % tolerance)."""
    nc = full_clean["qa_defects"]
    nc_scrap = nc.loc[nc.disposition == "Scrap", "qty_affected"].sum()
    nc_rework = nc.loc[nc.disposition == "Rework", "qty_affected"].sum()
    assert full_clean["scrap_log"].qty_scrapped.sum() == pytest.approx(nc_scrap, rel=0.02)
    assert full_clean["rework_log"].qty_reworked.sum() == nc_rework
    rw = full_clean["rework_log"]
    assert (rw.qty_rework_ok <= rw.qty_reworked).all() and (rw.labour_hours > 0).all()


def test_ids_follow_data_dictionary_formats(full_clean):
    patterns = {("qa_defects", "nc_id"): r"NC-\d{4}-\d{6}", ("scrap_log", "scrap_id"): r"SC-\d{4}-\d{6}",
                ("rework_log", "rework_id"): r"RW-\d{4}-\d{6}", ("eight_d_tracker", "eight_d_id"): r"8D-\d{4}-\d{3}",
                ("eight_d_actions", "action_id"): r"8D-\d{4}-\d{3}-A\d{2}",
                ("audit_findings", "finding_id"): r"AF-\d{4}-\d{4}"}
    for (frame, col), pat in patterns.items():
        values = full_clean[frame][col]
        assert values.is_unique, col
        assert values.str.fullmatch(pat).all(), col
    # audit_id is a degenerate dimension: one audit has many findings, so it repeats
    assert full_clean["audit_findings"].audit_id.str.fullmatch(r"AU-\d{4}-\d{3}").all()


def test_8d_milestones_ordered_and_links_valid(full_clean):
    t, nc = full_clean["eight_d_tracker"], full_clean["qa_defects"]
    for r in t.itertuples():
        ms = [d for d in (r.d1_date, r.d2_date, r.d3_date, r.d4_date, r.d5_date, r.d6_date, r.d7_date, r.d8_date) if d]
        assert ms == sorted(ms), r.eight_d_id
        assert r.d1_date == r.opened_date
    links = [i for s in t.linked_nc_ids if s for i in s.split(", ")]
    assert len(links) == len(set(links)), "an NC is linked to two 8Ds"
    assert set(links) <= set(nc.nc_id)
    assert set(full_clean["eight_d_actions"].eight_d_id) <= set(t.eight_d_id)


# --- the scripted story (blueprint §4 built-in realism) --------------------------------------
def test_paint_line_night_shift_is_worse(full_clean):
    ins = full_clean["qa_inspections"]
    l4 = ins[ins.line_code == "L04"].groupby("shift_code")[["qty_passed_first_time", "qty_inspected"]].sum()
    fpy = l4.qty_passed_first_time / l4.qty_inspected
    assert fpy["N"] < fpy["E"] - 0.01 and fpy["N"] < fpy["L"] - 0.01


def test_ev_launch_spike_fades(full_clean):
    ins = full_clean["qa_inspections"]
    ev = ins[ins.part_number == "DAC-40415"].assign(ym=lambda d: pd.to_datetime(d.event_date).dt.strftime("%Y-%m"))
    rate = ev.groupby("ym").apply(lambda g: g.qty_failed.sum() / g.qty_inspected.sum(), include_groups=False)
    assert rate["2025-09"] > 2 * rate["2026-01"]


def test_repeat_defect_is_inside_the_window(full_clean):
    t = full_clean["eight_d_tracker"]
    sink = t[(t.part_number == "DAC-10421") & (t.defect_code == "SUR-01")].sort_values("opened_date")
    assert len(sink) >= 2
    gap = (sink.iloc[1].opened_date - sink.iloc[0].d8_date).days
    assert 0 < gap <= load_settings()["kpi"]["repeat_defect_window_days"]


def test_defects_improve_over_time(full_clean):
    nc = full_clean["qa_defects"].assign(ym=lambda d: pd.to_datetime(d.event_date).dt.strftime("%Y-%m"))
    monthly = nc.groupby("ym").qty_affected.sum()
    assert monthly.iloc[-6:].mean() < monthly.iloc[:6].mean()


def test_hatchback_stops_after_eop(full_clean):
    prod = full_clean["production"]
    hatch = {p.part_number for p in scenario.PARTS if p.program_code == "VP-HATCH-B"}
    assert prod[prod.part_number.isin(hatch)].event_date.max() <= dt.date(2026, 6, 30)


def test_8d_population_is_realistic(full_clean):
    t = full_clean["eight_d_tracker"]
    assert 25 <= len(t) <= 60
    assert t.d8_date.isna().sum() >= 2                        # some still open
    assert set(t.problem_source) == {"Internal", "Customer complaint", "Audit"}
    af = full_clean["audit_findings"]
    assert af.line_code.isna().any()                          # plant-wide findings exist
    assert (af.classification == "Major NC").any() and af.eight_d_id.notna().any()
