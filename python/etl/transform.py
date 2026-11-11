"""Value parsing, standardisation and derivations (no database access).

Every parser returns (value, error). error is None on success, otherwise a
short reason; the caller decides which validation rule that becomes.
"""
from __future__ import annotations

import datetime as dt
import math
import re

from etl.sources import spelling_lookup

EXCEL_EPOCH = dt.date(1899, 12, 30)              # Excel serial 1 = 1900-01-01 (with the 1900 leap-year bug)
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")     # accepted text formats (VR-GEN-03)
_SPELLING_KINDS = {"line_code": "line_code", "primary_line_code": "line_code", "shift_code": "shift_code",
                   "classification": "classification", "disposition": "disposition"}


def is_blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())


# --- parsers ---------------------------------------------------------------------------
def parse_date(v) -> tuple[dt.date | None, str | None]:
    if is_blank(v):
        return None, None
    if isinstance(v, dt.datetime):
        return v.date(), None
    if isinstance(v, dt.date):
        return v, None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 1 <= v < 2958466:                                  # Excel serial date
            return EXCEL_EPOCH + dt.timedelta(days=int(v)), None
        return None, f"number {v!r} is not an Excel date"
    text = str(v).strip()
    if re.fullmatch(r"\d{4,6}(\.0+)?", text):                 # serial stored as text
        return parse_date(float(text))
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date(), None
        except ValueError:
            continue
    return None, f"unreadable date {text!r}"


def parse_int(v) -> tuple[int | None, str | None]:
    if is_blank(v):
        return None, None
    if isinstance(v, bool):
        return None, f"not a number: {v!r}"
    if isinstance(v, int):
        return v, None
    try:
        f = float(str(v).strip().replace(",", "."))
    except ValueError:
        return None, f"not a number: {v!r}"
    if not f.is_integer():
        return None, f"not a whole number: {v!r}"
    return int(f), None


def parse_decimal(v) -> tuple[float | None, str | None]:
    if is_blank(v):
        return None, None
    if isinstance(v, bool):
        return None, f"not a number: {v!r}"
    try:
        return float(str(v).strip().replace(",", ".")) if not isinstance(v, (int, float)) else float(v), None
    except ValueError:
        return None, f"not a number: {v!r}"


def parse_bool(v) -> tuple[bool | None, str | None]:
    if is_blank(v):
        return None, None
    text = str(v).strip().casefold()
    if text in {"y", "yes", "true", "1", "x"}:
        return True, None
    if text in {"n", "no", "false", "0"}:
        return False, None
    return None, f"not Y/N: {v!r}"


def parse_text(v) -> tuple[str | None, str | None]:
    if is_blank(v):
        return None, None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip(), None


PARSERS = {"date": parse_date, "int": parse_int, "decimal": parse_decimal, "bool": parse_bool,
           "str": parse_text, "code": parse_text}


# --- standardisation -------------------------------------------------------------------
def standardise(target: str, value: str | None) -> tuple[str | None, bool]:
    """Map a spelling variant to the standard code. Returns (value, mappable).

    Columns without a spelling list are returned unchanged (codes upper-cased are
    NOT forced: part numbers and IDs are compared exactly as the dictionary writes them).
    """
    kind = _SPELLING_KINDS.get(target)
    if value is None or kind is None:
        return value, True
    std = spelling_lookup(kind).get(value.strip().casefold())
    return (std, True) if std is not None else (value, False)


# --- derivations -----------------------------------------------------------------------
def impute_rework_cost(labour_hours: float, rate: float) -> float:
    """VR-RWK-02: labour only (material is unknown when the cost is missing), D12."""
    return round(labour_hours * rate, 2)


def split_nc_links(text: str | None) -> list[str]:
    """'NC-1, NC-2;NC-3' -> ['NC-1', 'NC-2', 'NC-3'] (order kept, duplicates removed)."""
    if is_blank(text):
        return []
    seen, out = set(), []
    for part in re.split(r"[,;\s]+", str(text)):
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return out


def json_safe(v):
    """Raw cell value for audit.rejected_row.raw_record (JSON)."""
    if is_blank(v):
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)
