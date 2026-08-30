"""Strands agents wired to the fraud analytics MCP server."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from strands import Agent
from strands.tools.mcp import MCPClient

from sherlock.config import Settings

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

SQL_GENERATION_PROMPT = """
You are a careful SQLite query planner for a fraud analytics application.
Translate the user's natural-language question into exactly one read-only SQLite
SELECT or WITH query.

Before writing SQL, inspect get_schema. Use get_sample_values only when actual
values are needed to resolve ambiguity, and use get_database_info only when its
compact coverage metadata is relevant. Prefer the canonical fraud_transactions
relation. Do not invent relations or columns.

Return the SQL query as structured output. Do not execute it. Never produce
INSERT, UPDATE, DELETE, DDL, PRAGMA, ATTACH, or multiple statements. is_fraud is
1 for fraud, 0 for non-fraud, and NULL when unlabelled.
""".strip()

RULE_GENERATION_PROMPT = """
You are a fraud-rule specialist. Translate the user's instruction into exactly
one SQLite WHERE predicate for the canonical fraud_transactions relation.

Before writing the predicate, inspect get_schema. Use get_sample_values when a
categorical value is needed. Return only a predicate as structured output, not
a SELECT statement. Never include comments, semicolons, subqueries, mutation,
DDL, PRAGMA, or ATTACH. Prefer readable USD and derived columns such as
amount_usd when they match the user's language. Generated rules are candidate
decision-support artifacts, not production decisions.
""".strip()


class SQLGeneration(BaseModel):
    """Structured output produced by the SQL-generation agent."""

    sql: str = Field(min_length=1)


class RuleGeneration(BaseModel):
    """Structured output produced by the candidate-rule agent."""

    rule: str = Field(min_length=1)


def create_sql_generation_agent(client: MCPClient) -> Agent:
    """Create an isolated agent that can inspect metadata but cannot execute SQL."""

    return Agent(
        system_prompt=SQL_GENERATION_PROMPT,
        tools=[client],
        structured_output_model=SQLGeneration,
        callback_handler=None,
        name="sherlock-sql-generator",
    )


def create_rule_generation_agent(client: MCPClient) -> Agent:
    """Create a stateless specialist that can inspect metadata but not query data."""

    return Agent(
        system_prompt=RULE_GENERATION_PROMPT,
        tools=[client],
        structured_output_model=RuleGeneration,
        callback_handler=None,
        name="sherlock-rule-agent",
        description="Generates schema-grounded candidate fraud-rule predicates.",
    )


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
    tools: list[Any] = list(clients)
    return Agent(system_prompt=SYSTEM_PROMPT, tools=tools)
