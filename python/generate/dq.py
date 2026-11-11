"""Inject data-quality defects into the clean frames and record every one of them.

Two kinds of injected change, both listed in the issues manifest:
* defects that a validation rule must catch (rule_id = VR-...), about
  settings.toml [synthetic] dq_issue_rate of the rows of each transactional file
* standardisation work (rule_id = None): spelling and date-format variants that
  are valid input and must be mapped, not rejected (config/source_schemas.toml)

Each frame gets helper columns: _order (final row order), _issue, _rule, _detail.
Uses its own random stream, so changing the injection never changes the clean data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from etl.sources import spelling

HELPER_COLUMNS = ["_order", "_issue", "_rule", "_detail", "_locked"]


@dataclass(frozen=True)
class Plan:
    issue: str
    rule: str | None
    share: float        # share of the file's DQ budget (rule issues) or of its rows (standardisation)
    minimum: int = 0


# Rule issues: the shares of one source add up to 1.0 of its DQ budget (dq_issue_rate x rows)
PLANS: dict[str, list[Plan]] = {
    "production": [
        Plan("duplicate_row", "VR-GEN-04", 0.30, 1), Plan("negative_qty", "VR-QTY-01", 0.20, 1),
        Plan("unknown_part", "VR-GEN-06", 0.15, 1), Plan("invalid_date", "VR-GEN-03", 0.10, 1),
        Plan("missing_value", "VR-GEN-02", 0.15, 1), Plan("future_date", "VR-GEN-07", 0.10, 1),
        Plan("line_variant", None, 0.05), Plan("date_text", None, 0.03),
    ],
    "qa_inspections": [
        Plan("duplicate_key_conflict", "VR-GEN-05", 0.25, 1), Plan("passed_gt_inspected", "VR-INS-01", 0.35, 1),
        Plan("negative_qty", "VR-QTY-01", 0.15, 1), Plan("unknown_stage", "VR-GEN-06", 0.10, 1),
        Plan("missing_value", "VR-GEN-02", 0.15, 1),
    ],
    "qa_defects": [
        Plan("unknown_defect", "VR-GEN-06", 0.30, 1), Plan("duplicate_row", "VR-GEN-04", 0.25, 1),
        Plan("negative_qty", "VR-QTY-01", 0.15, 1), Plan("future_date", "VR-GEN-07", 0.15, 1),
        Plan("missing_value", "VR-GEN-02", 0.15, 1),
    ],
    "scrap_log": [
        Plan("duplicate_row", "VR-GEN-04", 0.30, 1), Plan("unknown_defect", "VR-GEN-06", 0.20, 1),
        Plan("negative_qty", "VR-QTY-01", 0.20, 1), Plan("invalid_date", "VR-GEN-03", 0.15, 1),
        Plan("missing_value", "VR-GEN-02", 0.15, 1),
        Plan("line_variant", None, 0.10), Plan("date_text", None, 0.05),
    ],
    "rework_log": [
        Plan("missing_cost", "VR-RWK-02", 0.50, 1), Plan("ok_gt_reworked", "VR-RWK-01", 0.20, 1),
        Plan("duplicate_row", "VR-GEN-04", 0.15, 1), Plan("negative_qty", "VR-QTY-01", 0.15, 1),
        Plan("line_variant", None, 0.10), Plan("shift_variant", None, 0.10),
    ],
    "eight_d_tracker": [
        Plan("closed_without_d8", "VR-8D-02", 0.0, 2), Plan("d8_but_not_closed", "VR-8D-02", 0.0, 1),
        Plan("milestones_out_of_order", "VR-8D-01", 0.0, 2),
    ],
    "eight_d_actions": [Plan("unknown_8d", "VR-8D-03", 0.0, 2), Plan("missing_value", "VR-GEN-02", 0.0, 1)],
    "audit_findings": [
        Plan("unmappable_classification", "VR-AF-01", 0.0, 2), Plan("classification_variant", None, 0.25),
    ],
}

QTY_COLUMN = {"production": "qty_started", "qa_inspections": "qty_failed", "qa_defects": "qty_affected",
              "scrap_log": "qty_scrapped", "rework_log": "qty_reworked"}
DATE_COLUMN = {"production": "event_date", "qa_inspections": "event_date", "qa_defects": "event_date",
               "scrap_log": "event_date", "rework_log": "event_date"}
MISSING_COLUMN = {"production": "shift_code", "qa_inspections": "part_number", "qa_defects": "disposition",
                  "scrap_log": "part_number", "eight_d_actions": "owner_name"}


class Injector:
    def __init__(self, rng: np.random.Generator, rate: float):
        self.rng, self.rate = rng, rate

    def _pick(self, df: pd.DataFrame, n: int, mask: pd.Series | None = None) -> list[int]:
        free = df["_issue"].isna() & ~df["_locked"].astype(bool)
        if mask is not None:
            free &= mask
        idx = df.index[free].to_numpy()
        n = min(n, len(idx))
        return sorted(int(i) for i in self.rng.choice(idx, size=n, replace=False)) if n else []

    @staticmethod
    def _mark(df, i, issue, rule, detail):
        df.loc[i, ["_issue", "_rule", "_detail"]] = [issue, rule, detail]

    def inject(self, name: str, df: pd.DataFrame) -> pd.DataFrame:
        df = df.reset_index(drop=True).astype(object)
        df["_order"] = np.arange(len(df), dtype=float)
        for c in ("_issue", "_rule", "_detail"):
            df[c] = None
        df["_locked"] = False        # originals of duplicates: no later change may break the copy
        extra = []
        budget = self.rate * len(df)
        for plan in PLANS.get(name, []):
            base = plan.share * (budget if plan.rule else len(df))
            n = max(plan.minimum, int(round(base)))
            extra += getattr(self, plan.issue)(name, df, n, plan)
        if extra:
            df = pd.concat([df, pd.DataFrame(extra)], ignore_index=True)
        return df.sort_values("_order", kind="stable").reset_index(drop=True)

    # --- rule issues -------------------------------------------------------------------
    def duplicate_row(self, name, df, n, plan):
        out = []
        for i in self._pick(df, n):
            df.loc[i, "_locked"] = True
            dup = df.loc[i].copy()
            dup["_order"] += 0.5
            dup[["_issue", "_rule", "_detail"]] = [plan.issue, plan.rule, "exact copy of source row above"]
            out.append(dup)
        return out

    def duplicate_key_conflict(self, name, df, n, plan):
        out = []
        for i in self._pick(df, n):
            dup = df.loc[i].copy()
            dup["_order"] += 0.5
            dup["qty_inspected"] += 10
            dup["qty_passed_first_time"] += 10
            self._mark(df, i, plan.issue, plan.rule, "same business key as the next row, different quantities")
            dup[["_issue", "_rule", "_detail"]] = [plan.issue, plan.rule, "same business key as the row above"]
            out.append(dup)
        return out

    def negative_qty(self, name, df, n, plan):
        col = QTY_COLUMN[name]
        for i in self._pick(df, n, df[col].astype(float) > 0):
            df.loc[i, col] = -int(df.loc[i, col])
            self._mark(df, i, plan.issue, plan.rule, f"{col} negative")
        return []

    def unknown_part(self, name, df, n, plan):
        for k, i in enumerate(self._pick(df, n)):
            df.loc[i, "part_number"] = f"DAC-9{k:04d}"           # distinct: never two unknowns on one grain
            self._mark(df, i, plan.issue, plan.rule, "part number not in master_parts")
        return []

    def unknown_defect(self, name, df, n, plan):
        for i in self._pick(df, n):
            df.loc[i, "defect_code"] = "XXX-99"
            self._mark(df, i, plan.issue, plan.rule, "defect code not in master_defects")
        return []

    def unknown_stage(self, name, df, n, plan):
        for i in self._pick(df, n):
            df.loc[i, "stage_code"] = "VIS"
            self._mark(df, i, plan.issue, plan.rule, "stage code not in dim_inspection_stage")
        return []

    def invalid_date(self, name, df, n, plan):
        col = DATE_COLUMN[name]
        for i in self._pick(df, n):
            d = df.loc[i, col]
            df.loc[i, col] = f"31.02.{d.year}" if i % 2 else f"{d.year}-13-{d.day:02d}"
            self._mark(df, i, plan.issue, plan.rule, "date does not exist")
        return []

    def future_date(self, name, df, n, plan):
        col = DATE_COLUMN[name]
        for i in self._pick(df, n):
            d = df.loc[i, col]
            df.loc[i, col] = dt.date(2030, d.month, min(d.day, 28))
            self._mark(df, i, plan.issue, plan.rule, "event date in the future")
        return []

    def missing_value(self, name, df, n, plan):
        col = MISSING_COLUMN[name]
        for i in self._pick(df, n):
            df.loc[i, col] = None
            self._mark(df, i, plan.issue, plan.rule, f"mandatory {col} empty")
        return []

    def passed_gt_inspected(self, name, df, n, plan):
        for i in self._pick(df, n):
            df.loc[i, "qty_passed_first_time"] = int(df.loc[i, "qty_inspected"]) + int(self.rng.integers(1, 6))
            self._mark(df, i, plan.issue, plan.rule, "passed first time > inspected")
        return []

    def missing_cost(self, name, df, n, plan):
        for i in self._pick(df, n):
            df.loc[i, "rework_cost_eur"] = None
            self._mark(df, i, plan.issue, plan.rule, "rework cost blank: impute hours x rate")
        return []

    def ok_gt_reworked(self, name, df, n, plan):
        for i in self._pick(df, n):
            df.loc[i, "qty_rework_ok"] = int(df.loc[i, "qty_reworked"]) + int(self.rng.integers(1, 4))
            self._mark(df, i, plan.issue, plan.rule, "qty OK after rework > qty reworked")
        return []

    def closed_without_d8(self, name, df, n, plan):
        for i in self._pick(df, n, df["d8_date"].notna()):
            df.loc[i, "d8_date"] = None
            self._mark(df, i, plan.issue, plan.rule, "status Closed but D8 empty: stays open (D8 is the truth)")
        return []

    def d8_but_not_closed(self, name, df, n, plan):
        for i in self._pick(df, n, df["d8_date"].notna()):
            df.loc[i, "status_reported"] = "In progress"
            self._mark(df, i, plan.issue, plan.rule, "D8 set but status not Closed")
        return []

    def milestones_out_of_order(self, name, df, n, plan):
        for i in self._pick(df, n, df["d4_date"].notna() & df["d5_date"].notna()):
            df.loc[i, ["d4_date", "d5_date"]] = [df.loc[i, "d5_date"] + dt.timedelta(days=3), df.loc[i, "d4_date"]]
            self._mark(df, i, plan.issue, plan.rule, "D5 before D4")
        return []

    def unknown_8d(self, name, df, n, plan):
        for i in self._pick(df, n):
            df.loc[i, "eight_d_id"] = "8D-2023-999"
            self._mark(df, i, plan.issue, plan.rule, "8D ID not in tracker")
        return []

    def unmappable_classification(self, name, df, n, plan):
        for k, i in enumerate(self._pick(df, n)):
            df.loc[i, "classification"] = ("Critical?", "Major/Minor")[k % 2]
            self._mark(df, i, plan.issue, plan.rule, "classification cannot be mapped")
        return []

    # --- standardisation (valid input that must be mapped) ---------------------------------
    def _variant(self, df, n, col, kind, issue):
        variants = spelling(kind)
        for i in self._pick(df, n, df[col].notna()):
            options = variants[df.loc[i, col]][1:]
            df.loc[i, col] = options[int(self.rng.integers(0, len(options)))]
            self._mark(df, i, issue, None, f"{col} spelling variant")
        return []

    def line_variant(self, name, df, n, plan):
        return self._variant(df, n, "line_code", "line_code", plan.issue)

    def shift_variant(self, name, df, n, plan):
        return self._variant(df, n, "shift_code", "shift_code", plan.issue)

    def classification_variant(self, name, df, n, plan):
        return self._variant(df, n, "classification", "classification", plan.issue)

    def date_text(self, name, df, n, plan):
        col = DATE_COLUMN[name]
        for k, i in enumerate(self._pick(df, n)):
            d = df.loc[i, col]
            df.loc[i, col] = d.strftime("%d.%m.%Y") if k % 2 == 0 else d.strftime("%d/%m/%Y")
            self._mark(df, i, plan.issue, None, "date written as text in an accepted format")
        return []


def inject_all(frames: dict[str, pd.DataFrame], seed: int, rate: float) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng([seed, 1])      # separate stream from the clean data
    inj = Injector(rng, rate)
    out = {}
    for name in sorted(frames):
        df = frames[name]
        if name == "production":
            df = df.assign(_month=[d.strftime("%Y_%m") for d in df["event_date"]])
        out[name] = inj.inject(name, df)
    return out
