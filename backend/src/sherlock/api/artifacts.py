"""Typed structured artifacts returned alongside conversational prose."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from sherlock.api.schemas import (
    BacktestResponse,
    QueryData,
    RuleComparisonResponse,
    RuleGenerateResponse,
)


class SQLArtifact(BaseModel):
    """The exact read-only SQL used for an exploration result."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["sql"]
    sql: str = Field(min_length=1)


class TableArtifact(QueryData):
    """A tabular exploration result using the query service data shape."""

    model_config = ConfigDict(extra="forbid", ser_json_inf_nan="null")

    type: Literal["table"]


class CandidateRuleArtifact(RuleGenerateResponse):
    """A generated or refined deterministically validated candidate rule."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["candidate_rule"]


class BacktestArtifact(BacktestResponse):
    """Historical replay metrics for one validated candidate rule."""

    model_config = ConfigDict(extra="forbid", ser_json_inf_nan="null")

    type: Literal["backtest"]


class RuleComparisonArtifact(RuleComparisonResponse):
    """Comparison of the current candidate rule with its previous version."""

    type: Literal["rule_comparison"]


Artifact = Annotated[
    SQLArtifact
    | TableArtifact
    | CandidateRuleArtifact
    | BacktestArtifact
    | RuleComparisonArtifact,
    Field(discriminator="type"),
]
