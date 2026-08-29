"""Safe analytical query execution."""

from __future__ import annotations

import base64
import datetime as dt
import difflib
import math
import re
import sqlite3
from typing import Any

from fraud_mcp.errors import AnalyticsError, ErrorType
from fraud_mcp.models import QueryResponse
from fraud_mcp.services.database import Database, readonly_connection
from fraud_mcp.services.query_validator import validate_sql
from fraud_mcp.services.schema_service import column_names, relation_names


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def _suggest(unknown: str, candidates: list[str]) -> str | None:
    matches = difflib.get_close_matches(unknown, candidates, n=3, cutoff=0.5)
    if not matches:
        return None
    quoted = ", ".join(f"'{match}'" for match in matches)
    return f"Did you mean {quoted}?"


def _execution_error(
    connection: sqlite3.Connection,
    exc: sqlite3.Error,
) -> AnalyticsError:
    message = str(exc)
    if "interrupted" in message.lower():
        return AnalyticsError(
            ErrorType.QUERY_TIMEOUT,
            "The query exceeded the configured execution timeout.",
            "Simplify the query or filter the scanned data.",
        )

    relation_match = re.search(r"no such table: ([^\s]+)", message, re.IGNORECASE)
    if relation_match:
        unknown = relation_match.group(1)
        candidates = list(relation_names(connection))
        return AnalyticsError(
            ErrorType.UNKNOWN_RELATION,
            f"Relation '{unknown}' does not exist.",
            _suggest(unknown, candidates) or "Call get_schema for available relations.",
        )

    column_match = re.search(r"no such column: ([^\s]+)", message, re.IGNORECASE)
    if column_match:
        unknown = column_match.group(1)
        candidates: list[str] = []
        for relation in relation_names(connection):
            candidates.extend(column_names(connection, relation))
        return AnalyticsError(
            ErrorType.UNKNOWN_COLUMN,
            f"Column '{unknown}' does not exist.",
            _suggest(unknown, sorted(set(candidates)))
            or "Call get_schema for available columns.",
        )

    return AnalyticsError(
        ErrorType.QUERY_EXECUTION_ERROR,
        "The query could not be executed.",
    )


def run_query(
    database: Database,
    sql: str,
    *,
    max_rows: int,
    hard_max_rows: int,
    timeout_seconds: float,
) -> QueryResponse:
    normalized_sql = validate_sql(sql)
    row_limit = min(max_rows, hard_max_rows)

    with readonly_connection(
        database,
        timeout_seconds=timeout_seconds,
    ) as connection:
        try:
            cursor = connection.execute(normalized_sql)
            fetched = cursor.fetchmany(row_limit + 1)
        except sqlite3.Error as exc:
            raise _execution_error(connection, exc) from exc

        columns = [description[0] for description in cursor.description or ()]
        truncated = len(fetched) > row_limit
        rows = [[json_safe(value) for value in row] for row in fetched[:row_limit]]

    return QueryResponse(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        sql=normalized_sql,
    )
