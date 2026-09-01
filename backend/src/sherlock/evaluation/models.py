"""Validated fixture and report contracts for Sherlock evaluations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SuiteKind = Literal["text2sql", "rule_generation"]
RunMode = Literal["fixture", "live"]
CaseStatus = Literal["passed", "failed", "error"]
type JSONValue = (
    str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]
)


class EvaluationCase(BaseModel):
    """One versioned prompt, oracle, and optional deterministic response."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=100)
    prompt: str = Field(min_length=1, max_length=2_000)
    expected: dict[str, JSONValue] = Field(min_length=1)
    fixture_response: dict[str, JSONValue] | None = None
    rule_oracle: RuleGenerationOracle | None = None


class RuleGenerationOracle(BaseModel):
    """Semantic comparison contract for one candidate-rule generation case."""

    model_config = ConfigDict(extra="forbid")

    reference_predicate: str = Field(min_length=1, max_length=2_000)
    matching_policy: Literal["exact_transaction_ids"]
    transaction_id_tolerance: Literal[0] = 0


class EvaluationSuite(BaseModel):
    """A uniquely named collection of cases for one Sherlock workflow."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=100)
    description: str = Field(min_length=1, max_length=1_000)
    kind: SuiteKind
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_must_be_unique(self) -> EvaluationSuite:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case ids must be unique within a suite")
        return self

    @model_validator(mode="after")
    def rule_generation_cases_must_define_semantic_oracles(self) -> EvaluationSuite:
        if self.kind == "rule_generation" and any(
            case.rule_oracle is None for case in self.cases
        ):
            raise ValueError("rule_generation cases must define rule_oracle")
        return self


class RunMetadata(BaseModel):
    """Context required to interpret and reproduce an evaluation run."""

    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    model: str = Field(min_length=1)
    configuration: dict[str, JSONValue]
    dataset_revision: str = Field(min_length=1)
    mode: RunMode
    selected_suites: list[str] = Field(min_length=1)


class EvaluationCaseResult(BaseModel):
    """Machine-readable result for one executed case."""

    model_config = ConfigDict(extra="forbid")

    suite: str
    case_id: str
    status: CaseStatus
    latency_ms: float = Field(ge=0)
    repair_count: int = Field(ge=0)
    output: dict[str, JSONValue] | None = None
    error: str | None = None
    validation_passed: bool | None = None
    execution_passed: bool | None = None
    semantic_correct: bool | None = None


class EvaluationSummary(BaseModel):
    """Aggregate counts printed by the CLI and embedded in every report."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    total_latency_ms: float = Field(ge=0)
    repair_count: int = Field(ge=0)
    repair_rate: float = Field(ge=0, le=1)
    validation_passed: int = Field(ge=0)
    execution_passed: int = Field(ge=0)
    semantic_correct: int = Field(ge=0)
    semantic_evaluated: int = Field(ge=0)


class EvaluationReport(BaseModel):
    """Stable top-level schema for persisted evaluation results."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    metadata: RunMetadata
    results: list[EvaluationCaseResult]
    summary: EvaluationSummary
