from pathlib import Path

import pytest

from fraud_mcp.errors import AnalyticsError, ErrorType
from fraud_mcp.services.sample_service import get_sample_values
from fraud_mcp.services.schema_service import get_schema


def test_schema_recommends_canonical_view(database_path: Path) -> None:
    schema = get_schema(database_path)

    assert schema.recommended_relation == "fraud_transactions"
    assert schema.relations[0].name == "fraud_transactions"
    assert schema.relations[0].grain == "one row per transaction"
    assert "amount_usd" in {column.name for column in schema.relations[0].columns}


def test_sample_values_are_distinct_and_bounded(database_path: Path) -> None:
    response = get_sample_values(
        database_path, "fraud_transactions", "card_type", limit=2
    )
    assert response.values == ["Credit", "Debit"]


@pytest.mark.parametrize(
    ("relation", "column", "error_type"),
    [
        ("missing", "card_type", ErrorType.UNKNOWN_RELATION),
        ("fraud_transactions", "missing", ErrorType.UNKNOWN_COLUMN),
    ],
)
def test_sample_values_validates_identifiers(
    database_path: Path,
    relation: str,
    column: str,
    error_type: ErrorType,
) -> None:
    with pytest.raises(AnalyticsError) as caught:
        get_sample_values(database_path, relation, column)
    assert caught.value.error_type == error_type


def test_sample_values_enforces_hard_limit(database_path: Path) -> None:
    with pytest.raises(AnalyticsError) as caught:
        get_sample_values(database_path, "fraud_transactions", "card_type", 51)
    assert caught.value.error_type == ErrorType.INVALID_TOOL_ARGUMENT
