from __future__ import annotations

import asyncio

import pytest

from sherlock.services.backtest import (
    BacktestError,
    BacktestService,
    InvalidBacktestRule,
    _ratio,
)
from sherlock.services.rule_validation import (
    RuleSchemaError,
    RuleValidationError,
    RuleValidationResult,
)
from sherlock.services.text2sql import ExecutionResult, QueryData, QueryExecutionError


class StubValidator:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    async def validate(self, rule: str) -> RuleValidationResult:
        if self.valid:
            return RuleValidationResult(True, rule.upper(), [])
        return RuleValidationResult(
            False,
            None,
            [RuleValidationError("UNKNOWN_COLUMN", "Unknown column")],
        )


class StubExecutor:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.sql = ""

    async def execute(self, sql: str) -> ExecutionResult:
        self.sql = sql
        return self.result


class FailingValidator:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception

    async def validate(self, rule: str) -> RuleValidationResult:
        raise self.exception


class FailingExecutor:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception

    async def execute(self, sql: str) -> ExecutionResult:
        raise self.exception


def aggregate_result(values: list[object] | None = None) -> ExecutionResult:
    columns = [
        "population",
        "labelled_population",
        "fraud_total",
        "transactions_flagged",
        "unlabelled_flagged",
        "fraud_caught",
        "false_positives",
        "false_negatives",
        "true_negatives",
        "fraud_value_total_usd",
        "fraud_value_captured_usd",
        "observed_days",
    ]
    values = values or [100, 80, 10, 8, 1, 5, 2, 5, 68, 1000.0, 600.0, 4]
    return ExecutionResult(
        sql="SELECT aggregate",
        data=QueryData(columns, [values], 1, False),
    )


def test_backtest_calculates_quality_and_operational_metrics() -> None:
    executor = StubExecutor(aggregate_result())
    service = BacktestService(
        StubValidator(),
        executor,
    )

    result = asyncio.run(service.backtest("amount_usd > 1000"))

    assert result["rule"] == "AMOUNT_USD > 1000"
    metrics = result["metrics"]
    assert metrics["precision"] == 5 / 7
    assert metrics["recall"] == 0.5
    assert metrics["false_positive_rate"] == 2 / 70
    assert metrics["fraud_value_recall"] == 0.6
    assert metrics["alerts_per_day"] == 2.0
    assert "FROM fraud_transactions" in executor.sql


def test_zero_denominators_return_null_ratios() -> None:
    service = BacktestService(
        StubValidator(),
        StubExecutor(aggregate_result([0] * 12)),
    )

    metrics = asyncio.run(service.backtest("amount_usd > 1000"))["metrics"]

    assert metrics["precision"] is None
    assert metrics["recall"] is None
    assert metrics["false_positive_rate"] is None
    assert metrics["fraud_value_recall"] is None
    assert metrics["alerts_per_day"] is None
    assert _ratio(1, 2) == 0.5


def test_invalid_rule_stops_before_execution() -> None:
    service = BacktestService(
        StubValidator(False),
        StubExecutor(aggregate_result()),
    )

    with pytest.raises(InvalidBacktestRule) as caught:
        asyncio.run(service.backtest("missing > 1"))

    assert caught.value.validation.errors[0].code == "UNKNOWN_COLUMN"


def test_execution_error_is_exposed_as_backtest_error() -> None:
    service = BacktestService(
        StubValidator(),
        StubExecutor(
            ExecutionResult(
                sql="SELECT aggregate",
                error={"type": "QUERY_TIMEOUT", "message": "Replay timed out"},
            )
        ),
    )

    with pytest.raises(BacktestError, match="Replay timed out"):
        asyncio.run(service.backtest("amount_usd > 1"))


@pytest.mark.parametrize(
    ("validator", "executor", "message"),
    [
        (
            FailingValidator(RuleSchemaError("The schema service is unavailable.")),
            StubExecutor(aggregate_result()),
            "schema service is unavailable",
        ),
        (
            FailingValidator(QueryExecutionError("Validation query unavailable.")),
            StubExecutor(aggregate_result()),
            "Validation query unavailable",
        ),
        (
            StubValidator(),
            FailingExecutor(QueryExecutionError("Replay query unavailable.")),
            "Replay query unavailable",
        ),
    ],
)
def test_known_dependency_errors_are_normalized_to_backtest_error(
    validator: StubValidator | FailingValidator,
    executor: StubExecutor | FailingExecutor,
    message: str,
) -> None:
    service = BacktestService(validator, executor)

    with pytest.raises(BacktestError, match=message):
        asyncio.run(service.backtest("amount_usd > 1"))


def test_unrelated_runtime_error_is_not_normalized() -> None:
    service = BacktestService(
        FailingValidator(RuntimeError("programming defect")),
        StubExecutor(aggregate_result()),
    )

    with pytest.raises(RuntimeError, match="programming defect"):
        asyncio.run(service.backtest("amount_usd > 1"))


@pytest.mark.parametrize(
    "result",
    [
        ExecutionResult(sql="SELECT aggregate"),
        ExecutionResult(
            sql="SELECT aggregate",
            data=QueryData(["population"], [["not-an-int"]], 1, False),
        ),
    ],
)
def test_invalid_aggregate_data_is_rejected(result: ExecutionResult) -> None:
    service = BacktestService(
        StubValidator(),
        StubExecutor(result),
    )

    with pytest.raises(BacktestError, match="invalid aggregate data"):
        asyncio.run(service.backtest("amount_usd > 1"))


def test_lifecycle_is_started_once_and_closed() -> None:
    starts = 0
    closes = 0

    async def start() -> None:
        nonlocal starts
        starts += 1

    def close() -> None:
        nonlocal closes
        closes += 1

    service = BacktestService(
        StubValidator(),
        StubExecutor(aggregate_result()),
        start_callback=start,
        close_callback=close,
    )

    asyncio.run(service.backtest("amount_usd > 1"))
    asyncio.run(service.backtest("amount_usd > 2"))
    service.close()

    assert starts == 1
    assert closes == 1
