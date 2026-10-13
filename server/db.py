"""
db.py - everything that talks to the Azure SQL database.

Think of this file as the "database helper". server.py (the MCP tools) calls
the functions here, so server.py never has to know how connections work.

IMPORTANT: this server talks to the client through stdout. So never use
print() in the server code - it would break the communication. If you want to
debug, print to stderr instead:  print("hello", file=sys.stderr)
"""

import os
from pathlib import Path

import pyodbc                   # the library that connects Python to SQL Server / Azure SQL
from dotenv import load_dotenv  # reads the .env file and puts its values into os.environ

# ---------------------------------------------------------------------------
# Step 1: load the settings from the .env file
# ---------------------------------------------------------------------------
# __file__ is this file (server/db.py). ".parent" goes up one folder level.
# Two ".parent" calls = the project root folder, where .env lives.
project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

# The maximum number of rows we ever return, even if the caller asks for more.
# os.getenv("NAME", "default") reads a value from .env, or uses the default.
MAX_ROWS = int(os.getenv("MCP_MAX_ROWS", "500"))


def get_setting(name):
    """Read one required setting from .env. Stop with a clear message if missing."""
    value = os.getenv(name)
    if not value:
        raise ValueError("Missing setting '" + name + "'. Please add it to your .env file.")
    return value


# ---------------------------------------------------------------------------
# Step 2: connect to the database
# ---------------------------------------------------------------------------
def get_connection():
    """Open a new connection to Azure SQL using the values from .env."""
    driver = os.getenv("AZURE_SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    server = get_setting("AZURE_SQL_SERVER")
    database = get_setting("AZURE_SQL_DATABASE")
    username = get_setting("AZURE_SQL_USERNAME")
    password = get_setting("AZURE_SQL_PASSWORD")

    # A connection string is one long text with KEY=VALUE pairs separated by ';'
    # The password is wrapped in { } so special characters like ';' don't break it.
    connection_string = (
        "DRIVER={" + driver + "};"
        "SERVER=tcp:" + server + ",1433;"   # 1433 is the standard SQL Server port
        "DATABASE=" + database + ";"
        "UID=" + username + ";"
        "PWD={" + password + "};"
        "Encrypt=yes;"                       # Azure SQL requires an encrypted connection
        "TrustServerCertificate=no;"         # check that the server is really Azure
        "Connection Timeout=30;"             # give up after 30 seconds
    )
    return pyodbc.connect(connection_string)


# ---------------------------------------------------------------------------
# Step 3: run a query and return the result in a simple format
# ---------------------------------------------------------------------------
def make_json_safe(value):
    """Turn a database value into something that can be sent as JSON.

    Numbers, text, True/False and None are already fine. Everything else
    (dates, decimals, binary data...) is simply converted to text.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def run_query(sql, params=(), max_rows=100):
    """Run an SQL query and return the result as a dictionary.

    The result looks like this:
        {"columns": ["id", "name"], "rows": [[1, "Ann"], [2, "Bob"]], "row_count": 2, "truncated": False}

    sql     - the SQL text. Use ? as a placeholder for any value.
    params  - the values for the ? placeholders, e.g. ("dbo", "Customers").
              Passing values this way (instead of gluing them into the SQL text)
              protects us from "SQL injection" attacks.
    max_rows - how many rows to return at most.
    """
    # Never allow more rows than the safety limit from .env
    if max_rows > MAX_ROWS:
        max_rows = MAX_ROWS

    connection = get_connection()
    try:
        cursor = connection.cursor()        # a cursor is the object that runs SQL
        cursor.execute(sql, params)         # send the query to the database

        columns = []
        rows = []
        truncated = False

        # cursor.description is None for statements that return no table.
        if cursor.description is not None:
            for column_info in cursor.description:
                columns.append(column_info[0])   # the first item is the column name

            # Ask for one row MORE than we need. If we get it, we know the
            # result was longer than the limit and we cut it off.
            fetched_rows = cursor.fetchmany(max_rows + 1)
            if len(fetched_rows) > max_rows:
                truncated = True
                fetched_rows = fetched_rows[:max_rows]

            # Convert every value so it can be sent as JSON.
            for row in fetched_rows:
                clean_row = []
                for value in row:
                    clean_row.append(make_json_safe(value))
                rows.append(clean_row)
    finally:
        # "finally" always runs, even if an error happened. So the
        # connection is always closed and never left open by accident.
        connection.close()

    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}


def quote_name(name):
    """Wrap a table/schema name in [square brackets], the SQL Server way.

    A name can't be passed as a ? placeholder, so when we must put it into the
    SQL text we quote it. A ']' inside the name is doubled so it can't escape.
    """
    return "[" + name.replace("]", "]]") + "]"


# ---------------------------------------------------------------------------
# Step 4: check that a query is a safe, read-only SELECT
# ---------------------------------------------------------------------------
# Words that change data, change the structure, or run code. We refuse them.
FORBIDDEN_WORDS = [
    "insert", "update", "delete", "merge", "drop", "alter", "create",
    "truncate", "exec", "execute", "grant", "revoke", "deny", "into",
    "backup", "restore", "shutdown", "openrowset", "openquery", "bulk",
]


def check_read_only(sql):
    """Return the cleaned query if it is a plain SELECT. Otherwise raise ValueError.

    This is an extra safety net, NOT the main protection. The real protection
    is to log in with a database user that only has READ permission.
    """
    query = sql.strip()

    # A single trailing ';' is harmless, so remove it.
    if query.endswith(";"):
        query = query[:-1].strip()

    lowered = query.lower()

    if lowered == "":
        raise ValueError("The query is empty.")

    # Comments can be used to hide dangerous words, so we simply don't allow them.
    if "--" in lowered or "/*" in lowered:
        raise ValueError("Comments (-- or /* */) are not allowed in queries.")

    # A ';' in the middle would let someone run a second statement.
    if ";" in lowered:
        raise ValueError("Only one statement is allowed (found a ';').")

    # The query must begin with SELECT (or WITH, used for common table expressions).
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise ValueError("Only SELECT queries are allowed.")

    # Split the query into words: replace every non-letter/number with a space,
    # then split on spaces. Example: "select a,b from t" -> select a b from t
    cleaned = ""
    for character in lowered:
        if character.isalnum() or character == "_":
            cleaned = cleaned + character
        else:
            cleaned = cleaned + " "
    words = cleaned.split()

    # Refuse the query if it contains any forbidden word.
    for word in words:
        if word in FORBIDDEN_WORDS:
            raise ValueError("The word '" + word + "' is not allowed in queries.")

    return query
