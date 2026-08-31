"""Bounded multi-step analytical investigation over the Text2SQL service."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from pydantic import ValidationError
from strands import Agent, tool
from strands.types.agent import Limits

from sherlock.api.artifacts import AnalysisStepArtifact
from sherlock.api.chat_models import MAX_ASSISTANT_MESSAGE_LENGTH
from sherlock.api.schemas import QueryResponse
from sherlock.services.text2sql import Text2SQLError

ANALYSIS_SYSTEM_PROMPT = """
You are Sherlock's fraud-analysis specialist. Investigate the user's analytical
question by calling query_transaction_data one or more times, then return one
concise synthesis grounded only in successful tool results.

For broad questions, establish an appropriate cohort and baseline, inspect useful
dimensions, and adapt later questions to earlier structured results. For narrow
questions, stop after one sufficient query. Fraud/non-fraud comparisons must use
labelled rows only; never treat is_fraud NULL as non-fraud. Clearly distinguish
supported findings from limitations and suggested next steps.

Do not invent database facts. A database-fact answer requires at least one
successful query. Failed queries are unavailable evidence: retry within the
remaining budget when useful or explicitly qualify the gap. Candidate rules are
investigation hypotheses, not production fraud decisions.

Only describe a failed or unavailable analysis when query_transaction_data
returned an error for that analysis. Do not infer failed queries, zero-result
categories, or unobserved patterns from a limited table. Cite a limitation when
an available table is truncated.
""".strip()


@dataclass(frozen=True)
class AnalysisLimits:
    """Central request bounds for the analysis model and its query tool."""

    query_attempts: int = 5
    model_turns: int = 7
    deadline_seconds: float = 240.0

    def __post_init__(self) -> None:
        if self.query_attempts < 1 or self.model_turns < 1:
            raise ValueError("Analysis query and model-turn limits must be positive.")
        if self.deadline_seconds <= 0:
            raise ValueError("The analysis deadline must be positive.")


DEFAULT_ANALYSIS_LIMITS = AnalysisLimits()
MAX_ANALYSIS_ARTIFACT_CHARACTERS = 140_000


class AnalysisModel(Protocol):
    async def invoke_async(
        self,
        prompt: str,
        *,
        limits: Limits,
        cancel_signal: threading.Event,
    ) -> Any: ...

    def cleanup(self) -> None: ...


AnalysisModelFactory = Callable[[list[Any]], AnalysisModel]
AnalysisEventSink = Callable[[str, bool | None], None]


class AnalysisAgentError(RuntimeError):
    """The specialist could not produce a bounded grounded analysis."""


class AnalysisDeadlineError(AnalysisAgentError):
    """The specialist exceeded its request deadline."""


class AnalysisCancelledError(AnalysisAgentError):
    """The specialist was cancelled by its caller."""


@dataclass(frozen=True)
class AnalysisResult:
    """Grounded synthesis plus deterministic evidence and aggregate metadata."""

    message: str
    steps: list[AnalysisStepArtifact]
    repair_count: int
    cache_hit: bool


@dataclass
class _AnalysisExecution:
    service: Text2SQLWorkflow
    limits: AnalysisLimits
    cancel_signal: threading.Event
    event_sink: AnalysisEventSink | None = None
    attempts: int = 0
    steps: list[AnalysisStepArtifact] = field(default_factory=list)
    repair_count: int = 0
    all_cached: bool = True

    def check_cancelled(self) -> None:
        if self.cancel_signal.is_set():
            raise AnalysisCancelledError("The analysis was cancelled.")


class Text2SQLWorkflow(Protocol):
    async def query(self, question: str) -> dict[str, Any]: ...


def _create_strands_analysis_model(tools: list[Any]) -> AnalysisModel:
    return cast(
        AnalysisModel,
        Agent(
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            tools=tools,
            callback_handler=None,
            name="sherlock-analysis-agent",
            description="Runs bounded adaptive transaction-data investigations.",
        ),
    )


class AnalysisAgent:
    """Fresh specialist whose only data capability is the Text2SQL service."""

    def __init__(
        self,
        text2sql_service: Text2SQLWorkflow,
        *,
        limits: AnalysisLimits = DEFAULT_ANALYSIS_LIMITS,
        model_factory: AnalysisModelFactory = _create_strands_analysis_model,
    ) -> None:
        self._service = text2sql_service
        self._limits = limits
        self._model_factory = model_factory
        self._execution: _AnalysisExecution | None = None

    async def analyze(
        self,
        question: str,
        *,
        cancel_signal: threading.Event,
        event_sink: AnalysisEventSink | None = None,
    ) -> AnalysisResult:
        execution = _AnalysisExecution(
            self._service,
            self._limits,
            cancel_signal,
            event_sink,
        )
        self._execution = execution
        model = self._model_factory([self.query_transaction_data])
        try:
            try:
                execution.check_cancelled()
                async with asyncio.timeout(self._limits.deadline_seconds):
                    result = await model.invoke_async(
                        question,
                        limits={"turns": self._limits.model_turns},
                        cancel_signal=cancel_signal,
                    )
            except TimeoutError as exc:
                cancel_signal.set()
                raise AnalysisDeadlineError(
                    "The analysis deadline was exceeded."
                ) from exc
            execution.check_cancelled()
            stop_reason = getattr(result, "stop_reason", "end_turn")
            if stop_reason == "cancelled":
                raise AnalysisCancelledError("The analysis was cancelled.")
            if stop_reason != "end_turn":
                raise AnalysisAgentError(
                    "The AnalysisAgent reached its model-turn limit before synthesis."
                )
            if not execution.steps:
                raise AnalysisAgentError(
                    "The AnalysisAgent produced no successful analytical query."
                )
            message = str(result).strip()
            if not message:
                raise AnalysisAgentError("The AnalysisAgent returned no synthesis.")
            if len(message) > MAX_ASSISTANT_MESSAGE_LENGTH:
                raise AnalysisAgentError(
                    "The AnalysisAgent returned an oversized synthesis."
                )
            return AnalysisResult(
                message=message,
                steps=list(execution.steps),
                repair_count=execution.repair_count,
                cache_hit=execution.all_cached,
            )
        except asyncio.CancelledError:
            cancel_signal.set()
            raise
        finally:
            self._execution = None
            model.cleanup()

    @tool(name="query_transaction_data")
    async def query_transaction_data(self, question: str) -> dict[str, Any]:
        """Run one analytical question through Text2SQL and return bounded evidence."""

        execution = self._execution
        if execution is None:
            return {"error": "No analysis invocation is active."}
        execution.check_cancelled()
        if execution.attempts >= execution.limits.query_attempts:
            return {
                "error": (
                    "The analysis query-attempt budget is exhausted; synthesize from "
                    "the successful evidence already returned."
                )
            }
        execution.attempts += 1
        activity_id = f"analysis-query-{execution.attempts}"
        if execution.event_sink is not None:
            execution.event_sink(activity_id, None)
        succeeded = False
        try:
            if not question.strip():
                return {"error": "The analytical question must not be blank."}
            response = QueryResponse.model_validate(
                await execution.service.query(question.strip())
            )
            execution.check_cancelled()
            step = AnalysisStepArtifact(
                type="analysis_step",
                step=len(execution.steps) + 1,
                question=response.question,
                sql=response.sql,
                table=response.result,
            )
            if _analysis_artifact_size([*execution.steps, step]) > (
                MAX_ANALYSIS_ARTIFACT_CHARACTERS
            ):
                return {
                    "error": (
                        "The bounded analysis evidence limit was reached; synthesize "
                        "from the successful evidence already returned."
                    )
                }
            execution.steps.append(step)
            execution.repair_count += response.attempts - 1
            execution.all_cached = execution.all_cached and response.cached_sql
            succeeded = True
            return step.model_dump(mode="json")
        except (Text2SQLError, ValidationError) as exc:
            return {"error": str(exc)}
        finally:
            if execution.event_sink is not None:
                execution.event_sink(activity_id, succeeded)


class AnalysisAgentFactory:
    """Create fresh bounded specialists over a shared stateless service."""

    def __init__(
        self,
        text2sql_service: Text2SQLWorkflow,
        *,
        limits: AnalysisLimits = DEFAULT_ANALYSIS_LIMITS,
        model_factory: AnalysisModelFactory = _create_strands_analysis_model,
    ) -> None:
        self._service = text2sql_service
        self._limits = limits
        self._model_factory = model_factory

    def create(self) -> AnalysisAgent:
        return AnalysisAgent(
            self._service,
            limits=self._limits,
            model_factory=self._model_factory,
        )


def _analysis_artifact_size(steps: list[AnalysisStepArtifact]) -> int:
    """Measure the JSON carried by terminal grouped evidence, not Python objects."""

    return len(
        json.dumps(
            [step.model_dump(mode="json") for step in steps],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
