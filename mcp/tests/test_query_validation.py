import pytest

from fraud_mcp.errors import AnalyticsError, ErrorType
from fraud_mcp.services.query_validator import validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT AVG(amount_usd) FROM fraud_transactions WHERE is_fraud = TRUE",
        "SELECT card_type, AVG(is_fraud) FROM fraud_transactions GROUP BY card_type",
        """
        WITH fraud AS (
            SELECT * FROM fraud_transactions WHERE is_fraud = TRUE
        )
        SELECT COUNT(*) FROM fraud
        """,
        "SELECT ROW_NUMBER() OVER (ORDER BY transaction_id) FROM fraud_transactions",
    ],
)
def test_valid_analytical_queries(sql: str) -> None:
    assert validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO users(id) VALUES (3)",
        "UPDATE users SET gender = 'Male'",
        "DELETE FROM transactions",
        "DROP TABLE users",
        "CREATE TABLE secrets(value TEXT)",
        "PRAGMA table_info(users)",
        "ATTACH DATABASE '/tmp/example.db' AS example",
        "SELECT load_extension('extension')",
    ],
)
def test_unsafe_queries_are_rejected(sql: str) -> None:
    with pytest.raises(AnalyticsError) as caught:
        validate_sql(sql)
    assert caught.value.error_type == ErrorType.UNSAFE_SQL


def test_multiple_statements_are_rejected() -> None:
    with pytest.raises(AnalyticsError) as caught:
        validate_sql("SELECT 1; SELECT 2")
    assert caught.value.error_type == ErrorType.MULTIPLE_STATEMENTS


def test_empty_sql_is_invalid() -> None:
    with pytest.raises(AnalyticsError) as caught:
        validate_sql(" ")
    assert caught.value.error_type == ErrorType.INVALID_SQL
