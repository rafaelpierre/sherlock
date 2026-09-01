"""Deterministic suite orchestration and summary calculation."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from sherlock.evaluation.models import (
    CaseStatus,
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
            (
                status,
                validation_passed,
                execution_passed,
                semantic_correct,
            ) = _evaluate_case(suite, case, output)
            return EvaluationCaseResult(
                suite=suite.name,
                case_id=case.id,
                status=status,
                latency_ms=_elapsed_ms(started, self._clock()),
                repair_count=_repair_count(output),
                output=output,
                validation_passed=validation_passed,
                execution_passed=execution_passed,
                semantic_correct=semantic_correct,
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


def _evaluate_case(
    suite: EvaluationSuite, case: EvaluationCase, output: dict[str, Any]
) -> tuple[CaseStatus, bool | None, bool | None, bool | None]:
    if suite.kind != "rule_generation":
        return (
            "passed" if _contains_expected(output, case.expected) else "failed",
            None,
            None,
            None,
        )

    assert case.rule_oracle is not None
    validation_passed = output.get("valid") is True
    matched_ids = output.get("matched_transaction_ids")
    reference_ids = output.get("reference_transaction_ids")
    execution_passed = (
        validation_passed
        and isinstance(matched_ids, list)
        and isinstance(reference_ids, list)
    )
    semantic_correct = (
        _same_transaction_ids(matched_ids, reference_ids) if execution_passed else False
    )
    passed = (
        _contains_expected(output, case.expected)
        and validation_passed
        and execution_passed
        and semantic_correct
    )
    return (
        "passed" if passed else "failed",
        validation_passed,
        execution_passed,
        semantic_correct,
    )


def _same_transaction_ids(actual: list[Any], expected: list[Any]) -> bool:
    """Compare transaction IDs as a set; duplicate identifiers are not valid rows."""

    actual_ids = [str(value) for value in actual]
    expected_ids = [str(value) for value in expected]
    return len(actual_ids) == len(set(actual_ids)) and set(actual_ids) == set(
        expected_ids
    )


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
        validation_passed=sum(result.validation_passed is True for result in results),
        execution_passed=sum(result.execution_passed is True for result in results),
        semantic_correct=sum(result.semantic_correct is True for result in results),
        semantic_evaluated=sum(
            result.semantic_correct is not None for result in results
        ),
    )
