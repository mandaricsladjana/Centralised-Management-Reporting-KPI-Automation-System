"""The fictional plant: Danube Automotive Components (D3). All names are invented.

Master data plus the scripted quality story the report should reveal
(blueprint §4 "built-in realism"):
  * a worse night shift on the paint line (L04) until its 8D closes
  * the EV program launch (SOP 2025-09-01) with elevated defects for ~8 weeks
  * a repeat defect: sink marks on L01 return 143 days after their first 8D closed
  * improvement after each successful 8D (failure rate drops on that line/part)
  * a plant-wide improvement trend, run-out of the hatchback program (EOP 2026-06-30)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

D = dt.date

# --- Master data --------------------------------------------------------------------
LINES = [  # line_code, line_name, process_type, area
    ("L01", "Injection Moulding 1", "Injection Moulding", "Hall A"),
    ("L02", "Injection Moulding 2", "Injection Moulding", "Hall A"),
    ("L03", "Body Welding",         "Welding",            "Hall B"),
    ("L04", "Paint Shop",           "Painting",           "Hall C"),
    ("L05", "Assembly 1",           "Assembly",           "Hall D"),
    ("L06", "Assembly 2",           "Assembly",           "Hall D"),
]

PROGRAMS = [  # program_code, name, fictional OEM, segment, SOP, EOP
    ("VP-SUV-C",   "Compact SUV",         "Auron Motors",      "C-SUV",   D(2022, 9, 1), None),
    ("VP-HATCH-B", "Small Hatchback",     "Velora Automotive", "B-Hatch", D(2021, 3, 1), D(2026, 6, 30)),
    ("VP-EV-D",    "Electric Midsize",    "Kestrel EV Works",  "D-EV",    D(2025, 9, 1), None),
]


@dataclass(frozen=True)
class Part:
    part_number: str
    part_name: str
    component_type: str
    program_code: str
    line_code: str
    unit_cost_eur: float
    opportunities: int
    units_per_shift: int


PARTS = [
    Part("DAC-10421", "Door trim insert",       "Trim",          "VP-SUV-C",   "L01", 12.40,  6, 420),
    Part("DAC-10433", "Clip housing",           "Clip",          "VP-HATCH-B", "L01",  3.80,  4, 520),
    Part("DAC-10457", "Air vent bezel EV",      "Trim",          "VP-EV-D",    "L01",  9.60,  6, 300),
    Part("DAC-10512", "Bumper fascia (raw)",    "Bumper",        "VP-SUV-C",   "L02", 38.00,  8, 180),
    Part("DAC-10526", "Wheel arch liner EV",    "Liner",         "VP-EV-D",    "L02", 21.50,  5, 200),
    Part("DAC-20118", "Seat cross member",      "Bracket",       "VP-SUV-C",   "L03", 26.70, 10, 240),
    Part("DAC-20131", "Battery tray bracket",   "Bracket",       "VP-EV-D",    "L03", 31.20, 12, 160),
    Part("DAC-20144", "Tow hook bracket",       "Bracket",       "VP-HATCH-B", "L03", 14.90,  8, 220),
    Part("DAC-30210", "Front bumper, painted",  "Bumper",        "VP-SUV-C",   "L04", 92.50, 14, 150),
    Part("DAC-30224", "Mirror cap, painted",    "Trim",          "VP-HATCH-B", "L04", 18.30,  9, 260),
    Part("DAC-30238", "Front bumper EV, painted", "Bumper",      "VP-EV-D",    "L04", 104.00, 14, 110),
    Part("DAC-40310", "Dashboard module",       "Dashboard",     "VP-SUV-C",   "L05", 245.00, 20, 90),
    Part("DAC-40322", "Centre console",         "Console",       "VP-HATCH-B", "L05", 118.00, 16, 110),
    Part("DAC-40415", "Door panel assembly EV", "Door Panel",    "VP-EV-D",    "L06", 186.00, 18, 80),
    Part("DAC-40427", "Door panel assembly",    "Door Panel",    "VP-SUV-C",   "L06", 172.00, 18, 100),
]

DEFECTS = [  # code, name, category, severity
    ("DIM-01", "Out of tolerance",        "Dimensional", "B"),
    ("DIM-02", "Warpage",                 "Dimensional", "B"),
    ("DIM-03", "Flash",                   "Dimensional", "C"),
    ("SUR-01", "Sink mark",               "Surface",     "C"),
    ("SUR-02", "Scratch",                 "Surface",     "C"),
    ("SUR-03", "Paint run",               "Surface",     "C"),
    ("SUR-04", "Orange peel",             "Surface",     "C"),
    ("SUR-05", "Dirt inclusion",          "Surface",     "B"),
    ("ASM-01", "Missing clip",            "Assembly",    "B"),
    ("ASM-02", "Wrong torque",            "Assembly",    "A"),
    ("ASM-03", "Gap and flush out of spec", "Assembly",  "B"),
    ("MAT-01", "Short shot",              "Material",    "B"),
    ("MAT-02", "Weld porosity",           "Material",    "B"),
    ("FUN-01", "Connector not engaged",   "Functional",  "A"),
    ("FUN-02", "Weld crack",              "Functional",  "A"),
]

# Defect mix per (process, stage): code -> relative weight
DEFECT_MIX = {
    ("Injection Moulding", "EOL"): {"SUR-01": 30, "DIM-03": 22, "DIM-02": 15, "MAT-01": 13, "SUR-02": 12, "DIM-01": 8},
    ("Welding", "EOL"):            {"MAT-02": 40, "DIM-01": 35, "FUN-02": 10, "SUR-02": 15},
    ("Painting", "IPC"):           {"SUR-04": 35, "SUR-03": 30, "SUR-05": 25, "SUR-02": 10},
    ("Painting", "EOL"):           {"SUR-05": 30, "SUR-02": 30, "SUR-03": 20, "SUR-04": 20},
    ("Assembly", "IPC"):           {"ASM-01": 35, "ASM-02": 20, "FUN-01": 25, "SUR-02": 20},
    ("Assembly", "EOL"):           {"ASM-03": 35, "FUN-01": 25, "ASM-01": 25, "SUR-02": 15},
    ("Assembly", "FA"):            {"ASM-03": 40, "SUR-02": 35, "ASM-01": 25},
}

# Base first-pass failure probability per (process, stage)
BASE_FAIL = {
    ("Injection Moulding", "EOL"): 0.024, ("Welding", "EOL"): 0.017,
    ("Painting", "IPC"): 0.028, ("Painting", "EOL"): 0.018,
    ("Assembly", "IPC"): 0.014, ("Assembly", "EOL"): 0.019, ("Assembly", "FA"): 0.007,
}

# Disposition probabilities by severity: Scrap, Rework, Use-as-is
DISPOSITION_BY_SEVERITY = {
    "A": (0.65, 0.35, 0.00),
    "B": (0.32, 0.61, 0.07),     # tuned so COPQ % moves around its 1.5 % target (mixed RAG months)
    "C": (0.14, 0.60, 0.26),
}
RETURN_TO_SUPPLIER_SHARE_MATERIAL = 0.05     # material defects only
REWORK_SUCCESS = 0.90
REWORK_HOURS_PER_UNIT = (0.10, 0.45)         # uniform range
REWORK_MATERIAL_EUR_PER_UNIT = (0.20, 1.50)

SHIFT_FACTOR = {"E": 0.95, "L": 1.00, "N": 1.18}
LINE_SHIFT_FACTOR = {("L04", "N"): 1.35}      # the paint line's night shift is persistently worse
LAUNCH_DAYS = 56                              # ~8 weeks of elevated defects after SOP
LAUNCH_EXTRA = 1.6                            # +160 % on day 0, fading linearly
RUN_OUT_DAYS = 180
PLANT_TREND_IMPROVEMENT = 0.15                # -15 % failure rate over the 24 months


# --- Scripted 8D events -----------------------------------------------------------------
@dataclass(frozen=True)
class Event:
    """A quality problem that raises defects, triggers an 8D and (if closed) improves the line."""
    line_code: str
    part_number: str
    defect_code: str
    problem_source: str           # Internal | Customer complaint | Audit
    issue_start: dt.date          # defects start to rise
    opened: dt.date               # 8D opened (D1)
    closed: dt.date | None        # D8; None = still open at history end
    target_days: int
    fail_multiplier: float = 2.2
    night_only: bool = False
    improvement: float = 0.80     # failure-rate factor for this line/part after closure


EVENTS = [
    Event("L04", "DAC-30210", "SUR-03", "Internal",           D(2025, 1, 6),  D(2025, 1, 20), D(2025, 3, 14), 60,
          fail_multiplier=2.0, night_only=True, improvement=0.75),
    Event("L01", "DAC-10421", "SUR-01", "Internal",           D(2025, 2, 10), D(2025, 2, 24), D(2025, 4, 11), 60),
    Event("L05", "DAC-40310", "ASM-01", "Customer complaint", D(2025, 4, 28), D(2025, 5, 12), D(2025, 6, 27), 45),
    # Repeat of the L01 sink-mark problem: 143 days after the first 8D closed (< 180-day window)
    Event("L01", "DAC-10421", "SUR-01", "Internal",           D(2025, 8, 18), D(2025, 9, 1),  D(2025, 10, 24), 60),
    Event("L06", "DAC-40415", "ASM-03", "Internal",           D(2025, 9, 1),  D(2025, 9, 22), D(2025, 11, 28), 60),
    Event("L05", "DAC-40322", "ASM-02", "Audit",              D(2025, 10, 6), D(2025, 10, 15), D(2025, 12, 12), 60,
          fail_multiplier=1.6),
    Event("L03", "DAC-20131", "FUN-02", "Customer complaint", D(2026, 1, 12), D(2026, 2, 2),  D(2026, 4, 17), 45),
    Event("L04", "DAC-30238", "SUR-05", "Internal",           D(2026, 5, 4),  D(2026, 5, 18), None, 60),
    Event("L02", "DAC-10512", "DIM-02", "Internal",           D(2026, 8, 17), D(2026, 9, 7),  None, 60),
]
# The 'Audit' event above is raised by this customer audit's Major NC.
CUSTOMER_AUDIT_WITH_MAJOR_NC = D(2025, 10, 6)

# --- People (fictional) ------------------------------------------------------------------
OWNERS = {
    "Quality":     ["M. Horvat", "J. Novak", "A. Kovač", "S. Petrović"],
    "Production":  ["L. Varga", "T. Becker", "D. Marković"],
    "Engineering": ["E. Schmidt", "N. Popescu", "I. Tóth"],
    "Maintenance": ["R. Bauer", "K. Jovanović"],
}

# --- Audits -----------------------------------------------------------------------------
STANDARD_REFS = {
    "System":          ["IATF 16949 7.1.5", "IATF 16949 7.2.3", "IATF 16949 8.5.1", "IATF 16949 9.2.2", "IATF 16949 10.2.3"],
    "Process":         ["VDA 6.3 P5.2", "VDA 6.3 P6.2", "VDA 6.3 P6.4", "VDA 6.3 P7.3"],
    "Product":         ["IATF 16949 8.6.2", "IATF 16949 8.7.1"],
    "Layered Process": ["IATF 16949 8.5.1", "IATF 16949 8.5.6"],
    "Customer":        ["IATF 16949 8.5.1", "IATF 16949 8.7.1", "IATF 16949 10.2.3"],
}

FINDING_TEXTS = {
    "Major NC":    ["Reaction plan not followed for an out-of-control SPC chart",
                    "Non-conforming parts released without approved deviation",
                    "Torque verification skipped at safety-relevant station"],
    "Minor NC":    ["Gauge calibration overdue", "Work instruction outdated at station",
                    "Operator training record incomplete", "Control plan not updated after process change",
                    "FIFO not respected in WIP storage", "Rework area not segregated from good parts"],
    "Observation": ["Labelling of quarantine area unclear", "Layered audit performed late",
                    "5S standard partly not maintained", "Visual aid at station faded"],
    "OFI":         ["Error-proofing could replace visual check", "Shared lessons-learned board recommended",
                    "Digital check sheet would reduce transcription errors", "Kanban size could be reduced"],
}

ACTION_TEXTS = {
    "Containment": ["Sort and quarantine stock at plant and in transit", "100 % inspection at end of line",
                    "Block affected batches in ERP"],
    "Corrective":  ["Adjust process parameters and update control plan", "Replace worn tool insert",
                    "Add error-proofing sensor", "Retrain operators on updated work instruction"],
    "Preventive":  ["Transfer fix to sister lines", "Update PFMEA and lessons-learned database",
                    "Add check to layered process audit"],
}
