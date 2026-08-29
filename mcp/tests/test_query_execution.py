from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fraud_mcp.errors import AnalyticsError, ErrorType
from fraud_mcp.services.database import readonly_connection
from fraud_mcp.services.query_service import run_query


def execute(database_path: Path, sql: str, timeout: float = 1):
    return run_query(
        database_path,
        sql,
        max_rows=2,
        hard_max_rows=5,
        timeout_seconds=timeout,
    )


def test_query_results_are_structured_and_truncated(database_path: Path) -> None:
    result = execute(
        database_path,
        "SELECT transaction_id FROM fraud_transactions ORDER BY transaction_id",
    )
    assert result.columns == ["transaction_id"]
    assert result.rows == [[1], [2]]
    assert result.row_count == 2
    assert result.truncated is True


def test_aggregate_query_is_not_truncated(database_path: Path) -> None:
    result = execute(database_path, "SELECT COUNT(*) AS count FROM fraud_transactions")
    assert result.rows == [[3]]
    assert result.truncated is False


@pytest.mark.parametrize(
    ("sql", "error_type"),
    [
        ("SELECT * FROM unknown_relation", ErrorType.UNKNOWN_RELATION),
        ("SELECT amuont_usd FROM fraud_transactions", ErrorType.UNKNOWN_COLUMN),
    ],
)
def test_execution_maps_identifier_errors(
    database_path: Path, sql: str, error_type: ErrorType
) -> None:
    with pytest.raises(AnalyticsError) as caught:
        execute(database_path, sql)
    assert caught.value.error_type == error_type
    assert caught.value.suggestion


def test_query_timeout(database_path: Path) -> None:
    sql = """
        WITH RECURSIVE counter(value) AS (
            VALUES(0)
            UNION ALL
            SELECT value + 1 FROM counter WHERE value < 100000000
        )
        SELECT SUM(value) FROM counter
    """
    with pytest.raises(AnalyticsError) as caught:
        execute(database_path, sql, timeout=0.001)
    assert caught.value.error_type == ErrorType.QUERY_TIMEOUT


def test_runtime_connection_is_read_only(database_path: Path) -> None:
    with (
        readonly_connection(database_path) as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("DELETE FROM transactions")
