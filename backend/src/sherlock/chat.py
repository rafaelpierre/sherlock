"""Stateless ChatAgent orchestration over deterministic application services."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from strands import Agent, tool
from strands.types.content import Message

from sherlock.analysis import (
    AnalysisAgentError,
    AnalysisAgentFactory,
)
from sherlock.api.artifacts import (
    Artifact,
    BacktestArtifact,
    CandidateRuleArtifact,
    RuleComparisonArtifact,
)
from sherlock.api.chat_models import (
    MAX_ASSISTANT_MESSAGE_LENGTH,
    ChatIntent,
    ChatMetadata,
    ChatResponse,
    ChatTextDelta,
    ChatToolCall,
    ChatToolResult,
)
from sherlock.api.schemas import (
    BacktestResponse,
    ConversationMessage,
    RuleComparisonResponse,
    RuleGenerateResponse,
    RuleRefineResponse,
    StoredBacktest,
    WorkingState,
)
from sherlock.services.backtest import BacktestError, InvalidBacktestRule
from sherlock.services.rule_comparison import (
    InvalidComparisonRule,
)
from sherlock.services.rule_generation import (
    InvalidCurrentRule,
    RuleGenerationError,
)

CHAT_SYSTEM_PROMPT = """
You are Sherlock's conversational fraud analytics coordinator. Choose exactly
one ordinary tool for every user message. The tools call authoritative services;
never invent SQL, rules, query results, metrics, comparisons, or state.

Intent mapping:
- EXPLORE: analytical questions about transaction data. Call explore once; its
  specialist synthesis is terminal for this turn, so do not call another tool.
- GENERATE_RULE: requests to create a new candidate fraud rule.
- REFINE_RULE: requests to modify the current candidate rule.
- BACKTEST_RULE: requests to evaluate the current candidate rule historically.
- COMPARE_RULES: requests to compare the current and previous candidate rules.

Use recent history only for conversational interpretation. Critical rule
referents come from explicit working state inside the tools. After the tool
returns, summarize its result concisely. Candidate rules are investigation
hypotheses, not production fraud decisions.
""".strip()


class ChatModel(Protocol):
    async def invoke_async(self, prompt: str) -> Any: ...

    def stream_async(
        self, prompt: str, *, cancel_signal: threading.Event
    ) -> AsyncIterator[dict[str, Any]]: ...

    def cleanup(self) -> None: ...


ChatModelFactory = Callable[[list[Any], list[Message]], ChatModel]


class Text2SQLWorkflow(Protocol):
    async def query(self, question: str) -> dict[str, Any]: ...


class AnalysisWorkflow(Protocol):
    async def analyze(
        self,
        question: str,
        *,
        cancel_signal: threading.Event,
        event_sink: Callable[[str, bool | None], None] | None = None,
    ) -> Any: ...


class AnalysisWorkflowFactory(Protocol):
    def create(self) -> AnalysisWorkflow: ...


class RuleGenerationWorkflow(Protocol):
    async def generate(self, instruction: str) -> dict[str, Any]: ...

    async def refine(self, rule: str, instruction: str) -> dict[str, Any]: ...


class BacktestWorkflow(Protocol):
    async def backtest(self, rule: str) -> dict[str, Any]: ...


class RuleComparisonWorkflow(Protocol):
    async def compare(
        self, current_rule: str, previous_rule: str
    ) -> dict[str, Any]: ...


class ChatAgentError(RuntimeError):
    """The conversational model did not complete one supported workflow."""


class MissingChatState(ValueError):
    """The chosen workflow requires absent explicit working state."""

    def __init__(self, intent: ChatIntent, missing_fields: list[str]) -> None:
        joined = ", ".join(missing_fields)
        super().__init__(f"{intent} requires working state: {joined}.")
        self.intent = intent
        self.missing_fields = missing_fields


class InvalidChatState(ValueError):
    """A rule stored in working state failed deterministic validation."""

    def __init__(self, intent: ChatIntent, detail: dict[str, Any]) -> None:
        super().__init__(f"Stored rule state is invalid for {intent}.")
        self.intent = intent
        self.detail = detail


@dataclass
class _ChatExecution:
    working_state: WorkingState
    artifacts: list[Artifact] = field(default_factory=list)
    intent: ChatIntent | None = None
    repair_count: int = 0
    cache_hit: bool = False
    missing_state: MissingChatState | None = None
    invalid_state: InvalidChatState | None = None
    service_error: Exception | None = None
    routing_error: str | None = None
    event_sink: Callable[[ChatToolCall | ChatToolResult], None] | None = None
    activity_count: int = 0
    active_activities: dict[str, str] = field(default_factory=dict)
    cancel_signal: threading.Event = field(default_factory=threading.Event)
    analysis_message: str | None = None

    def select(self, intent: ChatIntent) -> bool:
        if self.intent is not None:
            self.routing_error = "The ChatAgent selected more than one workflow tool."
            return False
        self.intent = intent
        return True

    def require(self, intent: ChatIntent, *fields: str) -> bool:
        missing = [name for name in fields if getattr(self.working_state, name) is None]
        if missing:
            self.missing_state = MissingChatState(intent, missing)
            return False
        return True

    def begin_activity(self, tool_name: str) -> str | None:
        if self.event_sink is None:
            return None
        self.activity_count += 1
        activity_id = f"activity-{self.activity_count}"
        self.active_activities[activity_id] = tool_name
        kind, name, message, _ = _ACTIVITY_COPY[tool_name]
        self.event_sink(
            ChatToolCall(
                id=activity_id,
                kind=kind,
                name=name,
                message=message,
            )
        )
        return activity_id

    def finish_activity(
        self, tool_name: str, activity_id: str | None, *, succeeded: bool = True
    ) -> None:
        if self.event_sink is None or activity_id is None:
            return
        active_tool_name = self.active_activities.pop(activity_id, None)
        if active_tool_name is None:
            return
        message = (
            _ACTIVITY_COPY[active_tool_name][3]
            if succeeded
            else "This activity could not be completed."
        )
        self.event_sink(
            ChatToolResult(
                id=activity_id,
                message=message,
                outcome="succeeded" if succeeded else "failed",
            )
        )

    def analysis_activity(self, activity_id: str, succeeded: bool | None) -> None:
        """Publish a bounded specialist-query activity with stable pairing."""

        if self.event_sink is None:
            return
        if succeeded is None:
            self.active_activities[activity_id] = "analysis_query"
            kind, name, message, _ = _ACTIVITY_COPY["analysis_query"]
            self.event_sink(
                ChatToolCall(
                    id=activity_id,
                    kind=kind,
                    name=name,
                    message=message,
                )
            )
            return
        self.finish_activity("analysis_query", activity_id, succeeded=succeeded)

    def fail_active_activities(self) -> None:
        for activity_id, tool_name in list(self.active_activities.items()):
            self.finish_activity(tool_name, activity_id, succeeded=False)

    def allows_streamed_text(self) -> bool:
        return self.intent != "EXPLORE" and all(
            error is None
            for error in (
                self.missing_state,
                self.invalid_state,
                self.service_error,
                self.routing_error,
            )
        )


StreamEventName = Literal["text_delta", "tool_call", "tool_result", "complete"]
ChatStreamItem = tuple[
    StreamEventName,
    ChatTextDelta | ChatToolCall | ChatToolResult | ChatResponse,
]
StreamQueueItem = (
    ChatTextDelta | ChatToolCall | ChatToolResult | ChatResponse | Exception | None
)

_ACTIVITY_COPY: dict[
    str, tuple[Literal["tool_call", "agent_handoff"], str, str, str]
] = {
    "explore": (
        "agent_handoff",
        "Transaction analysis",
        "Sherlock's analysis specialist is investigating the transaction data.",
        "Transaction analysis is ready.",
    ),
    "analysis_query": (
        "tool_call",
        "Analysis evidence",
        "Sherlock is querying bounded transaction evidence.",
        "The analysis evidence is ready.",
    ),
    "generate_rule": (
        "tool_call",
        "Candidate rule",
        "Sherlock is drafting and validating a candidate rule.",
        "The candidate rule is ready for review.",
    ),
    "refine_rule": (
        "tool_call",
        "Rule refinement",
        "Sherlock is updating and validating the candidate rule.",
        "The refined candidate rule is ready for review.",
    ),
    "backtest_rule": (
        "tool_call",
        "Historical test",
        "Sherlock is replaying the candidate rule on historical transactions.",
        "The historical test is complete.",
    ),
    "compare_rules": (
        "tool_call",
        "Rule comparison",
        "Sherlock is comparing the current and previous candidate rules.",
        "The rule comparison is ready.",
    ),
}


async def _queue_events(
    queue: asyncio.Queue[StreamQueueItem],
) -> AsyncIterator[ChatTextDelta | ChatToolCall | ChatToolResult | ChatResponse]:
    while True:
        item = await queue.get()
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        yield item


def _stream_item(
    item: ChatTextDelta | ChatToolCall | ChatToolResult | ChatResponse,
) -> ChatStreamItem:
    if isinstance(item, ChatTextDelta):
        return "text_delta", item
    if isinstance(item, ChatToolCall):
        return "tool_call", item
    if isinstance(item, ChatToolResult):
        return "tool_result", item
    return "complete", item


async def _consume_native_stream(
    model: ChatModel,
    prompt: str,
    cancel_signal: threading.Event,
    queue: asyncio.Queue[StreamQueueItem],
    allows_text: Callable[[], bool],
) -> Any:
    result: Any | None = None
    streamed_text_length = 0
    async for native_event in model.stream_async(prompt, cancel_signal=cancel_signal):
        delta = native_event.get("data")
        if isinstance(delta, str) and delta and allows_text():
            streamed_text_length += len(delta)
            if streamed_text_length > MAX_ASSISTANT_MESSAGE_LENGTH:
                raise ChatAgentError(
                    "The ChatAgent returned an oversized assistant message."
                )
            queue.put_nowait(ChatTextDelta(delta=delta))
        if "result" in native_event:
            result = native_event["result"]
    if result is None:
        raise ChatAgentError("The ChatAgent returned no result event.")
    return result


def _stream_exception(exc: Exception) -> Exception:
    if isinstance(exc, (MissingChatState, InvalidChatState, ChatAgentError)):
        return exc
    return ChatAgentError("The ChatAgent invocation failed.")


def _create_strands_model(tools: list[Any], history: list[Message]) -> ChatModel:
    return cast(
        ChatModel,
        Agent(
            system_prompt=CHAT_SYSTEM_PROMPT,
            messages=history,
            tools=tools,
            callback_handler=None,
            name="sherlock-chat-agent",
            description="Routes one conversational turn through Sherlock services.",
        ),
    )


def _strands_history(history: list[ConversationMessage]) -> list[Message]:
    return [
        cast(
            Message,
            {"role": message.role, "content": [{"text": _history_text(message)}]},
        )
        for message in history
    ]


def _history_text(message: ConversationMessage) -> str:
    """Render client-owned continuity context without recreating native tool calls."""

    if not message.activities and message.outcome is None:
        return message.content
    activity_lines = [
        (
            "User-visible activity summaries from this assistant turn (context only; "
            "they are not authoritative tool results):"
        )
    ]
    activity_lines.extend(
        f"- {activity.kind}: {activity.name}. {activity.message} "
        f"Outcome: {activity.outcome}. {activity.result}"
        for activity in message.activities
    )
    if message.outcome == "failed":
        activity_lines.append("This assistant turn was interrupted before completion.")
    return f"{message.content}\n\n" + "\n".join(activity_lines)


class ChatAgent:
    """Fresh per-request agent with invocation-local tools and state."""

    def __init__(
        self,
        text2sql_service: Text2SQLWorkflow,
        rule_generation_service: RuleGenerationWorkflow,
        backtest_service: BacktestWorkflow,
        rule_comparison_service: RuleComparisonWorkflow,
        history: list[ConversationMessage],
        working_state: WorkingState,
        *,
        analysis_agent_factory: AnalysisWorkflowFactory | None = None,
        model_factory: ChatModelFactory = _create_strands_model,
    ) -> None:
        self._text2sql_service = text2sql_service
        self._rule_generation_service = rule_generation_service
        self._backtest_service = backtest_service
        self._rule_comparison_service = rule_comparison_service
        self._history = history
        self._execution = _ChatExecution(working_state.model_copy(deep=True))
        self._analysis_agent_factory = analysis_agent_factory or AnalysisAgentFactory(
            text2sql_service
        )
        self._model_factory = model_factory

    async def respond(self, message: str) -> ChatResponse:
        model = self._model_factory(self._tools(), _strands_history(self._history))
        prompt = self._prompt(message)
        try:
            try:
                result = await model.invoke_async(prompt)
            except Exception as exc:
                raise ChatAgentError("The ChatAgent invocation failed.") from exc
        finally:
            model.cleanup()

        return self._response(result)

    async def stream(self, message: str) -> AsyncIterator[ChatStreamItem]:
        """Yield translated public events while a fresh model invocation runs."""

        model = self._model_factory(self._tools(), _strands_history(self._history))
        queue: asyncio.Queue[StreamQueueItem] = asyncio.Queue()
        cancel_signal = threading.Event()
        self._execution.cancel_signal = cancel_signal

        def publish(event: ChatToolCall | ChatToolResult) -> None:
            queue.put_nowait(event)

        self._execution.event_sink = publish

        async def produce() -> None:
            try:
                result = await _consume_native_stream(
                    model,
                    self._prompt(message),
                    cancel_signal,
                    queue,
                    self._execution.allows_streamed_text,
                )
                response = self._response(result)
                if self._execution.intent == "EXPLORE":
                    queue.put_nowait(ChatTextDelta(delta=response.message))
                queue.put_nowait(response)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - cross-task error transport
                self._execution.fail_active_activities()
                queue.put_nowait(_stream_exception(exc))
            finally:
                self._execution.event_sink = None
                try:
                    model.cleanup()
                finally:
                    queue.put_nowait(None)

        producer = asyncio.create_task(produce())
        try:
            async for item in _queue_events(queue):
                yield _stream_item(item)
        finally:
            cancel_signal.set()
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    def _prompt(self, message: str) -> str:
        return (
            "Authoritative working state:\n"
            f"{self._execution.working_state.model_dump_json()}\n\n"
            f"User message:\n{message}"
        )

    def _response(self, result: Any) -> ChatResponse:
        self._raise_errors()
        if self._execution.intent is None or not self._execution.artifacts:
            raise ChatAgentError(
                "The ChatAgent did not select a supported workflow tool."
            )
        assistant_message = (
            self._execution.analysis_message
            if self._execution.intent == "EXPLORE"
            else str(result).strip()
        )
        if not assistant_message:
            raise ChatAgentError("The ChatAgent returned no assistant message.")
        if len(assistant_message) > MAX_ASSISTANT_MESSAGE_LENGTH:
            raise ChatAgentError(
                "The ChatAgent returned an oversized assistant message."
            )
        return ChatResponse(
            message=assistant_message,
            artifacts=self._execution.artifacts,
            working_state=self._execution.working_state,
            metadata=ChatMetadata(
                intent=self._execution.intent,
                repair_count=self._execution.repair_count,
                cache_hit=self._execution.cache_hit,
            ),
        )

    def _raise_errors(self) -> None:
        if self._execution.missing_state is not None:
            raise self._execution.missing_state
        if self._execution.invalid_state is not None:
            raise self._execution.invalid_state
        if self._execution.service_error is not None:
            raise ChatAgentError(
                str(self._execution.service_error)
            ) from self._execution.service_error
        if self._execution.routing_error is not None:
            raise ChatAgentError(self._execution.routing_error)

    def _tools(self) -> list[Any]:
        return [
            self.explore,
            self.generate_rule,
            self.refine_rule,
            self.backtest_rule,
            self.compare_rules,
        ]

    @tool(name="explore")
    async def explore(self, question: str) -> dict[str, Any]:
        """Hand one analytical request to the bounded AnalysisAgent."""

        if not self._execution.select("EXPLORE"):
            return {"error": self._execution.routing_error}
        activity_id = self._execution.begin_activity("explore")
        try:
            response = await self._analysis_agent_factory.create().analyze(
                question,
                cancel_signal=self._execution.cancel_signal,
                event_sink=self._execution.analysis_activity,
            )
        except asyncio.CancelledError:
            self._execution.finish_activity("explore", activity_id, succeeded=False)
            raise
        except AnalysisAgentError as exc:
            self._execution.service_error = exc
            self._execution.finish_activity("explore", activity_id, succeeded=False)
            return {"error": str(exc)}
        self._execution.artifacts.extend(response.steps)
        self._execution.working_state.last_sql = response.steps[-1].sql
        self._execution.repair_count = response.repair_count
        self._execution.cache_hit = response.cache_hit
        self._execution.analysis_message = response.message
        self._execution.finish_activity("explore", activity_id)
        return {
            "message": (
                "The bounded analysis is complete. Return without calling another "
                "tool; its synthesis and evidence are authoritative."
            )
        }

    @tool(name="generate_rule")
    async def generate_rule(self, instruction: str) -> dict[str, Any]:
        """Create and validate one new candidate rule from an instruction."""

        if not self._execution.select("GENERATE_RULE"):
            return {"error": self._execution.routing_error}
        activity_id = self._execution.begin_activity("generate_rule")
        try:
            response = RuleGenerateResponse.model_validate(
                await self._rule_generation_service.generate(instruction)
            )
        except RuleGenerationError as exc:
            self._execution.service_error = exc
            self._execution.finish_activity(
                "generate_rule", activity_id, succeeded=False
            )
            return {"error": str(exc)}
        self._execution.artifacts.append(
            CandidateRuleArtifact(type="candidate_rule", **response.model_dump())
        )
        self._execution.repair_count = response.repair_count
        if response.valid and response.rule is not None:
            self._execution.working_state.candidate_rule = response.rule
            self._execution.working_state.previous_rule = None
            self._execution.working_state.last_backtest = None
        self._execution.finish_activity("generate_rule", activity_id)
        return response.model_dump(mode="json")

    @tool(name="refine_rule")
    async def refine_rule(self, instruction: str) -> dict[str, Any]:
        """Refine the explicit current candidate rule in working state."""

        intent: ChatIntent = "REFINE_RULE"
        if not self._execution.select(intent):
            return {"error": self._execution.routing_error}
        activity_id = self._execution.begin_activity("refine_rule")
        if not self._execution.require(intent, "candidate_rule"):
            self._execution.finish_activity("refine_rule", activity_id, succeeded=False)
            return {"error": str(self._execution.missing_state)}
        current_rule = cast(str, self._execution.working_state.candidate_rule)
        try:
            response = RuleRefineResponse.model_validate(
                await self._rule_generation_service.refine(current_rule, instruction)
            )
        except InvalidCurrentRule as exc:
            self._execution.invalid_state = InvalidChatState(
                intent, exc.validation.as_dict()
            )
            self._execution.finish_activity("refine_rule", activity_id, succeeded=False)
            return {"error": str(self._execution.invalid_state)}
        except RuleGenerationError as exc:
            self._execution.service_error = exc
            self._execution.finish_activity("refine_rule", activity_id, succeeded=False)
            return {"error": str(exc)}
        self._execution.artifacts.append(
            CandidateRuleArtifact(
                type="candidate_rule",
                rule=response.rule,
                valid=response.valid,
                repair_count=response.repair_count,
                errors=response.errors,
            )
        )
        self._execution.repair_count = response.repair_count
        if response.valid and response.rule is not None:
            self._execution.working_state.previous_rule = response.previous_rule
            self._execution.working_state.candidate_rule = response.rule
            self._execution.working_state.last_backtest = None
        self._execution.finish_activity("refine_rule", activity_id)
        return response.model_dump(mode="json")

    @tool(name="backtest_rule")
    async def backtest_rule(self) -> dict[str, Any]:
        """Backtest the explicit current candidate rule in working state."""

        intent: ChatIntent = "BACKTEST_RULE"
        if not self._execution.select(intent):
            return {"error": self._execution.routing_error}
        activity_id = self._execution.begin_activity("backtest_rule")
        if not self._execution.require(intent, "candidate_rule"):
            self._execution.finish_activity(
                "backtest_rule", activity_id, succeeded=False
            )
            return {"error": str(self._execution.missing_state)}
        try:
            response = BacktestResponse.model_validate(
                await self._backtest_service.backtest(
                    cast(str, self._execution.working_state.candidate_rule)
                )
            )
        except InvalidBacktestRule as exc:
            self._execution.invalid_state = InvalidChatState(
                intent, exc.validation.as_dict()
            )
            self._execution.finish_activity(
                "backtest_rule", activity_id, succeeded=False
            )
            return {"error": str(self._execution.invalid_state)}
        except BacktestError as exc:
            self._execution.service_error = exc
            self._execution.finish_activity(
                "backtest_rule", activity_id, succeeded=False
            )
            return {"error": str(exc)}
        self._execution.artifacts.append(
            BacktestArtifact(type="backtest", **response.model_dump())
        )
        self._execution.working_state.candidate_rule = response.rule
        self._execution.working_state.last_backtest = StoredBacktest.model_validate(
            response.model_dump()
        )
        self._execution.finish_activity("backtest_rule", activity_id)
        return response.model_dump(mode="json")

    @tool(name="compare_rules")
    async def compare_rules(self) -> dict[str, Any]:
        """Compare explicit current and previous rules in working state."""

        intent: ChatIntent = "COMPARE_RULES"
        if not self._execution.select(intent):
            return {"error": self._execution.routing_error}
        activity_id = self._execution.begin_activity("compare_rules")
        if not self._execution.require(intent, "candidate_rule", "previous_rule"):
            self._execution.finish_activity(
                "compare_rules", activity_id, succeeded=False
            )
            return {"error": str(self._execution.missing_state)}
        try:
            response = RuleComparisonResponse.model_validate(
                await self._rule_comparison_service.compare(
                    cast(str, self._execution.working_state.candidate_rule),
                    cast(str, self._execution.working_state.previous_rule),
                )
            )
        except InvalidComparisonRule as exc:
            self._execution.invalid_state = InvalidChatState(
                intent,
                {"rule_role": exc.role, **exc.validation.as_dict()},
            )
            self._execution.finish_activity(
                "compare_rules", activity_id, succeeded=False
            )
            return {"error": str(self._execution.invalid_state)}
        except BacktestError as exc:
            self._execution.service_error = exc
            self._execution.finish_activity(
                "compare_rules", activity_id, succeeded=False
            )
            return {"error": str(exc)}
        self._execution.artifacts.append(
            RuleComparisonArtifact(type="rule_comparison", **response.model_dump())
        )
        self._execution.working_state.candidate_rule = response.current.rule
        self._execution.working_state.previous_rule = response.previous.rule
        self._execution.finish_activity("compare_rules", activity_id)
        return response.model_dump(mode="json")


class ChatAgentFactory:
    """Create a fresh ChatAgent while sharing stateless domain services."""

    def __init__(
        self,
        text2sql_service: Text2SQLWorkflow,
        rule_generation_service: RuleGenerationWorkflow,
        backtest_service: BacktestWorkflow,
        rule_comparison_service: RuleComparisonWorkflow,
        *,
        analysis_agent_factory: AnalysisWorkflowFactory | None = None,
        model_factory: ChatModelFactory = _create_strands_model,
    ) -> None:
        self._text2sql_service = text2sql_service
        self._rule_generation_service = rule_generation_service
        self._backtest_service = backtest_service
        self._rule_comparison_service = rule_comparison_service
        self._analysis_agent_factory = analysis_agent_factory or AnalysisAgentFactory(
            text2sql_service
        )
        self._model_factory = model_factory

    def create(
        self,
        history: list[ConversationMessage],
        working_state: WorkingState,
    ) -> ChatAgent:
        return ChatAgent(
            self._text2sql_service,
            self._rule_generation_service,
            self._backtest_service,
            self._rule_comparison_service,
            history,
            working_state,
            analysis_agent_factory=self._analysis_agent_factory,
            model_factory=self._model_factory,
        )
