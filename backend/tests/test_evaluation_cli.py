import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sherlock.evaluation.cli import (
    EXIT_CASE_FAILURE,
    EXIT_CONFIGURATION_ERROR,
    EXIT_SUCCESS,
    run_cli,
)
from sherlock.evaluation.models import EvaluationReport

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
REPORT_SCHEMA = Path(__file__).parents[2] / "evals" / "schemas" / "report.schema.json"


def write_suite(directory, *, expected: int = 3, response: int | None = 3) -> None:
    directory.mkdir()
    case = {
        "id": "count",
        "prompt": "Count transactions",
        "expected": {"count": expected},
    }
    if response is not None:
        case["fixture_response"] = {"count": response, "attempts": 1}
    payload = {
        "schema_version": 1,
        "name": "smoke",
        "description": "CLI smoke suite.",
        "kind": "text2sql",
        "cases": [case],
    }
    (directory / "smoke.json").write_text(json.dumps(payload), encoding="utf-8")


def arguments(fixtures, output) -> list[str]:
    return [
        "--fixtures",
        str(fixtures),
        "--output",
        str(output),
        "--dataset-revision",
        "fixture-v1",
    ]


def test_cli_runs_offline_writes_valid_report_and_prints_summary(
    tmp_path, capsys, monkeypatch
) -> None:
    fixtures = tmp_path / "fixtures"
    output = tmp_path / "report.json"
    write_suite(fixtures)

    def forbid_live_service(*args, **kwargs):
        raise AssertionError("offline execution must not construct live services")

    monkeypatch.setattr(
        "sherlock.evaluation.cli.LiveEvaluationService", forbid_live_service
    )

    exit_code = run_cli(arguments(fixtures, output), now=NOW)

    assert exit_code == EXIT_SUCCESS
    report = EvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert report.metadata.model == "deterministic-fixture-v1"
    assert report.metadata.started_at == NOW
    assert report.metadata.dataset_revision == "fixture-v1"
    assert report.metadata.mode == "fixture"
    assert report.metadata.configuration["rule_generation_comparison"] == (
        "reference-predicate-exact-transaction-ids-v1"
    )
    assert report.summary.passed == 1
    assert "Cases: 1 | Passed: 1" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("expected", "response", "status"),
    [(4, 3, "failed"), (3, None, "error")],
)
def test_cli_returns_one_for_case_failures_and_errors(
    tmp_path, expected: int, response: int | None, status: str
) -> None:
    fixtures = tmp_path / "fixtures"
    output = tmp_path / "report.json"
    write_suite(fixtures, expected=expected, response=response)

    exit_code = run_cli(arguments(fixtures, output), now=NOW)

    assert exit_code == EXIT_CASE_FAILURE
    report = EvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert report.results[0].status == status


def test_cli_reports_malformed_unknown_and_output_errors(tmp_path, capsys) -> None:
    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "bad.json").write_text("{", encoding="utf-8")
    assert (
        run_cli(arguments(malformed, tmp_path / "unused.json"), now=NOW)
        == EXIT_CONFIGURATION_ERROR
    )
    assert "Malformed JSON" in capsys.readouterr().err

    fixtures = tmp_path / "fixtures"
    write_suite(fixtures)
    unknown_args = [*arguments(fixtures, tmp_path / "unused.json"), "--suite", "absent"]
    assert run_cli(unknown_args, now=NOW) == EXIT_CONFIGURATION_ERROR
    assert "Unknown suite" in capsys.readouterr().err

    output_directory = tmp_path / "output-directory"
    output_directory.mkdir()
    assert (
        run_cli(arguments(fixtures, output_directory), now=NOW)
        == EXIT_CONFIGURATION_ERROR
    )
    assert "Evaluation configuration error" in capsys.readouterr().err


def test_cli_requires_an_explicit_model_for_live_execution(tmp_path) -> None:
    fixtures = tmp_path / "fixtures"
    write_suite(fixtures)

    with pytest.raises(SystemExit) as exc_info:
        run_cli([*arguments(fixtures, tmp_path / "report.json"), "--live"])

    assert exc_info.value.code == EXIT_CONFIGURATION_ERROR


def test_documented_report_schema_matches_the_runtime_contract() -> None:
    documented = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    generated = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **EvaluationReport.model_json_schema(),
    }

    assert documented == generated


def test_committed_v1_report_remains_readable_with_default_semantic_metrics() -> None:
    report_path = (
        Path(__file__).parents[2] / "evals" / "results" / "runner-smoke-2026-08-30.json"
    )

    report = EvaluationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )

    assert report.schema_version == 1
    assert report.summary.validation_passed == 0
    assert report.summary.execution_passed == 0
    assert report.summary.semantic_correct == 0


def test_committed_rule_generation_golden_suite_runs_offline(tmp_path) -> None:
    output = tmp_path / "rule-generation.json"
    fixtures = Path(__file__).parents[2] / "evals" / "cases"

    exit_code = run_cli(
        [
            "--fixtures",
            str(fixtures),
            "--suite",
            "rule-generation-golden",
            "--output",
            str(output),
            "--dataset-revision",
            "fixture-v1",
        ],
        now=NOW,
    )

    report = EvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert exit_code == EXIT_SUCCESS
    assert report.summary.model_dump(
        include={
            "total",
            "passed",
            "validation_passed",
            "execution_passed",
            "semantic_correct",
        }
    ) == {
        "total": 10,
        "passed": 10,
        "validation_passed": 10,
        "execution_passed": 10,
        "semantic_correct": 10,
    }
