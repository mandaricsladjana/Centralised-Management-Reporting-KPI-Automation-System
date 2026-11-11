"""Unit tests for connection targets and connection strings (no database needed)."""
import pytest

from etl.config import ConfigError, DbTarget, db_target
from etl.db import connection_string


@pytest.fixture
def env(monkeypatch):
    for name in ("ENVIRONMENT", "LOCAL_SQL_SERVER", "LOCAL_SQL_DATABASE", "AZURE_SQL_SERVER",
                 "AZURE_SQL_DATABASE", "AZURE_SQL_AUTH", "AZURE_SQL_USERNAME", "AZURE_SQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCAL_SQL_SERVER", r".\SQLEXPRESS")
    monkeypatch.setenv("LOCAL_SQL_DATABASE", "QualityKPI_Dev")
    monkeypatch.setenv("AZURE_SQL_SERVER", "srv.database.windows.net")
    monkeypatch.setenv("AZURE_SQL_DATABASE", "QualityKPI")
    return monkeypatch


def test_dev_uses_windows_auth_and_trusts_local_certificate(env):
    t = db_target("dev")
    assert (t.environment, t.auth) == ("dev", "windows")
    cs = connection_string(t)
    assert "Trusted_Connection=yes" in cs and "TrustServerCertificate=yes" in cs
    assert "UID=" not in cs and "PWD=" not in cs


def test_environment_defaults_to_env_var(env):
    env.setenv("ENVIRONMENT", "PROD")
    assert db_target().environment == "prod"


def test_prod_entra_has_no_credentials_and_validates_certificate(env):
    t = db_target("prod")
    assert t.auth == "entra"
    cs = connection_string(t)
    assert "TrustServerCertificate=no" in cs and "Encrypt=yes" in cs
    assert "PWD=" not in cs and "Trusted_Connection" not in cs


def test_prod_sql_auth_requires_credentials_and_never_logs_password(env):
    env.setenv("AZURE_SQL_AUTH", "sql")
    with pytest.raises(ConfigError, match="AZURE_SQL_USERNAME"):
        db_target("prod")
    env.setenv("AZURE_SQL_USERNAME", "etl_loader")
    env.setenv("AZURE_SQL_PASSWORD", "p;w}d")
    t = db_target("prod")
    assert "PWD={p;w}}d}" in connection_string(t)
    assert "p;w}d" not in t.describe()


def test_placeholder_values_are_rejected(env):
    env.setenv("AZURE_SQL_SERVER", "<server-name>.database.windows.net")
    with pytest.raises(ConfigError, match="AZURE_SQL_SERVER"):
        db_target("prod")


def test_unknown_environment_and_auth_are_rejected(env):
    with pytest.raises(ConfigError):
        db_target("test")
    env.setenv("AZURE_SQL_AUTH", "kerberos")
    with pytest.raises(ConfigError):
        db_target("prod")


def test_describe_is_safe(env):
    assert DbTarget("prod", "s", "d", "sql", "u", "secret").describe() == "prod: s/d (sql as u)"
