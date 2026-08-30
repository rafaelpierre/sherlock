"""Stateless ChatAgent orchestration over deterministic application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from strands import Agent, tool
from strands.types.content import Message

from sherlock.api.artifacts import (
    Artifact,
    BacktestArtifact,
    CandidateRuleArtifact,
    RuleComparisonArtifact,
    SQLArtifact,
    TableArtifact,
)
from sherlock.api.chat_models import (
    MAX_ASSISTANT_MESSAGE_LENGTH,
    ChatIntent,
    ChatMetadata,
    ChatResponse,
)
from sherlock.api.schemas import (
    BacktestResponse,
    ConversationMessage,
    QueryResponse,
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
from sherlock.services.text2sql import Text2SQLError

CHAT_SYSTEM_PROMPT = """
You are Sherlock's conversational fraud analytics coordinator. Choose exactly
one ordinary tool for every user message. The tools call authoritative services;
never invent SQL, rules, query results, metrics, comparisons, or state.

Intent mapping:
- EXPLORE: analytical questions about transaction data.
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

    def cleanup(self) -> None: ...


ChatModelFactory = Callable[[list[Any], list[Message]], ChatModel]


class Text2SQLWorkflow(Protocol):
    async def query(self, question: str) -> dict[str, Any]: ...


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
            {"role": message.role, "content": [{"text": message.content}]},
        )
        for message in history
    ]


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
        model_factory: ChatModelFactory = _create_strands_model,
    ) -> None:
        self._text2sql_service = text2sql_service
        self._rule_generation_service = rule_generation_service
        self._backtest_service = backtest_service
        self._rule_comparison_service = rule_comparison_service
        self._history = history
        self._execution = _ChatExecution(working_state.model_copy(deep=True))
        self._model_factory = model_factory

    async def respond(self, message: str) -> ChatResponse:
        model = self._model_factory(self._tools(), _strands_history(self._history))
        prompt = (
            "Authoritative working state:\n"
            f"{self._execution.working_state.model_dump_json()}\n\n"
            f"User message:\n{message}"
        )
        try:
            try:
                result = await model.invoke_async(prompt)
            except Exception as exc:
                raise ChatAgentError("The ChatAgent invocation failed.") from exc
        finally:
            model.cleanup()

        self._raise_errors()
        if self._execution.intent is None or not self._execution.artifacts:
            raise ChatAgentError(
                "The ChatAgent did not select a supported workflow tool."
            )
        assistant_message = str(result).strip()
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
        """Answer one analytical data question through the Text2SQL service."""

        if not self._execution.select("EXPLORE"):
            return {"error": self._execution.routing_error}
        try:
            response = QueryResponse.model_validate(
                await self._text2sql_service.query(question)
            )
        except Text2SQLError as exc:
            self._execution.service_error = exc
            return {"error": str(exc)}
        self._execution.artifacts.extend(
            [
                SQLArtifact(type="sql", sql=response.sql),
                TableArtifact(type="table", **response.result.model_dump()),
            ]
        )
        self._execution.working_state.last_sql = response.sql
        self._execution.repair_count = response.attempts - 1
        self._execution.cache_hit = response.cached_sql
        return response.model_dump(mode="json")

    @tool(name="generate_rule")
    async def generate_rule(self, instruction: str) -> dict[str, Any]:
        """Create and validate one new candidate rule from an instruction."""

        if not self._execution.select("GENERATE_RULE"):
            return {"error": self._execution.routing_error}
        try:
            response = RuleGenerateResponse.model_validate(
                await self._rule_generation_service.generate(instruction)
            )
        except RuleGenerationError as exc:
            self._execution.service_error = exc
            return {"error": str(exc)}
        self._execution.artifacts.append(
            CandidateRuleArtifact(type="candidate_rule", **response.model_dump())
        )
        self._execution.repair_count = response.repair_count
        if response.valid and response.rule is not None:
            self._execution.working_state.candidate_rule = response.rule
            self._execution.working_state.previous_rule = None
            self._execution.working_state.last_backtest = None
        return response.model_dump(mode="json")

    @tool(name="refine_rule")
    async def refine_rule(self, instruction: str) -> dict[str, Any]:
        """Refine the explicit current candidate rule in working state."""

        intent: ChatIntent = "REFINE_RULE"
        if not self._execution.select(intent):
            return {"error": self._execution.routing_error}
        if not self._execution.require(intent, "candidate_rule"):
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
            return {"error": str(self._execution.invalid_state)}
        except RuleGenerationError as exc:
            self._execution.service_error = exc
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
        return response.model_dump(mode="json")

    @tool(name="backtest_rule")
    async def backtest_rule(self) -> dict[str, Any]:
        """Backtest the explicit current candidate rule in working state."""

        intent: ChatIntent = "BACKTEST_RULE"
        if not self._execution.select(intent):
            return {"error": self._execution.routing_error}
        if not self._execution.require(intent, "candidate_rule"):
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
            return {"error": str(self._execution.invalid_state)}
        except BacktestError as exc:
            self._execution.service_error = exc
            return {"error": str(exc)}
        self._execution.artifacts.append(
            BacktestArtifact(type="backtest", **response.model_dump())
        )
        self._execution.working_state.candidate_rule = response.rule
        self._execution.working_state.last_backtest = StoredBacktest.model_validate(
            response.model_dump()
        )
        return response.model_dump(mode="json")

    @tool(name="compare_rules")
    async def compare_rules(self) -> dict[str, Any]:
        """Compare explicit current and previous rules in working state."""

        intent: ChatIntent = "COMPARE_RULES"
        if not self._execution.select(intent):
            return {"error": self._execution.routing_error}
        if not self._execution.require(intent, "candidate_rule", "previous_rule"):
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
            return {"error": str(self._execution.invalid_state)}
        except BacktestError as exc:
            self._execution.service_error = exc
            return {"error": str(exc)}
        self._execution.artifacts.append(
            RuleComparisonArtifact(type="rule_comparison", **response.model_dump())
        )
        self._execution.working_state.candidate_rule = response.current.rule
        self._execution.working_state.previous_rule = response.previous.rule
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
        model_factory: ChatModelFactory = _create_strands_model,
    ) -> None:
        self._text2sql_service = text2sql_service
        self._rule_generation_service = rule_generation_service
        self._backtest_service = backtest_service
        self._rule_comparison_service = rule_comparison_service
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
            model_factory=self._model_factory,
        )
