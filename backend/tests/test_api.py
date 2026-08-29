from __future__ import annotations

from fastapi.testclient import TestClient

from sherlock.api.app import create_app
from sherlock.api.routes import get_text2sql_service


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
