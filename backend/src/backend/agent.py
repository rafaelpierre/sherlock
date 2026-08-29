"""Strands agent wired to the fraud analytics MCP server."""

from __future__ import annotations

from strands import Agent
from strands.tools.mcp import MCPClient

from backend.config import Settings

SYSTEM_PROMPT = """
You are a careful fraud analytics assistant. Answer questions using the database
tools supplied by the Fraud Analytics MCP server.

For every question that requires database facts:
1. Inspect get_schema before writing SQL; do not invent relations or columns.
2. Use get_sample_values only when actual values are needed to resolve ambiguity.
3. Translate the user's question into one read-only SQLite SELECT or WITH query.
   Prefer the canonical fraud_transactions relation.
4. Execute the SQL with run_query. Never claim a database result without calling it.
5. Explain the result concisely and mention important filters, units, NULL handling,
   or truncation. is_fraud is 1 for fraud, 0 for non-fraud, and NULL when unlabelled.

Never attempt INSERT, UPDATE, DELETE, DDL, PRAGMA, ATTACH, or multiple statements.
If a tool reports a validation error, correct the query using the schema and retry.
""".strip()


def create_agent(settings: Settings | None = None) -> Agent:
    """Create a Text2SQL agent and connect it to the configured MCP transport."""

    settings = settings or Settings.from_environment()
    clients = MCPClient.load_servers(
        {"mcpServers": {"fraud-analytics": settings.mcp_server_config()}}
    )
    if len(clients) != 1:
        raise RuntimeError("Expected exactly one enabled fraud analytics MCP server")

    # MCPClient is a Strands ToolProvider. Agent discovers MCP tools and owns the
    # connection lifecycle; MCPAgentTool adapters are created by the client.
    return Agent(system_prompt=SYSTEM_PROMPT, tools=clients)
