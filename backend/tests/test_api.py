from __future__ import annotations

from fastapi.testclient import TestClient

from sherlock.api.app import create_app
from sherlock.api.routes import (
    get_backtest_service,
    get_rule_comparison_service,
    get_rule_generation_service,
    get_text2sql_service,
)
from sherlock.services.backtest import InvalidBacktestRule
from sherlock.services.rule_comparison import InvalidComparisonRule
from sherlock.services.rule_validation import (
    RuleValidationError,
    RuleValidationResult,
)


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

    async def refine(self, rule: str, instruction: str) -> dict[str, object]:
        assert instruction == "raise it to $1,500"
        return {
            "rule": "amount_usd > 1500 AND card_type = 'Debit'",
            "previous_rule": rule,
            "valid": True,
            "repair_count": 0,
            "errors": [],
        }


class StubBacktestService:
    async def backtest(self, rule: str) -> dict[str, object]:
        return {
            "rule": rule,
            "metrics": {
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
                "fraud_value_total_usd": 1000.0,
                "fraud_value_captured_usd": 600.0,
                "fraud_value_recall": 0.6,
                "alerts_per_day": 2.0,
            },
        }


class LabelLeakingBacktestService:
    async def backtest(self, rule: str) -> dict[str, object]:
        assert rule == "is_fraud = 1"
        raise InvalidBacktestRule(
            RuleValidationResult(
                valid=False,
                rule=None,
                errors=[
                    RuleValidationError(
                        code="OUTCOME_COLUMN_FORBIDDEN",
                        message=(
                            "Column 'is_fraud' is outcome-only and cannot be used "
                            "in candidate rules."
                        ),
                        suggestion=(
                            "Use transaction attributes that are available when a "
                            "decision is made."
                        ),
                    )
                ],
            )
        )


class StubComparisonService:
    async def compare(self, current_rule: str, previous_rule: str) -> dict[str, object]:
        backtester = StubBacktestService()
        current = await backtester.backtest(current_rule)
        previous = await backtester.backtest(previous_rule)
        return {
            "current": current,
            "previous": previous,
            "delta": {
                "precision": 0.1,
                "recall": None,
                "transactions_flagged": -2,
                "fraud_caught": 1,
                "fraud_value_captured_usd": 50.0,
            },
        }


class InvalidCurrentComparisonService:
    async def compare(self, current_rule: str, previous_rule: str) -> dict[str, object]:
        raise InvalidComparisonRule(
            "current",
            RuleValidationResult(
                valid=False,
                rule=None,
                errors=[
                    RuleValidationError(
                        code="OUTCOME_COLUMN_FORBIDDEN",
                        message="Outcome column is not allowed.",
                    )
                ],
            ),
        )


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


def test_backtest_endpoint_returns_split_cohort_metrics() -> None:
    app = create_app()
    app.dependency_overrides[get_backtest_service] = StubBacktestService

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/backtest", json={"rule": "  amount_usd > 1000  "}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rule"] == "amount_usd > 1000"
    assert payload["metrics"]["labelled_population"] == 80
    assert payload["metrics"]["unlabelled_flagged"] == 1


def test_backtest_endpoint_rejects_blank_rule() -> None:
    app = create_app()
    app.dependency_overrides[get_backtest_service] = StubBacktestService

    with TestClient(app) as client:
        response = client.post("/v1/rules/backtest", json={"rule": " "})

    assert response.status_code == 422


def test_backtest_endpoint_returns_structured_label_leakage_error() -> None:
    app = create_app()
    app.dependency_overrides[get_backtest_service] = LabelLeakingBacktestService

    with TestClient(app) as client:
        response = client.post("/v1/rules/backtest", json={"rule": "is_fraud = 1"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["valid"] is False
    assert detail["rule"] is None
    assert detail["errors"][0]["code"] == "OUTCOME_COLUMN_FORBIDDEN"


def test_rule_refinement_endpoint_preserves_previous_rule() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_generation_service] = StubRuleService

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/refine",
            json={
                "rule": "amount_usd > 1000 AND card_type = 'Debit'",
                "instruction": "raise it to $1,500",
            },
        )

    assert response.status_code == 200
    assert response.json()["previous_rule"] == (
        "amount_usd > 1000 AND card_type = 'Debit'"
    )
    assert response.json()["rule"].startswith("amount_usd > 1500")


def test_rule_refinement_endpoint_rejects_blank_fields() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_generation_service] = StubRuleService

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/refine", json={"rule": " ", "instruction": "raise it"}
        )

    assert response.status_code == 422


def test_rule_comparison_endpoint_returns_backtests_and_deltas() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_comparison_service] = StubComparisonService

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/compare",
            json={
                "current_rule": "  amount_usd > 1000  ",
                "previous_rule": "amount_usd > 500",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["current"]["rule"] == "amount_usd > 1000"
    assert payload["previous"]["rule"] == "amount_usd > 500"
    assert payload["delta"]["transactions_flagged"] == -2
    assert payload["delta"]["recall"] is None


def test_rule_comparison_endpoint_rejects_missing_rule() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_comparison_service] = StubComparisonService

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/compare", json={"current_rule": "amount_usd > 1000"}
        )

    assert response.status_code == 422


def test_rule_comparison_endpoint_rejects_blank_rule() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_comparison_service] = StubComparisonService

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/compare",
            json={"current_rule": " ", "previous_rule": "amount_usd > 500"},
        )

    assert response.status_code == 422


def test_rule_comparison_endpoint_identifies_invalid_rule_role() -> None:
    app = create_app()
    app.dependency_overrides[get_rule_comparison_service] = (
        InvalidCurrentComparisonService
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/rules/compare",
            json={
                "current_rule": "is_fraud = 1",
                "previous_rule": "amount_usd > 500",
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["rule_role"] == "current"
    assert detail["errors"][0]["code"] == "OUTCOME_COLUMN_FORBIDDEN"
