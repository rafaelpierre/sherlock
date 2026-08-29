from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from fraud_mcp.config import Settings
from fraud_mcp.server import create_server


@pytest.mark.anyio
async def test_streamable_http_lists_and_calls_tools(settings: Settings) -> None:
    server = create_server(settings)
    app = server.http_app(path=settings.mcp_path, transport="streamable-http")

    def client_factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **kwargs: Any,
    ) -> httpx.AsyncClient:
        kwargs.pop("follow_redirects", None)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            follow_redirects=True,
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    transport = StreamableHttpTransport(
        "http://testserver/mcp",
        httpx_client_factory=client_factory,
    )
    async with app.router.lifespan_context(app), Client(transport) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} >= {
            "get_schema",
            "get_sample_values",
            "run_query",
        }

        result = await client.call_tool(
            "run_query",
            {"sql": "SELECT COUNT(*) AS count FROM fraud_transactions"},
        )
        assert result.structured_content["rows"] == [[3]]


@pytest.mark.anyio
async def test_health_route(settings: Settings) -> None:
    app = create_server(settings).http_app(
        path=settings.mcp_path,
        transport="streamable-http",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
