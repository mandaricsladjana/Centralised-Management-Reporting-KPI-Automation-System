"""Clean ("true") data for every source, in standardised column names.

Data-quality defects are injected afterwards by generate.dq, so this module can
be reasoned about (and tested) as the ground truth. Everything is driven by one
numpy Generator, consumed in a fixed order: same seed -> same data.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import numpy as np
import pandas as pd

from generate import scenario as sc

SHIFTS = ("E", "L", "N")
ONE_DAY = dt.timedelta(days=1)


# --------------------------------------------------------------------------- calendar
def working_days(start: dt.date, end: dt.date, holidays: set[dt.date]) -> list[dt.date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            days.append(d)
        d += ONE_DAY
    return days


# --------------------------------------------------------------------------- masters
def masters() -> dict[str, pd.DataFrame]:
    return {
        "master_lines": pd.DataFrame(
            [(c, n, p, a, True) for c, n, p, a in sc.LINES],
            columns=["line_code", "line_name", "process_type", "area", "is_active"]),
        "master_programs": pd.DataFrame(
            sc.PROGRAMS, columns=["program_code", "program_name", "oem_customer", "vehicle_segment",
                                  "sop_date", "eop_date"]),
        "master_parts": pd.DataFrame(
            [(p.part_number, p.part_name, p.component_type, p.program_code, p.line_code,
              p.unit_cost_eur, p.opportunities, True) for p in sc.PARTS],
            columns=["part_number", "part_name", "component_type", "program_code", "primary_line_code",
                     "standard_unit_cost_eur", "opportunities_per_unit", "is_active"]),
        "master_defects": pd.DataFrame(
            sc.DEFECTS, columns=["defect_code", "defect_name", "defect_category", "severity"]),
    }


# --------------------------------------------------------------------------- production & quality
class Plant:
    def __init__(self, rng: np.random.Generator, settings: dict, start: dt.date, end: dt.date,
                 holidays: set[dt.date]):
        self.rng, self.start, self.end = rng, start, end
        self.days = working_days(start, end, holidays)
        self.total_days = max((end - start).days, 1)
        self.stages = settings["inspection"]["stages_by_process_type"]
        self.labour_rate = settings["costs"]["rework_labour_rate_eur_per_hour"]
        self.process = {c: p for c, _, p, _ in sc.LINES}
        self.programs = {p[0]: p for p in sc.PROGRAMS}
        self.severity = {code: sev for code, _, _, sev in sc.DEFECTS}
        self.category = {code: cat for code, _, cat, _ in sc.DEFECTS}
        self.parts_by_line = defaultdict(list)
        for p in sc.PARTS:
            self.parts_by_line[p.line_code].append(p)

    # -- volume -----------------------------------------------------------------------
    def active(self, part: sc.Part, day: dt.date) -> bool:
        _, _, _, _, sop, eop = self.programs[part.program_code]
        return sop <= day and (eop is None or day <= eop)

    def volume_factor(self, part: sc.Part, day: dt.date) -> float:
        _, _, _, _, sop, eop = self.programs[part.program_code]
        f = 1.0
        since_sop = (day - sop).days
        if since_sop < sc.LAUNCH_DAYS:                       # launch ramp-up 40 % -> 100 %
            f *= 0.4 + 0.6 * since_sop / sc.LAUNCH_DAYS
        if eop is not None and (eop - day).days < sc.RUN_OUT_DAYS:   # run-out: down to 50 %
            f *= 0.5 + 0.5 * (eop - day).days / sc.RUN_OUT_DAYS
        return f

    # -- quality ----------------------------------------------------------------------
    def fail_probability(self, line: str, process: str, stage: str, shift: str, day: dt.date,
                         part: sc.Part, noise: float) -> tuple[float, list[sc.Event]]:
        p = sc.BASE_FAIL[(process, stage)] * sc.SHIFT_FACTOR[shift] * sc.LINE_SHIFT_FACTOR.get((line, shift), 1.0)
        p *= 1 - sc.PLANT_TREND_IMPROVEMENT * (day - self.start).days / self.total_days
        since_sop = (day - self.programs[part.program_code][4]).days
        if 0 <= since_sop < sc.LAUNCH_DAYS:
            p *= 1 + sc.LAUNCH_EXTRA * (1 - since_sop / sc.LAUNCH_DAYS)
        mix = sc.DEFECT_MIX[(process, stage)]
        active = []
        for ev in sc.EVENTS:
            if ev.line_code != line or ev.part_number != part.part_number:
                continue
            if ev.closed is not None and day > ev.closed:
                p *= ev.improvement                      # successful 8D: lasting improvement
            effect_end = (ev.closed + dt.timedelta(days=7)) if ev.closed else self.end
            if (ev.issue_start <= day <= effect_end and ev.defect_code in mix
                    and (not ev.night_only or shift == "N")):
                p *= ev.fail_multiplier
                active.append(ev)
        return min(p * noise, 0.5), active

    def defect_split(self, process: str, stage: str, failed: int, active: list[sc.Event]) -> dict[str, int]:
        mix = dict(sc.DEFECT_MIX[(process, stage)])
        for ev in active:
            mix[ev.defect_code] *= 6                     # the event's defect dominates while active
        codes = sorted(mix)
        weights = np.array([mix[c] for c in codes], dtype=float)
        counts = self.rng.multinomial(failed, weights / weights.sum())
        return {c: int(n) for c, n in zip(codes, counts) if n > 0}

    def disposition(self, code: str) -> str:
        if self.category[code] == "Material" and self.rng.random() < sc.RETURN_TO_SUPPLIER_SHARE_MATERIAL:
            return "Return-to-supplier"
        scrap, rework, _ = sc.DISPOSITION_BY_SEVERITY[self.severity[code]]
        u = self.rng.random()
        return "Scrap" if u < scrap else "Rework" if u < scrap + rework else "Use-as-is"

    # -- simulation -------------------------------------------------------------------------
    def simulate(self) -> dict[str, pd.DataFrame]:
        prod, insp, ncs, scrap, rework = [], [], [], [], []
        rng = self.rng
        for day in self.days:
            for line, _, process, _ in sc.LINES:
                noise = float(rng.lognormal(0.0, 0.18))          # day-to-day process variation
                for part in self.parts_by_line[line]:
                    if not self.active(part, day):
                        continue
                    vf = self.volume_factor(part, day)
                    for shift in SHIFTS:
                        planned = max(int(round(part.units_per_shift * vf * rng.normal(1.0, 0.05))), 1)
                        started = int(round(planned * rng.uniform(0.90, 1.01)))
                        prod.append((day, shift, line, part.part_number, planned, started))
                        units_in = int(round(started * rng.uniform(0.97, 1.0)))
                        for stage in self.stages[process]:
                            p, active = self.fail_probability(line, process, stage, shift, day, part, noise)
                            failed = int(rng.binomial(units_in, p)) if units_in > 0 else 0
                            insp.append((day, shift, line, part.part_number, stage, units_in, units_in - failed, failed))
                            continuing = units_in - failed
                            for code, qty in self.defect_split(process, stage, failed, active).items():
                                disp = self.disposition(code)
                                key = (day, shift, line, part.part_number)
                                ncs.append((*key, stage, code, qty, disp))
                                if disp == "Scrap":
                                    if rng.random() > 0.01:              # ~1 % never booked by finance
                                        scrap.append((*key, code, qty, part.unit_cost_eur))
                                elif disp == "Rework":
                                    ok = int(rng.binomial(qty, sc.REWORK_SUCCESS))
                                    hours = round(qty * rng.uniform(*sc.REWORK_HOURS_PER_UNIT), 2) or 0.05
                                    material = qty * rng.uniform(*sc.REWORK_MATERIAL_EUR_PER_UNIT)
                                    cost = round(hours * self.labour_rate + material, 2)
                                    rework.append((*key, code, qty, ok, hours, cost))
                                    continuing += ok
                                elif disp == "Use-as-is":
                                    continuing += qty
                            units_in = continuing
        frames = {
            "production": pd.DataFrame(prod, columns=["event_date", "shift_code", "line_code", "part_number",
                                                      "qty_planned", "qty_started"]),
            "qa_inspections": pd.DataFrame(insp, columns=["event_date", "shift_code", "line_code", "part_number",
                                                          "stage_code", "qty_inspected", "qty_passed_first_time",
                                                          "qty_failed"]),
            "qa_defects": pd.DataFrame(ncs, columns=["event_date", "shift_code", "line_code", "part_number",
                                                     "stage_code", "defect_code", "qty_affected", "disposition"]),
            "scrap_log": pd.DataFrame(scrap, columns=["event_date", "shift_code", "line_code", "part_number",
                                                      "defect_code", "qty_scrapped", "unit_cost_eur"]),
            "rework_log": pd.DataFrame(rework, columns=["event_date", "shift_code", "line_code", "part_number",
                                                        "defect_code", "qty_reworked", "qty_rework_ok",
                                                        "labour_hours", "rework_cost_eur"]),
        }
        frames["qa_defects"].insert(0, "nc_id", yearly_ids(frames["qa_defects"]["event_date"], "NC", 6))
        frames["scrap_log"].insert(0, "scrap_id", yearly_ids(frames["scrap_log"]["event_date"], "SC", 6))
        sl = frames["scrap_log"]
        # Finance's own cost estimate: often left blank (informational, not loaded; D7)
        sl["reported_cost_eur"] = np.where(rng.random(len(sl)) < 0.6,
                                           (sl["qty_scrapped"] * sl["unit_cost_eur"]).round(2), np.nan)
        frames["scrap_log"] = sl.drop(columns="unit_cost_eur")
        frames["rework_log"].insert(0, "rework_id", yearly_ids(frames["rework_log"]["event_date"], "RW", 6))
        return frames


def yearly_ids(dates: pd.Series, prefix: str, width: int) -> list[str]:
    """PREFIX-YYYY-nnnnnn numbered per year in row order (rows are already chronological)."""
    counters: dict[int, int] = defaultdict(int)
    out = []
    for d in dates:
        counters[d.year] += 1
        out.append(f"{prefix}-{d.year}-{counters[d.year]:0{width}d}")
    return out


# --------------------------------------------------------------------------- 8D reports
MILESTONE_GAPS = [(1, 3), (1, 4), (5, 15), (3, 8), (5, 15), (3, 8), (2, 6)]   # D1->D2 ... D7->D8


def milestones(rng: np.random.Generator, d1: dt.date, closed: dt.date | None, end: dt.date,
               slow: bool = False) -> list[dt.date | None]:
    """D1..D8 dates. With a fixed closure date the gaps are scaled to end exactly on it."""
    gaps = [int(rng.integers(lo, hi + 1)) * (1.8 if slow else 1) for lo, hi in MILESTONE_GAPS]
    cum = np.cumsum(gaps)
    if closed is not None:
        cum = cum * (closed - d1).days / cum[-1]
    dates = [d1] + [d1 + dt.timedelta(days=int(round(c))) for c in cum]
    if closed is not None:
        dates[-1] = closed
    return [d if d <= end else None for d in dates]


def eight_ds(rng: np.random.Generator, nc: pd.DataFrame, start: dt.date, end: dt.date) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []   # (opened, line, part, defect, source, closed_or_None, target_days, slow)
    for ev in sc.EVENTS:
        if start <= ev.opened <= end:
            rows.append((ev.opened, ev.line_code, ev.part_number, ev.defect_code, ev.problem_source,
                         ev.closed if ev.closed and ev.closed <= end else None, ev.target_days, False, True))
    scripted = {(e.line_code, e.part_number, e.defect_code) for e in sc.EVENTS}

    # Unscripted 8Ds: every month 1-2 of the worst (line, part, defect) combinations get one
    month = nc.assign(ym=pd.to_datetime(nc["event_date"]).dt.to_period("M"))
    agg = month.groupby(["ym", "line_code", "part_number", "defect_code"], sort=True)["qty_affected"].sum().reset_index()
    for ym, grp in agg.groupby("ym", sort=True):
        grp = grp[[(r.line_code, r.part_number, r.defect_code) not in scripted for r in grp.itertuples()]]
        top = grp.sort_values(["qty_affected", "line_code", "part_number", "defect_code"],
                              ascending=[False, True, True, True]).head(4).reset_index(drop=True)
        n_8d = min(len(top), 1 + int(rng.random() < 0.35))
        for k in sorted(rng.choice(len(top), size=n_8d, replace=False)) if n_8d else []:
            pick = top.iloc[int(k)]
            opened = ym.start_time.date() + dt.timedelta(days=int(rng.integers(10, 26)))
            if opened.weekday() >= 5:
                opened += dt.timedelta(days=7 - opened.weekday())
            if opened > end:
                continue
            source = "Customer complaint" if rng.random() < 0.2 else "Internal"
            rows.append((opened, pick.line_code, pick.part_number, pick.defect_code, source, "auto",
                         45 if source == "Customer complaint" else 60, bool(rng.random() < 0.25), False))

    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    tracker, actions, used_nc = [], [], set()
    counters: dict[int, int] = defaultdict(int)
    nc_by_combo = {k: g for k, g in nc.groupby(["line_code", "part_number", "defect_code"], sort=False)}
    for opened, line, part, defect, source, closed, target_days, slow, is_scripted in rows:
        counters[opened.year] += 1
        eid = f"8D-{opened.year}-{counters[opened.year]:03d}"
        ms = milestones(rng, opened, None if closed == "auto" else closed, end, slow)
        if is_scripted and closed is None:
            ms[-1] = None                                # scripted as still open at history end
        d8 = ms[-1]
        target = opened + dt.timedelta(days=target_days)
        # Linked NCs: same line/part/defect in the 21 days up to opening, not linked elsewhere
        cand = nc_by_combo.get((line, part, defect))
        linked = []
        if cand is not None:
            window = cand[(cand["event_date"] >= opened - dt.timedelta(days=21)) & (cand["event_date"] <= opened)]
            linked = [i for i in window["nc_id"] if i not in used_nc][-12:]
            used_nc.update(linked)
        dept = "Quality" if rng.random() < 0.7 else "Engineering"
        owner = sc.OWNERS[dept][int(rng.integers(0, len(sc.OWNERS[dept])))]
        status = "Closed" if d8 else ("In progress" if ms[3] else "Open")
        tracker.append((eid, opened, part, line, defect, source, owner, dept, *ms, target, status, ", ".join(linked)))
        actions += eight_d_actions(rng, eid, ms, end)
    tracker_df = pd.DataFrame(tracker, columns=[
        "eight_d_id", "opened_date", "part_number", "line_code", "defect_code", "problem_source", "owner_name",
        "owner_department", "d1_date", "d2_date", "d3_date", "d4_date", "d5_date", "d6_date", "d7_date", "d8_date",
        "target_closure_date", "status_reported", "linked_nc_ids"])
    actions_df = pd.DataFrame(actions, columns=["action_id", "eight_d_id", "action_type", "action_description",
                                                "owner_name", "due_date", "completed_date", "status_reported"])
    return tracker_df, actions_df


def eight_d_actions(rng, eid: str, ms: list, end: dt.date) -> list[tuple]:
    d1 = ms[0]
    plan = [("Containment", ms[2] or d1 + dt.timedelta(days=3), 3)]
    plan += [("Corrective", ms[5] or d1 + dt.timedelta(days=30), 14)] * int(rng.integers(1, 3))
    if rng.random() < 0.7:
        plan.append(("Preventive", ms[6] or d1 + dt.timedelta(days=45), 21))
    out = []
    for n, (atype, planned_done, slack) in enumerate(plan, start=1):
        due = planned_done + dt.timedelta(days=int(rng.integers(0, slack)))
        done = planned_done + dt.timedelta(days=int(rng.integers(0, 4)))
        if rng.random() < 0.15:
            done = due + dt.timedelta(days=int(rng.integers(3, 20)))          # late
        if done > end or (ms[-1] is None and rng.random() < 0.3 and atype != "Containment"):
            done = None
        texts = sc.ACTION_TEXTS[atype]
        owner_pool = sc.OWNERS["Engineering"] + sc.OWNERS["Production"] + sc.OWNERS["Maintenance"]
        status = "Done" if done else ("Overdue" if due < end else "Open")
        out.append((f"{eid}-A{n:02d}", eid, atype, texts[int(rng.integers(0, len(texts)))],
                    owner_pool[int(rng.integers(0, len(owner_pool)))], due, done, status))
    return out


# --------------------------------------------------------------------------- audits
CLASS_PROBS = {   # Major, Minor, Observation, OFI
    "System": (0.04, 0.40, 0.30, 0.26), "Process": (0.05, 0.40, 0.30, 0.25),
    "Product": (0.03, 0.45, 0.32, 0.20), "Layered Process": (0.0, 0.25, 0.45, 0.30),
    "Customer": (0.08, 0.50, 0.25, 0.17),
}
CLASSES = ("Major NC", "Minor NC", "Observation", "OFI")
DUE_DAYS = {"Major NC": 30, "Minor NC": 60, "Observation": 90, "OFI": 120}
CLOSE_MEDIAN = {"Major NC": 21, "Minor NC": 40, "Observation": 55, "OFI": 80}


def audit_plan(rng, days: list[dt.date], start: dt.date, end: dt.date) -> list[tuple[dt.date, str, str | None, int]]:
    """(audit_date, audit_type, line_code or None, number_of_findings)."""
    lines = [c for c, *_ in sc.LINES]
    first_wd = lambda y, m, nth: [d for d in days if d.year == y and d.month == m][nth - 1:nth]  # noqa: E731
    plan = []
    months = sorted({(d.year, d.month) for d in days})
    for i, (y, m) in enumerate(months):
        if m == 3:
            plan += [(d, "System", None, int(rng.integers(4, 7))) for d in first_wd(y, m, 6)]
        if m in (1, 4, 7, 10):
            plan += [(d, "Process", lines[i % 6], int(rng.integers(2, 5))) for d in first_wd(y, m, 8)]
        plan += [(d, "Product", lines[int(rng.integers(0, 6))], int(rng.integers(0, 3))) for d in first_wd(y, m, 3)]
        plan += [(d, "Layered Process", lines[int(rng.integers(0, 6))], int(rng.integers(0, 3)))
                 for d in first_wd(y, m, 12)]
    for d, n in ((sc.CUSTOMER_AUDIT_WITH_MAJOR_NC, 3), (dt.date(2026, 6, 15), 3)):
        if start <= d <= end:
            plan.append((d, "Customer", "L05", n))
    return sorted(plan, key=lambda a: (a[0], a[1]))


def audit_findings(rng, days, start: dt.date, end: dt.date, tracker: pd.DataFrame) -> pd.DataFrame:
    audit_8d = tracker.loc[tracker["problem_source"] == "Audit", "eight_d_id"]
    audit_8d = audit_8d.iloc[0] if len(audit_8d) else None
    rows, a_count, f_count = [], defaultdict(int), defaultdict(int)
    for audit_date, atype, line, n in audit_plan(rng, days, start, end):
        a_count[audit_date.year] += 1
        audit_id = f"AU-{audit_date.year}-{a_count[audit_date.year]:03d}"
        for k in range(n):
            cls = CLASSES[int(rng.choice(4, p=CLASS_PROBS[atype]))]
            f_line, eid = line, None
            if atype == "System" and rng.random() < 0.4:
                f_line = sc.LINES[int(rng.integers(0, 6))][0]
            if atype == "Customer" and audit_date == sc.CUSTOMER_AUDIT_WITH_MAJOR_NC and k == 0:
                cls, eid = "Major NC", audit_8d          # this Major NC raised the 'Audit' 8D
            due = audit_date + dt.timedelta(days=DUE_DAYS[cls])
            close_days = int(round(rng.lognormal(np.log(CLOSE_MEDIAN[cls]), 0.4)))
            closed = audit_date + dt.timedelta(days=close_days)
            if closed > end or rng.random() < 0.05:
                closed = None
            f_count[audit_date.year] += 1
            texts, refs = sc.FINDING_TEXTS[cls], sc.STANDARD_REFS[atype]
            owner_pool = [o for v in sc.OWNERS.values() for o in v]
            status = "Closed" if closed else ("Overdue" if due < end else "Open")
            rows.append((f"AF-{audit_date.year}-{f_count[audit_date.year]:04d}", audit_id, audit_date, atype,
                         refs[int(rng.integers(0, len(refs)))], f_line, cls, texts[int(rng.integers(0, len(texts)))],
                         owner_pool[int(rng.integers(0, len(owner_pool)))], due, closed, eid, status))
    return pd.DataFrame(rows, columns=["finding_id", "audit_id", "audit_date", "audit_type", "standard_reference",
                                       "line_code", "classification", "finding_description", "owner_name",
                                       "due_date", "closed_date", "eight_d_id", "status_reported"])


# --------------------------------------------------------------------------- entry point
def generate_clean(seed: int, settings: dict, start: dt.date, end: dt.date,
                   holidays: set[dt.date]) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    frames = masters()
    plant = Plant(rng, settings, start, end, holidays)
    frames.update(plant.simulate())
    frames["eight_d_tracker"], frames["eight_d_actions"] = eight_ds(rng, frames["qa_defects"], start, end)
    frames["audit_findings"] = audit_findings(rng, plant.days, start, end, frames["eight_d_tracker"])
    return frames
