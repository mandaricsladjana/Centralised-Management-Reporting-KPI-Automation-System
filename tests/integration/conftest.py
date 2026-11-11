"""Fixtures for integration tests that need an EMPTY warehouse: they run against the
separate QualityKPI_Test database (reset per module), never DEV, because DEV holds
the 24-month load. Every test's own changes are rolled back."""
import pytest

from dbhelpers import STAGING, TEST_DB, prepare_test_db
from etl.config import db_target
from etl.db import connect


@pytest.fixture(scope="module")
def db():
    prepare_test_db()
    c = connect(db_target("dev", TEST_DB))
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
