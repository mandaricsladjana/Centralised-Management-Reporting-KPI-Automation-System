"""Contract tests for config/settings.toml.

These guard the values agreed in the blueprint and the data dictionary, so a
careless edit to the config cannot silently change KPI behaviour.
"""
import datetime as dt
import tomllib
from pathlib import Path

import pytest

SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.toml"


@pytest.fixture(scope="module")
def settings() -> dict:
    with SETTINGS_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_required_sections_present(settings):
    required = {"project", "periods", "costs", "shifts", "inspection",
                "calendar", "kpi", "storage", "paths", "synthetic"}
    assert required <= settings.keys()


def test_currency_is_eur(settings):  # decision D2
    assert settings["project"]["currency_code"] == "EUR"


def test_history_is_24_months_inside_calendar(settings):
    p = settings["periods"]
    assert p["calendar_start"] <= p["history_start"] < p["history_end"] <= p["calendar_end"]
    months = (p["history_end"].year - p["history_start"].year) * 12 + (
        p["history_end"].month - p["history_start"].month) + 1
    assert months == 24


def test_labour_rate_positive(settings):
    assert settings["costs"]["rework_labour_rate_eur_per_hour"] > 0


def test_inspection_stages_match_data_dictionary(settings):
    """Data dictionary §3.7: stages per process type, approved in the Phase 2 review."""
    stages = settings["inspection"]["stages_by_process_type"]
    assert stages == {
        "Injection Moulding": ["EOL"],
        "Welding": ["EOL"],
        "Painting": ["IPC", "EOL"],
        "Assembly": ["IPC", "EOL", "FA"],
    }
    valid = {"IPC", "EOL", "FA"}
    for stage_list in stages.values():
        assert set(stage_list) <= valid
        assert stage_list[-1] in {"EOL", "FA"}, "final stage must be EOL or FA"


def test_holidays_are_valid_dates(settings):
    for mm_dd in settings["calendar"]["annual_holidays_mm_dd"]:
        dt.date.fromisoformat(f"2025-{mm_dd}")  # raises if invalid
    for window in settings["calendar"]["summer_shutdown"]:
        assert window["start"] <= window["end"]


def test_dq_issue_rate_is_a_small_fraction(settings):
    assert 0 < settings["synthetic"]["dq_issue_rate"] <= 0.05
