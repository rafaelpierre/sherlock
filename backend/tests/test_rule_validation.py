from __future__ import annotations

import asyncio

import pytest

from sherlock.services.rule_validation import RuleValidationService
from sherlock.services.text2sql import ExecutionResult, QueryData


class StubSchema:
    async def columns(self, relation: str) -> set[str]:
        assert relation == "fraud_transactions"
        return {"amount_usd", "card_type", "errors", "is_fraud"}


class StubExecutor:
    def __init__(self, error: dict[str, str] | None = None) -> None:
        self.error = error
        self.sql: list[str] = []

    async def execute(self, sql: str) -> ExecutionResult:
        self.sql.append(sql)
        if self.error:
            return ExecutionResult(sql=sql, error=self.error)
        return ExecutionResult(
            sql=sql,
            data=QueryData(columns=["1"], rows=[[1]], row_count=1, truncated=False),
        )


def validate(rule: str, executor: StubExecutor | None = None):
    service = RuleValidationService(StubSchema(), executor or StubExecutor())
    return asyncio.run(service.validate(rule))


@pytest.mark.parametrize(
    "rule",
    [
        "amount_usd > 1000 AND card_type = 'Debit'",
        "card_type IN ('Debit', 'Credit')",
        "errors IS NULL OR errors != 'Bad CVV'",
        "(amount_usd >= 10 AND amount_usd <= 20)",
    ],
)
def test_valid_predicates_are_normalized_and_executed(rule: str) -> None:
    executor = StubExecutor()
    result = validate(rule, executor)

    assert result.valid is True
    assert result.rule
    assert result.errors == []
    assert executor.sql[0].startswith("SELECT 1 FROM fraud_transactions WHERE ")


@pytest.mark.parametrize("rule", ["", "   "])
def test_blank_rule_is_rejected(rule: str) -> None:
    result = validate(rule)

    assert result.valid is False
    assert result.errors[0].code == "EMPTY_RULE"


@pytest.mark.parametrize(
    "rule", ["amount_usd > 1; SELECT 1", "amount_usd > 1 -- comment", "1 /*x*/ = 1"]
)
def test_separators_and_comments_are_rejected(rule: str) -> None:
    result = validate(rule)

    assert result.errors[0].code == "INVALID_RULE_SCOPE"


def test_invalid_syntax_is_rejected() -> None:
    result = validate("amount_usd >")

    assert result.errors[0].code == "INVALID_RULE_SYNTAX"


def test_subquery_is_rejected() -> None:
    result = validate("amount_usd > (SELECT 1)")

    assert result.errors[0].code == "INVALID_RULE_SCOPE"


def test_unknown_relation_is_rejected() -> None:
    result = validate("other.amount_usd > 10")

    assert result.errors[0].code == "UNKNOWN_RELATION"
    assert result.errors[0].suggestion


def test_unknown_column_is_rejected() -> None:
    result = validate("missing > 10")

    assert result.errors[0].code == "UNKNOWN_COLUMN"
    assert result.errors[0].suggestion


def test_execution_error_is_returned_structurally() -> None:
    result = validate(
        "amount_usd > 'not-a-number'",
        StubExecutor(
            {
                "type": "QUERY_EXECUTION_ERROR",
                "message": "Rule could not execute",
                "suggestion": "Use a numeric value",
            }
        ),
    )

    assert result.valid is False
    assert result.as_dict()["errors"] == [
        {
            "code": "QUERY_EXECUTION_ERROR",
            "message": "Rule could not execute",
            "suggestion": "Use a numeric value",
        }
    ]
