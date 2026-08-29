"""Public request and response models for the Sherlock HTTP API."""

from __future__ import annotations

from typing import Any

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
