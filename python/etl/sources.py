"""Source file contract (config/source_schemas.toml), shared by generator and ETL."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from etl.config import REPO_ROOT

SOURCE_SCHEMAS_PATH = REPO_ROOT / "config" / "source_schemas.toml"
TYPES = {"str", "int", "decimal", "date", "code", "bool"}


@dataclass(frozen=True)
class Column:
    source: str
    target: str
    type: str
    required: bool
    load: bool = True       # False: read and checked, but not written to staging


@dataclass(frozen=True)
class Source:
    name: str
    file_pattern: str
    owner: str
    staging_table: str
    business_key: tuple[str, ...]
    columns: tuple[Column, ...]
    sheet: str | None = None

    @property
    def is_excel(self) -> bool:
        return self.file_pattern.endswith(".xlsx")

    @property
    def headers(self) -> list[str]:
        return [c.source for c in self.columns]

    @property
    def rename_to_source(self) -> dict[str, str]:
        return {c.target: c.source for c in self.columns}

    @property
    def rename_to_target(self) -> dict[str, str]:
        return {c.source: c.target for c in self.columns}


@cache
def load_contract(path: Path = SOURCE_SCHEMAS_PATH) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def sources(path: Path = SOURCE_SCHEMAS_PATH) -> dict[str, Source]:
    out = {}
    for name, s in load_contract(path)["sources"].items():
        cols = tuple(Column(c["source"], c["target"], c["type"], c["required"], c.get("load", True))
                     for c in s["columns"])
        out[name] = Source(name, s["file_pattern"], s["owner"], s["staging_table"],
                           tuple(s["business_key"]), cols, s.get("sheet"))
    return out


def spelling(kind: str, path: Path = SOURCE_SCHEMAS_PATH) -> dict[str, list[str]]:
    """{standard_code: [variants...]} for 'line_code', 'shift_code', 'classification', 'disposition'."""
    return load_contract(path)["spelling"][kind]


def spelling_lookup(kind: str, path: Path = SOURCE_SCHEMAS_PATH) -> dict[str, str]:
    """{variant (case-insensitive key, stripped): standard_code}."""
    return {v.strip().casefold(): std for std, variants in spelling(kind, path).items() for v in variants}
