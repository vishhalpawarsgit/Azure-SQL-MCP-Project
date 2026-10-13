"""
mcp_client.py - the part of the client that talks to the MCP server.

The Streamlit app (app.py) calls the two simple functions in this file:
    list_tools()               -> "what tools do you have?"
    call_tool(name, arguments) -> "please run this tool"

About "async" / "await":
    The MCP library is asynchronous (it can wait for the server without
    freezing). Async functions are written with "async def" and must be run
    with asyncio.run(...). To keep things easy, we hide that here, so
    list_tools() and call_tool() can be used like ordinary functions.

How a call works:
    1. Start server/server.py in the background (as a separate process).
    2. Say hello to it (the "handshake").
    3. Ask it something.
    4. Close it again.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# The full path to server/server.py (this file is in client/, so go up one folder).
SERVER_FILE = Path(__file__).resolve().parent.parent / "server" / "server.py"

# How to start the server: "python server/server.py".
# sys.executable is the Python currently running this app, so the server
# uses the same virtual environment and the same installed libraries.
server_settings = StdioServerParameters(command=sys.executable, args=[str(SERVER_FILE)])


async def _list_tools_async():
    """(async) Start the server, ask for its tool list, return a simple list."""
    # Start the server and get the two "pipes" used to talk to it.
    async with stdio_client(server_settings) as (read_pipe, write_pipe):
        # A session is one conversation with the server.
        async with ClientSession(read_pipe, write_pipe) as session:
            await session.initialize()                 # the handshake
            response = await session.list_tools()      # ask: what tools do you have?

            tools = []
            for tool in response.tools:
                tools.append({"name": tool.name, "description": tool.description})
            return tools


async def _call_tool_async(name, arguments):
    """(async) Start the server, run one tool, return the raw result."""
    async with stdio_client(server_settings) as (read_pipe, write_pipe):
        async with ClientSession(read_pipe, write_pipe) as session:
            await session.initialize()
            return await session.call_tool(name, arguments)   # run the tool


def list_tools():
    """Return a list of the server's tools, e.g. [{"name": "list_tables", "description": "..."}]."""
    # asyncio.run() runs an async function and gives back its result.
    return asyncio.run(_list_tools_async())


def call_tool(name, arguments=None):
    """Run a tool on the server and return its result as a Python dictionary."""
    if arguments is None:
        arguments = {}

    result = asyncio.run(_call_tool_async(name, arguments))

    # If the tool raised an error, the server marks the result as an error.
    # We turn that into a normal Python error so the app can show it.
    if result.isError:
        raise RuntimeError(result.content[0].text)

    # Newer versions of MCP give the data ready-made in "structuredContent".
    if result.structuredContent is not None:
        return result.structuredContent

    # Otherwise the data arrives as JSON text, which we convert to a dictionary.
    return json.loads(result.content[0].text)
