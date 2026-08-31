"""Run Text2SQL evaluation questions against the Sherlock backend API."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Literal

import click
import httpx

from sherlock_evals.oracles import ComparisonPolicy, RankKey, compare_result_sets
from sherlock_evals.safety import sql_safety_error

SOURCE_CASES = Path(__file__).parents[2] / "data" / "text2sql.json"
FailureType = Literal["http", "execution", "safety", "semantic"]


@dataclass(frozen=True)
class EvaluationCase:
    """One validated Text2SQL question, oracle, and comparison policy."""

    case_id: str
    question: str
    expected_columns: list[str]
    expected_rows: list[list[Any]]
    policy: ComparisonPolicy


@dataclass(frozen=True)
class CaseResult:
    """Machine-readable result for one HTTP evaluation case."""

    case_id: str
    status: Literal["passed", "failed"]
    failure_type: FailureType | None
    latency_ms: float
    message: str | None
    sql: str | None
    expected_result: dict[str, list[Any]]
    actual_result: dict[str, list[Any]] | None


def _case_error(case_id: str, message: str) -> click.ClickException:
    return click.ClickException(f"Case {case_id} {message}")


def _load_policy(case_id: str, value: Any) -> ComparisonPolicy:
    if not isinstance(value, dict):
        raise _case_error(case_id, "must have a comparison object")
    required = {
        "row_order",
        "column_order",
        "absolute_tolerance",
        "relative_tolerance",
        "null_equivalents",
    }
    allowed = required | {"rank_by"}
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise _case_error(
            case_id, f"comparison must contain exactly {sorted(required)!r}"
        )
    row_order = value["row_order"]
    column_order = value["column_order"]
    if row_order not in {"sensitive", "insensitive", "ranked"}:
        raise _case_error(case_id, "has an invalid comparison.row_order")
    if column_order not in {"sensitive", "insensitive"}:
        raise _case_error(case_id, "has an invalid comparison.column_order")
    absolute_tolerance = value["absolute_tolerance"]
    relative_tolerance = value["relative_tolerance"]
    if (
        isinstance(absolute_tolerance, bool)
        or not isinstance(absolute_tolerance, (int, float))
        or absolute_tolerance < 0
    ):
        raise _case_error(case_id, "has an invalid absolute tolerance")
    if (
        isinstance(relative_tolerance, bool)
        or not isinstance(relative_tolerance, (int, float))
        or relative_tolerance < 0
    ):
        raise _case_error(case_id, "has an invalid relative tolerance")
    null_equivalents = value["null_equivalents"]
    if not isinstance(null_equivalents, list) or not all(
        isinstance(item, str) for item in null_equivalents
    ):
        raise _case_error(case_id, "has invalid null equivalents")
    raw_rank_by = value.get("rank_by", [])
    if not isinstance(raw_rank_by, list) or not all(
        isinstance(item, dict)
        and set(item) == {"column", "direction"}
        and isinstance(item["column"], str)
        and bool(item["column"].strip())
        and item["direction"] in {"ascending", "descending"}
        for item in raw_rank_by
    ):
        raise _case_error(case_id, "has invalid comparison.rank_by")
    if (row_order == "ranked") != bool(raw_rank_by):
        raise _case_error(case_id, "must configure rank_by only for ranked rows")
    return ComparisonPolicy(
        row_order=row_order,
        column_order=column_order,
        absolute_tolerance=float(absolute_tolerance),
        relative_tolerance=float(relative_tolerance),
        null_equivalents=tuple(item.strip().casefold() for item in null_equivalents),
        rank_by=tuple(
            RankKey(column=item["column"], direction=item["direction"])
            for item in raw_rank_by
        ),
    )


def _load_expected(case_id: str, value: Any) -> tuple[list[str], list[list[Any]]]:
    if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
        raise _case_error(case_id, "must have expected columns and rows")
    columns = value["columns"]
    rows = value["rows"]
    if (
        not isinstance(columns, list)
        or not columns
        or not all(isinstance(column, str) and column.strip() for column in columns)
    ):
        raise _case_error(case_id, "must have non-empty expected column names")
    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        raise _case_error(case_id, "must have an expected row list")
    if any(len(row) != len(columns) for row in rows):
        raise _case_error(case_id, "has an expected row with the wrong width")
    return columns, rows


def _load_cases(path: Path) -> list[EvaluationCase]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise click.ClickException(f"Could not read cases from {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}"
        ) from exc

    cases = payload.get("cases") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and payload.get("schema_version") != 2:
        raise click.ClickException("Cases file must use schema_version 2")
    if not isinstance(cases, list) or not cases:
        raise click.ClickException("Cases file must contain a non-empty 'cases' list")

    validated: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise click.ClickException(f"Case {index} must be an object")
        raw_case_id = case.get("id")
        raw_question = case.get("question")
        if not isinstance(raw_case_id, str) or not raw_case_id.strip():
            raise click.ClickException(f"Case {index} must have a non-empty string id")
        case_id = raw_case_id.strip()
        if case_id in seen_ids:
            raise click.ClickException(f"Duplicate case id: {case_id}")
        if not isinstance(raw_question, str) or not raw_question.strip():
            raise _case_error(case_id, "must have a non-empty question")
        expected_columns, expected_rows = _load_expected(case_id, case.get("expected"))
        policy = _load_policy(case_id, case.get("comparison"))
        seen_ids.add(case_id)
        validated.append(
            EvaluationCase(
                case_id=case_id,
                question=raw_question.strip(),
                expected_columns=expected_columns,
                expected_rows=expected_rows,
                policy=policy,
            )
        )
    return validated


def _load_default_cases() -> list[EvaluationCase]:
    if SOURCE_CASES.is_file():
        return _load_cases(SOURCE_CASES)
    resource = files("sherlock_evals").joinpath("data/text2sql.json")
    with as_file(resource) as path:
        return _load_cases(path)


def _failed(
    case: EvaluationCase,
    failure_type: FailureType,
    message: str,
    latency_ms: float,
    *,
    sql: str | None = None,
    actual_result: dict[str, list[Any]] | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case.case_id,
        status="failed",
        failure_type=failure_type,
        latency_ms=latency_ms,
        message=message,
        sql=sql,
        expected_result={
            "columns": case.expected_columns,
            "rows": case.expected_rows,
        },
        actual_result=actual_result,
    )


def _evaluate_response(
    response: httpx.Response,
    case: EvaluationCase,
    latency_ms: float,
) -> CaseResult:
    if not response.is_success:
        return _failed(
            case,
            "http",
            f"HTTP {response.status_code}: {response.text[:200]}",
            latency_ms,
        )
    try:
        payload: Any = response.json()
    except ValueError:
        return _failed(case, "execution", "backend returned invalid JSON", latency_ms)
    if not isinstance(payload, dict):
        return _failed(
            case, "execution", "backend response must be a JSON object", latency_ms
        )
    if payload.get("question") != case.question:
        return _failed(
            case, "execution", "backend did not preserve the question", latency_ms
        )
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return _failed(
            case, "execution", "backend did not include generated SQL", latency_ms
        )
    if safety_error := sql_safety_error(sql):
        return _failed(case, "safety", safety_error, latency_ms, sql=sql)

    result = payload.get("result")
    if not isinstance(result, dict):
        return _failed(
            case,
            "execution",
            "backend did not include a result object",
            latency_ms,
            sql=sql,
        )
    columns = result.get("columns")
    rows = result.get("rows")
    if not isinstance(columns, list) or not all(
        isinstance(column, str) for column in columns
    ):
        return _failed(
            case,
            "execution",
            "result columns must be a string list",
            latency_ms,
            sql=sql,
        )
    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        return _failed(
            case, "execution", "result rows must be a row list", latency_ms, sql=sql
        )
    if result.get("truncated") is True:
        return _failed(case, "execution", "result was truncated", latency_ms, sql=sql)
    row_count = result.get("row_count")
    if row_count is not None and row_count != len(rows):
        return _failed(
            case,
            "execution",
            "result row_count does not match rows",
            latency_ms,
            sql=sql,
        )
    actual_result = {"columns": columns, "rows": rows}
    comparison = compare_result_sets(
        case.expected_columns,
        case.expected_rows,
        columns,
        rows,
        case.policy,
    )
    if not comparison.matches:
        return _failed(
            case,
            "semantic",
            comparison.message or "result set differs from the oracle",
            latency_ms,
            sql=sql,
            actual_result=actual_result,
        )
    return CaseResult(
        case_id=case.case_id,
        status="passed",
        failure_type=None,
        latency_ms=latency_ms,
        message=None,
        sql=sql,
        expected_result={
            "columns": case.expected_columns,
            "rows": case.expected_rows,
        },
        actual_result=actual_result,
    )


def _summary(results: list[CaseResult]) -> dict[str, int | float]:
    counts = {
        failure_type: sum(result.failure_type == failure_type for result in results)
        for failure_type in ("http", "execution", "safety", "semantic")
    }
    passed = sum(result.status == "passed" for result in results)
    semantic_cases = passed + counts["semantic"]
    return {
        "total": len(results),
        "passed": passed,
        "http_failures": counts["http"],
        "execution_failures": counts["execution"],
        "safety_failures": counts["safety"],
        "semantic_failures": counts["semantic"],
        "semantic_cases": semantic_cases,
        "semantic_accuracy": passed / semantic_cases if semantic_cases else 0.0,
    }


def _write_report(
    path: Path,
    *,
    base_url: str,
    started_at: datetime,
    results: list[CaseResult],
) -> None:
    report = {
        "schema_version": 1,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "base_url": base_url,
        "results": [asdict(result) for result in results],
        "summary": _summary(results),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Could not write report to {path}: {exc}") from exc


@click.command()
@click.option(
    "--base-url",
    envvar="SHERLOCK_BACKEND_URL",
    default="http://localhost:8080",
    show_default=True,
    help="Sherlock backend base URL.",
)
@click.option(
    "--cases",
    "cases_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    show_default="bundled data/text2sql.json",
    help="JSON file containing Text2SQL evaluation cases and oracles.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Write a machine-readable JSON report to this path.",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0.1),
    default=60.0,
    show_default=True,
    help="Timeout for each backend request in seconds.",
)
def main(
    base_url: str,
    cases_path: Path | None,
    output_path: Path | None,
    timeout: float,
) -> None:
    """Run every Text2SQL case against POST /v1/query."""

    cases = _load_cases(cases_path) if cases_path else _load_default_cases()
    normalized_base_url = base_url.rstrip("/")
    started_at = datetime.now(UTC)
    results: list[CaseResult] = []
    with httpx.Client(base_url=normalized_base_url, timeout=timeout) as client:
        for case in cases:
            started = time.perf_counter()
            try:
                response = client.post("/v1/query", json={"question": case.question})
                latency_ms = round((time.perf_counter() - started) * 1_000, 3)
                result = _evaluate_response(response, case, latency_ms)
            except httpx.HTTPError as exc:
                latency_ms = round((time.perf_counter() - started) * 1_000, 3)
                result = _failed(case, "http", str(exc), latency_ms)
            results.append(result)
            if result.status == "passed":
                click.echo(f"PASS {case.case_id}")
            else:
                click.echo(
                    f"FAIL {case.case_id} [{result.failure_type}]: {result.message}"
                )

    summary = _summary(results)
    click.echo(
        "\n"
        f"{summary['passed']}/{summary['total']} cases passed; "
        f"semantic={summary['semantic_failures']}, "
        f"http={summary['http_failures']}, "
        f"execution={summary['execution_failures']}, "
        f"safety={summary['safety_failures']}"
    )
    if output_path is not None:
        _write_report(
            output_path,
            base_url=normalized_base_url,
            started_at=started_at,
            results=results,
        )
        click.echo(f"Report: {output_path}")
    if summary["passed"] != summary["total"]:
        raise click.exceptions.Exit(1)
