"""Text2SQL generation, execution, caching, and retry orchestration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Protocol
from uuid import uuid4

from strands.tools.mcp import MCPClient

from sherlock.agent import SQLGeneration, create_sql_generation_agent
from sherlock.config import Settings
from sherlock.contracts import MAX_SQL_LENGTH

GENERATOR_TOOLS = ("get_schema", "get_sample_values", "get_database_info")
REPAIRABLE_ERRORS = frozenset(
    {
        "INVALID_SQL",
        "UNSAFE_SQL",
        "MULTIPLE_STATEMENTS",
        "UNKNOWN_RELATION",
        "UNKNOWN_COLUMN",
        "QUERY_TIMEOUT",
    }
)


class Text2SQLError(RuntimeError):
    """Base exception safe for the API layer to translate."""


class SQLGenerationError(Text2SQLError):
    """The model did not produce a usable SQL query."""


def _require_bounded_sql(sql: str) -> str:
    if len(sql) > MAX_SQL_LENGTH:
        raise SQLGenerationError(
            f"The generated SQL exceeds the {MAX_SQL_LENGTH:,} character limit."
        )
    return sql


class QueryExecutionError(Text2SQLError):
    """The MCP server could not execute the generated query."""


@dataclass(frozen=True)
class QueryData:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


@dataclass(frozen=True)
class ExecutionResult:
    sql: str
    data: QueryData | None = None
    error: dict[str, Any] | None = None


@dataclass(frozen=True)
class Text2SQLResult:
    question: str
    sql: str
    result: QueryData
    attempts: int
    cached_sql: bool


class SQLGenerator(Protocol):
    async def generate(self, question: str) -> tuple[str, bool]: ...

    async def repair(
        self,
        question: str,
        previous_sql: str,
        error: dict[str, Any],
    ) -> str: ...


class QueryExecutor(Protocol):
    async def execute(self, sql: str) -> ExecutionResult: ...


class StrandsSQLGenerator:
    """Generate SQL with isolated Strands agents and cache initial translations."""

    def __init__(self, client: MCPClient, *, model: str | None = None) -> None:
        self._client = client
        self._model = model
        self._generate_cached = lru_cache(maxsize=256)(self._generate_uncached)

    async def generate(self, question: str) -> tuple[str, bool]:
        before = self._generate_cached.cache_info()
        sql = await asyncio.to_thread(self._generate_cached, question)
        after = self._generate_cached.cache_info()
        return sql, after.hits > before.hits

    async def repair(
        self,
        question: str,
        previous_sql: str,
        error: dict[str, Any],
    ) -> str:
        prompt = (
            f"Original question:\n{question}\n\n"
            f"The previous SQL failed:\n{previous_sql}\n\n"
            f"Execution error:\n{json.dumps(error, sort_keys=True)}\n\n"
            "Produce a corrected query. Inspect the schema again if necessary."
        )
        return await asyncio.to_thread(self._invoke_agent, prompt)

    def _generate_uncached(self, question: str) -> str:
        return _require_bounded_sql(self._invoke_agent(question))

    def _invoke_agent(self, prompt: str) -> str:
        agent = create_sql_generation_agent(self._client, model=self._model)
        try:
            result = agent(prompt)
            output = result.structured_output
            if not isinstance(output, SQLGeneration):
                raise SQLGenerationError(
                    "The SQL generator returned no structured SQL."
                )
            return output.sql.strip()
        finally:
            agent.cleanup()

    def clear_cache(self) -> None:
        self._generate_cached.cache_clear()


class MCPQueryExecutor:
    """Execute SQL deterministically through the existing MCP security boundary."""

    def __init__(self, client: MCPClient) -> None:
        self._client = client

    async def execute(self, sql: str) -> ExecutionResult:
        result = await self._client.call_tool_async(
            tool_use_id=uuid4().hex,
            name="run_query",
            arguments={"sql": sql},
        )
        if result.get("status") == "error":
            raise QueryExecutionError("The query execution service is unavailable.")

        payload = result.get("structuredContent")
        if not isinstance(payload, dict):
            raise QueryExecutionError("The query execution service returned no data.")
        if isinstance(payload.get("error"), dict):
            return ExecutionResult(sql=sql, error=payload["error"])

        try:
            data = QueryData(
                columns=payload["columns"],
                rows=payload["rows"],
                row_count=payload["row_count"],
                truncated=payload["truncated"],
            )
            normalized_sql = payload["sql"]
        except (KeyError, TypeError) as exc:
            raise QueryExecutionError(
                "The query execution service returned an invalid response."
            ) from exc
        return ExecutionResult(sql=normalized_sql, data=data)


class Text2SQLService:
    """Coordinate SQL generation and deterministic execution."""

    def __init__(
        self,
        generator: SQLGenerator,
        executor: QueryExecutor,
        *,
        max_attempts: int = 3,
        start_callback: Callable[[], Awaitable[Any]] | None = None,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._generator = generator
        self._executor = executor
        self._max_attempts = max_attempts
        self._start_callback = start_callback
        self._close_callback = close_callback
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        """Initialize shared resources once, including under concurrent requests."""

        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            if self._start_callback is not None:
                await self._start_callback()
            self._started = True

    async def query(self, question: str) -> dict[str, Any]:
        await self.start()
        sql, cached_sql = await self._generator.generate(question)
        sql = _require_bounded_sql(sql)

        for attempt in range(1, self._max_attempts + 1):
            execution = await self._executor.execute(sql)
            if execution.data is not None:
                try:
                    normalized_sql = _require_bounded_sql(execution.sql)
                except SQLGenerationError:
                    self._clear_generator_cache()
                    raise
                return asdict(
                    Text2SQLResult(
                        question=question,
                        sql=normalized_sql,
                        result=execution.data,
                        attempts=attempt,
                        cached_sql=cached_sql,
                    )
                )

            error = execution.error or {}
            error_type = error.get("type")
            if error_type not in REPAIRABLE_ERRORS or attempt == self._max_attempts:
                message = error.get("message", "The generated query could not run.")
                raise QueryExecutionError(str(message))
            sql = await self._generator.repair(question, sql, error)
            sql = _require_bounded_sql(sql)

        raise AssertionError("unreachable")

    def close(self) -> None:
        self._clear_generator_cache()
        if self._close_callback is not None:
            self._close_callback()

    def _clear_generator_cache(self) -> None:
        clear_cache = getattr(self._generator, "clear_cache", None)
        if clear_cache is not None:
            clear_cache()


def create_text2sql_service(
    settings: Settings, *, model: str | None = None
) -> Text2SQLService:
    """Build the production Text2SQL service and its shared MCP connection."""

    clients = MCPClient.load_servers(
        {
            "mcpServers": {
                "fraud-analytics": settings.mcp_server_config(
                    allowed_tools=GENERATOR_TOOLS
                )
            }
        }
    )
    if len(clients) != 1:
        raise RuntimeError("Expected exactly one enabled fraud analytics MCP server")

    client = clients[0]
    owner = object()
    client.add_consumer(owner)

    def close_client() -> None:
        client.remove_consumer(owner)

    return Text2SQLService(
        StrandsSQLGenerator(client, model=model),
        MCPQueryExecutor(client),
        start_callback=client.load_tools,
        close_callback=close_client,
    )
