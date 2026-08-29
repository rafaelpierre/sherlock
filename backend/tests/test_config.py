from __future__ import annotations

import pytest

from backend.config import Settings


def test_stdio_is_the_default_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHERLOCK_MCP_TRANSPORT", raising=False)

    config = Settings.from_environment().mcp_server_config()

    assert config["transport"] == "stdio"
    assert config["command"] == "uv"
    assert config["args"][-2:] == ["run", "fraud-mcp-stdio"]
    assert "url" not in config


def test_streamable_http_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHERLOCK_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("SHERLOCK_MCP_URL", "https://mcp.internal.example/mcp")
    monkeypatch.setenv(
        "SHERLOCK_MCP_HTTP_HEADERS", '{"Authorization":"Bearer test-token"}'
    )

    config = Settings.from_environment().mcp_server_config()

    assert config["transport"] == "streamable-http"
    assert config["url"] == "https://mcp.internal.example/mcp"
    assert config["headers"] == {"Authorization": "Bearer test-token"}
    assert "command" not in config


def test_invalid_transport_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHERLOCK_MCP_TRANSPORT", "sse")

    with pytest.raises(ValueError, match="SHERLOCK_MCP_TRANSPORT"):
        Settings.from_environment()


def test_stdio_args_must_be_a_json_string_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHERLOCK_MCP_STDIO_ARGS", '["run", 1]')

    with pytest.raises(ValueError, match="JSON array of strings"):
        Settings.from_environment()
