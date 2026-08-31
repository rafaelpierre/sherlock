"""Shared bounded-value contracts used across service and API layers."""

from typing import Annotated

from pydantic import StringConstraints

MAX_SQL_LENGTH = 20_000
BoundedSQL = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SQL_LENGTH,
    ),
]
