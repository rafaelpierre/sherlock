from __future__ import annotations

import asyncio
import threading
from typing import Any, cast

import pytest
from strands.tools.mcp import MCPClient

from sherlock.contracts import MAX_SQL_LENGTH
from sherlock.services.text2sql import (
    ExecutionResult,
    QueryData,
    QueryExecutionError,
    SQLGenerationError,
    StrandsSQLGenerator,
    Text2SQLService,
)


class StubGenerator:
    def __init__(self) -> None:
        self.repairs: list[tuple[str, str, dict[str, Any]]] = []

    async def generate(self, question: str) -> tuple[str, bool]:
        return "SELECT missing FROM fraud_transactions", False

    async def repair(
        self,
        question: str,
        previous_sql: str,
        error: dict[str, Any],
    ) -> str:
        self.repairs.append((question, previous_sql, error))
        return "SELECT COUNT(*) AS count FROM fraud_transactions"


class FixedSQLGenerator:
    def __init__(self, sql: str, *, repaired_sql: str | None = None) -> None:
        self.sql = sql
        self.repaired_sql = repaired_sql or sql

    async def generate(self, question: str) -> tuple[str, bool]:
        return self.sql, False

    async def repair(
        self,
        question: str,
        previous_sql: str,
        error: dict[str, Any],
    ) -> str:
        return self.repaired_sql


class StubExecutor:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = iter(results)
        self.sql: list[str] = []

    async def execute(self, sql: str) -> ExecutionResult:
        self.sql.append(sql)
        return next(self.results)


class ConcurrentProbeGenerator(FixedSQLGenerator):
    def __init__(self) -> None:
        super().__init__("SELECT 1")
        self.active_generations = 0
        self.max_active_generations = 0

    async def generate(self, question: str) -> tuple[str, bool]:
        self.active_generations += 1
        self.max_active_generations = max(
            self.max_active_generations, self.active_generations
        )
        await asyncio.sleep(0)
        self.active_generations -= 1
        return await super().generate(question)


def successful_execution(sql: str = "SELECT 1") -> ExecutionResult:
    return ExecutionResult(
        sql=sql,
        data=QueryData(
            columns=["count"],
            rows=[[10]],
            row_count=1,
            truncated=False,
        ),
    )


def sql_with_length(length: int) -> str:
    prefix = "SELECT 1 -- "
    return prefix + ("x" * (length - len(prefix)))


def test_successful_query_returns_structured_result() -> None:
    service = Text2SQLService(
        StubGenerator(),
        StubExecutor(
            [successful_execution("SELECT COUNT(*) AS count FROM fraud_transactions")]
        ),
    )

    result = asyncio.run(service.query("How many transactions are there?"))

    assert result["attempts"] == 1
    assert result["cached_sql"] is False
    assert result["result"]["rows"] == [[10]]


def test_concurrent_queries_are_serialized_for_the_shared_mcp_client() -> None:
    generator = ConcurrentProbeGenerator()
    service = Text2SQLService(
        generator, StubExecutor([successful_execution(), successful_execution()])
    )

    async def run_queries() -> None:
        await asyncio.gather(
            service.query("first analytical question"),
            service.query("second analytical question"),
        )

    asyncio.run(run_queries())

    assert generator.max_active_generations == 1


def test_cancelled_threaded_generation_holds_query_lock_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = StrandsSQLGenerator(cast(MCPClient, object()))
    worker_started = threading.Event()
    release_worker = threading.Event()
    calls = 0

    def invoke_agent(prompt: str) -> str:
        nonlocal calls
        calls += 1
        worker_started.set()
        release_worker.wait(timeout=1)
        return "SELECT 1"

    monkeypatch.setattr(generator, "_invoke_agent", invoke_agent)
    service = Text2SQLService(generator, StubExecutor([successful_execution()]))

    async def run_queries() -> None:
        first = asyncio.create_task(service.query("first question"))
        assert await asyncio.to_thread(worker_started.wait, 1)
        first.cancel()
        second = asyncio.create_task(service.query("second question"))
        await asyncio.sleep(0)

        assert calls == 1
        assert not second.done()

        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        await second

    asyncio.run(run_queries())


@pytest.mark.parametrize("sql_length", [MAX_SQL_LENGTH - 1, MAX_SQL_LENGTH])
def test_boundary_sized_generated_sql_is_executed_and_returned(sql_length: int) -> None:
    sql = sql_with_length(sql_length)
    executor = StubExecutor([successful_execution(sql)])
    service = Text2SQLService(FixedSQLGenerator(sql), executor)

    result = asyncio.run(service.query("Return one value"))

    assert result["sql"] == sql
    assert executor.sql == [sql]


def test_oversized_generated_sql_is_rejected_before_execution() -> None:
    sql = sql_with_length(MAX_SQL_LENGTH + 1)
    executor = StubExecutor([])
    service = Text2SQLService(FixedSQLGenerator(sql), executor)

    with pytest.raises(SQLGenerationError, match="20,000 character limit"):
        asyncio.run(service.query("Return one value"))

    assert executor.sql == []


def test_oversized_initial_generation_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = StrandsSQLGenerator(cast(MCPClient, object()))
    outputs = iter([sql_with_length(MAX_SQL_LENGTH + 1), "SELECT 1"])
    calls = 0

    def invoke_agent(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return next(outputs)

    monkeypatch.setattr(generator, "_invoke_agent", invoke_agent)

    with pytest.raises(SQLGenerationError, match="20,000 character limit"):
        asyncio.run(generator.generate("Return one value"))

    generated, first_cache_hit = asyncio.run(generator.generate("Return one value"))
    cached, second_cache_hit = asyncio.run(generator.generate("Return one value"))

    assert generated == cached == "SELECT 1"
    assert first_cache_hit is False
    assert second_cache_hit is True
    assert calls == 2


def test_oversized_normalized_sql_is_rejected_before_service_response() -> None:
    generated_sql = "SELECT 1"
    executor = StubExecutor([successful_execution(sql_with_length(MAX_SQL_LENGTH + 1))])
    service = Text2SQLService(FixedSQLGenerator(generated_sql), executor)

    with pytest.raises(SQLGenerationError, match="20,000 character limit"):
        asyncio.run(service.query("Return one value"))

    assert executor.sql == [generated_sql]


def test_oversized_normalized_sql_is_evicted_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = StrandsSQLGenerator(cast(MCPClient, object()))
    outputs = iter(["SELECT 1", "SELECT 2"])
    calls = 0

    def invoke_agent(prompt: str) -> str:
        nonlocal calls
        calls += 1
        return next(outputs)

    monkeypatch.setattr(generator, "_invoke_agent", invoke_agent)
    executor = StubExecutor(
        [
            successful_execution(sql_with_length(MAX_SQL_LENGTH + 1)),
            successful_execution("SELECT 2"),
        ]
    )
    service = Text2SQLService(generator, executor)

    with pytest.raises(SQLGenerationError, match="20,000 character limit"):
        asyncio.run(service.query("Return one value"))
    result = asyncio.run(service.query("Return one value"))

    assert result["sql"] == "SELECT 2"
    assert executor.sql == ["SELECT 1", "SELECT 2"]
    assert calls == 2


def test_oversized_repaired_sql_is_rejected_before_second_execution() -> None:
    initial_sql = "SELECT missing FROM fraud_transactions"
    executor = StubExecutor(
        [
            ExecutionResult(
                sql=initial_sql,
                error={"type": "UNKNOWN_COLUMN", "message": "Unknown column"},
            )
        ]
    )
    service = Text2SQLService(
        FixedSQLGenerator(
            initial_sql,
            repaired_sql=sql_with_length(MAX_SQL_LENGTH + 1),
        ),
        executor,
    )

    with pytest.raises(SQLGenerationError, match="20,000 character limit"):
        asyncio.run(service.query("Return one value"))

    assert executor.sql == [initial_sql]


def test_repairable_execution_error_is_returned_to_generator() -> None:
    generator = StubGenerator()
    executor = StubExecutor(
        [
            ExecutionResult(
                sql="SELECT missing FROM fraud_transactions",
                error={"type": "UNKNOWN_COLUMN", "message": "Unknown column"},
            ),
            successful_execution("SELECT COUNT(*) AS count FROM fraud_transactions"),
        ]
    )
    service = Text2SQLService(generator, executor)

    result = asyncio.run(service.query("How many transactions are there?"))

    assert result["attempts"] == 2
    assert len(generator.repairs) == 1
    assert executor.sql == [
        "SELECT missing FROM fraud_transactions",
        "SELECT COUNT(*) AS count FROM fraud_transactions",
    ]


def test_nonrepairable_execution_error_is_not_retried() -> None:
    generator = StubGenerator()
    service = Text2SQLService(
        generator,
        StubExecutor(
            [
                ExecutionResult(
                    sql="SELECT 1",
                    error={
                        "type": "QUERY_EXECUTION_ERROR",
                        "message": "Database unavailable",
                    },
                )
            ]
        ),
    )

    with pytest.raises(QueryExecutionError, match="Database unavailable"):
        asyncio.run(service.query("How many transactions are there?"))

    assert generator.repairs == []


def test_invalid_attempt_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        Text2SQLService(StubGenerator(), StubExecutor([]), max_attempts=0)


def test_lifecycle_callbacks_run_once() -> None:
    started = 0
    closed = 0

    async def start() -> None:
        nonlocal started
        started += 1

    def close() -> None:
        nonlocal closed
        closed += 1

    service = Text2SQLService(
        StubGenerator(),
        StubExecutor([successful_execution(), successful_execution()]),
        start_callback=start,
        close_callback=close,
    )

    asyncio.run(service.query("first"))
    asyncio.run(service.query("second"))
    service.close()

    assert started == 1
    assert closed == 1
