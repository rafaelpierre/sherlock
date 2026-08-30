"""Public request and response models for the Sherlock HTTP API."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QueryRequest(BaseModel):
    """A natural-language analytics question."""

    question: str = Field(min_length=1, max_length=2_000)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class QueryData(BaseModel):
    """Tabular data returned by the fraud analytics MCP server."""

    model_config = ConfigDict(ser_json_inf_nan="null")

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


class QueryResponse(BaseModel):
    """Successful Text2SQL API response."""

    question: str
    sql: str
    result: QueryData
    attempts: int
    cached_sql: bool


class RuleGenerateRequest(BaseModel):
    """A natural-language instruction for a candidate fraud rule."""

    instruction: str = Field(min_length=1, max_length=2_000)

    @field_validator("instruction")
    @classmethod
    def instruction_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("instruction must not be blank")
        return value


class RuleValidationErrorResponse(BaseModel):
    code: str
    message: str
    suggestion: str | None = None


class RuleGenerateResponse(BaseModel):
    """A generated, deterministically validated candidate rule."""

    rule: str | None
    valid: bool
    repair_count: int = Field(ge=0, le=2)
    errors: list[RuleValidationErrorResponse]


class RuleRefineRequest(BaseModel):
    """An explicit current rule and natural-language modification."""

    rule: str = Field(min_length=1, max_length=5_000)
    instruction: str = Field(min_length=1, max_length=2_000)

    @field_validator("rule", "instruction")
    @classmethod
    def fields_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rule and instruction must not be blank")
        return value


class RuleRefineResponse(RuleGenerateResponse):
    previous_rule: str


class RuleRequest(BaseModel):
    """A request containing one candidate fraud-rule predicate."""

    rule: str = Field(min_length=1, max_length=5_000)

    @field_validator("rule")
    @classmethod
    def rule_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rule must not be blank")
        return value


class BacktestMetricsResponse(BaseModel):
    model_config = ConfigDict(ser_json_inf_nan="null")

    population: int
    labelled_population: int
    fraud_total: int
    transactions_flagged: int
    unlabelled_flagged: int
    fraud_caught: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float | None
    recall: float | None
    false_positive_rate: float | None
    fraud_value_total_usd: float
    fraud_value_captured_usd: float
    fraud_value_recall: float | None
    alerts_per_day: float | None


class BacktestResponse(BaseModel):
    rule: str
    metrics: BacktestMetricsResponse


MAX_HISTORY_MESSAGES = 20


class ConversationMessage(BaseModel):
    """One bounded, client-owned conversational turn."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message content must not be blank")
        return value


class WorkingState(BaseModel):
    """Authoritative structured referents supplied with every chat request."""

    model_config = ConfigDict(extra="forbid")

    candidate_rule: str | None = Field(default=None, max_length=5_000)
    previous_rule: str | None = Field(default=None, max_length=5_000)
    last_sql: str | None = Field(default=None, max_length=20_000)
    last_backtest: BacktestResponse | None = None

    @field_validator("candidate_rule", "previous_rule", "last_sql")
    @classmethod
    def optional_text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("working-state text must not be blank")
        return value


class ConversationState(BaseModel):
    """Complete stateless conversation context owned by the client."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    messages: list[ConversationMessage] = Field(default_factory=list)
    working_state: WorkingState = Field(default_factory=WorkingState)

    @field_validator("messages")
    @classmethod
    def keep_recent_history(
        cls, value: list[ConversationMessage]
    ) -> list[ConversationMessage]:
        return value[-MAX_HISTORY_MESSAGES:]
