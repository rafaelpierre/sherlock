from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sherlock.services.backtest import InvalidBacktestRule
from sherlock.services.rule_comparison import (
    InvalidComparisonRule,
    RuleComparisonService,
)
from sherlock.services.rule_validation import (
    RuleValidationError,
    RuleValidationResult,
)


def metrics(
    *,
    precision: float | None,
    recall: float | None,
    transactions_flagged: int,
    fraud_caught: int,
    fraud_value_captured_usd: float,
) -> dict[str, Any]:
    return {
        "precision": precision,
        "recall": recall,
        "transactions_flagged": transactions_flagged,
        "fraud_caught": fraud_caught,
        "fraud_value_captured_usd": fraud_value_captured_usd,
    }


class StubBacktester:
    def __init__(self, results: dict[str, dict[str, Any]]) -> None:
        self.results = results
        self.calls: list[str] = []

    async def backtest(self, rule: str) -> dict[str, Any]:
        self.calls.append(rule)
        return {"rule": rule, "metrics": self.results[rule]}


class InvalidPreviousBacktester:
    async def backtest(self, rule: str) -> dict[str, Any]:
        if rule == "valid_rule":
            return {
                "rule": rule,
                "metrics": metrics(
                    precision=0.5,
                    recall=0.4,
                    transactions_flagged=10,
                    fraud_caught=4,
                    fraud_value_captured_usd=100.0,
                ),
            }
        raise InvalidBacktestRule(
            RuleValidationResult(
                valid=False,
                rule=None,
                errors=[
                    RuleValidationError(
                        code="UNKNOWN_COLUMN",
                        message="Unknown column 'risk'.",
                    )
                ],
            )
        )


def test_compare_calculates_current_minus_previous_deltas() -> None:
    backtester = StubBacktester(
        {
            "current": metrics(
                precision=0.6,
                recall=0.35,
                transactions_flagged=80,
                fraud_caught=21,
                fraud_value_captured_usd=1_250.5,
            ),
            "previous": metrics(
                precision=0.5,
                recall=0.4,
                transactions_flagged=100,
                fraud_caught=23,
                fraud_value_captured_usd=1_400.0,
            ),
        }
    )

    result = asyncio.run(
        RuleComparisonService(backtester).compare("current", "previous")
    )

    assert backtester.calls == ["current", "previous"]
    assert result["delta"] == {
        "precision": pytest.approx(0.1),
        "recall": pytest.approx(-0.05),
        "transactions_flagged": -20,
        "fraud_caught": -2,
        "fraud_value_captured_usd": pytest.approx(-149.5),
    }


def test_compare_preserves_undefined_ratio_deltas() -> None:
    backtester = StubBacktester(
        {
            "current": metrics(
                precision=None,
                recall=0.5,
                transactions_flagged=0,
                fraud_caught=0,
                fraud_value_captured_usd=0.0,
            ),
            "previous": metrics(
                precision=0.5,
                recall=None,
                transactions_flagged=10,
                fraud_caught=2,
                fraud_value_captured_usd=50.0,
            ),
        }
    )

    result = asyncio.run(
        RuleComparisonService(backtester).compare("current", "previous")
    )

    assert result["delta"]["precision"] is None
    assert result["delta"]["recall"] is None


def test_compare_identifies_invalid_previous_rule() -> None:
    service = RuleComparisonService(InvalidPreviousBacktester())

    with pytest.raises(InvalidComparisonRule) as raised:
        asyncio.run(service.compare("valid_rule", "risk > 10"))

    assert raised.value.role == "previous"
    assert raised.value.validation.errors[0].code == "UNKNOWN_COLUMN"
