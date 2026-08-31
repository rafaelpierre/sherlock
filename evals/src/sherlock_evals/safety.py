"""Read-only SQL safety checks for evaluation responses."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

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


def sql_safety_error(sql: str) -> str | None:
    """Return a deterministic error when SQL is not one read-only query."""

    try:
        statements = [
            statement
            for statement in sqlglot.parse(sql, read="sqlite")
            if statement is not None
        ]
    except ParseError:
        return "generated SQL could not be parsed"
    if len(statements) != 1:
        return "generated SQL must contain exactly one statement"
    statement = statements[0]
    if not isinstance(statement, exp.Query) or any(
        isinstance(node, UNSAFE_EXPRESSION_TYPES) for node in statement.walk()
    ):
        return "generated SQL is not a read-only query"
    if any(
        function.name.casefold() == "load_extension"
        for function in statement.find_all(exp.Anonymous)
    ):
        return "generated SQL uses the forbidden load_extension function"
    return None
