"""Read department files into lists of raw rows keyed by the file's own headers."""
from __future__ import annotations

import csv
import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from etl.sources import Source


@dataclass
class RawFile:
    path: Path
    source: Source
    headers: list[str]
    rows: list[tuple[int, dict]]          # (source_row_number, {header: raw value})
    sha256: str
    size_bytes: int

    @property
    def missing_required_headers(self) -> list[str]:
        """VR-GEN-01: required columns absent from the header row."""
        present = {h.strip() for h in self.headers if h is not None}
        return [c.source for c in self.source.columns if c.required and c.source not in present]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover(source_dir: Path, source: Source) -> list[Path]:
    """Files of one source, sorted by name (monthly production files in month order)."""
    return sorted(p for p in source_dir.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, source.file_pattern))


def _row_is_empty(values) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in values)


def read(path: Path, source: Source) -> RawFile:
    if source.is_excel:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[source.sheet] if source.sheet in wb.sheetnames else wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            headers = [str(h).strip() if h is not None else None for h in next(it, ())]
            rows = []
            for n, values in enumerate(it, start=2):
                # read-only mode drops trailing empty cells: pad to the header width
                values = tuple(values) + (None,) * (len(headers) - len(values))
                if not _row_is_empty(values):
                    rows.append((n, {h: v for h, v in zip(headers, values) if h}))
        finally:
            wb.close()
    else:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            headers = [h.strip() for h in next(reader, [])]
            rows = []
            for n, values in enumerate(reader, start=2):
                values = values + [""] * (len(headers) - len(values))
                if not _row_is_empty(values):
                    rows.append((n, {h: (v if v != "" else None) for h, v in zip(headers, values)}))
    return RawFile(path, source, headers, rows, file_sha256(path), path.stat().st_size)
