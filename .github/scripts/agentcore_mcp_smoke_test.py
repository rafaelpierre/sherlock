#!/usr/bin/env python3
"""Run a bounded, payload-safe AgentCore MCP deployment smoke test."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

MCP_PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {
    "name": "sherlock-deployment-smoke-test",
    "version": "1.0",
}
TOOL_NAMES = ("get_schema", "get_database_info")


class SmokeTestError(RuntimeError):
    """A safe diagnostic error for a failed deployment smoke test."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _request(method: str, request_id: int, *, tool_name: str | None = None) -> bytes:
    if tool_name is None:
        params: dict[str, Any] = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }
    else:
        params = {"name": tool_name, "arguments": {}}
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        separators=(",", ":"),
    ).encode()


def _command(
    *,
    runtime_arn: str,
    runtime_session_id: str,
    request_path: Path,
    response_path: Path,
    method: str,
    tool_name: str | None,
    mcp_session_id: str | None,
) -> list[str]:
    command = [
        "aws",
        "bedrock-agentcore",
        "invoke-agent-runtime",
        "--agent-runtime-arn",
        runtime_arn,
        "--content-type",
        "application/json",
        "--accept",
        "application/json",
        "--runtime-session-id",
        runtime_session_id,
        "--mcp-protocol-version",
        MCP_PROTOCOL_VERSION,
        "--mcp-method",
        method,
        "--payload",
        f"fileb://{request_path}",
        "--cli-connect-timeout",
        "10",
        "--cli-read-timeout",
        "30",
        "--no-cli-pager",
    ]
    if tool_name is not None:
        command.extend(("--mcp-name", tool_name))
    if mcp_session_id is not None:
        command.extend(("--mcp-session-id", mcp_session_id))
    command.append(str(response_path))
    return command


def _parse_json(path: Path, operation: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeTestError(
            f"AgentCore MCP smoke test returned an invalid {operation} response."
        ) from error
    if not isinstance(value, dict):
        raise SmokeTestError(
            f"AgentCore MCP smoke test returned an invalid {operation} response."
        )
    return value


def _validate_response(response: dict[str, Any], operation: str) -> None:
    result = response.get("result")
    if not isinstance(result, dict) or response.get("error") is not None:
        raise SmokeTestError(f"AgentCore MCP smoke test failed during {operation}.")
    if result.get("isError") is True:
        raise SmokeTestError(f"AgentCore MCP smoke test failed during {operation}.")
    if operation == "initialize":
        _validate_initialize_result(result)
        return
    _validate_tool_result(result, operation)


def _validate_initialize_result(result: dict[str, Any]) -> None:
    server_info = result.get("serverInfo")
    if (
        result.get("protocolVersion") != MCP_PROTOCOL_VERSION
        or not isinstance(result.get("capabilities"), dict)
        or not isinstance(server_info, dict)
        or not isinstance(server_info.get("name"), str)
        or not server_info["name"]
        or not isinstance(server_info.get("version"), str)
        or not server_info["version"]
    ):
        raise SmokeTestError("AgentCore MCP smoke test failed during initialize.")


def _validate_tool_result(result: dict[str, Any], operation: str) -> None:
    content = result.get("content")
    structured_content = result.get("structuredContent")
    if (
        not isinstance(content, list)
        or not content
        or not isinstance(structured_content, dict)
    ):
        raise SmokeTestError(f"AgentCore MCP smoke test failed during {operation}.")
    if not any(
        isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
        and item["text"]
        for item in content
    ):
        raise SmokeTestError(f"AgentCore MCP smoke test failed during {operation}.")
    if operation == "get_schema":
        _validate_schema(structured_content)
    elif operation == "get_database_info":
        _validate_database_info(structured_content)
    else:
        raise SmokeTestError(f"AgentCore MCP smoke test failed during {operation}.")


def _validate_schema(structured_content: dict[str, Any]) -> None:
    relations = structured_content.get("relations")
    if (
        structured_content.get("recommended_relation") != "fraud_transactions"
        or not isinstance(relations, list)
        or not any(
            isinstance(relation, dict)
            and relation.get("name") == "fraud_transactions"
            and isinstance(relation.get("columns"), list)
            and relation["columns"]
            for relation in relations
        )
    ):
        raise SmokeTestError("AgentCore MCP smoke test failed during get_schema.")


def _validate_database_info(structured_content: dict[str, Any]) -> None:
    count_fields = (
        "transaction_count",
        "fraud_count",
        "non_fraud_count",
        "unlabelled_count",
    )
    if (
        structured_content.get("canonical_relation") != "fraud_transactions"
        or structured_content.get("read_only") is not True
        or not all(
            isinstance(structured_content.get(field), int)
            and structured_content[field] >= 0
            for field in count_fields
        )
        or not isinstance(structured_content.get("date_min"), str)
        or not structured_content["date_min"]
        or not isinstance(structured_content.get("date_max"), str)
        or not structured_content["date_max"]
    ):
        raise SmokeTestError(
            "AgentCore MCP smoke test failed during get_database_info."
        )


def _invoke(
    *,
    runtime_arn: str,
    runtime_session_id: str,
    request: bytes,
    operation: str,
    method: str,
    tool_name: str | None,
    mcp_session_id: str | None,
    directory: Path,
    runner: Runner,
) -> str | None:
    request_path = directory / f"{operation}-request.json"
    response_path = directory / f"{operation}-response.json"
    request_path.write_bytes(request)
    completed = runner(
        _command(
            runtime_arn=runtime_arn,
            runtime_session_id=runtime_session_id,
            request_path=request_path,
            response_path=response_path,
            method=method,
            tool_name=tool_name,
            mcp_session_id=mcp_session_id,
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SmokeTestError(
            f"AgentCore MCP smoke test failed during {operation} "
            f"(AWS CLI exit code {completed.returncode})."
        )

    _validate_response(_parse_json(response_path, operation), operation)
    metadata = _parse_json_from_text(completed.stdout, operation)
    session_id = metadata.get("mcpSessionId")
    if session_id is not None and not isinstance(session_id, str):
        raise SmokeTestError(
            f"AgentCore MCP smoke test returned an invalid {operation} session."
        )
    return session_id


def _parse_json_from_text(value: str, operation: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise SmokeTestError(
            f"AgentCore MCP smoke test returned invalid {operation} metadata."
        ) from error
    if not isinstance(parsed, dict):
        raise SmokeTestError(
            f"AgentCore MCP smoke test returned invalid {operation} metadata."
        )
    return parsed


def run_smoke_test(runtime_arn: str, *, runner: Runner = subprocess.run) -> None:
    """Exercise MCP initialization and safe, zero-argument metadata tools."""

    runtime_session_id = f"sherlock-smoke-{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="sherlock-agentcore-smoke-") as name:
        directory = Path(name)
        mcp_session_id = _invoke(
            runtime_arn=runtime_arn,
            runtime_session_id=runtime_session_id,
            request=_request("initialize", 1),
            operation="initialize",
            method="initialize",
            tool_name=None,
            mcp_session_id=None,
            directory=directory,
            runner=runner,
        )
        if not mcp_session_id:
            raise SmokeTestError(
                "AgentCore MCP smoke test did not return an initialize session."
            )
        for request_id, tool_name in enumerate(TOOL_NAMES, start=2):
            _invoke(
                runtime_arn=runtime_arn,
                runtime_session_id=runtime_session_id,
                request=_request("tools/call", request_id, tool_name=tool_name),
                operation=tool_name,
                method="tools/call",
                tool_name=tool_name,
                mcp_session_id=mcp_session_id,
                directory=directory,
                runner=runner,
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the safe AgentCore MCP deployment smoke test."
    )
    parser.add_argument("--runtime-arn", required=True)
    arguments = parser.parse_args()
    try:
        run_smoke_test(arguments.runtime_arn)
    except SmokeTestError as error:
        print(error, file=sys.stderr)
        return 1
    print("AgentCore MCP smoke test passed: initialize, get_schema, get_database_info.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
