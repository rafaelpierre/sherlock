from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from sherlock.api.app import create_app
from sherlock.api.artifacts import CandidateRuleArtifact
from sherlock.api.chat_models import (
    ChatMetadata,
    ChatResponse,
    ChatTextDelta,
    ChatToolCall,
    ChatToolResult,
)
from sherlock.api.routes import (
    get_backtest_service,
    get_chat_agent_factory,
    get_rule_comparison_service,
    get_rule_generation_service,
    get_text2sql_service,
)
from sherlock.api.schemas import ConversationMessage, WorkingState
from sherlock.chat import InvalidChatState, MissingChatState
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


class StubChatAgent:
    def __init__(self, state: WorkingState) -> None:
        self.state = state

    async def respond(self, message: str) -> ChatResponse:
        assert message == "Create a high-value rule"
        state = self.state.model_copy(deep=True)
        state.candidate_rule = "amount_usd > 1000"
        return ChatResponse(
            message="I created a candidate rule for review.",
            artifacts=[
                CandidateRuleArtifact(
                    type="candidate_rule",
                    rule="amount_usd > 1000",
                    valid=True,
                    repair_count=0,
                    errors=[],
                )
            ],
            working_state=state,
            metadata=ChatMetadata(intent="GENERATE_RULE"),
        )

    async def stream(self, message: str) -> AsyncIterator[tuple[str, object]]:
        response = await self.respond(message)
        yield (
            "tool_call",
            ChatToolCall(
                id="activity-1",
                kind="tool_call",
                name="Candidate rule",
                message="Sherlock is drafting and validating a candidate rule.",
            ),
        )
        yield (
            "tool_result",
            ChatToolResult(
                id="activity-1",
                message="The candidate rule is ready for review.",
                outcome="succeeded",
            ),
        )
        yield "text_delta", ChatTextDelta(delta=response.message)
        yield "complete", response


class StubChatAgentFactory:
    def __init__(self) -> None:
        self.history: list[ConversationMessage] = []

    def create(
        self, history: list[ConversationMessage], working_state: WorkingState
    ) -> StubChatAgent:
        self.history = history
        return StubChatAgent(working_state)


class MissingStateChatAgent:
    async def respond(self, message: str) -> ChatResponse:
        raise MissingChatState("BACKTEST_RULE", ["candidate_rule"])

    async def stream(self, message: str) -> AsyncIterator[tuple[str, object]]:
        yield (
            "tool_call",
            ChatToolCall(
                id="activity-1",
                kind="tool_call",
                name="Historical test",
                message="Sherlock is replaying the candidate rule on historical transactions.",
            ),
        )
        yield (
            "tool_result",
            ChatToolResult(
                id="activity-1",
                message="The historical test could not start.",
                outcome="failed",
            ),
        )
        raise MissingChatState("BACKTEST_RULE", ["candidate_rule"])


class MissingStateChatAgentFactory:
    def create(
        self, history: list[ConversationMessage], working_state: WorkingState
    ) -> MissingStateChatAgent:
        return MissingStateChatAgent()


class InvalidStateChatAgent:
    async def respond(self, message: str) -> ChatResponse:
        raise InvalidChatState(
            "BACKTEST_RULE",
            {
                "valid": False,
                "rule": None,
                "errors": [
                    {
                        "code": "OUTCOME_COLUMN_FORBIDDEN",
                        "message": "Outcome column is not allowed.",
                        "suggestion": None,
                    }
                ],
            },
        )

    async def stream(self, message: str) -> AsyncIterator[tuple[str, object]]:
        yield (
            "tool_call",
            ChatToolCall(
                id="activity-1",
                kind="tool_call",
                name="Historical test",
                message="Sherlock is replaying the candidate rule on historical transactions.",
            ),
        )
        yield (
            "tool_result",
            ChatToolResult(
                id="activity-1",
                message="This activity could not be completed.",
                outcome="failed",
            ),
        )
        await self.respond(message)


class InvalidStateChatAgentFactory:
    def create(
        self, history: list[ConversationMessage], working_state: WorkingState
    ) -> InvalidStateChatAgent:
        return InvalidStateChatAgent()


def test_health_endpoint_reports_api_readiness() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_endpoint_returns_service_result() -> None:
    app = create_app()
    app.dependency_overrides[get_text2sql_service] = StubService

    with TestClient(app) as client:
        response = client.post("/v1/query", json={"question": "  Count transactions  "})

    assert response.status_code == 200
    assert response.json()["question"] == "Count transactions"
    assert response.json()["result"]["rows"] == [[10]]


def test_chat_endpoint_returns_artifacts_authoritative_state_and_metadata() -> None:
    app = create_app()
    factory = StubChatAgentFactory()
    app.dependency_overrides[get_chat_agent_factory] = lambda: factory
    history = [{"role": "user", "content": f"message {index}"} for index in range(23)]

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "  Create a high-value rule  ",
                "history": history,
                "working_state": {},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "I created a candidate rule for review."
    assert payload["artifacts"][0]["type"] == "candidate_rule"
    assert payload["working_state"]["candidate_rule"] == "amount_usd > 1000"
    assert payload["metadata"] == {
        "intent": "GENERATE_RULE",
        "repair_count": 0,
        "cache_hit": False,
    }
    assert len(factory.history) == 20
    assert factory.history[0].content == "message 3"


def test_chat_endpoint_accepts_user_safe_failed_activity_history() -> None:
    app = create_app()
    factory = StubChatAgentFactory()
    app.dependency_overrides[get_chat_agent_factory] = lambda: factory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "Create a high-value rule",
                "history": [
                    {"role": "user", "content": "Explore further"},
                    {
                        "role": "assistant",
                        "content": "Sherlock could not complete the request. Please try again.",
                        "outcome": "failed",
                        "activities": [
                            {
                                "kind": "agent_handoff",
                                "name": "Transaction analysis",
                                "message": "Sherlock is investigating the transaction data.",
                                "result": "This activity could not be completed.",
                                "outcome": "failed",
                            }
                        ],
                    },
                ],
                "working_state": {},
            },
        )

    assert response.status_code == 200
    assert factory.history[1].outcome == "failed"
    assert factory.history[1].activities[0].name == "Transaction analysis"


def test_chat_endpoint_streams_translated_events_and_authoritative_completion() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_agent_factory] = StubChatAgentFactory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            headers={"Accept": "text/event-stream"},
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "Create a high-value rule",
                "working_state": {},
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    blocks = response.text.strip().split("\n\n")
    assert [block.splitlines()[0] for block in blocks] == [
        "event: tool_call",
        "event: tool_result",
        "event: text_delta",
        "event: complete",
    ]
    call = json.loads(blocks[0].splitlines()[1].removeprefix("data: "))
    complete = json.loads(blocks[-1].splitlines()[1].removeprefix("data: "))
    assert call == {
        "id": "activity-1",
        "kind": "tool_call",
        "name": "Candidate rule",
        "message": "Sherlock is drafting and validating a candidate rule.",
    }
    assert complete["artifacts"][0]["type"] == "candidate_rule"
    assert complete["working_state"]["candidate_rule"] == "amount_usd > 1000"


def test_chat_endpoint_honors_explicit_event_stream_refusal() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_agent_factory] = StubChatAgentFactory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            headers={"Accept": "application/json, text/event-stream;q=0"},
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "Create a high-value rule",
                "working_state": {},
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["artifacts"][0]["type"] == "candidate_rule"


def test_chat_event_stream_returns_safe_error_after_paired_activity() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_agent_factory] = MissingStateChatAgentFactory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            headers={"Accept": "text/event-stream"},
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "Backtest it",
                "working_state": {},
            },
        )

    assert [block.splitlines()[0] for block in response.text.strip().split("\n\n")] == [
        "event: tool_call",
        "event: tool_result",
        "event: error",
    ]
    assert "Create or select a candidate rule" in response.text
    assert "BACKTEST_RULE" not in response.text


def test_chat_event_stream_hides_invalid_rule_details() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_agent_factory] = InvalidStateChatAgentFactory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            headers={"Accept": "text/event-stream"},
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "Backtest it",
                "working_state": {"candidate_rule": "is_fraud = 1"},
            },
        )

    assert response.text.count("event: tool_call") == 1
    assert response.text.count("event: tool_result") == 1
    assert response.text.count("event: error") == 1
    assert "saved candidate rule is no longer valid" in response.text
    assert "is_fraud" not in response.text
    assert "OUTCOME_COLUMN_FORBIDDEN" not in response.text


def test_chat_endpoint_returns_structured_missing_state_error() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_agent_factory] = MissingStateChatAgentFactory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "Backtest it",
                "working_state": {},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "MISSING_WORKING_STATE",
        "message": "BACKTEST_RULE requires working state: candidate_rule.",
        "intent": "BACKTEST_RULE",
        "missing_fields": ["candidate_rule"],
    }


def test_chat_endpoint_returns_structured_invalid_state_error() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_agent_factory] = InvalidStateChatAgentFactory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": "Backtest it",
                "working_state": {"candidate_rule": "is_fraud = 1"},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_WORKING_STATE"
    assert detail["intent"] == "BACKTEST_RULE"
    assert detail["errors"][0]["code"] == "OUTCOME_COLUMN_FORBIDDEN"


def test_chat_endpoint_rejects_blank_message() -> None:
    app = create_app()
    app.dependency_overrides[get_chat_agent_factory] = StubChatAgentFactory

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat",
            json={
                "conversation_id": "3b621bd5-98dd-4be0-b713-89b1ac751fab",
                "message": " ",
            },
        )

    assert response.status_code == 422


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
