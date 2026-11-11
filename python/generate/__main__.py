"""Synthetic department files for Danube Automotive Components (Phase 5).

    python -m generate                 # 24 months -> data/raw        (git-ignored)
    python -m generate --sample        # March 2025 -> data/sample    (committed)
    python -m generate --seed 7 --out C:/tmp/x

Same seed -> byte-identical files. Every injected data-quality defect and every
spelling variant is listed in <out>/_generation/injected_issues.csv, so the ETL
tests (Phase 6) can prove that each one is caught or standardised.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from etl.config import REPO_ROOT, load_settings
from etl.deploy import plant_holidays
from etl.sources import sources
from generate import dq, model, scenario, writer

log = logging.getLogger("generate")

FRAME_TO_SOURCE = {"production": "production_daily"}
SAMPLE_PERIOD = (dt.date(2025, 3, 1), dt.date(2025, 3, 31))
MANIFEST_DIR = "_generation"


def generate(out_dir: Path, seed: int, start: dt.date, end: dt.date) -> dict:
    settings = load_settings()
    holidays = set(plant_holidays(settings))
    clean = model.generate_clean(seed, settings, start, end, holidays)
    dirty = dq.inject_all(clean, seed, settings["synthetic"]["dq_issue_rate"])
    contract = sources()

    out_dir.mkdir(parents=True, exist_ok=True)
    patterns = [s.file_pattern for s in contract.values()]
    for f in out_dir.iterdir():                      # remove files of a previous run (stale months)
        if f.is_file() and any(fnmatch.fnmatch(f.name, p) for p in patterns):
            f.unlink()

    files, issues = [], []
    for name in sorted(dirty):
        df, src = dirty[name], contract[FRAME_TO_SOURCE.get(name, name)]
        parts = ([(f"production_daily_{m}.xlsx", g) for m, g in df.groupby("_month", sort=True)]
                 if name == "production" else [(src.file_pattern, df)])
        for file_name, part in parts:
            part = part.reset_index(drop=True)
            path = out_dir / file_name
            writer.write_source(part, src, path)
            n_rule = int(part["_rule"].notna().sum())
            files.append({"file": file_name, "source": src.name, "rows": len(part),
                          "rule_issues": n_rule, "sha256": writer.sha256(path)})
            key = src.business_key
            for i, r in part[part["_issue"].notna()].iterrows():
                issues.append({"file": file_name, "source_row_number": i + 2,
                               "business_key": " | ".join("" if writer._is_blank(r[k]) else str(r[k]) for k in key),
                               "issue": r["_issue"], "rule_id": r["_rule"] or "",
                               "expected": ("Standardise" if not r["_rule"] else
                                            "Warn" if r["_rule"] in WARN_RULES else "Reject"),
                               "detail": r["_detail"]})

    manifest_dir = out_dir / MANIFEST_DIR
    manifest_dir.mkdir(exist_ok=True)
    pd.DataFrame(issues).to_csv(manifest_dir / "injected_issues.csv", index=False, lineterminator="\n")
    tx = [f for f in files if not f["file"].startswith("master_")]
    summary = {
        "company": settings["project"]["company"], "seed": seed,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "files": files,
        "transaction_rows": sum(f["rows"] for f in tx),
        "rule_issues": sum(f["rule_issues"] for f in files),
        "rule_issue_rate": round(sum(f["rule_issues"] for f in tx) / max(sum(f["rows"] for f in tx), 1), 4),
        "standardisation_variants": sum(1 for i in issues if i["expected"] == "Standardise"),
        "scripted_8d_events": [{"line": e.line_code, "part": e.part_number, "defect": e.defect_code,
                                "source": e.problem_source, "opened": e.opened.isoformat(),
                                "closed": e.closed.isoformat() if e.closed else None}
                               for e in scenario.EVENTS if start <= e.opened <= end],
    }
    (manifest_dir / "generation.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                                  encoding="utf-8", newline="\n")
    return summary


# Warn rules load the row (data dictionary §7); everything else with a rule is rejected
WARN_RULES = {"VR-GEN-04", "VR-GEN-06", "VR-RWK-02", "VR-8D-01", "VR-8D-02"}


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    p = argparse.ArgumentParser(prog="python -m generate", description=__doc__.splitlines()[0])
    p.add_argument("--sample", action="store_true", help="one month (March 2025) into data/sample")
    p.add_argument("--seed", type=int, default=settings["synthetic"]["random_seed"])
    p.add_argument("--out", type=Path, help="output folder (default: data/raw, or data/sample with --sample)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")

    paths = settings["paths"]
    if args.sample:
        start, end = SAMPLE_PERIOD
        out = args.out or REPO_ROOT / paths["sample_dir"]
    else:
        start, end = settings["periods"]["history_start"], settings["periods"]["history_end"]
        out = args.out or REPO_ROOT / paths["raw_dir"]
    summary = generate(out, args.seed, start, end)
    log.info("%d files, %d transaction rows, %d rule issues (%.1f %%), %d spelling/format variants -> %s",
             len(summary["files"]), summary["transaction_rows"], summary["rule_issues"],
             100 * summary["rule_issue_rate"], summary["standardisation_variants"], out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
