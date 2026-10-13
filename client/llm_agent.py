"""
llm_agent.py - lets you ask questions in plain English.

The idea (this is called a "tool-calling agent"):
    1. We send your question to the Groq LLM, together with the list of MCP
       tools the server offers.
    2. The LLM does NOT touch the database. It only replies with
       "please call tool X with these arguments".
    3. WE run that tool on the MCP server and send the result back to the LLM.
    4. Steps 2-3 repeat until the LLM has enough information and writes
       the final answer in normal words.

    You  ->  Groq LLM  ->  "call list_tables"  ->  MCP server  ->  Azure SQL
                       <-  result goes back to the LLM  <-

Settings (both are REQUIRED and are read from the .env file):
    GROQ_API_KEY  - your key from https://console.groq.com
    GROQ_MODEL    - the Groq model name to use (it must support tool calling)
"""

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq                        # the official Groq library
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from mcp_client import server_settings       # how to start our MCP server

# Read the .env file in the project root (one folder above client/).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# The LLM may call tools several times. This stops it from looping forever.
MAX_STEPS = 8

# Tool results can be long. We cut them to save tokens (= money and time).
MAX_RESULT_CHARS = 6000

# The instructions that tell the LLM how to behave.
SYSTEM_PROMPT = (
    "You are an assistant that answers questions about an Azure SQL database. "
    "Use the tools to find out what tables and columns exist - never guess names. "
    "A good order is: list_tables, then describe_table, then run_select_query. "
    "The database is SQL Server, so use T-SQL: write SELECT TOP 10 ..., not LIMIT. "
    "You can only read data, not change it. "
    "When you have the answer, explain it briefly in plain English."
)


def make_groq_tools(mcp_tools):
    """Convert MCP tool descriptions into the format that Groq expects."""
    groq_tools = []
    for tool in mcp_tools:
        groq_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,   # says which arguments the tool takes
            },
        })
    return groq_tools


async def _ask_async(question):
    """(async) Run the whole question -> tools -> answer loop."""
    # Read both settings from .env. Stop with a clear message if either is missing.
    api_key = os.getenv("GROQ_API_KEY")
    model_name = os.getenv("GROQ_MODEL")
    if not api_key:
        raise ValueError("Missing GROQ_API_KEY. Please add it to your .env file.")
    if not model_name:
        raise ValueError("Missing GROQ_MODEL. Please add it to your .env file.")
    groq_client = Groq(api_key=api_key)

    # The list of "steps" we show in the page so you can see what happened.
    steps = []

    # Open ONE connection to the MCP server and reuse it for every tool call.
    async with stdio_client(server_settings) as (read_pipe, write_pipe):
        async with ClientSession(read_pipe, write_pipe) as session:
            await session.initialize()

            # Ask the server for its tools and convert them for Groq.
            tool_list = await session.list_tools()
            groq_tools = make_groq_tools(tool_list.tools)

            # "messages" is the whole conversation so far.
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ]

            for _ in range(MAX_STEPS):
                # Ask the LLM what to do next.
                response = groq_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    tools=groq_tools,
                    tool_choice="auto",    # the LLM decides whether it needs a tool
                )
                reply = response.choices[0].message

                # No tool requested = the LLM is done. Its text is the answer.
                if not reply.tool_calls:
                    return reply.content, steps

                # Remember that the LLM asked for tools (Groq needs this in the history).
                requested = []
                for call in reply.tool_calls:
                    requested.append({
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    })
                messages.append({"role": "assistant", "content": reply.content or "", "tool_calls": requested})

                # Run each requested tool on the MCP server.
                for call in reply.tool_calls:
                    tool_name = call.function.name
                    arguments = json.loads(call.function.arguments or "{}")

                    result = await session.call_tool(tool_name, arguments)
                    result_text = result.content[0].text   # the tool's output (or its error message)
                    if len(result_text) > MAX_RESULT_CHARS:
                        result_text = result_text[:MAX_RESULT_CHARS] + " ...(cut)"

                    # Give the result back to the LLM. If the tool failed, the
                    # error text goes back too, so the LLM can fix its mistake.
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result_text})
                    steps.append({"tool": tool_name, "arguments": arguments, "result": result_text})

    return "I could not finish within " + str(MAX_STEPS) + " steps. Try a more specific question.", steps


def ask(question):
    """Answer a question. Returns (answer_text, list_of_steps)."""
    try:
        return asyncio.run(_ask_async(question))
    except BaseException as error:
        # The MCP library runs things in a "TaskGroup". If anything fails inside
        # it, the real error is hidden inside a wrapper saying
        # "unhandled errors in a TaskGroup". A wrapper has a list called
        # .exceptions, so we open wrappers until we reach the real error.
        while hasattr(error, "exceptions") and len(error.exceptions) > 0:
            error = error.exceptions[0]
        # Raise just the real error, so the page can show a useful message.
        raise error
