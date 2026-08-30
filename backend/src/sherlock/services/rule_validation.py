"""Deterministic validation for candidate fraud-rule predicates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from uuid import uuid4

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from sherlock.services.text2sql import ExecutionResult, QueryExecutor

CANONICAL_RELATION = "fraud_transactions"
OUTCOME_ONLY_COLUMNS = frozenset({"is_fraud"})
DISALLOWED_PREDICATE_NODES = (
    exp.Query,
    exp.DDL,
    exp.DML,
    exp.Command,
    exp.Subquery,
)


class RuleSchemaError(RuntimeError):
    """Canonical schema metadata could not be loaded."""


class SchemaProvider(Protocol):
    """Provide columns for the canonical analytical relation."""

    async def columns(self, relation: str) -> set[str]: ...


class MCPToolCaller(Protocol):
    """Call an MCP tool without coupling schema parsing to a concrete client."""

    async def call_tool_async(
        self,
        tool_use_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class MCPSchemaProvider:
    """Read canonical columns from MCP instead of duplicating schema in the backend."""

    def __init__(self, client: MCPToolCaller) -> None:
        self._client = client

    async def columns(self, relation: str) -> set[str]:
        result = await self._client.call_tool_async(
            tool_use_id=uuid4().hex,
            name="get_schema",
            arguments={},
        )
        payload = result.get("structuredContent")
        if result.get("status") == "error" or not isinstance(payload, dict):
            raise RuleSchemaError("The schema service is unavailable.")
        relations = payload.get("relations")
        if not isinstance(relations, list):
            raise RuleSchemaError("The schema service returned invalid data.")
        for item in relations:
            if isinstance(item, dict) and item.get("name") == relation:
                columns = item.get("columns")
                if not isinstance(columns, list):
                    break
                return {
                    str(column["name"])
                    for column in columns
                    if isinstance(column, dict) and "name" in column
                }
        raise RuleSchemaError(f"Relation '{relation}' is not available.")


@dataclass(frozen=True)
class RuleValidationError:
    """A stable validation failure safe to expose through the API."""

    code: str
    message: str
    suggestion: str | None = None


@dataclass(frozen=True)
class RuleValidationResult:
    """The normalized predicate and any deterministic validation failures."""

    valid: bool
    rule: str | None
    errors: list[RuleValidationError]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuleValidator(Protocol):
    """Validate candidate predicates for deterministic domain services."""

    async def validate(self, rule: str) -> RuleValidationResult: ...


class RuleValidationService:
    """Validate a SQL predicate before it can be used by domain services."""

    def __init__(self, schema: SchemaProvider, executor: QueryExecutor) -> None:
        self._schema = schema
        self._executor = executor

    async def validate(self, rule: str) -> RuleValidationResult:
        if not isinstance(rule, str) or not rule.strip():
            return self._invalid("EMPTY_RULE", "Rule must be a non-empty predicate.")

        stripped = rule.strip()
        if ";" in stripped or "--" in stripped or "/*" in stripped:
            return self._invalid(
                "INVALID_RULE_SCOPE",
                "Comments and statement separators are not allowed in rules.",
            )

        parsed = self._parse_predicate(stripped)
        if isinstance(parsed, RuleValidationResult):
            return parsed

        columns = list(parsed.find_all(exp.Column))
        column_error = await self._validate_columns(columns)
        if column_error is not None:
            return column_error

        normalized_rule = parsed.sql(dialect="sqlite", pretty=False)
        execution = await self._executor.execute(
            f"SELECT 1 FROM {CANONICAL_RELATION} WHERE {normalized_rule} LIMIT 1"
        )
        if execution.error is not None:
            return self._execution_failure(execution)

        return RuleValidationResult(valid=True, rule=normalized_rule, errors=[])

    def _parse_predicate(self, rule: str) -> exp.Expression | RuleValidationResult:
        try:
            statement = sqlglot.parse_one(
                f"SELECT 1 FROM {CANONICAL_RELATION} WHERE {rule} LIMIT 1",
                read="sqlite",
            )
        except ParseError as exc:
            return self._invalid(
                "INVALID_RULE_SYNTAX", f"Rule could not be parsed: {exc}"
            )

        where = statement.args.get("where")
        if not isinstance(statement, exp.Select) or not isinstance(where, exp.Where):
            return self._invalid(
                "INVALID_RULE_SCOPE", "Rule must be a SQL WHERE predicate."
            )

        predicate = where.this
        if any(
            isinstance(node, DISALLOWED_PREDICATE_NODES) for node in predicate.walk()
        ):
            return self._invalid(
                "INVALID_RULE_SCOPE",
                "Queries and data-changing expressions are not allowed in rules.",
            )

        return predicate

    async def _validate_columns(
        self, columns: list[exp.Column]
    ) -> RuleValidationResult | None:
        for column in columns:
            if column.table and column.table != CANONICAL_RELATION:
                return self._invalid(
                    "UNKNOWN_RELATION",
                    f"Relation '{column.table}' is not available in rule scope.",
                    f"Use columns from {CANONICAL_RELATION} without another qualifier.",
                )
            if column.name.lower() in OUTCOME_ONLY_COLUMNS:
                return self._invalid(
                    "OUTCOME_COLUMN_FORBIDDEN",
                    f"Column '{column.name}' is outcome-only and cannot be used "
                    "in candidate rules.",
                    "Use transaction attributes that are available when a decision "
                    "is made.",
                )

        allowed_columns = await self._schema.columns(CANONICAL_RELATION)
        for column in columns:
            if column.name not in allowed_columns:
                return self._invalid(
                    "UNKNOWN_COLUMN",
                    f"Column '{column.name}' does not exist in {CANONICAL_RELATION}.",
                    "Inspect the canonical schema and use an available column.",
                )

        return None

    @staticmethod
    def _invalid(
        code: str,
        message: str,
        suggestion: str | None = None,
    ) -> RuleValidationResult:
        return RuleValidationResult(
            valid=False,
            rule=None,
            errors=[RuleValidationError(code, message, suggestion)],
        )

    def _execution_failure(self, execution: ExecutionResult) -> RuleValidationResult:
        error = execution.error or {}
        return self._invalid(
            str(error.get("type", "RULE_EXECUTION_ERROR")),
            str(error.get("message", "Rule could not be validated.")),
            str(error["suggestion"]) if error.get("suggestion") else None,
        )
