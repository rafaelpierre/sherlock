from __future__ import annotations

from fastapi.testclient import TestClient

from sherlock.api.app import create_app
from sherlock.api.routes import get_rule_generation_service, get_text2sql_service


class StubService:
    async def query(self, question: str) -> dict[str, object]:
        return {
            "question": question,
            "sql": "SELECT COUNT(*) AS count FROM fraud_transactions",
            "result": {
                "columns": ["count"],
                "rows": [[10]],
                "row_count": 1,
                "truncated": False,
            },
            "attempts": 1,
            "cached_sql": False,
        }


class StubRuleService:
    async def generate(self, instruction: str) -> dict[str, object]:
        assert instruction == "debit above $1,000"
        return {
            "rule": "amount_usd > 1000 AND card_type = 'Debit'",
            "valid": True,
            "repair_count": 0,
            "errors": [],
        }


def test_query_endpoint_returns_service_result() -> None:
    app = create_app()
    app.dependency_overrides[get_text2sql_service] = StubService

    with TestClient(app) as client:
        response = client.post("/v1/query", json={"question": "  Count transactions  "})

    assert response.status_code == 200
    assert response.json()["question"] == "Count transactions"
    assert response.json()["result"]["rows"] == [[10]]


def test_query_endpoint_rejects_blank_question() -> None:
    app = create_app()
    app.dependency_overrides[get_text2sql_service] = StubService

    with TestClient(app) as client:
        response = client.post("/v1/query", json={"question": "   "})

    assert response.status_code == 422


def test_rule_generation_endpoint_returns_validated_candidate() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_generation_service] = StubRuleService

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/generate", json={"instruction": "  debit above $1,000  "}
        )

    assert response.status_code == 200
    assert response.json() == {
        "rule": "amount_usd > 1000 AND card_type = 'Debit'",
        "valid": True,
        "repair_count": 0,
        "errors": [],
    }


def test_rule_generation_endpoint_rejects_blank_instruction() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_generation_service] = StubRuleService

    with TestClient(app) as client:
        response = client.post("/v1/rules/generate", json={"instruction": " "})

    assert response.status_code == 422
