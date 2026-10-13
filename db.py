"""Azure SQL helpers used by the MCP tools.

This module owns connection setup and the small set of read-only database
operations exposed to the client. It deliberately does not offer arbitrary SQL
execution, which keeps the MCP surface easier to understand and safer to use.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import Engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import create_engine

load_dotenv()


def _required_setting(name: str) -> str:
    """Return a required environment value, with an actionable error."""
    value = os.getenv(name)
    if not value:
        raise ValueError(
            f"Missing {name}. Copy .env.example to .env and set this value."
        )
    return value


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create one pooled SQLAlchemy engine from the Azure SQL .env settings."""
    connection_url = URL.create(
        "mssql+pyodbc",
        username=_required_setting("AZURE_SQL_USERNAME"),
        password=_required_setting("AZURE_SQL_PASSWORD"),
        host=_required_setting("AZURE_SQL_SERVER"),
        database=_required_setting("AZURE_SQL_DATABASE"),
        query={
            "driver": os.getenv(
                "AZURE_SQL_DRIVER", "ODBC Driver 18 for SQL Server"
            ),
            "Encrypt": os.getenv("AZURE_SQL_ENCRYPT", "yes"),
            "TrustServerCertificate": os.getenv(
                "AZURE_SQL_TRUST_SERVER_CERTIFICATE", "no"
            ),
        },
    )
    return create_engine(connection_url, pool_pre_ping=True)


def _quote_identifier(identifier: str) -> str:
    """Escape a SQL Server identifier before using it in a query expression."""
    if not identifier or "\x00" in identifier:
        raise ValueError("Schema and table names must be non-empty identifiers.")
    return f"[{identifier.replace(']', ']]')}]"


def list_tables() -> list[dict[str, str]]:
    """List user tables (schema and table name) in the configured database."""
    statement = text(
        """
        SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
    )
    try:
        with get_engine().connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]
    except SQLAlchemyError as error:
        raise RuntimeError(f"Could not list Azure SQL tables: {error}") from error


def describe_table(schema: str, table: str) -> list[dict[str, Any]]:
    """Return column names, types, nullability, and key information for a table."""
    statement = text(
        """
        SELECT
            c.COLUMN_NAME AS column_name,
            c.DATA_TYPE AS data_type,
            c.IS_NULLABLE AS is_nullable,
            c.CHARACTER_MAXIMUM_LENGTH AS max_length,
            CASE WHEN k.COLUMN_NAME IS NULL THEN 0 ELSE 1 END AS is_primary_key
        FROM INFORMATION_SCHEMA.COLUMNS AS c
        LEFT JOIN (
            SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS ku
              ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA = ku.TABLE_SCHEMA
             AND tc.TABLE_NAME = ku.TABLE_NAME
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ) AS k
          ON c.TABLE_SCHEMA = k.TABLE_SCHEMA
         AND c.TABLE_NAME = k.TABLE_NAME
         AND c.COLUMN_NAME = k.COLUMN_NAME
        WHERE c.TABLE_SCHEMA = :schema AND c.TABLE_NAME = :table
        ORDER BY c.ORDINAL_POSITION
        """
    )
    try:
        with get_engine().connect() as connection:
            columns = [
                dict(row)
                for row in connection.execute(
                    statement, {"schema": schema, "table": table}
                ).mappings()
            ]
    except SQLAlchemyError as error:
        raise RuntimeError(
            f"Could not describe table {schema}.{table}: {error}"
        ) from error

    if not columns:
        raise ValueError(f"Table {schema}.{table} was not found.")
    return columns


def preview_table(
    schema: str, table: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Return a bounded sample of rows from a table."""
    if not 1 <= limit <= 100:
        raise ValueError("Preview limit must be between 1 and 100.")

    # Resolve the name using bound values before inserting escaped identifiers
    # into the SELECT statement; SQL parameters cannot represent identifiers.
    describe_table(schema, table)
    qualified_name = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    statement = text(f"SELECT TOP {limit} * FROM {qualified_name}")
    try:
        with get_engine().connect() as connection:
            return [
                dict(row)
                for row in connection.execute(statement).mappings()
            ]
    except SQLAlchemyError as error:
        raise RuntimeError(
            f"Could not preview table {schema}.{table}: {error}"
        ) from error


def count_table_rows(schema: str, table: str) -> int:
    """Return the exact row count for a table."""
    describe_table(schema, table)
    qualified_name = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    statement = text(f"SELECT COUNT_BIG(*) FROM {qualified_name}")
    try:
        with get_engine().connect() as connection:
            return int(connection.execute(statement).scalar_one())
    except SQLAlchemyError as error:
        raise RuntimeError(
            f"Could not count rows in {schema}.{table}: {error}"
        ) from error
