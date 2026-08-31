from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, TypeAdapter, ValidationError

from sherlock.api.app import create_app
from sherlock.api.artifacts import (
    AnalysisStepArtifact,
    Artifact,
    BacktestArtifact,
    CandidateRuleArtifact,
    RuleComparisonArtifact,
    SQLArtifact,
    TableArtifact,
)
from sherlock.api.schemas import BacktestResponse, QueryData

ARTIFACT_ADAPTER = TypeAdapter(Artifact)
METRICS: dict[str, Any] = {
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


def test_sql_artifact_serializes_independently() -> None:
    artifact = SQLArtifact(type="sql", sql="SELECT COUNT(*) FROM fraud_transactions")

    assert artifact.model_dump() == {
        "type": "sql",
        "sql": "SELECT COUNT(*) FROM fraud_transactions",
    }


def test_table_artifact_reuses_query_data_shape() -> None:
    artifact = TableArtifact(
        type="table",
        columns=["card_type", "fraud_rate"],
        rows=[["Debit", 0.0031]],
        row_count=1,
        truncated=False,
    )

    assert isinstance(artifact, QueryData)
    assert artifact.model_dump()["rows"] == [["Debit", 0.0031]]


def test_analysis_step_groups_question_sql_and_table_evidence() -> None:
    artifact = AnalysisStepArtifact(
        type="analysis_step",
        step=1,
        question="Compare fraud rates by card type",
        sql="SELECT card_type, AVG(is_fraud) FROM fraud_transactions",
        table={
            "columns": ["card_type", "fraud_rate"],
            "rows": [["Debit", 0.0031]],
            "row_count": 1,
            "truncated": False,
        },
    )

    assert artifact.table.rows == [["Debit", 0.0031]]


def test_candidate_rule_artifact_serializes_validation_result() -> None:
    artifact = CandidateRuleArtifact(
        type="candidate_rule",
        rule="amount_usd > 1000",
        valid=True,
        repair_count=0,
        errors=[],
    )

    assert artifact.model_dump()["type"] == "candidate_rule"
    assert artifact.model_dump()["valid"] is True


def test_backtest_artifact_reuses_domain_response_shape() -> None:
    artifact = BacktestArtifact(
        type="backtest", rule="amount_usd > 1000", metrics=METRICS
    )

    assert isinstance(artifact, BacktestResponse)
    assert artifact.metrics.unlabelled_flagged == 1


def test_rule_comparison_artifact_serializes_shared_backtests_and_deltas() -> None:
    artifact = RuleComparisonArtifact(
        type="rule_comparison",
        current={"rule": "amount_usd > 1000", "metrics": METRICS},
        previous={"rule": "amount_usd > 500", "metrics": METRICS},
        delta={
            "precision": 0.1,
            "recall": None,
            "transactions_flagged": -20,
            "fraud_caught": -2,
            "fraud_value_captured_usd": -100.0,
        },
    )

    payload = artifact.model_dump(mode="json")
    assert payload["type"] == "rule_comparison"
    assert payload["current"]["metrics"]["labelled_population"] == 80
    assert payload["delta"]["recall"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "sql", "columns": [], "rows": []},
        {"type": "table", "sql": "SELECT 1"},
        {
            "type": "candidate_rule",
            "rule": "amount_usd > 1000",
            "metrics": METRICS,
        },
        {"type": "unknown", "value": "unsupported"},
        {"sql": "SELECT 1"},
    ],
)
def test_artifact_union_rejects_mismatched_or_missing_discriminator(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ARTIFACT_ADAPTER.validate_python(payload)


def test_artifact_union_schema_has_complete_discriminator_mapping() -> None:
    schema = ARTIFACT_ADAPTER.json_schema()

    assert schema["discriminator"] == {
        "mapping": {
            "analysis_step": "#/$defs/AnalysisStepArtifact",
            "backtest": "#/$defs/BacktestArtifact",
            "candidate_rule": "#/$defs/CandidateRuleArtifact",
            "rule_comparison": "#/$defs/RuleComparisonArtifact",
            "sql": "#/$defs/SQLArtifact",
            "table": "#/$defs/TableArtifact",
        },
        "propertyName": "type",
    }
    assert len(schema["oneOf"]) == 6


def test_artifact_union_generates_complete_openapi_components() -> None:
    class ArtifactEnvelope(BaseModel):
        artifacts: list[Artifact]

    app = FastAPI()
    app.add_api_route(
        "/artifacts",
        lambda: {"artifacts": []},
        response_model=ArtifactEnvelope,
    )

    schemas = app.openapi()["components"]["schemas"]
    item_schema = schemas["ArtifactEnvelope"]["properties"]["artifacts"]["items"]

    assert set(item_schema["discriminator"]["mapping"]) == {
        "analysis_step",
        "sql",
        "table",
        "candidate_rule",
        "backtest",
        "rule_comparison",
    }
    assert len(item_schema["oneOf"]) == 6


def test_chat_openapi_response_exposes_artifact_discriminator() -> None:
    schema = create_app().openapi()
    response = schema["paths"]["/v1/chat"]["post"]["responses"]["200"]
    response_schema = response["content"]["application/json"]["schema"]
    chat_schema_name = response_schema["$ref"].rsplit("/", 1)[-1]
    artifacts = schema["components"]["schemas"][chat_schema_name]["properties"][
        "artifacts"
    ]["items"]

    assert set(artifacts["discriminator"]["mapping"]) == {
        "analysis_step",
        "sql",
        "table",
        "candidate_rule",
        "backtest",
        "rule_comparison",
    }
