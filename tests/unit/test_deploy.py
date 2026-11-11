"""Unit tests for the schema deploy runner (no database needed)."""
import datetime as dt

import pytest

from etl import deploy
from etl.deploy import DeployError, Script


# --- split_batches -----------------------------------------------------------
def test_split_on_go_lines_case_insensitive_with_comments():
    sql = "SELECT 1;\nGO\nSELECT 2;\n  go  \nSELECT 3;\nGO -- end\n"
    assert deploy.split_batches(sql) == ["SELECT 1;", "SELECT 2;", "SELECT 3;"]


def test_split_ignores_go_inside_words_and_lines():
    sql = "GOTO label;\nSELECT 'GO' AS x; -- GO here\nlabel:\nSELECT 1 AS go_flag;"
    assert deploy.split_batches(sql) == [sql]


def test_split_handles_crlf_and_drops_empty_batches():
    assert deploy.split_batches("GO\r\n\r\nSELECT 1;\r\nGO\r\nGO\r\n") == ["SELECT 1;"]


# --- checksum ------------------------------------------------------------------
def test_checksum_ignores_line_endings_and_bom():
    lf = b"SELECT 1;\nGO\n"
    assert deploy.checksum(lf) == deploy.checksum(b"SELECT 1;\r\nGO\r\n")
    assert deploy.checksum(lf) == deploy.checksum(b"\xef\xbb\xbf" + lf)
    assert deploy.checksum(lf) != deploy.checksum(b"SELECT 2;\nGO\n")


# --- discovery (runs against the real sql/ folder) ----------------------------
def test_real_scripts_are_well_formed():
    scripts = deploy.discover_scripts()
    versioned = [s for s in scripts if s.kind == "versioned"]
    assert versioned[0].name == "sql/ddl/000_schema_version.sql", "bootstrap must run first"
    assert [s.name for s in versioned] == sorted(s.name for s in versioned)
    kinds = [s.kind for s in scripts]
    assert kinds == sorted(kinds, key=lambda k: k == "repeatable"), "versioned scripts run before repeatable"
    for s in scripts:
        batches = s.batches()
        assert batches, f"{s.name} has no SQL"
        text = s.path.read_text(encoding="utf-8").upper()
        # Same scripts must run on SQL Express and Azure SQL: no database switching.
        assert "\nUSE " not in text, f"{s.name} contains USE"


def test_repeatable_scripts_use_create_or_alter():
    for s in deploy.discover_scripts():
        if s.kind == "repeatable":
            assert "CREATE OR ALTER" in s.path.read_text(encoding="utf-8").upper(), s.name


def test_discovery_rejects_bad_names_and_duplicate_versions(tmp_path):
    for sub in ("ddl", "etl", "rpt"):
        (tmp_path / sub).mkdir()
    (tmp_path / "ddl" / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "ddl" / "001_b.sql").write_text("SELECT 1;")
    with pytest.raises(DeployError, match="Duplicate version"):
        deploy.discover_scripts(tmp_path)
    (tmp_path / "ddl" / "001_b.sql").unlink()
    (tmp_path / "ddl" / "2_Bad-Name.sql").write_text("SELECT 1;")
    with pytest.raises(DeployError, match="NNN_snake_case"):
        deploy.discover_scripts(tmp_path)


# --- plan ------------------------------------------------------------------------
def test_plan_skips_applied_and_reruns_changed_repeatable(monkeypatch):
    v, r = Script(deploy.VERSIONED_DIR / "x.sql", "versioned"), Script(deploy.SQL_DIR / "etl" / "y.sql", "repeatable")
    monkeypatch.setattr(Script, "checksum", property(lambda self: "new"))
    monkeypatch.setattr(Script, "name", property(lambda self: self.path.name))
    assert deploy.plan([v, r], {}) == [v, r]
    assert deploy.plan([v, r], {"x.sql": "new", "y.sql": "new"}) == []
    assert deploy.plan([v, r], {"x.sql": "new", "y.sql": "old"}) == [r]
    with pytest.raises(DeployError, match="checksum mismatch"):
        deploy.plan([v, r], {"x.sql": "old"})


# --- plant holidays ---------------------------------------------------------------
def test_plant_holidays_from_settings():
    settings = {
        "periods": {"calendar_start": dt.date(2025, 1, 1), "calendar_end": dt.date(2025, 12, 31)},
        "calendar": {
            "annual_holidays_mm_dd": ["01-01", "08-05"],
            "summer_shutdown": [{"start": dt.date(2025, 8, 4), "end": dt.date(2025, 8, 6)},
                                {"start": dt.date(2026, 8, 3), "end": dt.date(2026, 8, 4)}],  # outside range
        },
    }
    assert deploy.plant_holidays(settings) == {
        dt.date(2025, 1, 1): "Annual",
        dt.date(2025, 8, 4): "Shutdown",
        dt.date(2025, 8, 5): "Annual",      # annual wins when it falls inside a shutdown
        dt.date(2025, 8, 6): "Shutdown",
    }


def test_plant_holidays_real_settings_count():
    from etl.config import load_settings
    s = load_settings()
    years = s["periods"]["calendar_end"].year - s["periods"]["calendar_start"].year + 1
    shutdown_days = sum((w["end"] - w["start"]).days + 1 for w in s["calendar"]["summer_shutdown"])
    holidays = deploy.plant_holidays(s)
    assert len(holidays) == years * len(s["calendar"]["annual_holidays_mm_dd"]) + shutdown_days
