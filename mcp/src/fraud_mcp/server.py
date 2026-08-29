"""FastMCP Streamable HTTP server and tool definitions."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from fraud_mcp.config import Settings, get_settings
from fraud_mcp.errors import AnalyticsError, ErrorType
from fraud_mcp.services.database import InMemoryDatabase, readonly_connection
from fraud_mcp.services.query_service import run_query as execute_query
from fraud_mcp.services.sample_service import get_sample_values as sample_values
from fraud_mcp.services.schema_service import get_schema as inspect_schema

LOGGER = logging.getLogger("fraud_mcp")


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _log_call(
    *,
    tool: str,
    started: float,
    success: bool,
    error_type: str | None = None,
    **fields: Any,
) -> None:
    event = {
        "tool": tool,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "success": success,
        "error_type": error_type,
        **fields,
    }
    LOGGER.info(json.dumps(event, separators=(",", ":"), default=str))


def _invoke_tool(
    tool: str,
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = operation()
    except AnalyticsError as exc:
        _log_call(
            tool=tool,
            started=started,
            success=False,
            error_type=exc.error_type.value,
        )
        return exc.as_dict()
    except Exception:
        LOGGER.exception("Unexpected failure in MCP tool %s", tool)
        error = AnalyticsError(
            ErrorType.QUERY_EXECUTION_ERROR,
            "The server could not complete the operation.",
        )
        _log_call(
            tool=tool,
            started=started,
            success=False,
            error_type=error.error_type.value,
        )
        return error.as_dict()

    extra: dict[str, Any] = {}
    if tool == "run_query":
        extra = {
            "normalized_sql": result.get("sql"),
            "returned_row_count": result.get("row_count"),
            "truncated": result.get("truncated"),
        }
    _log_call(tool=tool, started=started, success=True, **extra)
    return result


def create_server(settings: Settings | None = None) -> FastMCP:
    settings = settings or get_settings()
    database = InMemoryDatabase(settings.database_path)

    @asynccontextmanager
    async def lifespan(_: FastMCP):
        database.load()
        LOGGER.info("Loaded SQLite database into memory from %s", database.source_path)
        try:
            yield {}
        finally:
            database.close()

    server = FastMCP(
        "Fraud Analytics",
        instructions=(
            "Use fraud_transactions as the preferred relation for analytical "
            "fraud queries. The server returns data, not business conclusions."
        ),
        version="0.1.0",
        mask_error_details=True,
        strict_input_validation=True,
        lifespan=lifespan,
    )

    @server.tool(name="get_schema")
    def get_schema_tool() -> dict[str, Any]:
        """Return database relations, columns, keys, and the canonical relation."""

        return _invoke_tool(
            "get_schema",
            lambda: inspect_schema(database).model_dump(mode="json"),
        )

    @server.tool(name="get_sample_values")
    def get_sample_values_tool(
        relation: str,
        column: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return bounded distinct values for a validated relation column."""

        return _invoke_tool(
            "get_sample_values",
            lambda: sample_values(
                database,
                relation,
                column,
                limit,
            ).model_dump(mode="json"),
        )

    @server.tool(name="run_query")
    def run_query_tool(sql: str) -> dict[str, Any]:
        """Validate and execute one read-only analytical SQL query."""

        return _invoke_tool(
            "run_query",
            lambda: execute_query(
                database,
                sql,
                max_rows=settings.max_query_rows,
                hard_max_rows=settings.hard_max_query_rows,
                timeout_seconds=settings.query_timeout_seconds,
            ).model_dump(mode="json"),
        )

    @server.tool(name="get_database_info")
    def get_database_info_tool() -> dict[str, Any]:
        """Return compact database coverage and label metadata."""

        def operation() -> dict[str, Any]:
            with readonly_connection(
                database,
                timeout_seconds=settings.query_timeout_seconds,
            ) as connection:
                row = connection.execute(
                    """
                    SELECT
                        COUNT(*) AS transaction_count,
                        SUM(is_fraud = 1) AS fraud_count,
                        SUM(is_fraud = 0) AS non_fraud_count,
                        SUM(is_fraud IS NULL) AS unlabelled_count,
                        MIN(transaction_datetime) AS date_min,
                        MAX(transaction_datetime) AS date_max
                    FROM fraud_transactions
                    """
                ).fetchone()
                if row is None:
                    raise AnalyticsError(
                        ErrorType.QUERY_EXECUTION_ERROR,
                        "Database metadata could not be calculated.",
                    )
                return {
                    "database": settings.database_path.stem,
                    "canonical_relation": "fraud_transactions",
                    "transaction_count": row[0],
                    "fraud_count": row[1],
                    "non_fraud_count": row[2],
                    "unlabelled_count": row[3],
                    "date_min": row[4],
                    "date_max": row[5],
                    "read_only": True,
                }

        return _invoke_tool("get_database_info", operation)

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> JSONResponse:
        try:
            with readonly_connection(database) as connection:
                connection.execute("SELECT 1").fetchone()
            return JSONResponse({"status": "ok"})
        except Exception:
            LOGGER.exception("Health check failed")
            return JSONResponse({"status": "unavailable"}, status_code=503)

    return server


mcp = create_server()


def main() -> None:
    settings = get_settings()
    _configure_logging(settings)
    LOGGER.info(
        "Starting Fraud Analytics MCP at http://%s:%s%s",
        settings.mcp_host,
        settings.mcp_port,
        settings.mcp_path,
    )
    try:
        mcp.run(
            transport="streamable-http",
            host=settings.mcp_host,
            port=settings.mcp_port,
            path=settings.mcp_path,
        )
    except KeyboardInterrupt:
        LOGGER.info("Fraud Analytics MCP stopped")


def stdio_main() -> None:
    """Run the same MCP server over stdio for local subprocess clients."""

    settings = get_settings()
    _configure_logging(settings)
    LOGGER.info("Starting Fraud Analytics MCP over stdio")
    try:
        mcp.run(transport="stdio", show_banner=False)
    except KeyboardInterrupt:
        LOGGER.info("Fraud Analytics MCP stopped")


if __name__ == "__main__":
    main()
