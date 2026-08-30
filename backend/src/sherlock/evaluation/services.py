"""Offline and explicitly enabled live services for evaluation cases."""

from __future__ import annotations

from typing import Any, Protocol

from sherlock.config import Settings
from sherlock.evaluation.models import EvaluationCase, SuiteKind
from sherlock.services.rule_generation import (
    RuleGenerationService,
    create_rule_generation_service,
)
from sherlock.services.text2sql import Text2SQLService, create_text2sql_service


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

    async def execute(self, kind: SuiteKind, case: EvaluationCase) -> dict[str, Any]:
        if kind == "text2sql":
            return await self._text2sql.query(case.prompt)
        return await self._rules.generate(case.prompt)

    def close(self) -> None:
        self._text2sql.close()
        self._rules.close()
