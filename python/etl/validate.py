"""Validation rule engine (data dictionary §7).

Input: one RawFile. Output: typed, standardised rows for staging plus every
rejection (-> audit.rejected_row) and warning (-> audit.dq_result).

Order per file:
  VR-GEN-01 header check (whole file) -> VR-GEN-04 exact duplicates dropped ->
  per row: VR-GEN-02 mandatory, VR-GEN-03 dates, VR-GEN-08 types/allowed values,
  VR-AF-01 classification, VR-GEN-07 date range, VR-QTY-01 quantities,
  source rules (INS-01, RWK-01, 8D-03) -> warnings (GEN-06, RWK-02, 8D-01/02) ->
  VR-GEN-05 business-key conflicts among the remaining rows.
A row is rejected for the first rule it breaks.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from etl import transform as tf
from etl.extract import RawFile

# Allowed values that dw CHECK constraints enforce (sql/ddl/010, 020): rejecting
# here keeps one bad row from failing the whole file's MERGE.
ALLOWED = {
    "process_type": {"Injection Moulding", "Painting", "Assembly", "Welding"},
    "defect_category": {"Dimensional", "Surface", "Assembly", "Material", "Functional"},
    "severity": {"A", "B", "C"},
    "disposition": {"Scrap", "Rework", "Use-as-is", "Return-to-supplier"},
    "problem_source": {"Internal", "Customer complaint", "Audit"},
    "action_type": {"Containment", "Corrective", "Preventive"},
    "audit_type": {"System", "Process", "Product", "Layered Process", "Customer"},
}
EVENT_DATE_COLUMNS = {"event_date", "opened_date", "audit_date"}        # VR-GEN-07
POSITIVE_COLUMNS = {"labour_hours", "standard_unit_cost_eur", "opportunities_per_unit"}
# Code column -> master set name in Context.known (VR-GEN-06)
LOOKUPS = {"line_code": "line", "primary_line_code": "line", "shift_code": "shift", "part_number": "part",
           "stage_code": "stage", "defect_code": "defect", "program_code": "program"}
MILESTONES = [f"d{i}_date" for i in range(1, 9)]


@dataclass
class Context:
    known: dict[str, set[str]]            # line, shift, part, stage, defect, program, eight_d
    labour_rate_eur: float
    earliest_event_date: dt.date
    today: dt.date


@dataclass
class Rejection:
    source_row_number: int | None
    rule_id: str
    reason: str
    raw: dict


@dataclass
class Result:
    rows: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)                   # stg.eight_d_nc_link
    rejections: list[Rejection] = field(default_factory=list)
    warnings: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))   # rule -> details
    rows_read: int = 0
    duplicates_dropped: int = 0
    file_rejected: bool = False

    def warn(self, rule: str, detail: str) -> None:
        self.warnings[rule].append(detail)


class RowError(Exception):
    def __init__(self, rule: str, reason: str):
        super().__init__(reason)
        self.rule, self.reason = rule, reason


def _raw_json(raw: dict) -> dict:
    return {k: tf.json_safe(v) for k, v in raw.items()}


def _parse_row(raw: dict, f: RawFile, ctx: Context) -> dict:
    out = {}
    for col in f.source.columns:
        value = raw.get(col.source)
        if tf.is_blank(value):
            if col.required:
                raise RowError("VR-GEN-02", f"mandatory '{col.source}' is empty")
            out[col.target] = None
            continue
        parsed, err = tf.PARSERS[col.type](value)
        if err:
            raise RowError("VR-GEN-03" if col.type == "date" else "VR-GEN-08", f"'{col.source}': {err}")
        if col.type in ("code", "str"):
            parsed, ok = tf.standardise(col.target, parsed)
            if not ok and col.target == "classification":
                raise RowError("VR-AF-01", f"classification {parsed!r} cannot be mapped")
            if not ok and col.target == "disposition":
                raise RowError("VR-GEN-08", f"disposition {parsed!r} not allowed")
        if col.target in ALLOWED and parsed not in ALLOWED[col.target]:
            raise RowError("VR-GEN-08", f"'{col.source}' value {parsed!r} not allowed")
        out[col.target] = parsed
    return out


def _check_row(row: dict, name: str, ctx: Context) -> None:
    for col in EVENT_DATE_COLUMNS & row.keys():
        d = row[col]
        if d is not None and (d > ctx.today or d < ctx.earliest_event_date):
            raise RowError("VR-GEN-07", f"{col} {d.isoformat()} outside {ctx.earliest_event_date}..today")
    for col, v in row.items():
        if v is None:
            continue
        if col.startswith("qty_") and v < 0:
            raise RowError("VR-QTY-01", f"{col} is negative ({v})")
        if col in POSITIVE_COLUMNS and v <= 0:
            raise RowError("VR-QTY-01", f"{col} must be > 0 ({v})")
    if name == "qa_inspections" and row["qty_passed_first_time"] + row["qty_failed"] != row["qty_inspected"]:
        raise RowError("VR-INS-01", f"passed {row['qty_passed_first_time']} + failed {row['qty_failed']}"
                                    f" <> inspected {row['qty_inspected']}")
    if name == "rework_log" and row["qty_rework_ok"] > row["qty_reworked"]:
        raise RowError("VR-RWK-01", f"qty OK {row['qty_rework_ok']} > reworked {row['qty_reworked']}")
    if name == "eight_d_actions" and row["eight_d_id"] not in ctx.known["eight_d"]:
        raise RowError("VR-8D-03", f"8D {row['eight_d_id']!r} not in the 8D tracker")


def _derive_and_warn(row: dict, n: int, name: str, ctx: Context, res: Result) -> None:
    key = f"row {n}"
    for col, master in LOOKUPS.items():
        v = row.get(col)
        if v is not None and v not in ctx.known[master]:
            res.warn("VR-GEN-06", f"{key}: {col} {v!r} unknown -> loaded as -1")
    if name == "rework_log":
        row["labour_rate_eur"] = ctx.labour_rate_eur
        if row["rework_cost_eur"] is None:
            row["rework_cost_eur"] = tf.impute_rework_cost(row["labour_hours"], ctx.labour_rate_eur)
            row["cost_source"] = "Imputed"
            res.warn("VR-RWK-02", f"{key}: {row['rework_id']} cost imputed {row['rework_cost_eur']}")
        else:
            row["cost_source"] = "Reported"
    if name == "eight_d_tracker":
        dates = [(m, row[m]) for m in MILESTONES if row[m] is not None]
        for (m1, d1), (m2, d2) in zip(dates, dates[1:]):
            if d2 < d1:
                res.warn("VR-8D-01", f"{key}: {row['eight_d_id']} {m2} before {m1}")
                break
        says_closed = row["status_reported"] is not None and "closed" in row["status_reported"].casefold()
        if says_closed != (row["d8_date"] is not None):
            res.warn("VR-8D-02", f"{key}: {row['eight_d_id']} status {row['status_reported']!r}, "
                                 f"D8 {'set' if row['d8_date'] else 'empty'} (D8 wins)")
    if name == "audit_findings" and row["eight_d_id"] and row["eight_d_id"] not in ctx.known["eight_d"]:
        res.warn("VR-GEN-06", f"{key}: 8D {row['eight_d_id']!r} unknown -> link left empty")


def validate(f: RawFile, ctx: Context) -> Result:
    res = Result(rows_read=len(f.rows))
    name = f.source.name
    missing = f.missing_required_headers
    if missing:
        res.file_rejected = True
        res.rejections.append(Rejection(None, "VR-GEN-01", f"missing columns: {', '.join(missing)}",
                                        {"headers": f.headers}))
        return res

    # VR-GEN-04: exact duplicate rows (same raw values in every contract column) -> keep the first
    seen = set()
    candidates = []
    for n, raw in f.rows:
        sig = tuple(str(tf.json_safe(raw.get(c.source))) for c in f.source.columns)
        if sig in seen:
            res.duplicates_dropped += 1
            res.warn("VR-GEN-04", f"row {n}: exact duplicate dropped")
            continue
        seen.add(sig)
        candidates.append((n, raw))

    valid: list[tuple[int, dict, dict]] = []
    for n, raw in candidates:
        try:
            row = _parse_row(raw, f, ctx)
            _check_row(row, name, ctx)
        except RowError as e:
            res.rejections.append(Rejection(n, e.rule, e.reason, _raw_json(raw)))
            continue
        _derive_and_warn(row, n, name, ctx, res)
        valid.append((n, raw, row))

    # VR-GEN-05: same business key, different values -> reject all of them
    by_key = defaultdict(list)
    for item in valid:
        by_key[tuple(item[2][k] for k in f.source.business_key)].append(item)
    for key, items in by_key.items():
        if len(items) > 1:
            shown = " | ".join("" if k is None else str(k) for k in key)
            res.warn("VR-GEN-05", f"rows {[i[0] for i in items]}: key {shown} conflicts")
            for n, raw, _ in items:
                res.rejections.append(Rejection(n, "VR-GEN-05", f"business key {shown} appears "
                                                                f"{len(items)} times with different values",
                                                _raw_json(raw)))
            continue
        n, _, row = items[0]
        loaded = {c.target: row[c.target] for c in f.source.columns if c.load}
        if name == "rework_log":
            loaded |= {"labour_rate_eur": row["labour_rate_eur"], "cost_source": row["cost_source"]}
        loaded |= {"source_file": f.path.name, "source_row_number": n}
        res.rows.append(loaded)
        if name == "eight_d_tracker":
            for nc_id in tf.split_nc_links(row["linked_nc_ids"]):
                res.links.append({"eight_d_id": row["eight_d_id"], "nc_id": nc_id,
                                  "source_file": f.path.name, "source_row_number": n})
    res.rejections.sort(key=lambda r: (r.source_row_number or 0, r.rule_id))
    return res
