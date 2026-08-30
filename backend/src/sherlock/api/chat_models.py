"""Request and response contracts for the stateless conversational API."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sherlock.api.artifacts import Artifact
from sherlock.api.schemas import (
    MAX_HISTORY_MESSAGES,
    ConversationMessage,
    WorkingState,
)

ChatIntent = Literal[
    "EXPLORE",
    "GENERATE_RULE",
    "REFINE_RULE",
    "BACKTEST_RULE",
    "COMPARE_RULES",
]
MAX_ASSISTANT_MESSAGE_LENGTH = 10_000
MAX_STREAM_ACTIVITY_ID_LENGTH = 200
MAX_STREAM_ACTIVITY_NAME_LENGTH = 200
MAX_STREAM_ACTIVITY_MESSAGE_LENGTH = 2_000


class ChatRequest(BaseModel):
    """One user turn with bounded history and explicit working state."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message: str = Field(min_length=1, max_length=2_000)
    history: list[ConversationMessage] = Field(default_factory=list)
    working_state: WorkingState = Field(default_factory=WorkingState)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @field_validator("history", mode="before")
    @classmethod
    def keep_recent_history(cls, value: Any) -> Any:
        if isinstance(value, list):
            return value[-MAX_HISTORY_MESSAGES:]
        return value


class ChatMetadata(BaseModel):
    """Small observability fields derived from the selected workflow."""

    model_config = ConfigDict(extra="forbid")

    intent: ChatIntent
    repair_count: int = Field(default=0, ge=0)
    cache_hit: bool = False


class ChatResponse(BaseModel):
    """Assistant prose plus authoritative artifacts and replacement state."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_ASSISTANT_MESSAGE_LENGTH)
    artifacts: list[Artifact]
    working_state: WorkingState
    metadata: ChatMetadata


class ChatStateErrorResponse(BaseModel):
    """Structured client error for missing deterministic workflow referents."""

    model_config = ConfigDict(extra="forbid")

    code: Literal["MISSING_WORKING_STATE"]
    message: str
    intent: ChatIntent
    missing_fields: list[str]


class ChatTextDelta(BaseModel):
    """One bounded assistant-text addition in the public stream."""

    model_config = ConfigDict(extra="forbid")

    delta: str = Field(min_length=1, max_length=MAX_ASSISTANT_MESSAGE_LENGTH)


class ChatToolCall(BaseModel):
    """A product-facing activity without native model or tool details."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MAX_STREAM_ACTIVITY_ID_LENGTH)
    kind: Literal["tool_call", "agent_handoff"]
    name: str = Field(min_length=1, max_length=MAX_STREAM_ACTIVITY_NAME_LENGTH)
    message: str = Field(min_length=1, max_length=MAX_STREAM_ACTIVITY_MESSAGE_LENGTH)


class ChatToolResult(BaseModel):
    """A concise product-facing outcome paired with one activity."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MAX_STREAM_ACTIVITY_ID_LENGTH)
    message: str = Field(min_length=1, max_length=MAX_STREAM_ACTIVITY_MESSAGE_LENGTH)


class ChatStreamError(BaseModel):
    """A bounded user-safe failure emitted after an SSE response starts."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_STREAM_ACTIVITY_MESSAGE_LENGTH)
