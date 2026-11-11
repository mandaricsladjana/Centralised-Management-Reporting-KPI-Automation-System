"""Cross-source data-quality checks after a load (data dictionary §7, audit.dq_result).

Each check measures the whole warehouse (not only this run's files), because the
sources it compares may have been loaded in different runs.
"""
from __future__ import annotations

from etl.load import insert_dq

TOLERANCE = 0.02            # DQ-07 / DQ-08: +-2 % per line and month
MAX_REJECT_SHARE = 0.05     # DQ-10


def _share(cur, sql: str) -> tuple[float | None, int, int]:
    ok, total = cur.execute(sql).fetchone()
    total = total or 0
    return (None if total == 0 else (ok or 0) / total), ok or 0, total


def _reconcile(cur, run_id: int, check_id: str, disposition: str, fact: str, qty: str) -> None:
    """Sum of NC qty with a disposition vs. the department's bookings, per line and month."""
    rows = cur.execute(f"""
        WITH nc AS (SELECT c.year_month, l.line_code, SUM(CAST(f.qty_affected AS BIGINT)) AS q
                    FROM dw.fact_nonconformity f JOIN dw.dim_calendar c ON c.date_key = f.date_key
                    JOIN dw.dim_production_line l ON l.line_key = f.line_key
                    WHERE f.disposition = ? GROUP BY c.year_month, l.line_code),
             bk AS (SELECT c.year_month, l.line_code, SUM(CAST(f.{qty} AS BIGINT)) AS q
                    FROM {fact} f JOIN dw.dim_calendar c ON c.date_key = f.date_key
                    JOIN dw.dim_production_line l ON l.line_key = f.line_key
                    GROUP BY c.year_month, l.line_code)
        SELECT COALESCE(nc.year_month, bk.year_month), COALESCE(nc.line_code, bk.line_code),
               ISNULL(nc.q, 0), ISNULL(bk.q, 0)
        FROM nc FULL JOIN bk ON bk.year_month = nc.year_month AND bk.line_code = nc.line_code""",
                       disposition).fetchall()
    if not rows:
        return
    devs = sorted(((abs(b - a) / a if a else (0.0 if b == 0 else 1.0)), ym, line, a, b) for ym, line, a, b in rows)
    worst = devs[-1][0]
    outside = [d for d in devs if d[0] > TOLERANCE]
    details = (f"{len(outside)} of {len(devs)} line-months outside +-{TOLERANCE:.0%}"
               + ("; worst: " + "; ".join(f"{ym} {line}: NC {a} vs {b} ({d:.1%})"
                                          for d, ym, line, a, b in reversed(outside[-5:])) if outside else ""))
    insert_dq(cur, run_id, check_id, worst <= TOLERANCE, round(worst, 6), TOLERANCE, details)


def run_checks(cur, run_id: int, rows_read: int, rows_rejected: int) -> None:
    # DQ-01: every inspected (date, shift, line, part) exists in production
    share, ok, total = _share(cur, """
        SELECT SUM(CASE WHEN p.production_key IS NOT NULL THEN 1 ELSE 0 END), COUNT(*)
        FROM (SELECT DISTINCT date_key, shift_key, line_key, part_key FROM dw.fact_inspection) i
        LEFT JOIN dw.fact_production p ON p.date_key = i.date_key AND p.shift_key = i.shift_key
                                      AND p.line_key = i.line_key AND p.part_key = i.part_key""")
    if share is not None:
        insert_dq(cur, run_id, "DQ-01", share >= 1.0, round(share, 6), 1.0,
                  f"{total - ok} of {total} inspection keys have no production record")

    # DQ-02: final-stage inspected <= started, per line and month
    share, ok, total = _share(cur, """
        WITH i AS (SELECT c.year_month, f.line_key, SUM(CAST(f.qty_inspected AS BIGINT)) AS q
                   FROM rpt.v_fact_inspection f JOIN dw.dim_calendar c ON c.date_key = f.date_key
                   WHERE f.is_final_stage = 1 GROUP BY c.year_month, f.line_key),
             p AS (SELECT c.year_month, f.line_key, SUM(CAST(f.qty_started AS BIGINT)) AS q
                   FROM dw.fact_production f JOIN dw.dim_calendar c ON c.date_key = f.date_key
                   GROUP BY c.year_month, f.line_key)
        SELECT SUM(CASE WHEN i.q <= ISNULL(p.q, 0) THEN 1 ELSE 0 END), COUNT(*)
        FROM i LEFT JOIN p ON p.year_month = i.year_month AND p.line_key = i.line_key""")
    if share is not None:
        insert_dq(cur, run_id, "DQ-02", share >= 1.0, round(share, 6), 1.0,
                  f"{total - ok} of {total} line-months inspect more units than were started")

    _reconcile(cur, run_id, "DQ-07", "Scrap", "dw.fact_scrap", "qty_scrapped")
    _reconcile(cur, run_id, "DQ-08", "Rework", "dw.fact_rework", "qty_reworked")

    # DQ-LNK-NC: NC IDs listed in this run's 8D tracker that are still not in the warehouse
    listed, missing = cur.execute("""
        SELECT COUNT(*), SUM(CASE WHEN nc.nc_key IS NULL THEN 1 ELSE 0 END)
        FROM stg.eight_d_nc_link k LEFT JOIN dw.fact_nonconformity nc ON nc.nc_id = k.nc_id""").fetchone()
    if listed:
        insert_dq(cur, run_id, "DQ-LNK-NC", not missing, missing or 0, 0,
                  f"{missing or 0} of {listed} NC IDs listed in the 8D tracker are not in the warehouse "
                  "(rejected or not yet delivered); they are linked when a later tracker lists them again")

    # DQ-10: rejected rows / rows read in this run
    if rows_read:
        share = rows_rejected / rows_read
        insert_dq(cur, run_id, "DQ-10", share <= MAX_REJECT_SHARE, round(share, 6), MAX_REJECT_SHARE,
                  f"{rows_rejected} of {rows_read} rows rejected in this run")
