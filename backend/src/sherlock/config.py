"""Sherlock configuration for the fraud analytics MCP connection."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session
from strands.tools.mcp import MCPClient

MCPTransport = Literal["stdio", "streamable-http", "agentcore"]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MCP_DIRECTORY = REPOSITORY_ROOT / "mcp"
DEFAULT_STDIO_ARGS = (
    "--directory",
    str(DEFAULT_MCP_DIRECTORY),
    "run",
    "fraud-mcp-stdio",
)


class AgentCoreSigV4Auth(httpx.Auth):
    """Sign each AgentCore MCP invocation with the ECS task identity."""

    def __init__(self, region: str) -> None:
        self._region = region

    def auth_flow(self, request: httpx.Request):
        credentials = Session().get_credentials()
        if credentials is None:
            raise RuntimeError("AWS credentials are required for AgentCore MCP")

        signed_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(
            credentials.get_frozen_credentials(), "bedrock-agentcore", self._region
        ).add_auth(signed_request)
        request.headers.update(signed_request.headers.items())
        yield request


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
    workflow_deadline_seconds: float = 240.0
    model_in_flight_limit: int = 8
    mcp_in_flight_limit: int = 16
    agentcore_runtime_arn: str | None = None
    phoenix_secret_id: str | None = None
    cognito_issuer: str | None = None
    cognito_client_id: str | None = None
    auth_required: bool = True

    @classmethod
    def from_environment(cls) -> Settings:
        transport = os.getenv("SHERLOCK_MCP_TRANSPORT", "stdio").strip().lower()
        if transport not in {"stdio", "streamable-http", "agentcore"}:
            raise ValueError(
                "SHERLOCK_MCP_TRANSPORT must be 'stdio', 'streamable-http', or 'agentcore'"
            )

        stdio_args_value = os.getenv("SHERLOCK_MCP_STDIO_ARGS")
        headers_value = os.getenv("SHERLOCK_MCP_HTTP_HEADERS")

        cognito_issuer = os.getenv("SHERLOCK_COGNITO_ISSUER")
        cognito_client_id = os.getenv("SHERLOCK_COGNITO_CLIENT_ID")
        auth_required_value = (
            os.getenv("SHERLOCK_AUTH_REQUIRED", "true").strip().lower()
        )
        if auth_required_value not in {"true", "false"}:
            raise ValueError("SHERLOCK_AUTH_REQUIRED must be 'true' or 'false'")
        auth_required = auth_required_value == "true"
        if bool(cognito_issuer) != bool(cognito_client_id):
            raise ValueError(
                "SHERLOCK_COGNITO_ISSUER and SHERLOCK_COGNITO_CLIENT_ID must be set together"
            )
        if auth_required and not cognito_issuer:
            raise ValueError(
                "SHERLOCK_COGNITO_ISSUER and SHERLOCK_COGNITO_CLIENT_ID are required when authentication is enabled"
            )

        settings = cls(
            mcp_transport=transport,
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
            workflow_deadline_seconds=float(
                os.getenv("SHERLOCK_WORKFLOW_DEADLINE_SECONDS", "240")
            ),
            model_in_flight_limit=int(os.getenv("SHERLOCK_MODEL_IN_FLIGHT_LIMIT", "8")),
            mcp_in_flight_limit=int(os.getenv("SHERLOCK_MCP_IN_FLIGHT_LIMIT", "16")),
            agentcore_runtime_arn=os.getenv("SHERLOCK_AGENTCORE_RUNTIME_ARN"),
            phoenix_secret_id=os.getenv("SHERLOCK_PHOENIX_SECRET_ID"),
            cognito_issuer=cognito_issuer.rstrip("/") if cognito_issuer else None,
            cognito_client_id=cognito_client_id,
            auth_required=auth_required,
        )
        if (
            not math.isfinite(settings.workflow_deadline_seconds)
            or settings.workflow_deadline_seconds <= 0
        ):
            raise ValueError(
                "SHERLOCK_WORKFLOW_DEADLINE_SECONDS must be greater than zero"
            )
        if settings.model_in_flight_limit < 1:
            raise ValueError("SHERLOCK_MODEL_IN_FLIGHT_LIMIT must be at least 1")
        if settings.mcp_in_flight_limit < 1:
            raise ValueError("SHERLOCK_MCP_IN_FLIGHT_LIMIT must be at least 1")
        return settings

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

        if self.mcp_transport == "agentcore":
            raise ValueError("AgentCore MCP clients must be created with mcp_client()")

        config = {**common, "url": self.mcp_url}
        if self.mcp_http_headers:
            config["headers"] = self.mcp_http_headers
        return config

    def mcp_client(self, *, allowed_tools: tuple[str, ...] | None = None) -> MCPClient:
        """Create one MCP client for the selected transport."""

        if self.mcp_transport != "agentcore":
            clients = MCPClient.load_servers(
                {
                    "mcpServers": {
                        "fraud-analytics": self.mcp_server_config(
                            allowed_tools=allowed_tools
                        )
                    }
                }
            )
            if len(clients) != 1:
                raise RuntimeError(
                    "Expected exactly one enabled fraud analytics MCP server"
                )
            return clients[0]

        if not self.agentcore_runtime_arn:
            raise ValueError(
                "SHERLOCK_AGENTCORE_RUNTIME_ARN is required for agentcore transport"
            )

        encoded_arn = quote(self.agentcore_runtime_arn, safe="")
        endpoint = (
            f"https://bedrock-agentcore.{os.getenv('AWS_REGION', 'eu-west-2')}"
            f".amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
        )
        return MCPClient(
            url=endpoint,
            auth_provider=AgentCoreSigV4Auth(os.getenv("AWS_REGION", "eu-west-2")),
            startup_timeout=self.mcp_startup_timeout,
            tool_filters={"allowed": list(allowed_tools)} if allowed_tools else None,
            application_name="sherlock-text2sql-agent",
            application_version="0.1.0",
        )
