from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

import pytest
from strands.types.agent import Limits
from strands.types.content import Message

from sherlock.analysis import AnalysisAgentFactory
from sherlock.api.artifacts import AnalysisStepArtifact
from sherlock.api.chat_models import (
    MAX_ASSISTANT_MESSAGE_LENGTH,
    ChatResponse,
    ChatTextDelta,
    ChatToolCall,
    ChatToolResult,
)
from sherlock.api.schemas import ConversationMessage, WorkingState
from sherlock.chat import (
    ChatAgent,
    ChatAgentError,
    ChatAgentFactory,
    ChatStreamItem,
    InvalidChatState,
    MissingChatState,
    _strands_history,
)
from sherlock.services.backtest import InvalidBacktestRule
from sherlock.services.rule_validation import (
    RuleValidationError,
    RuleValidationResult,
)

METRICS = {
    "population": 100,
    "labelled_population": 80,
    "fraud_total": 10,
    "transactions_flagged": 8,
    "unlabelled_flagged": 1,
    "fraud_caught": 5,
    "false_positives": 2,
    "false_negatives": 5,
    "true_negatives": 68,
    "precision": 5 / 7,
    "recall": 0.5,
    "false_positive_rate": 2 / 70,
    "fraud_value_total_usd": 1_000.0,
    "fraud_value_captured_usd": 600.0,
    "fraud_value_recall": 0.6,
    "alerts_per_day": 2.0,
}


class StubWorkflows:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.generated: list[str] = []
        self.refined: list[tuple[str, str]] = []
        self.backtested: list[str] = []
        self.compared: list[tuple[str, str]] = []

    async def query(self, question: str) -> dict[str, Any]:
        self.queries.append(question)
        return {
            "question": question,
            "sql": "SELECT card_type, COUNT(*) FROM fraud_transactions",
            "result": {
                "columns": ["card_type", "count"],
                "rows": [["Debit", 10]],
                "row_count": 1,
                "truncated": False,
            },
            "attempts": 2,
            "cached_sql": True,
        }

    async def generate(self, instruction: str) -> dict[str, Any]:
        self.generated.append(instruction)
        return {
            "rule": "amount_usd > 1000",
            "valid": True,
            "repair_count": 1,
            "errors": [],
        }

    async def refine(self, rule: str, instruction: str) -> dict[str, Any]:
        self.refined.append((rule, instruction))
        return {
            "rule": "amount_usd > 1500",
            "previous_rule": rule,
            "valid": True,
            "repair_count": 0,
            "errors": [],
        }

    async def backtest(self, rule: str) -> dict[str, Any]:
        self.backtested.append(rule)
        return {"rule": rule, "metrics": METRICS}

    async def compare(self, current_rule: str, previous_rule: str) -> dict[str, Any]:
        self.compared.append((current_rule, previous_rule))
        return {
            "current": {"rule": current_rule, "metrics": METRICS},
            "previous": {"rule": previous_rule, "metrics": METRICS},
            "delta": {
                "precision": 0.0,
                "recall": 0.0,
                "transactions_flagged": 0,
                "fraud_caught": 0,
                "fraud_value_captured_usd": 0.0,
            },
        }


class InvalidBacktestWorkflows(StubWorkflows):
    async def backtest(self, rule: str) -> dict[str, Any]:
        raise InvalidBacktestRule(
            RuleValidationResult(
                valid=False,
                rule=None,
                errors=[
                    RuleValidationError(
                        code="OUTCOME_COLUMN_FORBIDDEN",
                        message="Outcome column is not allowed.",
                    )
                ],
            )
        )


class TextResult:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.text = text
        self.stop_reason = stop_reason

    def __str__(self) -> str:
        return self.text


class ScriptedModel:
    def __init__(
        self,
        tools: list[Any],
        actions: list[tuple[str, dict[str, Any]]],
        response_text: str,
    ) -> None:
        self.tools = {item.tool_name: item for item in tools}
        self.actions = actions
        self.response_text = response_text
        self.cleaned = False

    async def invoke_async(self, prompt: str) -> TextResult:
        assert "Authoritative working state:" in prompt
        for name, arguments in self.actions:
            await self.tools[name](**arguments)
        return TextResult(self.response_text)

    async def stream_async(
        self, prompt: str, *, cancel_signal: threading.Event
    ) -> AsyncIterator[dict[str, Any]]:
        assert not cancel_signal.is_set()
        result = await self.invoke_async(prompt)
        midpoint = len(self.response_text) // 2
        for delta in (self.response_text[:midpoint], self.response_text[midpoint:]):
            if delta:
                yield {"data": delta}
        yield {"result": result}

    def cleanup(self) -> None:
        self.cleaned = True


class ScriptedModelFactory:
    def __init__(
        self,
        *scripts: list[tuple[str, dict[str, Any]]],
        response_text: str = "Completed the requested workflow.",
    ) -> None:
        self.scripts = list(scripts)
        self.response_text = response_text
        self.histories: list[list[Message]] = []
        self.models: list[ScriptedModel] = []

    def __call__(self, tools: list[Any], history: list[Message]) -> ScriptedModel:
        self.histories.append(history)
        model = ScriptedModel(tools, self.scripts.pop(0), self.response_text)
        self.models.append(model)
        return model


class SingleQueryAnalysisModel:
    def __init__(self, tools: list[Any]) -> None:
        self.tool = tools[0]

    async def invoke_async(
        self,
        prompt: str,
        *,
        limits: Limits,
        cancel_signal: threading.Event,
    ) -> TextResult:
        assert limits == {"turns": 7}
        assert not cancel_signal.is_set()
        await self.tool(question=prompt)
        return TextResult("Grounded analysis synthesis.")

    def cleanup(self) -> None:
        pass


class ThreeQueryAnalysisModel(SingleQueryAnalysisModel):
    async def invoke_async(
        self,
        prompt: str,
        *,
        limits: Limits,
        cancel_signal: threading.Event,
    ) -> TextResult:
        await self.tool(question="Baseline")
        await self.tool(question="Card types")
        await self.tool(question="Adaptive Debit drill-down")
        return TextResult("Grounded in all three analysis steps.")


class SequentialQueryWorkflows(StubWorkflows):
    async def query(self, question: str) -> dict[str, Any]:
        self.queries.append(question)
        index = len(self.queries)
        return {
            "question": question,
            "sql": f"SELECT {index} AS finding",
            "result": {
                "columns": ["finding"],
                "rows": [[index]],
                "row_count": 1,
                "truncated": False,
            },
            "attempts": index,
            "cached_sql": index != 2,
        }


def single_query_analysis_factory(workflows: StubWorkflows) -> AnalysisAgentFactory:
    return AnalysisAgentFactory(workflows, model_factory=SingleQueryAnalysisModel)


def create_agent(
    workflows: StubWorkflows,
    model_factory: ScriptedModelFactory,
    state: WorkingState | None = None,
    history: list[ConversationMessage] | None = None,
) -> ChatAgent:
    return ChatAgent(
        workflows,
        workflows,
        workflows,
        workflows,
        history or [],
        state or WorkingState(),
        analysis_agent_factory=single_query_analysis_factory(workflows),
        model_factory=model_factory,
    )


async def collect_stream(agent: ChatAgent, message: str) -> list[ChatStreamItem]:
    return [event async for event in agent.stream(message)]


async def collect_stream_error(
    agent: ChatAgent, message: str
) -> tuple[list[ChatStreamItem], Exception | None]:
    events: list[ChatStreamItem] = []
    try:
        async for event in agent.stream(message):
            events.append(event)
    except Exception as exc:  # noqa: BLE001 - test captures the stream boundary
        return events, exc
    return events, None


def test_stream_translates_workflow_activity_and_text_without_raw_results() -> None:
    workflows = StubWorkflows()
    agent = create_agent(
        workflows,
        ScriptedModelFactory([("explore", {"question": "fraud rate"})]),
    )

    events = asyncio.run(collect_stream(agent, "What is the fraud rate?"))

    assert [name for name, _ in events] == [
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
        "text_delta",
        "complete",
    ]
    call = events[0][1]
    result = events[3][1]
    assert isinstance(call, ChatToolCall)
    assert call.kind == "agent_handoff"
    assert call.name == "Transaction analysis"
    assert "explore" not in call.model_dump_json()
    assert "fraud_transactions" not in call.model_dump_json()
    assert isinstance(result, ChatToolResult)
    assert result.id == call.id
    assert "fraud_transactions" not in result.model_dump_json()
    assert all(
        isinstance(payload, ChatTextDelta)
        for name, payload in events
        if name == "text_delta"
    )
    complete = events[-1][1]
    assert isinstance(complete, ChatResponse)
    assert complete.artifacts[0].type == "analysis_step"
    calls = [payload for name, payload in events if name == "tool_call"]
    results = [payload for name, payload in events if name == "tool_result"]
    assert {item.id for item in calls if isinstance(item, ChatToolCall)} == {
        item.id for item in results if isinstance(item, ChatToolResult)
    }


def test_stream_pairs_activity_before_reporting_missing_state() -> None:
    agent = create_agent(
        StubWorkflows(),
        ScriptedModelFactory(
            [("backtest_rule", {})],
            response_text="BACKTEST_RULE requires candidate_rule and is_fraud details",
        ),
    )

    events, error = asyncio.run(collect_stream_error(agent, "Backtest it"))

    assert isinstance(error, MissingChatState)
    assert [name for name, _ in events] == ["tool_call", "tool_result"]
    call = events[0][1]
    result = events[-1][1]
    assert isinstance(call, ChatToolCall)
    assert isinstance(result, ChatToolResult)
    assert result.id == call.id
    assert result.message == "This activity could not be completed."


def test_stream_rejects_excessive_cumulative_text() -> None:
    agent = create_agent(
        StubWorkflows(),
        ScriptedModelFactory(
            [("generate_rule", {"instruction": "high amount"})],
            response_text="x" * (MAX_ASSISTANT_MESSAGE_LENGTH + 1),
        ),
    )

    with pytest.raises(ChatAgentError, match="oversized"):
        asyncio.run(collect_stream(agent, "Create a rule"))


class BlockingStreamingModel(ScriptedModel):
    cancel_signal: threading.Event | None = None

    async def stream_async(
        self, prompt: str, *, cancel_signal: threading.Event
    ) -> AsyncIterator[dict[str, Any]]:
        self.cancel_signal = cancel_signal
        await self.tools["generate_rule"](instruction="high amount")
        yield {"data": "Starting"}
        await asyncio.Event().wait()


class PreambleStreamingModel(ScriptedModel):
    async def stream_async(
        self, prompt: str, *, cancel_signal: threading.Event
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"data": "Coordinator preamble. "}
        result = await self.invoke_async(prompt)
        yield {"data": "Coordinator follow-up."}
        yield {"result": result}


class PreambleModelFactory:
    def __call__(
        self, tools: list[Any], history: list[Message]
    ) -> PreambleStreamingModel:
        return PreambleStreamingModel(
            tools,
            [("explore", {"question": "fraud rate"})],
            "Coordinator result.",
        )


def test_explore_stream_discards_coordinator_preamble_before_specialist_text() -> None:
    workflows = StubWorkflows()
    agent = ChatAgent(
        workflows,
        workflows,
        workflows,
        workflows,
        [],
        WorkingState(),
        analysis_agent_factory=single_query_analysis_factory(workflows),
        model_factory=PreambleModelFactory(),
    )

    events = asyncio.run(collect_stream(agent, "Explore fraud"))

    deltas = [
        payload.delta
        for name, payload in events
        if name == "text_delta" and isinstance(payload, ChatTextDelta)
    ]
    assert deltas == ["Grounded analysis synthesis."]


class BlockingModelFactory:
    def __init__(self) -> None:
        self.model: BlockingStreamingModel | None = None

    def __call__(
        self, tools: list[Any], history: list[Message]
    ) -> BlockingStreamingModel:
        self.model = BlockingStreamingModel(tools, [], "unused")
        return self.model


def test_closing_stream_cancels_model_and_cleans_up() -> None:
    workflows = StubWorkflows()
    factory = BlockingModelFactory()
    agent = ChatAgent(
        workflows,
        workflows,
        workflows,
        workflows,
        [],
        WorkingState(),
        model_factory=factory,
    )

    async def consume_one_event() -> None:
        stream = cast(
            AsyncGenerator[ChatStreamItem],
            agent.stream("Analyze transactions"),
        )
        while (await anext(stream))[0] != "text_delta":
            pass
        await stream.aclose()

    asyncio.run(consume_one_event())

    assert factory.model is not None
    assert factory.model.cancel_signal is not None
    assert factory.model.cancel_signal.is_set()
    assert factory.model.cleaned is True


class MalformedExploreWorkflows(StubWorkflows):
    async def query(self, question: str) -> dict[str, Any]:
        return {"question": question, "malformed": True}


def test_unexpected_workflow_failure_finishes_started_activity() -> None:
    agent = create_agent(
        MalformedExploreWorkflows(),
        ScriptedModelFactory([("explore", {"question": "fraud rate"})]),
    )

    events, error = asyncio.run(collect_stream_error(agent, "Analyze fraud rate"))

    assert isinstance(error, ChatAgentError)
    assert [name for name, _ in events] == [
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
    ]
    call = events[0][1]
    result = events[-1][1]
    assert isinstance(call, ChatToolCall)
    assert isinstance(result, ChatToolResult)
    assert result.id == call.id
    assert result.message == "This activity could not be completed."


def test_history_formats_activity_context_as_plain_user_visible_text() -> None:
    history = _strands_history(
        [
            ConversationMessage(
                role="assistant",
                content="Sherlock could not complete the request. Please try again.",
                outcome="failed",
                activities=[
                    {
                        "kind": "agent_handoff",
                        "name": "Transaction analysis",
                        "message": "Sherlock is investigating the transaction data.",
                        "result": "This activity could not be completed.",
                        "outcome": "failed",
                    }
                ],
            )
        ]
    )

    text = history[0]["content"][0]["text"]
    assert "context only" in text
    assert "Transaction analysis" in text
    assert "Outcome: failed" in text
    assert "toolUse" not in text


def test_explore_tool_reuses_text2sql_and_returns_sql_and_table_artifacts() -> None:
    workflows = StubWorkflows()
    model_factory = ScriptedModelFactory(
        [("explore", {"question": "fraud rate by card type"})]
    )
    agent = create_agent(
        workflows,
        model_factory,
        history=[ConversationMessage(role="user", content="Start an analysis")],
    )

    response = asyncio.run(agent.respond("What about card types?"))

    assert workflows.queries == ["fraud rate by card type"]
    assert [artifact.type for artifact in response.artifacts] == ["analysis_step"]
    assert isinstance(response.artifacts[0], AnalysisStepArtifact)
    assert response.working_state.last_sql == response.artifacts[0].sql
    assert response.metadata.intent == "EXPLORE"
    assert response.metadata.repair_count == 1
    assert response.metadata.cache_hit is True
    assert response.message == "Grounded analysis synthesis."
    assert model_factory.histories[0][0]["content"] == [{"text": "Start an analysis"}]
    assert model_factory.models[0].cleaned is True


def test_explore_aggregates_ordered_multi_step_evidence_and_metadata() -> None:
    workflows = SequentialQueryWorkflows()
    model_factory = ScriptedModelFactory(
        [("explore", {"question": "Investigate fraud characteristics"})]
    )
    agent = ChatAgent(
        workflows,
        workflows,
        workflows,
        workflows,
        [],
        WorkingState(),
        analysis_agent_factory=AnalysisAgentFactory(
            workflows, model_factory=ThreeQueryAnalysisModel
        ),
        model_factory=model_factory,
    )

    response = asyncio.run(agent.respond("Explore fraud patterns"))

    steps = [
        artifact
        for artifact in response.artifacts
        if isinstance(artifact, AnalysisStepArtifact)
    ]
    assert [artifact.step for artifact in steps] == [1, 2, 3]
    assert [artifact.question for artifact in steps] == workflows.queries
    assert response.working_state.last_sql == "SELECT 3 AS finding"
    assert response.metadata.repair_count == 3
    assert response.metadata.cache_hit is False
    assert response.message == "Grounded in all three analysis steps."


def test_generate_rule_tool_updates_authoritative_candidate_state() -> None:
    workflows = StubWorkflows()
    model_factory = ScriptedModelFactory(
        [("generate_rule", {"instruction": "transactions above $1,000"})]
    )
    agent = create_agent(
        workflows,
        model_factory,
        WorkingState(
            candidate_rule="amount_usd > 500",
            previous_rule="amount_usd > 250",
        ),
    )

    response = asyncio.run(agent.respond("Create a new rule"))

    assert workflows.generated == ["transactions above $1,000"]
    assert response.artifacts[0].type == "candidate_rule"
    assert response.working_state.candidate_rule == "amount_usd > 1000"
    assert response.working_state.previous_rule is None
    assert response.metadata.intent == "GENERATE_RULE"
    assert response.metadata.repair_count == 1


def test_refine_rule_uses_explicit_working_state_not_model_arguments() -> None:
    workflows = StubWorkflows()
    model_factory = ScriptedModelFactory(
        [("refine_rule", {"instruction": "raise it to $1,500"})]
    )
    agent = create_agent(
        workflows,
        model_factory,
        WorkingState(candidate_rule="amount_usd > 1000"),
    )

    response = asyncio.run(agent.respond("Make it stricter"))

    assert workflows.refined == [("amount_usd > 1000", "raise it to $1,500")]
    assert response.working_state.previous_rule == "amount_usd > 1000"
    assert response.working_state.candidate_rule == "amount_usd > 1500"
    assert response.metadata.intent == "REFINE_RULE"


def test_backtest_rule_uses_candidate_state_and_stores_small_result() -> None:
    workflows = StubWorkflows()
    model_factory = ScriptedModelFactory([("backtest_rule", {})])
    agent = create_agent(
        workflows,
        model_factory,
        WorkingState(candidate_rule="amount_usd > 1000"),
    )

    response = asyncio.run(agent.respond("Backtest it"))

    assert workflows.backtested == ["amount_usd > 1000"]
    assert response.artifacts[0].type == "backtest"
    assert response.working_state.last_backtest is not None
    assert response.working_state.last_backtest.metrics.labelled_population == 80
    assert response.metadata.intent == "BACKTEST_RULE"


def test_compare_rules_uses_current_and_previous_working_state() -> None:
    workflows = StubWorkflows()
    model_factory = ScriptedModelFactory([("compare_rules", {})])
    agent = create_agent(
        workflows,
        model_factory,
        WorkingState(
            candidate_rule="amount_usd > 1500",
            previous_rule="amount_usd > 1000",
        ),
    )

    response = asyncio.run(agent.respond("Is it better?"))

    assert workflows.compared == [("amount_usd > 1500", "amount_usd > 1000")]
    assert response.artifacts[0].type == "rule_comparison"
    assert response.metadata.intent == "COMPARE_RULES"


@pytest.mark.parametrize(
    ("tool_name", "state", "intent", "missing_fields"),
    [
        ("refine_rule", WorkingState(), "REFINE_RULE", ["candidate_rule"]),
        ("backtest_rule", WorkingState(), "BACKTEST_RULE", ["candidate_rule"]),
        (
            "compare_rules",
            WorkingState(candidate_rule="amount_usd > 1000"),
            "COMPARE_RULES",
            ["previous_rule"],
        ),
    ],
)
def test_state_dependent_tools_fail_before_calling_service(
    tool_name: str,
    state: WorkingState,
    intent: str,
    missing_fields: list[str],
) -> None:
    workflows = StubWorkflows()
    actions = [
        (tool_name, {"instruction": "change it"} if tool_name == "refine_rule" else {})
    ]
    model_factory = ScriptedModelFactory(actions)
    agent = create_agent(workflows, model_factory, state)

    with pytest.raises(MissingChatState) as raised:
        asyncio.run(agent.respond("Do it"))

    assert raised.value.intent == intent
    assert raised.value.missing_fields == missing_fields
    assert workflows.refined == []
    assert workflows.backtested == []
    assert workflows.compared == []
    assert model_factory.models[0].cleaned is True


def test_chat_agent_rejects_multiple_or_missing_tool_selections() -> None:
    workflows = StubWorkflows()
    multiple = create_agent(
        workflows,
        ScriptedModelFactory(
            [
                ("generate_rule", {"instruction": "high amount"}),
                ("explore", {"question": "count transactions"}),
            ]
        ),
    )
    missing = create_agent(workflows, ScriptedModelFactory([]))

    with pytest.raises(ChatAgentError, match="more than one"):
        asyncio.run(multiple.respond("Do two things"))
    with pytest.raises(ChatAgentError, match="did not select"):
        asyncio.run(missing.respond("Hello"))


@pytest.mark.parametrize(
    ("response_text", "message"),
    [
        (" ", "no assistant message"),
        ("x" * 10_001, "oversized assistant message"),
    ],
)
def test_chat_agent_rejects_invalid_assistant_text(
    response_text: str, message: str
) -> None:
    workflows = StubWorkflows()
    agent = create_agent(
        workflows,
        ScriptedModelFactory(
            [("generate_rule", {"instruction": "high amount"})],
            response_text=response_text,
        ),
    )

    with pytest.raises(ChatAgentError, match=message):
        asyncio.run(agent.respond("Create a rule"))


def test_backtest_tool_returns_invalid_working_state_error() -> None:
    workflows = InvalidBacktestWorkflows()
    agent = create_agent(
        workflows,
        ScriptedModelFactory([("backtest_rule", {})]),
        WorkingState(candidate_rule="is_fraud = 1"),
    )

    with pytest.raises(InvalidChatState) as raised:
        asyncio.run(agent.respond("Backtest it"))

    assert raised.value.intent == "BACKTEST_RULE"
    assert raised.value.detail["errors"][0]["code"] == "OUTCOME_COLUMN_FORBIDDEN"


def test_factory_creates_fresh_agents_without_cross_request_state() -> None:
    workflows = StubWorkflows()
    model_factory = ScriptedModelFactory(
        [("backtest_rule", {})],
        [("backtest_rule", {})],
    )
    factory = ChatAgentFactory(
        workflows,
        workflows,
        workflows,
        workflows,
        model_factory=model_factory,
    )

    first = factory.create([], WorkingState(candidate_rule="amount_usd > 1000"))
    second = factory.create([], WorkingState(candidate_rule="amount_usd > 2000"))
    first_response = asyncio.run(first.respond("Backtest it"))
    second_response = asyncio.run(second.respond("Backtest it"))

    assert first is not second
    assert workflows.backtested == ["amount_usd > 1000", "amount_usd > 2000"]
    assert first_response.working_state.candidate_rule == "amount_usd > 1000"
    assert second_response.working_state.candidate_rule == "amount_usd > 2000"
