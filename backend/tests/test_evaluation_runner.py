import asyncio
from datetime import UTC, datetime

from sherlock.evaluation.models import EvaluationSuite, RunMetadata
from sherlock.evaluation.runner import EvaluationRunner


class FakeService:
    def __init__(self, responses: dict[str, dict | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    async def execute(self, kind, case) -> dict:
        self.calls.append((kind, case.id))
        response = self.responses[case.id]
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


def make_suite() -> EvaluationSuite:
    return EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "runner",
            "description": "Runner behavior.",
            "kind": "text2sql",
            "cases": [
                {
                    "id": "passes",
                    "prompt": "pass",
                    "expected": {"result": {"rows": [[1]]}},
                },
                {
                    "id": "fails",
                    "prompt": "fail",
                    "expected": {"value": 2},
                },
                {
                    "id": "errors",
                    "prompt": "error",
                    "expected": {"value": 3},
                },
            ],
        }
    )


def metadata() -> RunMetadata:
    return RunMetadata(
        started_at=datetime(2026, 8, 30, tzinfo=UTC),
        model="fake-v1",
        configuration={"temperature": 0},
        dataset_revision="test-data-v1",
        mode="fixture",
        selected_suites=["runner"],
    )


def test_runner_continues_after_failures_and_summarizes_metadata() -> None:
    service = FakeService(
        {
            "passes": {
                "result": {"rows": [[1]], "columns": ["count"]},
                "attempts": 2,
            },
            "fails": {"value": 9, "repair_count": 0},
            "errors": RuntimeError("service unavailable"),
        }
    )
    times = iter([1.0, 1.01, 2.0, 2.02, 3.0, 3.03])

    report = asyncio.run(
        EvaluationRunner(service, clock=lambda: next(times)).run(
            [make_suite()], metadata()
        )
    )

    assert service.calls == [
        ("text2sql", "passes"),
        ("text2sql", "fails"),
        ("text2sql", "errors"),
    ]
    assert [result.status for result in report.results] == [
        "passed",
        "failed",
        "error",
    ]
    assert report.results[0].repair_count == 1
    assert report.results[2].output is None
    assert report.results[2].error == "RuntimeError: service unavailable"
    assert report.summary.model_dump() == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "errors": 1,
        "total_latency_ms": 60.0,
        "repair_count": 1,
        "repair_rate": 0.5,
    }
    assert report.metadata.dataset_revision == "test-data-v1"


def test_runner_handles_an_all_error_run() -> None:
    suite = make_suite().model_copy(update={"cases": [make_suite().cases[2]]})
    service = FakeService({"errors": ValueError("bad case")})

    report = asyncio.run(
        EvaluationRunner(service, clock=lambda: 1.0).run([suite], metadata())
    )

    assert report.summary.repair_rate == 0
    assert report.summary.total_latency_ms == 0


def test_runner_does_not_treat_booleans_as_numeric_oracle_matches() -> None:
    suite = make_suite().model_copy(
        update={
            "cases": [
                make_suite().cases[0].model_copy(update={"expected": {"valid": True}})
            ]
        }
    )
    service = FakeService({"passes": {"valid": 1}})

    report = asyncio.run(
        EvaluationRunner(service, clock=lambda: 1.0).run([suite], metadata())
    )

    assert report.results[0].status == "failed"
