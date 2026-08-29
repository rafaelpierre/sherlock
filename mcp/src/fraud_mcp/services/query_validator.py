"""SQLGlot-based validation for read-only analytical statements."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from fraud_mcp.errors import AnalyticsError, ErrorType

UNSAFE_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Transaction,
    exp.Merge,
    exp.Copy,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Use,
    exp.Set,
)


def validate_sql(sql: str) -> str:
    """Return normalized SQLite SQL after enforcing the query policy."""

    if not isinstance(sql, str) or not sql.strip():
        raise AnalyticsError(ErrorType.INVALID_SQL, "SQL must be a non-empty string.")

    try:
        statements = [
            statement
            for statement in sqlglot.parse(sql, read="sqlite")
            if statement is not None
        ]
    except ParseError as exc:
        raise AnalyticsError(
            ErrorType.INVALID_SQL, f"SQL could not be parsed: {exc}"
        ) from exc

    if len(statements) != 1:
        raise AnalyticsError(
            ErrorType.MULTIPLE_STATEMENTS,
            "Exactly one SQL statement is allowed per call.",
        )

    statement = statements[0]
    if not isinstance(statement, exp.Query) or any(
        isinstance(node, UNSAFE_EXPRESSION_TYPES) for node in statement.walk()
    ):
        raise AnalyticsError(
            ErrorType.UNSAFE_SQL,
            "Only read-only SELECT and WITH analytical queries are allowed.",
        )

    for function in statement.find_all(exp.Anonymous):
        if function.name.lower() == "load_extension":
            raise AnalyticsError(
                ErrorType.UNSAFE_SQL,
                "The load_extension function is not allowed.",
            )

    return statement.sql(dialect="sqlite", pretty=False)
