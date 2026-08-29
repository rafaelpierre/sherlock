from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sherlock.services.text2sql import (
    ExecutionResult,
    QueryData,
    QueryExecutionError,
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
