"""Tests for the workflow-owned AgentCore MCP deployment smoke test."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "scripts"
    / "agentcore_mcp_smoke_test.py"
)
WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "terraform.yml"
)
SPEC = importlib.util.spec_from_file_location("agentcore_mcp_smoke_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SMOKE_TEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE_TEST)


def _valid_result(operation: str) -> dict[str, object]:
    if operation == "initialize":
        return {
            "protocolVersion": SMOKE_TEST.MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "serverInfo": {"name": "Fraud Analytics", "version": "0.1.0"},
        }
    if operation == "get_schema":
        structured_content: dict[str, object] = {
            "recommended_relation": "fraud_transactions",
            "relations": [
                {
                    "name": "fraud_transactions",
                    "columns": [{"name": "transaction_id"}],
                }
            ],
        }
    else:
        structured_content = {
            "canonical_relation": "fraud_transactions",
            "read_only": True,
            "transaction_count": 1,
            "fraud_count": 0,
            "non_fraud_count": 1,
            "unlabelled_count": 0,
            "date_min": "2019-01-01T00:00:00",
            "date_max": "2019-01-01T00:00:00",
        }
    return {
        "content": [{"type": "text", "text": "validated metadata"}],
        "structuredContent": structured_content,
    }


class AgentCoreMcpSmokeTestTests(unittest.TestCase):
    def test_command_targets_the_runtime_and_bounded_mcp_tool_call(self) -> None:
        command = SMOKE_TEST._command(
            runtime_arn="arn:aws:bedrock-agentcore:eu-west-2:123:runtime/fraud-mcp",
            runtime_session_id="sherlock-smoke-12345678901234567890123456789012",
            request_path=Path("request.json"),
            response_path=Path("response.json"),
            method="tools/call",
            tool_name="get_schema",
            mcp_session_id="agentcore-session",
        )

        self.assertEqual(
            command[:3], ["aws", "bedrock-agentcore", "invoke-agent-runtime"]
        )
        self.assertIn("--agent-runtime-arn", command)
        self.assertIn("--mcp-method", command)
        self.assertIn("--mcp-name", command)
        self.assertIn("get_schema", command)
        self.assertIn("--mcp-session-id", command)
        self.assertEqual(
            command[command.index("--accept") + 1], SMOKE_TEST.ACCEPT_HEADER
        )
        self.assertIn("--cli-connect-timeout", command)
        self.assertIn("--cli-read-timeout", command)
        self.assertNotIn("run_query", command)

    def test_run_smoke_test_reuses_initialize_session_for_safe_tools(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            response_path = Path(command[-1])
            operation = (
                "initialize"
                if "initialize" in command
                else "get_schema"
                if "get_schema" in command
                else "get_database_info"
            )
            response_path.write_text(
                json.dumps({"jsonrpc": "2.0", "result": _valid_result(operation)})
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"mcpSessionId": "agentcore-session"}),
            )

        SMOKE_TEST.run_smoke_test("runtime-arn", runner=runner)

        self.assertEqual(len(commands), 3)
        self.assertIn("initialize", commands[0])
        self.assertIn("get_schema", commands[1])
        self.assertIn("get_database_info", commands[2])
        self.assertIn("agentcore-session", commands[1])
        self.assertIn("agentcore-session", commands[2])

    def test_empty_result_fails_for_each_smoke_operation(self) -> None:
        for operation in ("initialize", "get_schema", "get_database_info"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(SMOKE_TEST.SmokeTestError, operation):
                    SMOKE_TEST._validate_response({"result": {}}, operation)

    def test_streamable_http_response_is_parsed_without_logging_payload(self) -> None:
        with TemporaryDirectory() as directory:
            response_path = Path(directory) / "response.txt"
            response_path.write_text(
                "event: message\n"
                'data: {"jsonrpc":"2.0","result":{"protocolVersion":"2025-03-26"}}\n\n'
            )

            response = SMOKE_TEST._parse_response(response_path, "initialize")

        self.assertEqual(response["result"]["protocolVersion"], "2025-03-26")

    def test_tool_result_requires_expected_safe_metadata_shape(self) -> None:
        malformed_schema = _valid_result("get_schema")
        malformed_schema["structuredContent"] = {"relations": []}
        malformed_database_info = _valid_result("get_database_info")
        malformed_database_info["structuredContent"] = {
            "canonical_relation": "fraud_transactions",
            "read_only": True,
        }

        with self.assertRaisesRegex(SMOKE_TEST.SmokeTestError, "get_schema"):
            SMOKE_TEST._validate_response({"result": malformed_schema}, "get_schema")
        with self.assertRaisesRegex(SMOKE_TEST.SmokeTestError, "get_database_info"):
            SMOKE_TEST._validate_response(
                {"result": malformed_database_info}, "get_database_info"
            )

    def test_failed_invocation_uses_safe_diagnostic_without_cli_output(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                SMOKE_TEST.SmokeTestError, "exit code 7"
            ) as error:
                SMOKE_TEST._invoke(
                    runtime_arn="runtime-arn",
                    runtime_session_id="sherlock-smoke-12345678901234567890123456789012",
                    request=b"{}",
                    operation="get_schema",
                    method="tools/call",
                    tool_name="get_schema",
                    mcp_session_id="session",
                    directory=Path(directory),
                    runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
                        [], 7, stderr="provider response with sensitive details"
                    ),
                )

        self.assertNotIn("sensitive details", str(error.exception))

    def test_tool_error_response_fails_without_disclosing_payload(self) -> None:
        with TemporaryDirectory() as directory:

            def runner(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_text(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "result": {"isError": True, "content": ["rows"]},
                        }
                    )
                )
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps({"mcpSessionId": "session"})
                )

            with self.assertRaisesRegex(
                SMOKE_TEST.SmokeTestError, "get_schema"
            ) as error:
                SMOKE_TEST._invoke(
                    runtime_arn="runtime-arn",
                    runtime_session_id="sherlock-smoke-12345678901234567890123456789012",
                    request=b"{}",
                    operation="get_schema",
                    method="tools/call",
                    tool_name="get_schema",
                    mcp_session_id="session",
                    directory=Path(directory),
                    runner=runner,
                )

        self.assertNotIn("rows", str(error.exception))

    def test_existing_stack_workflow_gates_backend_promotion_with_safe_mcp_tools(
        self,
    ) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        gate = workflow.index("Gate backend promotion on AgentCore MCP tools")
        full_apply = workflow.index(
            "Configure Cognito and authenticated infrastructure"
        )

        self.assertLess(gate, full_apply)
        self.assertIn("agentcore_mcp_smoke_test.py", workflow[gate:full_apply])
        self.assertIn("get_schema", SMOKE_TEST.TOOL_NAMES)
        self.assertIn("get_database_info", SMOKE_TEST.TOOL_NAMES)
        self.assertIn("rollback_mcp", workflow[gate:full_apply])


if __name__ == "__main__":
    unittest.main()
