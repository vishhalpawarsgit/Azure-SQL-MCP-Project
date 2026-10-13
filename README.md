# Azure SQL · MCP Server + Streamlit Client

```
┌──────────────┐  call_tool()   ┌──────────────┐  stdio (JSON)  ┌──────────────┐  ODBC/TLS  ┌───────────┐
│ Streamlit UI │ ─────────────► │  MCP client  │ ─────────────► │  MCP server  │ ─────────► │ Azure SQL │
│ client/app.py│                │mcp_client.py │  (subprocess)  │server/server │            └───────────┘
└──────────────┘                └──────────────┘                └──────────────┘
```

The UI never sees database credentials. Only the server reads `.env` and talks to Azure SQL.

## Layout

| File | Role |
|------|------|
| `server/server.py` | Defines the MCP tools (the "menu" of what can be done) |
| `server/db.py` | Connection, query execution, JSON shaping, read-only SQL guard |
| `client/mcp_client.py` | Starts the server, performs the MCP handshake, lists/calls tools |
| `client/llm_agent.py` | Sends your question to Groq; runs the MCP tools the LLM asks for |
| `client/app.py` | Streamlit page (Connection, Browse tables, Run a query, Available tools) |
| `.env.example` | Template for your settings |

## Tools

| Tool | What it does |
|------|--------------|
| `test_connection` | Server version, database name, login |
| `list_schemas` | All schemas |
| `list_tables` | Tables (optionally views), filter by schema |
| `describe_table` | Columns, types, nullability, primary key flag |
| `get_foreign_keys` | Tables this table references |
| `preview_table` | First N rows |
| `count_rows` | Row count |
| `run_select_query` | Free-form read-only `SELECT` |

## Setup

1. **ODBC driver** (needed by `pyodbc`). On macOS:
   ```bash
   brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
   brew install unixodbc msodbcsql18
   ```
   Other OSes: https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server
2. **Python environment**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Configure**: `cp .env.example .env` and fill in server, database, username, password.
4. **Azure firewall**: in the Azure portal, add your client IP under *SQL server → Networking*, otherwise the connection is refused.
5. **Run**
   ```bash
   streamlit run client/app.py
   ```

## Ask in plain English (Groq)

Add both `GROQ_API_KEY` and `GROQ_MODEL` (the model name, which must support tool calling) to `.env`, then use the first tab. The LLM never touches the database: it only asks the client to call MCP tools, and the server runs them (read-only). The tab shows each tool call so you can see how the answer was found.

**Privacy:** tool results (table names, columns, rows) are sent to Groq, a third-party service. Get approval before using it with confidential or regulated data, and consider a database user limited to non-sensitive tables.

## Safety model

* `run_select_query` accepts one `SELECT`/`WITH` statement; comments, `;` in the middle, and words like INSERT/UPDATE/DELETE/DROP/EXEC/INTO are rejected.
* Row counts are capped by `MCP_MAX_ROWS`.
* Table/schema names are verified against `INFORMATION_SCHEMA` and bracket-quoted; values are always bound as `?` parameters.
* Errors are passed to the client as plain messages (no custom error handling, to keep the code simple).
* **The keyword filter is defence in depth, not a security boundary.** Connect with a database user that only has `db_datareader`.

## Adding a new tool

Write a normal function in `server/server.py` and put `@mcp.tool()` above it:

```python
@mcp.tool()
def list_views(schema: str = "dbo"):   # always add type hints (str, int, bool)
    """List the views in a schema."""   # <- this text becomes the tool description
    return db.run_query(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = ?",
        (schema,),
    )
```

Restart Streamlit; it shows up in the **Available tools** tab. To use it from the page, add a button in `client/app.py` that calls `run_tool("list_views", {...})`.

## Using the server from other MCP clients (e.g. Claude Desktop)

```json
{ "mcpServers": { "azure-sql": {
    "command": "/absolute/path/.venv/bin/python",
    "args": ["/absolute/path/server/server.py"] } } }
```

## streamlit run client/app.py