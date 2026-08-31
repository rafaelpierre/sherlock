from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from sherlock_evals.cli import main


def _write_cases(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "count-transactions", "question": "How many?"},
                    {"id": "fraud-rate", "question": "What fraud rate?"},
                ]
            }
        ),
        encoding="utf-8",
    )


def test_cli_posts_every_case_and_reports_success(tmp_path: Path, monkeypatch) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(cases_path)
    questions: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://localhost:8080/v1/query"
        question = json.loads(request.content)["question"]
        questions.append(question)
        return httpx.Response(
            200,
            json={
                "question": question,
                "sql": "SELECT 1",
                "result": {"columns": ["value"], "rows": [[1]]},
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 0
    assert questions == ["How many?", "What fraud rate?"]
    assert "PASS count-transactions" in result.output
    assert "2/2 cases passed" in result.output


def test_cli_continues_after_a_failed_response(tmp_path: Path, monkeypatch) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(cases_path)

    def handler(request: httpx.Request) -> httpx.Response:
        question = json.loads(request.content)["question"]
        if question == "How many?":
            return httpx.Response(502, text="model unavailable")
        return httpx.Response(
            200,
            json={
                "question": question,
                "sql": "SELECT 1",
                "result": {"columns": ["value"], "rows": [[1]]},
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert "FAIL count-transactions: HTTP 502" in result.output
    assert "PASS fraud-rate" in result.output
    assert "1/2 cases passed" in result.output


def test_cli_normalizes_case_values(tmp_path: Path, monkeypatch) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [{"id": " case ", "question": " How many? "}]}),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        question = json.loads(request.content)["question"]
        assert question == "How many?"
        return httpx.Response(
            200,
            json={
                "question": question,
                "sql": "SELECT 1",
                "result": {"columns": ["value"], "rows": [[1]]},
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 0
    assert "PASS case" in result.output


def test_cli_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {"id": "duplicate", "question": "First"},
                    {"id": "duplicate", "question": "Second"},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert "Duplicate case id: duplicate" in result.output


def test_cli_rejects_malformed_json(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("{", encoding="utf-8")

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert "Invalid JSON" in result.output


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (None, "Backend returned invalid JSON"),
        ([], "Backend response must be a JSON object"),
        ({"question": "wrong"}, "did not preserve the question"),
        ({"question": "How many?", "sql": ""}, "generated SQL"),
        (
            {"question": "How many?", "sql": "SELECT 1"},
            "result object",
        ),
        (
            {
                "question": "How many?",
                "sql": "SELECT 1",
                "result": {"columns": "value", "rows": [[1]]},
            },
            "columns and rows",
        ),
    ],
)
def test_cli_rejects_malformed_backend_responses(
    tmp_path: Path, monkeypatch, payload: object, expected_error: str
) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [{"id": "case", "question": "How many?"}]}),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        if payload is None:
            return httpx.Response(200, text="not json")
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert expected_error in result.output
