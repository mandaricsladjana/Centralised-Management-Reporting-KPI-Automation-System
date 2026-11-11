"""SQL Server / Azure SQL connections (pyodbc, ODBC Driver 18)."""
from __future__ import annotations

import struct

import pyodbc

from etl.config import DbTarget

ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
SQL_COPT_SS_ACCESS_TOKEN = 1256            # msodbcsql pre-connect attribute for an Entra access token
AZURE_SQL_SCOPE = "https://database.windows.net/.default"


def connection_string(target: DbTarget) -> str:
    """ODBC connection string. Pure function, so it can be unit-tested without a server."""
    parts = [f"Driver={{{ODBC_DRIVER}}}", f"Server={target.server}", f"Database={target.database}",
             "Encrypt=yes", "Connection Timeout=30"]
    if target.auth == "windows":
        # Local SQL Express uses a self-signed certificate.
        parts += ["Trusted_Connection=yes", "TrustServerCertificate=yes"]
    else:
        parts += ["TrustServerCertificate=no"]
        if target.auth == "sql":
            # Braces allow ; and = in the password; a literal } is escaped as }} (ODBC rule).
            pwd = (target.password or "").replace("}", "}}")
            parts += [f"UID={target.username}", f"PWD={{{pwd}}}"]
    return ";".join(parts) + ";"


def _entra_token_struct() -> bytes:
    """Access token from the Azure CLI login (az login), packed for msodbcsql."""
    from azure.identity import AzureCliCredential   # imported lazily: only PROD needs it

    token = AzureCliCredential().get_token(AZURE_SQL_SCOPE).token.encode("utf-16-le")
    return struct.pack(f"<I{len(token)}s", len(token), token)


def connect(target: DbTarget, autocommit: bool = False) -> pyodbc.Connection:
    kwargs = {}
    if target.auth == "entra":
        kwargs["attrs_before"] = {SQL_COPT_SS_ACCESS_TOKEN: _entra_token_struct()}
    return pyodbc.connect(connection_string(target), autocommit=autocommit, **kwargs)
