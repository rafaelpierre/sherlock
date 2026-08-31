from __future__ import annotations

import pytest

from sherlock_evals.safety import sql_safety_error


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM fraud_transactions",
        "WITH labelled AS (SELECT * FROM fraud_transactions) SELECT * FROM labelled",
    ],
)
def test_read_only_queries_are_safe(sql: str) -> None:
    assert sql_safety_error(sql) is None


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("not valid sql !!!", "could not be parsed"),
        ("SELECT 'unterminated", "could not be parsed"),
        ("SELECT 1; SELECT 2", "exactly one statement"),
        ("DELETE FROM fraud_transactions", "not a read-only query"),
        ("SELECT load_extension('x')", "forbidden load_extension"),
    ],
)
def test_unsafe_queries_are_rejected(sql: str, message: str) -> None:
    assert message in sql_safety_error(sql)
