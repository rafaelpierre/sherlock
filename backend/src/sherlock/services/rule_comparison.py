"""Deterministic comparison of current and previous candidate rules."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from sherlock.services.backtest import InvalidBacktestRule
from sherlock.services.rule_validation import RuleValidationResult

RuleRole = Literal["current", "previous"]


class Backtester(Protocol):
    async def backtest(self, rule: str) -> dict[str, Any]: ...


class InvalidComparisonRule(ValueError):
    """Identify which comparison input failed deterministic validation."""

    def __init__(
        self,
        role: RuleRole,
        validation: RuleValidationResult,
    ) -> None:
        super().__init__(f"The {role} candidate rule is invalid.")
        self.role = role
        self.validation = validation


def _optional_delta(current: Any, previous: Any) -> float | None:
    if current is None or previous is None:
        return None
    return float(current) - float(previous)


class RuleComparisonService:
    """Backtest two explicit rules and calculate current-minus-previous deltas."""

    def __init__(self, backtester: Backtester) -> None:
        self._backtester = backtester

    async def compare(
        self,
        current_rule: str,
        previous_rule: str,
    ) -> dict[str, Any]:
        current = await self._backtest("current", current_rule)
        previous = await self._backtest("previous", previous_rule)
        current_metrics = current["metrics"]
        previous_metrics = previous["metrics"]

        return {
            "current": current,
            "previous": previous,
            "delta": {
                "precision": _optional_delta(
                    current_metrics["precision"], previous_metrics["precision"]
                ),
                "recall": _optional_delta(
                    current_metrics["recall"], previous_metrics["recall"]
                ),
                "transactions_flagged": int(current_metrics["transactions_flagged"])
                - int(previous_metrics["transactions_flagged"]),
                "fraud_caught": int(current_metrics["fraud_caught"])
                - int(previous_metrics["fraud_caught"]),
                "fraud_value_captured_usd": float(
                    current_metrics["fraud_value_captured_usd"]
                )
                - float(previous_metrics["fraud_value_captured_usd"]),
            },
        }

    async def _backtest(self, role: RuleRole, rule: str) -> dict[str, Any]:
        try:
            return await self._backtester.backtest(rule)
        except InvalidBacktestRule as exc:
            raise InvalidComparisonRule(role, exc.validation) from exc
