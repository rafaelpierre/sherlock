from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sherlock.contracts import MAX_SQL_LENGTH
from sherlock.services.text2sql import (
    ExecutionResult,
    QueryData,
    QueryExecutionError,
    SQLGenerationError,
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


def test_oversized_normalized_sql_is_rejected_before_service_response() -> None:
    generated_sql = "SELECT 1"
    executor = StubExecutor([successful_execution(sql_with_length(MAX_SQL_LENGTH + 1))])
    service = Text2SQLService(FixedSQLGenerator(generated_sql), executor)

    with pytest.raises(SQLGenerationError, match="20,000 character limit"):
        asyncio.run(service.query("Return one value"))

    assert executor.sql == [generated_sql]


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
