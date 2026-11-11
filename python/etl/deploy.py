"""Apply the versioned SQL schema to DEV or PROD (blueprint §8.6).

    python -m etl.deploy --env dev            # apply pending scripts
    python -m etl.deploy --env dev --dry-run  # list what would run

Two kinds of scripts (the same model as Flyway's V/R migrations):

* versioned   sql/ddl/NNN_name.sql  Applied once, in order. The SHA-256 is
                                    recorded in audit.schema_version; editing an
                                    applied script is an error: add a new script.
* repeatable  sql/etl/*.sql,        CREATE OR ALTER procedures and views. Re-applied
              sql/rpt/*.sql         whenever the file's checksum changes.

Each script runs in its own transaction together with its schema_version row,
so a failed script leaves no half-applied state. After the scripts, the plant
holidays are synchronised from config/settings.toml and dw.dim_calendar is
(re)built. Both steps are idempotent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from etl.config import REPO_ROOT, ConfigError, db_target, load_env, load_settings
from etl.db import connect

log = logging.getLogger("etl.deploy")

SQL_DIR = REPO_ROOT / "sql"
VERSIONED_DIR = SQL_DIR / "ddl"
REPEATABLE_DIRS = (SQL_DIR / "etl", SQL_DIR / "rpt")
VERSIONED_NAME = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")

# A line holding only GO (any case), optionally followed by a -- comment.
_GO_LINE = re.compile(r"^[ \t]*GO[ \t]*(?:--[^\n]*)?$", re.IGNORECASE | re.MULTILINE)


class DeployError(RuntimeError):
    """The database and the repository disagree in a way that needs a human."""


@dataclass(frozen=True)
class Script:
    path: Path
    kind: str  # versioned | repeatable

    @property
    def name(self) -> str:
        """Repo-relative POSIX path: the key in audit.schema_version."""
        return self.path.relative_to(REPO_ROOT).as_posix()

    @property
    def checksum(self) -> str:
        return checksum(self.path.read_bytes())

    def batches(self) -> list[str]:
        return split_batches(self.path.read_text(encoding="utf-8-sig"))


# --------------------------------------------------------------------------- pure helpers
def checksum(content: bytes) -> str:
    """SHA-256 of the file with BOM removed and line endings normalised to LF,
    so a checkout with CRLF (Windows) and one with LF produce the same value."""
    text = content.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def split_batches(sql: str) -> list[str]:
    """Split a script on GO separator lines, like sqlcmd. Empty batches are dropped."""
    return [b.strip() for b in _GO_LINE.split(sql.replace("\r\n", "\n")) if b.strip()]


def discover_scripts(sql_dir: Path = SQL_DIR) -> list[Script]:
    """Versioned scripts in numeric order, then repeatable scripts by folder and name."""
    ddl = sorted((sql_dir / "ddl").glob("*.sql"))
    bad = [p.name for p in ddl if not VERSIONED_NAME.match(p.name)]
    if bad:
        raise DeployError(f"Versioned scripts must be named NNN_snake_case.sql: {bad}")
    prefixes = [p.name[:3] for p in ddl]
    dupes = sorted({x for x in prefixes if prefixes.count(x) > 1})
    if dupes:
        raise DeployError(f"Duplicate version numbers in sql/ddl: {dupes}")

    scripts = [Script(p, "versioned") for p in ddl]
    for folder in ("etl", "rpt"):
        scripts += [Script(p, "repeatable") for p in sorted((sql_dir / folder).glob("*.sql"))]
    return scripts


def plant_holidays(settings: dict) -> dict[dt.date, str]:
    """Expand settings.toml [calendar] into {date: 'Annual' | 'Shutdown'} for the calendar range."""
    periods, cal = settings["periods"], settings["calendar"]
    start, end = periods["calendar_start"], periods["calendar_end"]
    days: dict[dt.date, str] = {}
    for window in cal.get("summer_shutdown", []):
        d = window["start"]
        while d <= window["end"]:
            days[d] = "Shutdown"
            d += dt.timedelta(days=1)
    for year in range(start.year, end.year + 1):
        for mm_dd in cal.get("annual_holidays_mm_dd", []):
            days[dt.date.fromisoformat(f"{year}-{mm_dd}")] = "Annual"   # Annual wins on overlap
    return {d: t for d, t in sorted(days.items()) if start <= d <= end}


def plan(scripts: list[Script], applied: dict[str, str]) -> list[Script]:
    """Scripts to run, given {script_name: checksum} already recorded in the database."""
    changed = [s.name for s in scripts
               if s.kind == "versioned" and s.name in applied and applied[s.name] != s.checksum]
    if changed:
        raise DeployError(
            "Applied versioned scripts were edited (checksum mismatch): "
            f"{changed}. Revert the edit and put the change in a new NNN_ script.")
    return [s for s in scripts if applied.get(s.name) != s.checksum]


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True, timeout=10)
        return out.stdout.strip()[:40] or None
    except (OSError, subprocess.SubprocessError):
        return None


# --------------------------------------------------------------------------- database steps
def _drain(cursor) -> None:
    """Consume every result set: pyodbc only raises errors from later statements
    in a batch when their result is fetched."""
    while cursor.nextset():
        pass


def applied_scripts(conn) -> dict[str, str]:
    cur = conn.cursor()
    if cur.execute("SELECT OBJECT_ID(N'audit.schema_version', N'U')").fetchval() is None:
        return {}
    return {name: chk for name, chk in cur.execute(
        "SELECT script_name, checksum FROM [audit].schema_version").fetchall()}


def apply_script(conn, script: Script, commit: str | None) -> None:
    cur = conn.cursor()
    try:
        for i, batch in enumerate(script.batches(), start=1):
            try:
                cur.execute(batch)
                _drain(cur)
            except Exception as exc:
                raise DeployError(f"{script.name}, batch {i}: {exc}") from exc
        cur.execute(
            """MERGE [audit].schema_version AS t
               USING (SELECT ? AS script_name, ? AS script_kind, ? AS checksum, ? AS git_commit) AS s
               ON t.script_name = s.script_name
               WHEN MATCHED THEN UPDATE SET checksum = s.checksum, script_kind = s.script_kind,
                    git_commit = s.git_commit, applied_at_utc = SYSUTCDATETIME(), applied_by = SUSER_SNAME()
               WHEN NOT MATCHED THEN INSERT (script_name, script_kind, checksum, git_commit)
                    VALUES (s.script_name, s.script_kind, s.checksum, s.git_commit);""",
            script.name, script.kind, script.checksum, commit)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def sync_plant_holidays(conn, holidays: dict[dt.date, str]) -> tuple[int, int, int]:
    """Make dw.plant_holiday equal to settings.toml. Returns (inserted, updated, deleted)."""
    cur = conn.cursor()
    current = dict(cur.execute("SELECT holiday_date, holiday_type FROM dw.plant_holiday").fetchall())
    to_insert = [(d, t) for d, t in holidays.items() if d not in current]
    to_update = [(t, d) for d, t in holidays.items() if d in current and current[d] != t]
    to_delete = [(d,) for d in current if d not in holidays]
    src = "config/settings.toml"
    if to_insert:
        cur.executemany("INSERT INTO dw.plant_holiday (holiday_date, holiday_type, etl_run_id, source_file) "
                        f"VALUES (?, ?, 0, N'{src}')", to_insert)
    if to_update:
        cur.executemany("UPDATE dw.plant_holiday SET holiday_type = ?, updated_at_utc = SYSUTCDATETIME() "
                        "WHERE holiday_date = ?", to_update)
    if to_delete:
        cur.executemany("DELETE FROM dw.plant_holiday WHERE holiday_date = ?", to_delete)
    return len(to_insert), len(to_update), len(to_delete)


def build_calendar(conn, settings: dict) -> int:
    p = settings["periods"]
    cur = conn.cursor()
    cur.execute("EXEC etl.usp_build_calendar @start_date = ?, @end_date = ?",
                p["calendar_start"], p["calendar_end"])
    _drain(cur)
    return cur.execute("SELECT COUNT(*) FROM dw.dim_calendar").fetchval()


def deploy(environment: str, dry_run: bool = False, database: str | None = None) -> int:
    target = db_target(environment, database)
    settings = load_settings()
    scripts = discover_scripts()
    log.info("Target %s", target.describe())

    with connect(target) as conn:
        pending = plan(scripts, applied_scripts(conn))
        if dry_run:
            for s in pending:
                log.info("would apply  %-10s %s", s.kind, s.name)
            log.info("%d of %d scripts pending (dry run, nothing changed)", len(pending), len(scripts))
            return 0

        commit = git_commit()
        for s in pending:
            log.info("applying     %-10s %s", s.kind, s.name)
            apply_script(conn, s, commit)
        log.info("%d of %d scripts applied", len(pending), len(scripts))

        try:
            ins, upd, dele = sync_plant_holidays(conn, plant_holidays(settings))
            days = build_calendar(conn, settings)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        log.info("plant_holiday: +%d ~%d -%d; dim_calendar: %d days", ins, upd, dele, days)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m etl.deploy", description=__doc__.splitlines()[0])
    parser.add_argument("--env", choices=("dev", "prod"),
                        help="target environment (required for prod; defaults to ENVIRONMENT for dev)")
    parser.add_argument("--dry-run", action="store_true", help="list pending scripts without applying them")
    parser.add_argument("--database", help="override the database name (e.g. QualityKPI_Test)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    load_env()
    env = args.env or os.getenv("ENVIRONMENT", "dev").strip().lower()
    if env == "prod" and args.env != "prod":
        log.error("Refusing to deploy to PROD implicitly: pass --env prod explicitly.")
        return 2
    try:
        return deploy(env, dry_run=args.dry_run, database=args.database)
    except (ConfigError, DeployError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
