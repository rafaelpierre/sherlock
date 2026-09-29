"""Candidate fraud-rule generation and bounded repair orchestration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from strands.tools.mcp import MCPClient

from sherlock.agent import RuleGeneration, create_rule_generation_agent
from sherlock.config import Settings
from sherlock.execution import run_blocking_provider_call
from sherlock.services.rule_validation import (
    MCPSchemaProvider,
    RuleValidationResult,
    RuleValidationService,
    RuleValidator,
)
from sherlock.services.text2sql import GENERATOR_TOOLS, MCPQueryExecutor


class RuleGenerationError(RuntimeError):
    """The candidate-rule workflow could not produce a usable result."""


class InvalidCurrentRule(RuleGenerationError):
    """A rule refinement request supplied an invalid current rule."""

    def __init__(self, validation: RuleValidationResult) -> None:
        super().__init__("Current candidate rule is invalid.")
        self.validation = validation


class RuleGenerator(Protocol):
    async def generate(self, instruction: str) -> str: ...

    async def repair(
        self,
        instruction: str,
        previous_rule: str,
        validation: RuleValidationResult,
    ) -> str: ...

    async def refine(self, rule: str, instruction: str) -> str: ...

    async def repair_refinement(
        self,
        current_rule: str,
        instruction: str,
        previous_rule: str,
        validation: RuleValidationResult,
    ) -> str: ...


@dataclass(frozen=True)
class RuleGenerationResult:
    rule: str | None
    valid: bool
    repair_count: int
    errors: list[dict[str, Any]]


@dataclass(frozen=True)
class RuleRefinementResult(RuleGenerationResult):
    previous_rule: str


class StrandsRuleGenerator:
    """Invoke a fresh schema-aware RuleAgent for every attempt."""

    def __init__(self, client: MCPClient, *, model: str | None = None) -> None:
        self._client = client
        self._model = model

    async def generate(self, instruction: str) -> str:
        return await run_blocking_provider_call(self._invoke, instruction)

    async def repair(
        self,
        instruction: str,
        previous_rule: str,
        validation: RuleValidationResult,
    ) -> str:
        prompt = (
            f"Original instruction:\n{instruction}\n\n"
            f"Invalid candidate rule:\n{previous_rule}\n\n"
            f"Validation errors:\n{json.dumps(validation.as_dict(), sort_keys=True)}\n\n"
            "Return a corrected WHERE predicate."
        )
        return await run_blocking_provider_call(self._invoke, prompt)

    async def refine(self, rule: str, instruction: str) -> str:
        prompt = (
            f"Current candidate rule:\n{rule}\n\n"
            f"Refinement instruction:\n{instruction}\n\n"
            "Return the complete refined WHERE predicate. Preserve every unrelated "
            "condition and apply only the requested modification."
        )
        return await run_blocking_provider_call(self._invoke, prompt)

    async def repair_refinement(
        self,
        current_rule: str,
        instruction: str,
        previous_rule: str,
        validation: RuleValidationResult,
    ) -> str:
        prompt = (
            f"Current candidate rule:\n{current_rule}\n\n"
            f"Refinement instruction:\n{instruction}\n\n"
            f"Invalid refined rule:\n{previous_rule}\n\n"
            f"Validation errors:\n{json.dumps(validation.as_dict(), sort_keys=True)}\n\n"
            "Return a corrected complete WHERE predicate. Preserve every unrelated "
            "condition from the current candidate rule and apply only the requested "
            "modification."
        )
        return await run_blocking_provider_call(self._invoke, prompt)

    def _invoke(self, prompt: str) -> str:
        agent = create_rule_generation_agent(self._client, model=self._model)
        try:
            result = agent(prompt)
            output = result.structured_output
            if not isinstance(output, RuleGeneration):
                raise RuleGenerationError(
                    "The RuleAgent returned no structured candidate rule."
                )
            return output.rule.strip()
        finally:
            agent.cleanup()


class RuleGenerationService:
    """Generate, validate, and repair a transient candidate fraud rule."""

    def __init__(
        self,
        generator: RuleGenerator,
        validator: RuleValidator,
        *,
        max_repair_attempts: int = 2,
        start_callback: Callable[[], Awaitable[Any]] | None = None,
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts cannot be negative")
        self._generator = generator
        self._validator = validator
        self._max_repair_attempts = max_repair_attempts
        self._start_callback = start_callback
        self._close_callback = close_callback
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            if self._start_callback is not None:
                await self._start_callback()
            self._started = True

    async def generate(self, instruction: str) -> dict[str, Any]:
        await self.start()
        rule = await self._generator.generate(instruction)
        validation = await self._validator.validate(rule)
        repair_count = 0

        while not validation.valid and repair_count < self._max_repair_attempts:
            rule = await self._generator.repair(instruction, rule, validation)
            repair_count += 1
            validation = await self._validator.validate(rule)

        result = RuleGenerationResult(
            rule=validation.rule,
            valid=validation.valid,
            repair_count=repair_count,
            errors=[asdict(error) for error in validation.errors],
        )
        return asdict(result)

    async def refine(self, rule: str, instruction: str) -> dict[str, Any]:
        """Validate the current rule and produce its next transient version."""

        await self.start()
        current = await self._validator.validate(rule)
        if not current.valid or current.rule is None:
            raise InvalidCurrentRule(current)

        candidate = await self._generator.refine(current.rule, instruction)
        validation = await self._validator.validate(candidate)
        repair_count = 0
        while not validation.valid and repair_count < self._max_repair_attempts:
            candidate = await self._generator.repair_refinement(
                current.rule,
                instruction,
                candidate,
                validation,
            )
            repair_count += 1
            validation = await self._validator.validate(candidate)

        result = RuleRefinementResult(
            rule=validation.rule,
            valid=validation.valid,
            repair_count=repair_count,
            errors=[asdict(error) for error in validation.errors],
            previous_rule=current.rule,
        )
        return asdict(result)

    def close(self) -> None:
        if self._close_callback is not None:
            self._close_callback()


def create_rule_generation_service(
    settings: Settings, *, model: str | None = None
) -> RuleGenerationService:
    """Build isolated metadata and execution clients for the RuleAgent workflow."""

    metadata_client = settings.mcp_client(allowed_tools=GENERATOR_TOOLS)
    execution_client = settings.mcp_client(allowed_tools=("get_schema", "run_query"))
    metadata_owner = object()
    execution_owner = object()
    metadata_client.add_consumer(metadata_owner)
    execution_client.add_consumer(execution_owner)

    async def start_clients() -> None:
        await asyncio.gather(
            metadata_client.load_tools(), execution_client.load_tools()
        )

    def close_clients() -> None:
        metadata_client.remove_consumer(metadata_owner)
        execution_client.remove_consumer(execution_owner)

    validator = RuleValidationService(
        MCPSchemaProvider(execution_client), MCPQueryExecutor(execution_client)
    )
    return RuleGenerationService(
        StrandsRuleGenerator(metadata_client, model=model),
        validator,
        start_callback=start_clients,
        close_callback=close_clients,
    )
