"""Configuration: secrets from .env, non-secret settings from config/settings.toml.

.env is always loaded with override=True so stale shell variables (for example
those injected by VS Code's python.envFile) can never win over the file
(documentation/03_azure_infrastructure.md, issue I-04).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "config" / "settings.toml"
ENV_PATH = REPO_ROOT / ".env"

ENVIRONMENTS = ("dev", "prod")


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def load_env(path: Path = ENV_PATH) -> None:
    """Load .env into os.environ; values in the file win over the shell."""
    load_dotenv(path, override=True)


@cache
def load_settings(path: Path = SETTINGS_PATH) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


@dataclass(frozen=True)
class DbTarget:
    """Where and how to connect. Holds no secrets except an optional SQL password."""

    environment: str        # dev | prod
    server: str
    database: str
    auth: str               # windows | entra | sql
    username: str | None = None
    password: str | None = None

    def describe(self) -> str:
        """Safe for logs: never includes the password."""
        user = f" as {self.username}" if self.username else ""
        return f"{self.environment}: {self.server}/{self.database} ({self.auth}{user})"


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("<"):
        raise ConfigError(f"{name} is not set in .env (see .env.example)")
    return value


def db_target(environment: str | None = None) -> DbTarget:
    """Build the connection target for an environment from environment variables.

    DEV  = local SQL Server Express, Windows authentication.
    PROD = Azure SQL, AZURE_SQL_AUTH=entra (az login token) or sql (etl_loader), D5.
    """
    env = (environment or os.getenv("ENVIRONMENT", "dev")).strip().lower()
    if env not in ENVIRONMENTS:
        raise ConfigError(f"ENVIRONMENT must be one of {ENVIRONMENTS}, got {env!r}")

    if env == "dev":
        return DbTarget("dev", _require("LOCAL_SQL_SERVER"), _require("LOCAL_SQL_DATABASE"), "windows")

    auth = os.getenv("AZURE_SQL_AUTH", "entra").strip().lower()
    server, database = _require("AZURE_SQL_SERVER"), _require("AZURE_SQL_DATABASE")
    if auth == "entra":
        return DbTarget("prod", server, database, "entra")
    if auth == "sql":
        return DbTarget("prod", server, database, "sql",
                        username=_require("AZURE_SQL_USERNAME"), password=_require("AZURE_SQL_PASSWORD"))
    raise ConfigError(f"AZURE_SQL_AUTH must be 'entra' or 'sql', got {auth!r}")
