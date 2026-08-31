"""Run Text2SQL evaluation questions against the Sherlock backend API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import httpx

DEFAULT_CASES = Path(__file__).parents[2] / "data" / "text2sql.json"


def _load_cases(path: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise click.ClickException(f"Could not read cases from {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}"
        ) from exc

    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise click.ClickException("Cases file must contain a non-empty 'cases' list")

    validated: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise click.ClickException(f"Case {index} must be an object")
        case_id = case.get("id")
        question = case.get("question")
        if not isinstance(case_id, str) or not case_id.strip():
            raise click.ClickException(f"Case {index} must have a non-empty string id")
        if case_id in seen_ids:
            raise click.ClickException(f"Duplicate case id: {case_id}")
        if not isinstance(question, str) or not question.strip():
            raise click.ClickException(f"Case {case_id} must have a non-empty question")
        seen_ids.add(case_id)
        validated.append({"id": case_id, "question": question})
    return validated


def _response_error(response: httpx.Response, question: str) -> str | None:
    if not response.is_success:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    try:
        payload: Any = response.json()
    except ValueError:
        return "Backend returned invalid JSON"
    if not isinstance(payload, dict):
        return "Backend response must be a JSON object"
    if payload.get("question") != question:
        return "Backend response did not preserve the question"
    if not isinstance(payload.get("sql"), str) or not payload["sql"].strip():
        return "Backend response did not include generated SQL"
    result = payload.get("result")
    if not isinstance(result, dict):
        return "Backend response did not include a result object"
    if not isinstance(result.get("columns"), list) or not isinstance(
        result.get("rows"), list
    ):
        return "Backend result must include columns and rows"
    return None


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
    default=DEFAULT_CASES,
    show_default=True,
    help="JSON file containing Text2SQL evaluation cases.",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0.1),
    default=60.0,
    show_default=True,
    help="Timeout for each backend request in seconds.",
)
def main(base_url: str, cases_path: Path, timeout: float) -> None:
    """Run every Text2SQL case against POST /v1/query."""

    cases = _load_cases(cases_path)
    failures = 0
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        for case in cases:
            try:
                response = client.post("/v1/query", json={"question": case["question"]})
                error = _response_error(response, case["question"])
            except httpx.HTTPError as exc:
                error = str(exc)

            if error is None:
                click.echo(f"PASS {case['id']}")
            else:
                failures += 1
                click.echo(f"FAIL {case['id']}: {error}")

    passed = len(cases) - failures
    click.echo(f"\n{passed}/{len(cases)} cases passed")
    if failures:
        raise click.exceptions.Exit(1)
