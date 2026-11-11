"""Fixtures for DEV integration tests. Every test's changes are rolled back."""
import pyodbc
import pytest

from dbhelpers import STAGING
from etl.config import ConfigError, db_target, load_env
from etl.db import connect


@pytest.fixture(scope="module")
def db():
    """Module-wide DEV connection; skips the module when DEV is unreachable or not deployed."""
    load_env()
    try:
        c = connect(db_target("dev"))
    except (ConfigError, pyodbc.Error) as exc:
        pytest.skip(f"DEV database not reachable: {exc}")
    if c.cursor().execute("SELECT OBJECT_ID(N'audit.schema_version')").fetchval() is None:
        pytest.skip("DEV schema not deployed: run python -m etl.deploy --env dev")
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def dbcur(db):
    """Cursor with empty staging tables; everything is rolled back after the test."""
    c = db.cursor()
    for t in STAGING:
        c.execute(f"DELETE FROM stg.{t}")
    yield c
    db.rollback()


@pytest.fixture
def run_id(dbcur):
    """A fresh 'Running' pipeline run (rolled back with the test)."""
    return dbcur.execute("SET NOCOUNT ON; INSERT INTO [audit].etl_run (environment, status, triggered_by) "
                         "OUTPUT inserted.etl_run_id VALUES ('dev', 'Running', 'manual')").fetchval()
