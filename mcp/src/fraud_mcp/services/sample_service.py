"""Representative distinct-value lookup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fraud_mcp.errors import AnalyticsError, ErrorType
from fraud_mcp.models import SampleValuesResponse
from fraud_mcp.services.database import readonly_connection
from fraud_mcp.services.schema_service import (
    column_names,
    quote_identifier,
    relation_names,
)

MAX_SAMPLE_VALUES = 50


def get_sample_values(
    database_path: Path,
    relation: str,
    column: str,
    limit: int = 20,
) -> SampleValuesResponse:
    if not relation.strip() or not column.strip():
        raise AnalyticsError(
            ErrorType.INVALID_TOOL_ARGUMENT,
            "Relation and column must be non-empty strings.",
        )
    if limit < 1 or limit > MAX_SAMPLE_VALUES:
        raise AnalyticsError(
            ErrorType.INVALID_TOOL_ARGUMENT,
            f"Limit must be between 1 and {MAX_SAMPLE_VALUES}.",
        )

    with readonly_connection(database_path) as connection:
        relations = relation_names(connection)
        if relation not in relations:
            raise AnalyticsError(
                ErrorType.UNKNOWN_RELATION,
                f"Relation '{relation}' does not exist.",
                "Call get_schema to inspect available relations.",
            )

        columns = column_names(connection, relation)
        if column not in columns:
            raise AnalyticsError(
                ErrorType.UNKNOWN_COLUMN,
                f"Column '{column}' does not exist in relation '{relation}'.",
                "Call get_schema to inspect available columns.",
            )

        relation_sql = quote_identifier(relation)
        column_sql = quote_identifier(column)
        rows = connection.execute(
            f"""
            SELECT DISTINCT {column_sql}
            FROM {relation_sql}
            ORDER BY {column_sql}
            LIMIT ?
            """,
            (limit,),
        )
        values: list[Any] = [row[0] for row in rows]

    return SampleValuesResponse(
        relation=relation,
        column=column,
        values=values,
    )
