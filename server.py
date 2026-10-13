"""MCP server that exposes a small, read-only Azure SQL toolkit."""

import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from db import count_table_rows, describe_table, list_tables, preview_table

load_dotenv()

# FastMCP turns these documented Python functions into discoverable MCP tools.
mcp = FastMCP(
    "Azure SQL Explorer",
    host=os.getenv("MCP_SERVER_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_SERVER_PORT", "8000")),
)


@mcp.tool()
def list_database_tables() -> list[dict[str, str]]:
    """List all accessible user tables with their schema and table names."""
    return list_tables()


@mcp.tool()
def describe_database_table(schema: str, table: str) -> list[dict]:
    """Show a table's columns, SQL data types, nullability, and primary keys."""
    return describe_table(schema, table)


@mcp.tool()
def preview_database_table(
    schema: str, table: str, limit: int = 20
) -> list[dict]:
    """Preview at most 100 rows from a table (default: 20 rows)."""
    return preview_table(schema, table, limit)


@mcp.tool()
def count_database_table_rows(schema: str, table: str) -> dict[str, int]:
    """Return the exact row count for a table."""
    return {"row_count": count_table_rows(schema, table)}


if __name__ == "__main__":
    # Streamable HTTP lets the separately run Streamlit process connect locally.
    mcp.run(transport="streamable-http")
