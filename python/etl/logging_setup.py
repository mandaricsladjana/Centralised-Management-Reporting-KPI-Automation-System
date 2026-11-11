"""Console + file logging for pipeline runs (logs/ is git-ignored)."""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup(log_dir: Path, level: str = "INFO") -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"etl_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    for handler in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
    logging.getLogger("azure").setLevel(logging.WARNING)
    return path
