"""Read-only SQLite connection management."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def readonly_connection(
    database_path: Path,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[sqlite3.Connection]:
    """Open a read-only connection, optionally interrupting long operations."""

    path = database_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {path}")

    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")

    if timeout_seconds is not None:
        deadline = time.monotonic() + timeout_seconds
        connection.set_progress_handler(
            lambda: int(time.monotonic() >= deadline),
            1_000,
        )

    try:
        yield connection
    finally:
        connection.set_progress_handler(None, 0)
        connection.close()
