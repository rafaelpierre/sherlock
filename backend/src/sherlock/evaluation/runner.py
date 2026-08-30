"""Deterministic suite orchestration and summary calculation."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from sherlock.evaluation.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationReport,
    EvaluationSuite,
    EvaluationSummary,
    RunMetadata,
)
from sherlock.evaluation.services import EvaluationService


class EvaluationRunner:
    """Execute every selected case while isolating individual failures."""

    def __init__(
        self,
        service: EvaluationService,
        *,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._service = service
        self._clock = clock

    async def run(
        self, suites: list[EvaluationSuite], metadata: RunMetadata
    ) -> EvaluationReport:
        results: list[EvaluationCaseResult] = []
        for suite in suites:
            for case in suite.cases:
                results.append(await self._run_case(suite, case))
        return EvaluationReport(
            schema_version=1,
            metadata=metadata,
            results=results,
            summary=_summarize(results),
        )

    async def _run_case(
        self, suite: EvaluationSuite, case: EvaluationCase
    ) -> EvaluationCaseResult:
        started = self._clock()
        try:
            output = await self._service.execute(suite.kind, case)
            status = "passed" if _contains_expected(output, case.expected) else "failed"
            return EvaluationCaseResult(
                suite=suite.name,
                case_id=case.id,
                status=status,
                latency_ms=_elapsed_ms(started, self._clock()),
                repair_count=_repair_count(output),
                output=output,
            )
        except Exception as exc:  # noqa: BLE001 - a case failure must not abort its suite
            return EvaluationCaseResult(
                suite=suite.name,
                case_id=case.id,
                status="error",
                latency_ms=_elapsed_ms(started, self._clock()),
                repair_count=0,
                error=f"{type(exc).__name__}: {exc}",
            )


def _contains_expected(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains_expected(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _contains_expected(actual_item, expected_item)
                for actual_item, expected_item in zip(actual, expected, strict=True)
            )
        )
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def _repair_count(output: dict[str, Any]) -> int:
    count = output.get("repair_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    attempts = output.get("attempts")
    if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 0:
        return attempts - 1
    return 0


def _elapsed_ms(started: float, finished: float) -> float:
    return round(max(0.0, finished - started) * 1_000, 3)


def _summarize(results: list[EvaluationCaseResult]) -> EvaluationSummary:
    completed = [result for result in results if result.status != "error"]
    repaired = sum(result.repair_count > 0 for result in completed)
    return EvaluationSummary(
        total=len(results),
        passed=sum(result.status == "passed" for result in results),
        failed=sum(result.status == "failed" for result in results),
        errors=sum(result.status == "error" for result in results),
        total_latency_ms=round(sum(result.latency_ms for result in results), 3),
        repair_count=sum(result.repair_count for result in results),
        repair_rate=repaired / len(completed) if completed else 0.0,
    )
