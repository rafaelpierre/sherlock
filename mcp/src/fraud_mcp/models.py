"""Pydantic models for MCP tool responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    primary_key: bool


class ForeignKeyInfo(BaseModel):
    column: str
    referenced_relation: str
    referenced_column: str


class RelationInfo(BaseModel):
    name: str
    type: str
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo]
    grain: str | None = None


class SchemaResponse(BaseModel):
    recommended_relation: str
    relations: list[RelationInfo]


class SampleValuesResponse(BaseModel):
    relation: str
    column: str
    values: list[Any]


class QueryResponse(BaseModel):
    model_config = ConfigDict(ser_json_inf_nan="null")

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    sql: str
