from __future__ import annotations

import pytest

from sherlock.config import Settings


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


def test_agentcore_requires_a_runtime_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHERLOCK_MCP_TRANSPORT", "agentcore")
    settings = Settings.from_environment()

    with pytest.raises(ValueError, match="SHERLOCK_AGENTCORE_RUNTIME_ARN"):
        settings.mcp_client()


def test_agentcore_client_uses_the_runtime_invocation_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHERLOCK_MCP_TRANSPORT", "agentcore")
    monkeypatch.setenv(
        "SHERLOCK_AGENTCORE_RUNTIME_ARN",
        "arn:aws:bedrock-agentcore:eu-west-2:123:runtime/fraud-mcp",
    )
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("sherlock.config.MCPClient", FakeClient)

    client = Settings.from_environment().mcp_client(allowed_tools=("run_query",))

    assert isinstance(client, FakeClient)
    assert captured["url"] == (
        "https://bedrock-agentcore.eu-west-2.amazonaws.com/runtimes/"
        "arn%3Aaws%3Abedrock-agentcore%3Aeu-west-2%3A123%3Aruntime%2Ffraud-mcp/"
        "invocations?qualifier=DEFAULT"
    )
    assert captured["tool_filters"] == {"allowed": ["run_query"]}


def test_stdio_args_must_be_a_json_string_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHERLOCK_MCP_STDIO_ARGS", '["run", 1]')

    with pytest.raises(ValueError, match="JSON array of strings"):
        Settings.from_environment()


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("SHERLOCK_WORKFLOW_DEADLINE_SECONDS", "0", "greater than zero"),
        ("SHERLOCK_WORKFLOW_DEADLINE_SECONDS", "Infinity", "greater than zero"),
        ("SHERLOCK_MODEL_IN_FLIGHT_LIMIT", "0", "at least 1"),
        ("SHERLOCK_MCP_IN_FLIGHT_LIMIT", "0", "at least 1"),
    ],
)
def test_execution_limits_are_validated(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, message: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_environment()
