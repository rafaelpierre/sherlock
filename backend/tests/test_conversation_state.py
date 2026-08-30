from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from sherlock.api.schemas import (
    MAX_HISTORY_MESSAGES,
    ConversationMessage,
    ConversationState,
    WorkingState,
)

CONVERSATION_ID = UUID("3b621bd5-98dd-4be0-b713-89b1ac751fab")
EMPTY_METRICS = {
    "population": 0,
    "labelled_population": 0,
    "fraud_total": 0,
    "transactions_flagged": 0,
    "unlabelled_flagged": 0,
    "fraud_caught": 0,
    "false_positives": 0,
    "false_negatives": 0,
    "true_negatives": 0,
    "precision": None,
    "recall": None,
    "false_positive_rate": None,
    "fraud_value_total_usd": 0.0,
    "fraud_value_captured_usd": 0.0,
    "fraud_value_recall": None,
    "alerts_per_day": None,
}


def test_empty_conversation_state_has_explicit_empty_working_state() -> None:
    state = ConversationState(conversation_id=CONVERSATION_ID)

    assert state.model_dump(mode="json") == {
        "conversation_id": str(CONVERSATION_ID),
        "messages": [],
        "working_state": {
            "candidate_rule": None,
            "previous_rule": None,
            "last_sql": None,
            "last_backtest": None,
        },
    }


def test_conversation_history_keeps_latest_twenty_messages() -> None:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "discarded before nested validation"},
        {"role": "user", "content": "also discarded"},
        {"role": "user", "content": "discarded too"},
        *[
            {"role": "user", "content": f"message {index}"}
            for index in range(MAX_HISTORY_MESSAGES)
        ],
    ]

    state = ConversationState(
        conversation_id=CONVERSATION_ID,
        messages=messages,
        working_state=WorkingState(candidate_rule="amount_usd > 1000"),
    )

    assert len(state.messages) == MAX_HISTORY_MESSAGES
    assert state.messages[0].content == "message 0"
    assert state.messages[-1].content == "message 19"
    assert state.working_state.candidate_rule == "amount_usd > 1000"


def test_conversation_state_round_trips_structured_referents() -> None:
    state = ConversationState(
        conversation_id=CONVERSATION_ID,
        messages=[
            ConversationMessage(role="user", content="Backtest it."),
            ConversationMessage(role="assistant", content="Here are the results."),
        ],
        working_state=WorkingState(
            candidate_rule="amount_usd > 1000",
            previous_rule="amount_usd > 500",
            last_sql="SELECT COUNT(*) FROM fraud_transactions",
            last_backtest={
                "rule": "amount_usd > 1000",
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
                    "fraud_value_total_usd": 1_000.0,
                    "fraud_value_captured_usd": 600.0,
                    "fraud_value_recall": 0.6,
                    "alerts_per_day": 2.0,
                },
            },
        ),
    )

    restored = ConversationState.model_validate_json(state.model_dump_json())

    assert restored == state
    assert restored.working_state.last_backtest is not None
    assert restored.working_state.last_backtest.metrics.unlabelled_flagged == 1


@pytest.mark.parametrize("rule", [" ", "x" * 20_001])
def test_saved_backtest_rejects_unbounded_rule(rule: str) -> None:
    with pytest.raises(ValidationError):
        WorkingState.model_validate(
            {
                "last_backtest": {
                    "rule": rule,
                    "metrics": EMPTY_METRICS,
                }
            }
        )


def test_saved_backtest_accepts_expanded_normalized_rule() -> None:
    normalized_rule = "amount_usd > 0 OR " * 400 + "amount_usd > 0"

    state = WorkingState.model_validate(
        {
            "candidate_rule": normalized_rule,
            "previous_rule": normalized_rule,
            "last_backtest": {
                "rule": normalized_rule,
                "metrics": EMPTY_METRICS,
            },
        }
    )

    assert state.last_backtest is not None
    assert state.candidate_rule == normalized_rule
    assert state.previous_rule == normalized_rule
    assert state.last_backtest.rule == normalized_rule


@pytest.mark.parametrize("field", ["candidate_rule", "previous_rule"])
def test_working_state_rejects_unbounded_normalized_rule(field: str) -> None:
    with pytest.raises(ValidationError):
        WorkingState.model_validate({field: "x" * 20_001})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_rule", " "),
        ("previous_rule", "\n"),
        ("last_sql", "\t"),
    ],
)
def test_working_state_rejects_blank_structured_referents(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        WorkingState.model_validate({field: value})


def test_conversation_message_normalizes_content_and_rejects_unknown_role() -> None:
    message = ConversationMessage(role="user", content="  Compare them.  ")

    assert message.content == "Compare them."
    with pytest.raises(ValidationError):
        ConversationMessage.model_validate({"role": "system", "content": "hidden"})
    with pytest.raises(ValidationError):
        ConversationMessage(role="assistant", content="   ")
