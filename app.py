"""Streamlit interface for the Azure SQL MCP server."""

import asyncio
import json
import os
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")


async def _call_mcp_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Connect for one request, initialize MCP, and return the tool's result."""
    async with streamablehttp_client(MCP_SERVER_URL) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)

    if result.isError:
        error_text = "\n".join(
            block.text
            for block in result.content
            if getattr(block, "text", None)
        )
        raise RuntimeError(error_text or f"MCP tool {name} returned an error.")

    # SDK versions may expose structured data directly or as JSON text.
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured

    text_blocks = [
        block.text
        for block in result.content
        if getattr(block, "text", None)
    ]
    if len(text_blocks) == 1:
        try:
            return json.loads(text_blocks[0])
        except json.JSONDecodeError:
            return text_blocks[0]
    return text_blocks


def call_mcp_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Bridge Streamlit's synchronous rerun model to the async MCP client."""
    return asyncio.run(_call_mcp_tool(name, arguments))


def _show_result(result: Any) -> None:
    """Render tabular tool output as a dataframe when possible."""
    if isinstance(result, list) and result and all(
        isinstance(row, dict) for row in result
    ):
        st.dataframe(result, use_container_width=True)
    else:
        st.json(result)


st.set_page_config(page_title="Azure SQL Explorer", page_icon="🗄️", layout="wide")
st.title("Azure SQL Explorer")
st.caption("A Streamlit client for the Azure SQL MCP server.")
st.info(
    "The database credentials are read by the MCP server from `.env`. "
    "Only metadata, bounded previews, and row counts are exposed here."
)
st.code(f"MCP endpoint: {MCP_SERVER_URL}", language="text")

with st.sidebar:
    st.header("Available tools")
    st.markdown(
        """
        - **List tables** — discover accessible tables.
        - **Describe table** — inspect columns and primary keys.
        - **Preview rows** — display a bounded sample (maximum 100).
        - **Count rows** — get an exact row count.
        """
    )

list_tab, details_tab, preview_tab, count_tab = st.tabs(
    ["List tables", "Describe table", "Preview rows", "Count rows"]
)

with list_tab:
    st.subheader("Tables in the configured database")
    if st.button("Load tables", key="load_tables"):
        try:
            _show_result(call_mcp_tool("list_database_tables", {}))
        except Exception as error:
            st.error(f"Could not reach the MCP server or list tables: {error}")

with details_tab:
    st.subheader("Inspect a table's structure")
    with st.form("describe_table_form"):
        describe_schema = st.text_input("Schema", value="dbo")
        describe_table_name = st.text_input("Table name")
        describe_submitted = st.form_submit_button("Describe table")
    if describe_submitted:
        try:
            _show_result(
                call_mcp_tool(
                    "describe_database_table",
                    {"schema": describe_schema, "table": describe_table_name},
                )
            )
        except Exception as error:
            st.error(f"Could not describe the table: {error}")

with preview_tab:
    st.subheader("View a safe, limited sample")
    with st.form("preview_table_form"):
        preview_schema = st.text_input("Schema", value="dbo", key="preview_schema")
        preview_table_name = st.text_input("Table name", key="preview_table")
        preview_limit = st.number_input(
            "Rows to show", min_value=1, max_value=100, value=20
        )
        preview_submitted = st.form_submit_button("Preview rows")
    if preview_submitted:
        try:
            _show_result(
                call_mcp_tool(
                    "preview_database_table",
                    {
                        "schema": preview_schema,
                        "table": preview_table_name,
                        "limit": int(preview_limit),
                    },
                )
            )
        except Exception as error:
            st.error(f"Could not preview the table: {error}")

with count_tab:
    st.subheader("Count table rows")
    with st.form("count_table_form"):
        count_schema = st.text_input("Schema", value="dbo", key="count_schema")
        count_table_name = st.text_input("Table name", key="count_table")
        count_submitted = st.form_submit_button("Count rows")
    if count_submitted:
        try:
            _show_result(
                call_mcp_tool(
                    "count_database_table_rows",
                    {"schema": count_schema, "table": count_table_name},
                )
            )
        except Exception as error:
            st.error(f"Could not count table rows: {error}")
