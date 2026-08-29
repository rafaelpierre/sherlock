"""Read-only SQLite connection management."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class InMemoryDatabase:
    """A process-local SQLite snapshot kept alive for the server lifetime."""

    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path.expanduser().resolve()
        self._uri = f"file:fraud-mcp-{uuid4().hex}?mode=memory&cache=shared"
        self._keeper: sqlite3.Connection | None = None

    def load(self) -> None:
        """Copy the source database into memory."""

        if self._keeper is not None:
            return
        if not self.source_path.is_file():
            raise FileNotFoundError(
                f"SQLite database does not exist: {self.source_path}"
            )

        source = sqlite3.connect(
            f"{self.source_path.as_uri()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        keeper = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        try:
            source.backup(keeper)
            keeper.execute("PRAGMA query_only = ON")
        except Exception:
            keeper.close()
            raise
        finally:
            source.close()

        self._keeper = keeper

    def connect(self) -> sqlite3.Connection:
        """Open an isolated connection to the shared in-memory snapshot."""

        if self._keeper is None:
            raise RuntimeError("The in-memory database has not been loaded")
        return sqlite3.connect(self._uri, uri=True, check_same_thread=False)

    def close(self) -> None:
        """Release the snapshot."""

        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None


Database = Path | InMemoryDatabase


@contextmanager
def readonly_connection(
    database: Database,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[sqlite3.Connection]:
    """Open a read-only connection, optionally interrupting long operations."""

    if isinstance(database, InMemoryDatabase):
        connection = database.connect()
    else:
        path = database.expanduser().resolve()
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
