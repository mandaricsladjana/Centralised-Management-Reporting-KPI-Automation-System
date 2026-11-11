"""Write department files exactly as described in config/source_schemas.toml.

Determinism: same frames -> byte-identical files (Phase 5 acceptance; the ETL's
incremental load skips files by SHA-256, D20). openpyxl stamps the current time
into docProps/core.xml and every zip entry, so .xlsx files are re-zipped with
fixed timestamps after saving.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import math
import re
import zipfile
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from etl.sources import Source

FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
FIXED_W3CDTF = b"2026-01-01T00:00:00Z"


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT


def cell_value(v, col_type: str):
    """Python value for one cell (Excel); strings stay strings (they may be injected defects)."""
    if _is_blank(v):
        return None
    if isinstance(v, str):
        return v
    if col_type == "bool":
        return "Y" if v else "N"
    if col_type == "int":
        return int(v)
    if col_type == "decimal":
        return round(float(v), 4)
    if col_type == "date":
        return v.date() if isinstance(v, (pd.Timestamp, dt.datetime)) else v
    return str(v)


def csv_text(v, col_type: str) -> str:
    v = cell_value(v, col_type)
    if v is None:
        return ""
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def normalise_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path) as z:
        entries = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            if name == "docProps/core.xml":
                for tag in (b"created", b"modified"):
                    data = re.sub(rb"(<dcterms:" + tag + rb"[^>]*>)[^<]*(</dcterms:" + tag + rb">)",
                                  rb"\g<1>" + FIXED_W3CDTF + rb"\g<2>", data)
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data, compresslevel=6)
    path.write_bytes(buf.getvalue())


def write_source(df: pd.DataFrame, source: Source, path: Path) -> None:
    cols = source.columns
    rows = df[[c.target for c in cols]].itertuples(index=False, name=None)
    if source.is_excel:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(source.sheet or "Sheet1")
        header = []
        for c in cols:
            cell = WriteOnlyCell(ws, value=c.source)
            cell.font = Font(bold=True)
            header.append(cell)
        ws.append(header)
        for row in rows:
            out = []
            for v, c in zip(row, cols):
                val = cell_value(v, c.type)
                if isinstance(val, dt.date):
                    cell = WriteOnlyCell(ws, value=val)
                    cell.number_format = "yyyy-mm-dd"
                    out.append(cell)
                else:
                    out.append(val)
            ws.append(out)
        wb.save(path)
        normalise_xlsx(path)
    else:
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(source.headers)
            for row in rows:
                w.writerow([csv_text(v, c.type) for v, c in zip(row, cols)])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
