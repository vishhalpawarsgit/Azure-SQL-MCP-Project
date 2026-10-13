"""
app.py - the Streamlit web page (the "client" the user sees).

Start it with:   streamlit run client/app.py

How Streamlit works:
    Streamlit runs this whole file from top to bottom every time the user
    clicks a button or changes an input. Code like
        if st.button("Go"):
            ...
    only runs the inner part on the run where the button was clicked.

This page never connects to the database itself. Every button calls an MCP
tool through mcp_client.py, and the server does the database work.
"""

import json

import pandas as pd          # pandas turns our rows into a nice table
import streamlit as st

from mcp_client import call_tool, list_tools
from llm_agent import ask   # the Groq-powered question answering

# ---------------------------------------------------------------------------
# Page title
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Azure SQL MCP Client", layout="wide")
st.title("Azure SQL - MCP Client")
st.caption("This page  ->  MCP client  ->  MCP server  ->  Azure SQL")


# ---------------------------------------------------------------------------
# Small helper functions used by the buttons below
# ---------------------------------------------------------------------------
def run_tool(tool_name, arguments):
    """Call a tool. Show a red error message on the page if it fails.

    Returns the result, or None if something went wrong.
    """
    try:
        # st.spinner shows "Working..." while we wait for the server.
        with st.spinner("Calling tool: " + tool_name):
            return call_tool(tool_name, arguments)
    except Exception as error:
        st.error("Something went wrong: " + str(error))
        return None


def show_table(result):
    """Show a result ({"columns": ..., "rows": ...}) as a table on the page."""
    table = pd.DataFrame(result["rows"], columns=result["columns"])
    st.dataframe(table, use_container_width=True)

    message = str(result["row_count"]) + " row(s)"
    if result["truncated"]:
        message = message + " (more rows exist, but the row limit was reached)"
    st.caption(message)


# ---------------------------------------------------------------------------
# Create four tabs
# ---------------------------------------------------------------------------
tab_ask, tab_connect, tab_browse, tab_query, tab_tools = st.tabs(
    ["Ask in plain English", "1. Connection", "2. Browse tables", "3. Run a query", "4. Available tools"]
)

# ---------------------------------------------------------------------------
# Tab 0: ask a question in normal words (uses the Groq LLM)
# ---------------------------------------------------------------------------
with tab_ask:
    st.subheader("Ask your database a question")
    st.write("Example: *How many tables do I have?* or *Show the 5 newest orders.*")
    st.warning(
        "Privacy: your question, table names, and the rows the LLM looks at are sent "
        "to Groq (an external service). Do not use this tab with confidential data "
        "unless your organisation has approved Groq."
    )

    question = st.text_input("Your question")

    if st.button("Ask") and question.strip() != "":
        try:
            with st.spinner("Thinking..."):
                answer, steps = ask(question)
            st.success(answer)

            # Show every tool the LLM used, so the process is easy to follow.
            with st.expander("How the answer was found (" + str(len(steps)) + " tool calls)"):
                for number, step in enumerate(steps, start=1):
                    st.write(str(number) + ". Tool: " + step["tool"])
                    st.code(json.dumps(step["arguments"]), language="json")
                    st.text(step["result"][:1000])   # show only the start of long results
        except Exception as error:
            st.error("Something went wrong: " + str(error))

# ---------------------------------------------------------------------------
# Tab 1: test the connection
# ---------------------------------------------------------------------------
with tab_connect:
    st.subheader("Test the connection")
    st.write("Click the button to check that the server can reach Azure SQL.")

    if st.button("Test connection"):
        info = run_tool("test_connection", {})
        if info is not None:
            st.success("Connected!")
            st.write("Database:", info["database"])
            st.write("Logged in as:", info["login"])
            st.write("Server version:", info["version"])

# ---------------------------------------------------------------------------
# Tab 2: browse tables
# ---------------------------------------------------------------------------
with tab_browse:
    st.subheader("See what is in your database")

    # --- list all tables ---
    if st.button("List all tables"):
        result = run_tool("list_tables", {})
        if result is not None:
            show_table(result)

    st.divider()  # a thin line to separate the sections

    # --- inspect one table: first type its name ---
    st.write("Type a table name to look inside it:")
    schema = st.text_input("Schema", value="dbo")
    table = st.text_input("Table name")
    rows_to_show = st.number_input("Rows to preview", min_value=1, max_value=500, value=10)

    # Four buttons side by side.
    col1, col2, col3, col4 = st.columns(4)

    if col1.button("Show columns"):
        result = run_tool("describe_table", {"table": table, "schema": schema})
        if result is not None:
            show_table(result)

    if col2.button("Show foreign keys"):
        result = run_tool("get_foreign_keys", {"table": table, "schema": schema})
        if result is not None:
            if result["row_count"] == 0:
                st.info("This table has no foreign keys.")
            else:
                show_table(result)

    if col3.button("Count rows"):
        result = run_tool("count_rows", {"table": table, "schema": schema})
        if result is not None:
            st.metric("Rows in " + result["table"], result["row_count"])

    if col4.button("Preview data"):
        result = run_tool(
            "preview_table",
            {"table": table, "schema": schema, "limit": int(rows_to_show)},
        )
        if result is not None:
            show_table(result)

# ---------------------------------------------------------------------------
# Tab 3: run your own SELECT query
# ---------------------------------------------------------------------------
with tab_query:
    st.subheader("Run a read-only query")
    st.write("Only a single SELECT is allowed. Anything that changes data is refused.")

    query = st.text_area("SQL query", value="SELECT TOP 10 name FROM sys.tables", height=150)
    max_rows = st.slider("Maximum rows", min_value=1, max_value=500, value=100)

    if st.button("Run query"):
        result = run_tool("run_select_query", {"query": query, "max_rows": max_rows})
        if result is not None:
            show_table(result)

# ---------------------------------------------------------------------------
# Tab 4: list the tools the server offers
# ---------------------------------------------------------------------------
with tab_tools:
    st.subheader("Tools offered by the MCP server")
    st.write("This list is fetched live from the server, so new tools appear here automatically.")

    if st.button("Load tool list"):
        try:
            tools = list_tools()
            for tool in tools:
                # st.expander makes a section you can click to open.
                with st.expander(tool["name"]):
                    st.write(tool["description"])
        except Exception as error:
            st.error("Could not reach the MCP server: " + str(error))
