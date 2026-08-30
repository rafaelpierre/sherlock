from __future__ import annotations

import asyncio

import pytest

from sherlock.services.rule_validation import (
    MCPSchemaProvider,
    RuleSchemaError,
    RuleValidationService,
)
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


class StubMCPClient:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    async def call_tool_async(self, **kwargs):
        assert kwargs["name"] == "get_schema"
        return self.result


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


@pytest.mark.parametrize(
    "rule",
    [
        "is_fraud = 1",
        "amount_usd > 1000 AND is_fraud = 1",
        "IS_FRAUD IS NULL OR card_type = 'Debit'",
    ],
)
def test_outcome_columns_are_rejected_before_execution(rule: str) -> None:
    executor = StubExecutor()

    result = validate(rule, executor)

    assert result.valid is False
    assert result.rule is None
    assert result.errors[0].code == "OUTCOME_COLUMN_FORBIDDEN"
    assert result.errors[0].suggestion
    assert executor.sql == []


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


def test_mcp_schema_provider_returns_relation_columns() -> None:
    provider = MCPSchemaProvider(
        StubMCPClient(  # type: ignore[arg-type]
            {
                "structuredContent": {
                    "relations": [
                        {
                            "name": "fraud_transactions",
                            "columns": [{"name": "amount_usd"}, {"name": "card_type"}],
                        }
                    ]
                }
            }
        )
    )

    columns = asyncio.run(provider.columns("fraud_transactions"))

    assert columns == {"amount_usd", "card_type"}


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "error"},
        {"structuredContent": {"relations": "invalid"}},
        {"structuredContent": {"relations": []}},
    ],
)
def test_mcp_schema_provider_rejects_unavailable_or_invalid_data(
    payload: dict[str, object],
) -> None:
    provider = MCPSchemaProvider(StubMCPClient(payload))  # type: ignore[arg-type]

    with pytest.raises(RuleSchemaError):
        asyncio.run(provider.columns("fraud_transactions"))
