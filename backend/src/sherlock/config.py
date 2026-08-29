"""Sherlock configuration for the fraud analytics MCP connection."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

MCPTransport = Literal["stdio", "streamable-http"]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MCP_DIRECTORY = REPOSITORY_ROOT / "mcp"
DEFAULT_STDIO_ARGS = (
    "--directory",
    str(DEFAULT_MCP_DIRECTORY),
    "run",
    "fraud-mcp-stdio",
)


def _json_list(value: str, variable: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        raise ValueError(f"{variable} must be a JSON array of strings")
    return tuple(parsed)


def _json_object(value: str, variable: str) -> dict[str, str]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in parsed.items()
    ):
        raise ValueError(f"{variable} must be a JSON object of string values")
    return parsed


@dataclass(frozen=True)
class Settings:
    """Connection settings controlled entirely by backend environment variables."""

    mcp_transport: MCPTransport = "stdio"
    mcp_url: str = "http://localhost:8000/mcp"
    mcp_stdio_command: str = "uv"
    mcp_stdio_args: tuple[str, ...] = DEFAULT_STDIO_ARGS
    mcp_http_headers: dict[str, str] | None = None
    mcp_startup_timeout: int = 30

    @classmethod
    def from_environment(cls) -> Settings:
        transport = os.getenv("SHERLOCK_MCP_TRANSPORT", "stdio").strip().lower()
        if transport not in {"stdio", "streamable-http"}:
            raise ValueError(
                "SHERLOCK_MCP_TRANSPORT must be 'stdio' or 'streamable-http'"
            )

        stdio_args_value = os.getenv("SHERLOCK_MCP_STDIO_ARGS")
        headers_value = os.getenv("SHERLOCK_MCP_HTTP_HEADERS")

        return cls(
            mcp_transport=cast(MCPTransport, transport),
            mcp_url=os.getenv("SHERLOCK_MCP_URL", "http://localhost:8000/mcp").strip(),
            mcp_stdio_command=os.getenv("SHERLOCK_MCP_STDIO_COMMAND", "uv").strip(),
            mcp_stdio_args=(
                _json_list(stdio_args_value, "SHERLOCK_MCP_STDIO_ARGS")
                if stdio_args_value is not None
                else DEFAULT_STDIO_ARGS
            ),
            mcp_http_headers=(
                _json_object(headers_value, "SHERLOCK_MCP_HTTP_HEADERS")
                if headers_value is not None
                else None
            ),
            mcp_startup_timeout=int(os.getenv("SHERLOCK_MCP_STARTUP_TIMEOUT", "30")),
        )

    def mcp_server_config(
        self,
        *,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return a Strands-compatible MCP server entry."""

        allowed_tools = allowed_tools or (
            "get_schema",
            "get_sample_values",
            "run_query",
            "get_database_info",
        )
        common: dict[str, Any] = {
            "transport": self.mcp_transport,
            "startup_timeout": self.mcp_startup_timeout,
            "application_name": "sherlock-text2sql-agent",
            "application_version": "0.1.0",
            "tool_filters": {"allowed": list(allowed_tools)},
        }
        if self.mcp_transport == "stdio":
            return {
                **common,
                "command": self.mcp_stdio_command,
                "args": list(self.mcp_stdio_args),
            }

        config = {**common, "url": self.mcp_url}
        if self.mcp_http_headers:
            config["headers"] = self.mcp_http_headers
        return config
