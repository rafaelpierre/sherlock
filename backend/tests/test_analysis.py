from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

import pytest
from strands.types.agent import Limits

from sherlock.analysis import (
    DEFAULT_ANALYSIS_LIMITS,
    AnalysisAgent,
    AnalysisAgentError,
    AnalysisCancelledError,
    AnalysisDeadlineError,
    AnalysisLimits,
)
from sherlock.services.text2sql import QueryExecutionError


class Result:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.text = text
        self.stop_reason = stop_reason

    def __str__(self) -> str:
        return self.text


class ScriptedService:
    def __init__(self, failures: set[int] | None = None) -> None:
        self.questions: list[str] = []
        self.failures = failures or set()

    async def query(self, question: str) -> dict[str, Any]:
        self.questions.append(question)
        index = len(self.questions)
        if index in self.failures:
            raise QueryExecutionError("query unavailable")
        value = "Debit" if index == 2 else index * 10
        return {
            "question": question,
            "sql": f"SELECT {index} AS finding",
            "result": {
                "columns": ["finding"],
                "rows": [[value]],
                "row_count": 1,
                "truncated": False,
            },
            "attempts": 2 if index == 1 else 1,
            "cached_sql": index != 2,
        }


class ScriptedAnalysisModel:
    def __init__(
        self,
        tools: list[Any],
        script: Callable[[Any], Any],
        *,
        text: str = "Grounded synthesis.",
        stop_reason: str = "end_turn",
    ) -> None:
        self.tool = tools[0]
        self.script = script
        self.text = text
        self.stop_reason = stop_reason
        self.received_limits: Limits | None = None
        self.cancel_signal: threading.Event | None = None
        self.cleaned = False

    async def invoke_async(
        self,
        prompt: str,
        *,
        limits: Limits,
        cancel_signal: threading.Event,
    ) -> Result:
        self.received_limits = limits
        self.cancel_signal = cancel_signal
        await self.script(self.tool)
        return Result(self.text, self.stop_reason)

    def cleanup(self) -> None:
        self.cleaned = True


class ModelFactory:
    def __init__(
        self,
        script: Callable[[Any], Any],
        *,
        text: str = "Grounded synthesis.",
        stop_reason: str = "end_turn",
    ) -> None:
        self.script = script
        self.text = text
        self.stop_reason = stop_reason
        self.model: ScriptedAnalysisModel | None = None

    def __call__(self, tools: list[Any]) -> ScriptedAnalysisModel:
        self.model = ScriptedAnalysisModel(
            tools,
            self.script,
            text=self.text,
            stop_reason=self.stop_reason,
        )
        return self.model


def run_analysis(
    service: ScriptedService,
    factory: ModelFactory,
    *,
    limits: AnalysisLimits = DEFAULT_ANALYSIS_LIMITS,
    cancel_signal: threading.Event | None = None,
    events: list[tuple[str, bool | None]] | None = None,
):
    def record_event(activity_id: str, succeeded: bool | None) -> None:
        if events is not None:
            events.append((activity_id, succeeded))

    return asyncio.run(
        AnalysisAgent(service, limits=limits, model_factory=factory).analyze(
            "Investigate fraud characteristics",
            cancel_signal=cancel_signal or threading.Event(),
            event_sink=record_event if events is not None else None,
        )
    )


def test_three_queries_adapt_to_earlier_structured_evidence() -> None:
    service = ScriptedService()
    observed: list[Any] = []

    async def script(tool: Any) -> None:
        baseline = await tool(question="Establish the labelled fraud baseline")
        observed.append(baseline)
        cards = await tool(question="Compare card types within labelled rows")
        observed.append(cards)
        leading_card = cards["table"]["rows"][0][0]
        observed.append(
            await tool(question=f"Validate the {leading_card} concentration by amount")
        )

    factory = ModelFactory(script, text="Debit is concentrated; see all three steps.")
    events: list[tuple[str, bool | None]] = []

    result = run_analysis(service, factory, events=events)

    assert service.questions[-1] == "Validate the Debit concentration by amount"
    assert [step.step for step in result.steps] == [1, 2, 3]
    assert [step.question for step in result.steps] == service.questions
    assert result.repair_count == 1
    assert result.cache_hit is False
    assert result.message == "Debit is concentrated; see all three steps."
    assert events == [
        ("analysis-query-1", None),
        ("analysis-query-1", True),
        ("analysis-query-2", None),
        ("analysis-query-2", True),
        ("analysis-query-3", None),
        ("analysis-query-3", True),
    ]
    assert factory.model is not None
    assert factory.model.received_limits == {"turns": 7}
    assert factory.model.cleaned is True


def test_narrow_analysis_can_stop_after_one_query() -> None:
    async def script(tool: Any) -> None:
        await tool(question="Count labelled fraud transactions")

    result = run_analysis(ScriptedService(), ModelFactory(script))

    assert len(result.steps) == 1


def test_query_attempt_limit_counts_failures_and_rejects_extra_calls() -> None:
    service = ScriptedService(failures={2})
    tool_results: list[dict[str, Any]] = []

    async def script(tool: Any) -> None:
        for index in range(1, 7):
            tool_results.append(await tool(question=f"Question {index}"))

    result = run_analysis(service, ModelFactory(script))

    assert len(service.questions) == 5
    assert len(result.steps) == 4
    assert "query-attempt budget is exhausted" in tool_results[-1]["error"]


def test_cumulative_evidence_size_limit_keeps_prior_steps() -> None:
    class OversizedEvidenceService(ScriptedService):
        async def query(self, question: str) -> dict[str, Any]:
            response = await super().query(question)
            if len(self.questions) == 2:
                response["result"]["rows"] = [["x" * 150_000]]
            return response

    tool_results: list[dict[str, Any]] = []

    async def script(tool: Any) -> None:
        tool_results.append(await tool(question="Baseline"))
        tool_results.append(await tool(question="Wide result"))

    result = run_analysis(OversizedEvidenceService(), ModelFactory(script))

    assert len(result.steps) == 1
    assert "evidence limit" in tool_results[1]["error"]


def test_cumulative_evidence_limit_counts_utf16_units() -> None:
    class EmojiEvidenceService(ScriptedService):
        async def query(self, question: str) -> dict[str, Any]:
            response = await super().query(question)
            response["result"]["rows"] = [["\U0001f600" * 80_000]]
            return response

    tool_results: list[dict[str, Any]] = []

    async def script(tool: Any) -> None:
        tool_results.append(await tool(question="Emoji result"))

    with pytest.raises(AnalysisAgentError, match="no successful"):
        run_analysis(EmojiEvidenceService(), ModelFactory(script))

    assert "evidence limit" in tool_results[0]["error"]


def test_partial_failure_is_paired_and_successful_evidence_remains() -> None:
    service = ScriptedService(failures={1})
    events: list[tuple[str, bool | None]] = []

    async def script(tool: Any) -> None:
        failed = await tool(question="Unavailable dimension")
        assert "error" in failed
        await tool(question="Available baseline")

    result = run_analysis(service, ModelFactory(script), events=events)

    assert len(result.steps) == 1
    assert events == [
        ("analysis-query-1", None),
        ("analysis-query-1", False),
        ("analysis-query-2", None),
        ("analysis-query-2", True),
    ]


def test_all_failed_and_zero_tool_analyses_are_rejected() -> None:
    async def one_query(tool: Any) -> None:
        await tool(question="Unavailable")

    async def no_query(tool: Any) -> None:
        return None

    with pytest.raises(AnalysisAgentError, match="no successful"):
        run_analysis(ScriptedService(failures={1}), ModelFactory(one_query))
    with pytest.raises(AnalysisAgentError, match="no successful"):
        run_analysis(ScriptedService(), ModelFactory(no_query))


def test_model_turn_limit_and_oversized_synthesis_are_rejected() -> None:
    async def one_query(tool: Any) -> None:
        await tool(question="Baseline")

    with pytest.raises(AnalysisAgentError, match="model-turn limit"):
        run_analysis(
            ScriptedService(),
            ModelFactory(one_query, stop_reason="limit_turns"),
        )
    with pytest.raises(AnalysisAgentError, match="oversized"):
        run_analysis(
            ScriptedService(),
            ModelFactory(one_query, text="x" * 10_001),
        )


def test_deadline_and_precancelled_invocations_cleanup() -> None:
    async def block(tool: Any) -> None:
        await asyncio.Event().wait()

    deadline_factory = ModelFactory(block)
    signal = threading.Event()
    with pytest.raises(AnalysisDeadlineError):
        run_analysis(
            ScriptedService(),
            deadline_factory,
            limits=AnalysisLimits(deadline_seconds=0.01),
            cancel_signal=signal,
        )
    assert signal.is_set()
    assert deadline_factory.model is not None
    assert deadline_factory.model.cleaned is True

    cancelled_factory = ModelFactory(block)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(AnalysisCancelledError):
        run_analysis(
            ScriptedService(),
            cancelled_factory,
            cancel_signal=cancelled,
        )
    assert cancelled_factory.model is not None
    assert cancelled_factory.model.cleaned is True


def test_active_cancellation_stops_query_and_pairs_activity() -> None:
    started = asyncio.Event()

    class BlockingService(ScriptedService):
        async def query(self, question: str) -> dict[str, Any]:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def script(tool: Any) -> None:
        await tool(question="Blocking query")

    async def scenario() -> None:
        signal = threading.Event()
        events: list[tuple[str, bool | None]] = []
        factory = ModelFactory(script)
        task = asyncio.create_task(
            AnalysisAgent(BlockingService(), model_factory=factory).analyze(
                "Investigate",
                cancel_signal=signal,
                event_sink=lambda activity_id, succeeded: events.append(
                    (activity_id, succeeded)
                ),
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert signal.is_set()
        assert events == [
            ("analysis-query-1", None),
            ("analysis-query-1", False),
        ]
        assert factory.model is not None
        assert factory.model.cleaned is True

    asyncio.run(scenario())
