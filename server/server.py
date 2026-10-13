"""
server.py - the MCP server. It publishes a list of "tools" that a client can call.

What is MCP?
    MCP (Model Context Protocol) is a standard way for a program (the CLIENT)
    to ask another program (the SERVER) "what can you do?" and then say
    "please do this".  Each thing the server can do is called a TOOL.

How do we create a tool?
    1. Write a normal Python function.
    2. Put  @mcp.tool()  on the line above it.
    That's all! FastMCP uses:
        - the function NAME         as the tool name
        - the DOCSTRING (the text in triple quotes) as the tool description
        - the function PARAMETERS   as the tool inputs
    IMPORTANT: always write the type of each parameter (str, int, bool).
    The LLM reads these types. Without them every parameter is treated as
    text, and the LLM's calls get rejected (e.g. "expected string, got boolean").

What happens when something goes wrong?
    We don't catch errors here on purpose. If a tool raises an error, MCP
    automatically sends the error message to the client, which shows it.

You normally don't run this file yourself. The Streamlit app starts it
automatically in the background.
"""

from mcp.server.fastmcp import FastMCP   # the helper that makes building servers easy

import db   # our own file: server/db.py (the database helper)

# Create the server and give it a name.
mcp = FastMCP("azure-sql")


def check_table_exists(schema: str, table: str):
    """Raise an error if the table does not exist. (A normal helper, not a tool.)"""
    result = db.run_query(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
        (schema, table),
    )
    # result["rows"] is empty when nothing matched
    if len(result["rows"]) == 0:
        raise ValueError("Table '" + schema + "." + table + "' was not found.")


# ===========================================================================
# TOOLS - each function below can be called by the client
# ===========================================================================

@mcp.tool()
def test_connection():
    """Check that we can reach Azure SQL. Returns the server version, database name and login name."""
    result = db.run_query("SELECT @@VERSION, DB_NAME(), SUSER_NAME()")
    first_row = result["rows"][0]
    return {
        "version": first_row[0],
        "database": first_row[1],
        "login": first_row[2],
    }


@mcp.tool()
def list_schemas():
    """List all schemas in the database (a schema is a folder that groups tables, e.g. dbo)."""
    return db.run_query("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA ORDER BY SCHEMA_NAME")


@mcp.tool()
def list_tables(schema: str = "", include_views: bool = False):
    """List the tables in the database. Give a schema name to show only that schema. Set include_views=True to also list views."""
    # Start with a query that matches everything...
    sql = "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES WHERE 1 = 1"
    params = []

    # ...then add filters only when they were asked for.
    if not include_views:
        sql = sql + " AND TABLE_TYPE = 'BASE TABLE'"
    if schema != "":
        sql = sql + " AND TABLE_SCHEMA = ?"
        params.append(schema)

    sql = sql + " ORDER BY TABLE_SCHEMA, TABLE_NAME"
    return db.run_query(sql, tuple(params))


@mcp.tool()
def describe_table(table: str, schema: str = "dbo"):
    """Show the columns of a table: name, data type, whether it can be empty (NULL), and whether it is the primary key."""
    check_table_exists(schema, table)

    # This query lists every column of the table. The LEFT JOIN part marks the
    # columns that belong to the PRIMARY KEY (the column that identifies each row).
    sql = """
        SELECT c.COLUMN_NAME,
               c.DATA_TYPE,
               c.CHARACTER_MAXIMUM_LENGTH,
               c.IS_NULLABLE,
               CASE WHEN pk.COLUMN_NAME IS NULL THEN 'no' ELSE 'yes' END AS IS_PRIMARY_KEY
        FROM INFORMATION_SCHEMA.COLUMNS c
        LEFT JOIN (
            SELECT k.TABLE_SCHEMA, k.TABLE_NAME, k.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS t
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
              ON t.CONSTRAINT_NAME = k.CONSTRAINT_NAME
             AND t.TABLE_SCHEMA = k.TABLE_SCHEMA
            WHERE t.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ) pk ON pk.TABLE_SCHEMA = c.TABLE_SCHEMA
            AND pk.TABLE_NAME = c.TABLE_NAME
            AND pk.COLUMN_NAME = c.COLUMN_NAME
        WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ?
        ORDER BY c.ORDINAL_POSITION
    """
    return db.run_query(sql, (schema, table))


@mcp.tool()
def get_foreign_keys(table: str, schema: str = "dbo"):
    """Show which other tables this table points to (its foreign keys)."""
    check_table_exists(schema, table)

    # sys.foreign_key_columns describes each link between two tables:
    # the "parent" side is our table, the "referenced" side is the table it points to.
    sql = """
        SELECT fk.name AS constraint_name,
               pc.name AS column_name,
               rs.name AS referenced_schema,
               rt.name AS referenced_table,
               rc.name AS referenced_column
        FROM sys.foreign_key_columns fkc
        JOIN sys.foreign_keys fk ON fk.object_id = fkc.constraint_object_id
        JOIN sys.tables  pt ON pt.object_id = fkc.parent_object_id
        JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
        JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
        JOIN sys.tables  rt ON rt.object_id = fkc.referenced_object_id
        JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
        JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
        WHERE ps.name = ? AND pt.name = ?
        ORDER BY fk.name
    """
    return db.run_query(sql, (schema, table))


@mcp.tool()
def preview_table(table: str, schema: str = "dbo", limit: int = 10):
    """Show the first rows of a table (default 10) so you can see what the data looks like."""
    check_table_exists(schema, table)

    # Keep the limit between 1 and the safety maximum.
    if limit < 1:
        limit = 1
    if limit > db.MAX_ROWS:
        limit = db.MAX_ROWS

    # A table name can't be a ? placeholder, so we quote it and put it in the text.
    # This is safe because check_table_exists() already proved the name is real.
    full_name = db.quote_name(schema) + "." + db.quote_name(table)
    sql = "SELECT TOP " + str(int(limit)) + " * FROM " + full_name
    return db.run_query(sql, max_rows=limit)


@mcp.tool()
def count_rows(table: str, schema: str = "dbo"):
    """Count how many rows a table has."""
    check_table_exists(schema, table)

    full_name = db.quote_name(schema) + "." + db.quote_name(table)
    result = db.run_query("SELECT COUNT_BIG(*) FROM " + full_name)
    return {"table": schema + "." + table, "row_count": result["rows"][0][0]}


@mcp.tool()
def run_select_query(query: str, max_rows: int = 100):
    """Run your own read-only SQL SELECT query and return the rows. Use T-SQL syntax, e.g. SELECT TOP 10 * FROM dbo.Customers (T-SQL has no LIMIT)."""
    # check_read_only() raises an error if the query is not a plain SELECT.
    safe_query = db.check_read_only(query)
    return db.run_query(safe_query, max_rows=max_rows)


# This block runs only when the file is started directly (not when imported).
if __name__ == "__main__":
    # "stdio" means: talk to the client through standard input/output.
    mcp.run(transport="stdio")
