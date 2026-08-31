from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from sherlock_evals.cli import _load_cases, main


def _case(case_id: str, question: str, *, expected: object = 1) -> dict[str, object]:
    return {
        "id": case_id,
        "question": question,
        "expected": {"columns": ["value"], "rows": [[expected]]},
        "comparison": {
            "row_order": "sensitive",
            "column_order": "insensitive",
            "absolute_tolerance": 0.01,
            "relative_tolerance": 0.000001,
            "null_equivalents": ["n/a"],
        },
    }


def _write_cases(path: Path, cases: list[dict[str, object]] | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "cases": cases
                or [
                    _case("count-transactions", "How many?"),
                    _case("fraud-rate", "What fraud rate?"),
                ],
            }
        ),
        encoding="utf-8",
    )


def _patch_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )


def test_cli_posts_every_case_and_writes_machine_report(
    tmp_path: Path, monkeypatch
) -> None:
    cases_path = tmp_path / "cases.json"
    report_path = tmp_path / "reports" / "result.json"
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
                "sql": "SELECT 1 AS value",
                "result": {
                    "columns": [" VALUE "],
                    "rows": [[1.005]],
                    "row_count": 1,
                    "truncated": False,
                },
            },
        )

    _patch_client(monkeypatch, handler)
    result = CliRunner().invoke(
        main, ["--cases", str(cases_path), "--output", str(report_path)]
    )

    assert result.exit_code == 0
    assert questions == ["How many?", "What fraud rate?"]
    assert "PASS count-transactions" in result.output
    assert "2/2 cases passed" in result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["summary"] == {
        "total": 2,
        "passed": 2,
        "http_failures": 0,
        "execution_failures": 0,
        "safety_failures": 0,
        "semantic_failures": 0,
        "semantic_cases": 2,
        "semantic_accuracy": 1.0,
    }
    assert report["results"][0]["failure_type"] is None


def test_cli_reports_failure_categories_separately(tmp_path: Path, monkeypatch) -> None:
    cases_path = tmp_path / "cases.json"
    report_path = tmp_path / "report.json"
    _write_cases(
        cases_path,
        [
            _case("http", "http"),
            _case("execution", "execution"),
            _case("safety", "safety"),
            _case("semantic", "semantic"),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        question = json.loads(request.content)["question"]
        if question == "http":
            return httpx.Response(502, text="model unavailable")
        if question == "execution":
            return httpx.Response(200, text="not json")
        sql = "DELETE FROM fraud_transactions" if question == "safety" else "SELECT 2"
        return httpx.Response(
            200,
            json={
                "question": question,
                "sql": sql,
                "result": {
                    "columns": ["value"],
                    "rows": [[2]],
                    "row_count": 1,
                    "truncated": False,
                },
            },
        )

    _patch_client(monkeypatch, handler)
    result = CliRunner().invoke(
        main, ["--cases", str(cases_path), "--output", str(report_path)]
    )

    assert result.exit_code == 1
    assert "FAIL http [http]: HTTP 502" in result.output
    assert "FAIL execution [execution]" in result.output
    assert "FAIL safety [safety]" in result.output
    assert "FAIL semantic [semantic]" in result.output
    summary = json.loads(report_path.read_text(encoding="utf-8"))["summary"]
    assert summary == {
        "total": 4,
        "passed": 0,
        "http_failures": 1,
        "execution_failures": 1,
        "safety_failures": 1,
        "semantic_failures": 1,
        "semantic_cases": 1,
        "semantic_accuracy": 0.0,
    }


def test_cli_classifies_request_exception_as_http_failure(
    tmp_path: Path, monkeypatch
) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(cases_path, [_case("case", "Question")])

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    _patch_client(monkeypatch, handler)
    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert "FAIL case [http]: offline" in result.output


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({"cases": []}, "non-empty 'cases' list"),
        ({"cases": [None]}, "Case 1 must be an object"),
        ({"cases": [{"id": "", "question": "q"}]}, "non-empty string id"),
        (
            {"cases": [_case("duplicate", "First"), _case("duplicate", "Second")]},
            "Duplicate case id: duplicate",
        ),
        (
            {"cases": [{**_case("case", "Question"), "expected": {}}]},
            "expected columns and rows",
        ),
        (
            {
                "cases": [
                    {
                        **_case("case", "Question"),
                        "comparison": {
                            **_case("x", "x")["comparison"],
                            "row_order": "sometimes",
                        },
                    }
                ]
            },
            "invalid comparison.row_order",
        ),
    ],
)
def test_cli_rejects_invalid_case_contract(
    tmp_path: Path, payload: object, expected_error: str
) -> None:
    cases_path = tmp_path / "cases.json"
    if isinstance(payload, dict):
        payload.setdefault("schema_version", 2)
    cases_path.write_text(json.dumps(payload), encoding="utf-8")

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert expected_error in result.output


def test_load_cases_normalizes_case_values(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(cases_path, [_case(" case ", " Question ")])

    loaded = _load_cases(cases_path)

    assert loaded[0].case_id == "case"
    assert loaded[0].question == "Question"
    assert loaded[0].policy.null_equivalents == ("n/a",)


def test_committed_suite_has_an_explicit_oracle_for_every_question() -> None:
    cases_path = Path(__file__).parents[1] / "data" / "text2sql.json"

    loaded = _load_cases(cases_path)

    assert len(loaded) == 25
    assert all(case.expected_columns and case.policy.row_order for case in loaded)


def test_committed_suite_can_compare_all_oracles(tmp_path: Path, monkeypatch) -> None:
    cases_path = Path(__file__).parents[1] / "data" / "text2sql.json"
    cases = _load_cases(cases_path)
    cases_by_question = {case.question: case for case in cases}

    def handler(request: httpx.Request) -> httpx.Response:
        question = json.loads(request.content)["question"]
        case = cases_by_question[question]
        return httpx.Response(
            200,
            json={
                "question": question,
                "sql": "SELECT * FROM fraud_transactions",
                "result": {
                    "columns": case.expected_columns,
                    "rows": case.expected_rows,
                    "row_count": len(case.expected_rows),
                    "truncated": False,
                },
            },
        )

    _patch_client(monkeypatch, handler)
    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 0
    assert "25/25 cases passed" in result.output


def test_cli_rejects_malformed_json(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("{", encoding="utf-8")

    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert "Invalid JSON" in result.output


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ([], "JSON object"),
        ({"question": "wrong"}, "preserve the question"),
        ({"question": "Question", "sql": ""}, "generated SQL"),
        (
            {"question": "Question", "sql": "SELECT 1"},
            "result object",
        ),
        (
            {
                "question": "Question",
                "sql": "SELECT 1",
                "result": {"columns": "value", "rows": [[1]]},
            },
            "columns must be a string list",
        ),
        (
            {
                "question": "Question",
                "sql": "SELECT 1",
                "result": {"columns": ["value"], "rows": "wrong"},
            },
            "rows must be a row list",
        ),
        (
            {
                "question": "Question",
                "sql": "SELECT 1",
                "result": {
                    "columns": ["value"],
                    "rows": [[1]],
                    "row_count": 1,
                    "truncated": True,
                },
            },
            "result was truncated",
        ),
        (
            {
                "question": "Question",
                "sql": "SELECT 1",
                "result": {
                    "columns": ["value"],
                    "rows": [[1]],
                    "row_count": 2,
                    "truncated": False,
                },
            },
            "row_count does not match rows",
        ),
        (
            {
                "question": "Question",
                "sql": "SELECT 1",
                "result": {
                    "columns": ["value"],
                    "rows": [[1]],
                    "row_count": 1,
                },
            },
            "truncated must be a boolean",
        ),
        (
            {
                "question": "Question",
                "sql": "SELECT 1",
                "result": {
                    "columns": ["value"],
                    "rows": [[1]],
                    "row_count": True,
                    "truncated": False,
                },
            },
            "row_count must be a non-negative integer",
        ),
    ],
)
def test_cli_rejects_malformed_backend_responses(
    tmp_path: Path, monkeypatch, payload: object, expected_error: str
) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(cases_path, [_case("case", "Question")])

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    _patch_client(monkeypatch, handler)
    result = CliRunner().invoke(main, ["--cases", str(cases_path)])

    assert result.exit_code == 1
    assert expected_error in result.output
