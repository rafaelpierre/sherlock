"""Offline and explicitly enabled live services for evaluation cases."""

from __future__ import annotations

from typing import Any, Protocol

from sherlock.config import Settings
from sherlock.evaluation.models import EvaluationCase, SuiteKind
from sherlock.services.rule_generation import (
    RuleGenerationService,
    create_rule_generation_service,
)
from sherlock.services.rule_validation import CANONICAL_RELATION
from sherlock.services.text2sql import (
    MCPQueryExecutor,
    Text2SQLService,
    create_text2sql_service,
)


class EvaluationService(Protocol):
    async def execute(
        self, kind: SuiteKind, case: EvaluationCase
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


class FixtureEvaluationService:
    """Return committed fixture responses without network or Bedrock access."""

    async def execute(self, kind: SuiteKind, case: EvaluationCase) -> dict[str, Any]:
        del kind
        if case.fixture_response is None:
            raise ValueError(
                f"Case {case.id} has no fixture_response; rerun with --live"
            )
        return dict(case.fixture_response)

    def close(self) -> None:
        return None


class LiveEvaluationService:
    """Call production workflows only after the CLI's explicit live opt-in."""

    def __init__(self, settings: Settings, *, model: str) -> None:
        self._text2sql: Text2SQLService = create_text2sql_service(settings, model=model)
        self._rules: RuleGenerationService = create_rule_generation_service(
            settings, model=model
        )
        self._matching_client = settings.mcp_client(
            allowed_tools=("get_schema", "run_query")
        )
        self._matching_owner = object()
        self._matching_client.add_consumer(self._matching_owner)
        self._matching_executor = MCPQueryExecutor(self._matching_client)
        self._matching_started = False

    async def execute(self, kind: SuiteKind, case: EvaluationCase) -> dict[str, Any]:
        if kind == "text2sql":
            return await self._text2sql.query(case.prompt)
        output = await self._rules.generate(case.prompt)
        if output.get("valid") is not True or not isinstance(output.get("rule"), str):
            return output

        assert case.rule_oracle is not None
        if not self._matching_started:
            await self._matching_client.load_tools()
            self._matching_started = True
        try:
            output["transaction_id_comparison"] = await self._compare_transaction_ids(
                output["rule"], case.rule_oracle.reference_predicate
            )
        except RuntimeError as exc:
            output["execution_error"] = str(exc)
        return output

    def close(self) -> None:
        self._text2sql.close()
        self._rules.close()
        self._matching_client.remove_consumer(self._matching_owner)

    async def _compare_transaction_ids(
        self, candidate_predicate: str, reference_predicate: str
    ) -> dict[str, int]:
        execution = await self._matching_executor.execute(
            "SELECT "
            f"(SELECT COUNT(*) FROM {CANONICAL_RELATION} WHERE ({candidate_predicate})) "
            "AS candidate_count, "
            f"(SELECT COUNT(*) FROM {CANONICAL_RELATION} WHERE ({reference_predicate})) "
            "AS reference_count, "
            "(SELECT COUNT(*) FROM ("
            f"SELECT transaction_id FROM {CANONICAL_RELATION} WHERE ({candidate_predicate}) "
            "EXCEPT "
            f"SELECT transaction_id FROM {CANONICAL_RELATION} WHERE ({reference_predicate})"
            ")) AS candidate_only_count, "
            "(SELECT COUNT(*) FROM ("
            f"SELECT transaction_id FROM {CANONICAL_RELATION} WHERE ({reference_predicate}) "
            "EXCEPT "
            f"SELECT transaction_id FROM {CANONICAL_RELATION} WHERE ({candidate_predicate})"
            ")) AS reference_only_count"
        )
        if execution.error is not None:
            raise RuntimeError(str(execution.error.get("message", "Query failed.")))
        if (
            execution.data is None
            or execution.data.truncated
            or len(execution.data.rows) != 1
        ):
            raise RuntimeError("Transaction-ID comparison returned invalid data.")
        row = execution.data.rows[0]
        if len(row) != 4 or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in row
        ):
            raise RuntimeError("Transaction-ID comparison returned invalid counts.")
        return dict(
            zip(
                (
                    "candidate_count",
                    "reference_count",
                    "candidate_only_count",
                    "reference_only_count",
                ),
                row,
                strict=True,
            )
        )
