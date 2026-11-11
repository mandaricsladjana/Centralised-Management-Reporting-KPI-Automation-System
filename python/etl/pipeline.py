"""Load the department files into the warehouse (Phase 6).

    python -m etl.pipeline --env dev                      # data/raw -> QualityKPI_Dev
    python -m etl.pipeline --env dev --source-dir ../data/sample
    python -m etl.pipeline --env prod --triggered-by scheduler

Per run: open audit.etl_run -> clear staging -> for each source in load order,
for each file: skip if its SHA-256 was already loaded (incremental, D20), read,
validate and standardise, then in ONE transaction write file_manifest +
rejected rows + staging + MERGE + warnings -> cross-source DQ checks -> close the run.
A file that fails is rolled back and logged as Failed; the run goes on (status Partial).
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from etl import dq_checks, extract, load
from etl.config import REPO_ROOT, ConfigError, db_target, load_env, load_settings
from etl.db import connect
from etl.deploy import git_commit
from etl.sources import sources
from etl.validate import Context, validate

log = logging.getLogger("etl.pipeline")


@dataclass
class RunSummary:
    run_id: int
    status: str = "Running"
    files_loaded: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    duplicates_dropped: int = 0
    merged: dict = field(default_factory=dict)       # source -> {"rows_inserted": n, "rows_updated": n}


def _context(cur, settings: dict) -> Context:
    return Context(known=load.known_codes(cur),
                   labour_rate_eur=settings["costs"]["rework_labour_rate_eur_per_hour"],
                   earliest_event_date=settings["periods"]["earliest_valid_event_date"],
                   today=dt.date.today())


def _process_file(conn, run: RunSummary, path: Path, src, settings: dict, blob) -> str:
    cur = conn.cursor()
    raw = extract.read(path, src)
    if load.already_loaded(cur, src.name, raw.sha256):
        load.insert_manifest(cur, run.run_id, src.name, path, raw.sha256, raw.size_bytes, "Skipped-duplicate",
                             len(raw.rows))
        conn.commit()
        run.files_skipped += 1
        log.info("skip   %-30s already loaded (same SHA-256)", path.name)
        return "skipped"

    res = validate(raw, _context(cur, settings))
    blob_path = blob.upload_raw(path, src.name, dt.date.today()) if blob else None
    try:
        file_id = load.insert_manifest(cur, run.run_id, src.name, path, raw.sha256, raw.size_bytes,
                                       "Failed" if res.file_rejected else "Loaded", res.rows_read, blob_path)
        load.insert_rejections(cur, run.run_id, file_id, res.rejections)
        merged = {}
        if not res.file_rejected:
            table, proc = load.TARGETS[src.name]
            load.stage(cur, table, res.rows)
            if src.name == "eight_d_tracker":
                load.stage(cur, "stg.eight_d_nc_link", res.links)
            merged = load.call_proc(cur, proc, run.run_id)
            if src.name in ("eight_d_tracker", "qa_defects"):
                # 8D <-> NC links: after the tracker (existing NCs) and after the NCs (new ones)
                # (unresolved links are counted once, at the end of the run: dq_checks DQ-LNK-NC)
                merged |= load.call_proc(cur, "usp_apply_8d_nc_links", run.run_id, load.LINK_OUTPUTS)
        load.insert_warnings(cur, run.run_id, path.name, res.warnings)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if blob and not res.file_rejected:
        blob.archive_loaded(src.name, path.name, dt.date.today())

    run.rows_read += res.rows_read
    run.rows_rejected += len(res.rejections)
    run.duplicates_dropped += res.duplicates_dropped
    run.rows_loaded += len(res.rows)
    agg = run.merged.setdefault(src.name, {"rows_inserted": 0, "rows_updated": 0})
    for k in agg:
        agg[k] += merged.get(k, 0) or 0
    if res.file_rejected:
        run.files_failed += 1
        log.error("reject %-30s %s", path.name, res.rejections[0].reason)
        return "failed"
    run.files_loaded += 1
    log.info("load   %-30s read %6d  staged %6d  rejected %4d  dup %3d  +%d ~%d",
             path.name, res.rows_read, len(res.rows), len(res.rejections), res.duplicates_dropped,
             merged.get("rows_inserted", 0), merged.get("rows_updated", 0))
    return "loaded"


def run_pipeline(environment: str, source_dir: Path, triggered_by: str = "manual",
                 upload_blob: bool = False, database: str | None = None) -> RunSummary:
    settings = load_settings()
    contract = sources()
    if not source_dir.is_dir():
        raise ConfigError(f"source folder not found: {source_dir}")
    blob = None
    if upload_blob:
        from etl.storage import BlobArchive
        blob = BlobArchive(settings)

    with connect(db_target(environment, database)) as conn:
        run = RunSummary(load.start_run(conn, environment, triggered_by, git_commit()))
        log.info("run %d started (%s, %s)", run.run_id, environment, source_dir)
        try:
            cur = conn.cursor()
            load.truncate_staging(cur)          # staging is per run (data dictionary §5)
            conn.commit()
            for name in load.LOAD_ORDER:
                for path in extract.discover(source_dir, contract[name]):
                    try:
                        _process_file(conn, run, path, contract[name], settings, blob)
                    except Exception as exc:     # one bad file must not stop the others
                        run.files_failed += 1
                        log.exception("FAILED %s: %s", path.name, exc)
                        cur = conn.cursor()
                        load.insert_manifest(cur, run.run_id, name, path, extract.file_sha256(path),
                                             path.stat().st_size, "Failed", None)
                        load.insert_dq(cur, run.run_id, "LOAD-ERR", False, None, None,
                                       f"{path.name}: {str(exc)[:3500]}")
                        conn.commit()
            cur = conn.cursor()
            dq_checks.run_checks(cur, run.run_id, run.rows_read, run.rows_rejected)
            conn.commit()
            run.status = "Partial" if run.files_failed else "Succeeded"
            load.finish_run(conn, run.run_id, run.status, run.files_loaded + run.files_failed,
                            run.rows_read, run.rows_loaded, run.rows_rejected)
        except Exception as exc:
            conn.rollback()
            run.status = "Failed"
            load.finish_run(conn, run.run_id, "Failed", run.files_loaded + run.files_failed, run.rows_read,
                            run.rows_loaded, run.rows_rejected, str(exc)[:4000])
            raise
    log.info("run %d %s: %d files loaded, %d skipped, %d failed; rows read %d, loaded %d, rejected %d, "
             "duplicates dropped %d", run.run_id, run.status, run.files_loaded, run.files_skipped,
             run.files_failed, run.rows_read, run.rows_loaded, run.rows_rejected, run.duplicates_dropped)
    return run


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    p = argparse.ArgumentParser(prog="python -m etl.pipeline", description=__doc__.splitlines()[0])
    p.add_argument("--env", choices=("dev", "prod"), help="required for prod; defaults to ENVIRONMENT for dev")
    p.add_argument("--source-dir", type=Path, default=REPO_ROOT / settings["paths"]["raw_dir"])
    p.add_argument("--triggered-by", choices=("manual", "scheduler"), default="manual")
    p.add_argument("--upload-blob", action="store_true", help="also upload and archive files in Azure Blob")
    p.add_argument("--database", help="override the database name (e.g. QualityKPI_Test)")
    args = p.parse_args(argv)

    load_env()
    from etl.logging_setup import setup
    log_file = setup(REPO_ROOT / settings["paths"]["log_dir"], os.getenv("LOG_LEVEL", "INFO"))
    env = args.env or os.getenv("ENVIRONMENT", "dev").strip().lower()
    if env == "prod" and args.env != "prod":
        log.error("Refusing to load PROD implicitly: pass --env prod explicitly.")
        return 2
    try:
        run = run_pipeline(env, args.source_dir.resolve(), args.triggered_by, args.upload_blob, args.database)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1
    log.info("log file: %s", log_file)
    return 0 if run.status == "Succeeded" else 3


if __name__ == "__main__":
    sys.exit(main())
